import base64
import csv
import io
import json
import logging
import re
from urllib.parse import unquote, urljoin

import httpx
from bs4 import BeautifulSoup
from google.cloud import secretmanager
from google.cloud.secretmanager_v1.types import SecretVersion
from google.oauth2 import service_account
from googleapiclient.discovery import build
from openai import OpenAI
from rapidocr_onnxruntime import RapidOCR

BASE_URL = "https://poona.ffbad.org"


def new():
    return PoonaUpdate()


class PoonaUpdate:
    _initialized = False

    def start(self, cfg):
        self.username = cfg["POONA_USERNAME"]
        self.password = cfg["POONA_PASSWORD"]
        self.export_template_id = cfg.get("POONA_EXPORT_TEMPLATE_ID", "26292")
        self.sheet_id = cfg["GOOGLE_SHEETS_ID"]
        self.sheet_name = cfg["GOOGLE_SHEET_NAME"]
        self._openai = OpenAI(api_key=cfg["OPENAI_API_KEY"])
        self._ocr = RapidOCR()
        self._secret_name = cfg.get("POONA_SECRET_NAME")

        sa_info = json.loads(cfg["GOOGLE_SERVICE_ACCOUNT_JSON"])
        sheets_credentials = service_account.Credentials.from_service_account_info(
            sa_info,
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        self.sheet = build("sheets", "v4", credentials=sheets_credentials)

        if self._secret_name:
            sm_credentials = service_account.Credentials.from_service_account_info(
                sa_info,
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
            self._secret_client = secretmanager.SecretManagerServiceClient(credentials=sm_credentials)
            self.session_cookies = self._load_cookies_from_secret_manager()
        else:
            raise ValueError("POONA_SECRET_NAME environment variable is required to persist session cookies. Without heva_device_tracker and PHPSESSID cookies, the function cannot be used and will require manual 2FA authentication.")

        self._initialized = True
        logging.info("Poona Update function started")
        logging.debug("Configuration: username=%s, sheet_id=%s, sheet_name=%s, export_template_id=%s, session_cookies=%s", self.username, self.sheet_id, self.sheet_name, self.export_template_id, self.session_cookies)

    async def handle(self, scope, receive, send):
        try:
            csv_content = await self._fetch_csv()
            diff = self._update_sheet(csv_content)
            status = 200
            body = json.dumps(diff, ensure_ascii=False).encode()
            content_type = b"application/json"
        except Exception as e:
            logging.exception("Failed to update members list")
            status = 500
            body = str(e).encode()
            content_type = b"text/plain"

        await send({"type": "http.response.start", "status": status, "headers": [[b"content-type", content_type]]})
        await send({"type": "http.response.body", "body": body})

    async def _fetch_csv(self):
        cookies = httpx.Cookies()
        for name, value in self.session_cookies.items():
            cookies.set(name, value, domain="poona.ffbad.org")
            logging.debug("Set session cookie: %s=%s", name, value)
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        }
        async with httpx.AsyncClient(follow_redirects=True, cookies=cookies, headers=headers) as client:
            await self._login(client)
            self._persist_cookies(client)
            logging.info("Logged in as %s", self.username)
            await self._load_export_template(client)
            await self._select_csv_format(client)
            csv_url = await self._get_csv_url(client)
            resp = await client.get(csv_url)
            resp.raise_for_status()
            return resp.content

    def _load_cookies_from_secret_manager(self):
        try:
            version = self._secret_client.access_secret_version(
                name=f"{self._secret_name}/versions/latest"
            )
            data = json.loads(version.payload.data.decode())
            logging.debug("Loaded cookies from Secret Manager: %s", list(data.keys()))
            return data
        except Exception as e:
            logging.warning("Failed to load cookies from Secret Manager: %s", e)
            return {}

    def _persist_cookies(self, client):
        updated = {
            name: value
            for name, value in client.cookies.items()
            if name in ("heva_device_tracker", "PHPSESSID")
        }
        if not updated:
            return
        self.session_cookies.update(updated)
        if self._secret_client:
            self._write_cookies_to_secret_manager()
        else:
            logging.debug("No Secret Manager configured, cookies not persisted across restarts")

    def _write_cookies_to_secret_manager(self):
        try:
            new_version = self._secret_client.add_secret_version(
                parent=self._secret_name,
                payload={"data": json.dumps(self.session_cookies).encode()},
            )
            logging.info("Wrote cookies to Secret Manager version: %s", new_version.name)
            for version in self._secret_client.list_secret_versions(
                parent=self._secret_name
            ):
                if version.name != new_version.name and version.state == SecretVersion.State.ENABLED:
                    self._secret_client.destroy_secret_version(name=version.name)
                    logging.debug("Destroyed old secret version: %s", version.name)
        except Exception as e:
            logging.warning("Failed to write cookies to Secret Manager: %s", e)

    async def _login(self, client):
        logging.info(f"Logging in as {self.username} on BASE_URL {BASE_URL}")
        resp = await client.get(f"{BASE_URL}/")
        soup = BeautifulSoup(resp.text, "html.parser")
        form = soup.find("form")
        if not form:
            raise ValueError("Login form not found on Poona homepage")

        data = {}
        for inp in form.find_all("input"):
            name = inp.get("name")
            if not name:
                continue
            data[str(name)] = inp.get("value", "")
        data["login_text_login"] = self.username
        data["login_text_password"] = self.password
        data["login_hidden_js"] = json.dumps({"platform":"MacIntel","screen":"1800x1169","timezone":"Europe/Luxembourg","language":"en-GB","color_depth":30,"touch":False})
        data.update(self._build_captcha_payload(resp.text))
        login_resp = await client.post(BASE_URL, data=data)

        if self._looks_like_sms_page(login_resp.text):
            raise ValueError("SMS login not supported")

        self._ensure_authenticated(login_resp, "submitting login form")

    async def _get_csrf(self, client, url):
        resp = await client.get(url)
        self._ensure_authenticated(resp, f"accessing {url}")
        m = re.search(r'name="_csrf_token"[^>]*value="([^"]+)"', resp.text)
        if not m:
            raise ValueError(f"CSRF token not found on {url}")
        return m.group(1)

    async def _load_export_template(self, client):
        csrf = await self._get_csrf(client, f"{BASE_URL}/page.php?P=bo/adherent/adherents/export/index")
        await client.post(
            f"{BASE_URL}/page.php?P=bo/adherent/adherents/export/list",
            data={
                "Action": "ixnet_export_process",
                "requestForm": "formLoad",
                "_csrf_token": csrf,
                "page_hidden_class": "IXNET_EXPORT_PROCESS_ADHERENT",
                "page_hidden_step": "index",
                "page_select_load": self.export_template_id,
            },
        )

    async def _select_csv_format(self, client):
        csrf = await self._get_csrf(client, f"{BASE_URL}/page.php?P=bo/adherent/adherents/export/format")
        await client.post(
            f"{BASE_URL}/page.php?P=bo/adherent/adherents/export/download",
            data={
                "Action": "ixnet_export_process",
                "requestForm": "formFormat",
                "_csrf_token": csrf,
                "page_hidden_class": "IXNET_EXPORT_PROCESS_ADHERENT",
                "page_hidden_step": "format",
                "page_radio_format": "IXNET_EXPORT_COMMUN_CSV_UTF",
            },
        )

    async def _get_csv_url(self, client):
        page_resp = await client.get(f"{BASE_URL}/page.php?P=bo/adherent/adherents/export/download")
        self._ensure_authenticated(page_resp, "opening export download page")
        resp = await client.post(
            f"{BASE_URL}/includer.php?inc=ajax/global/export/ixnet_export_format",
            data={"process": "IXNET_EXPORT_PROCESS_ADHERENT", "step": "list"},
        )
        self._ensure_authenticated(resp, "requesting export generation")
        m = re.search(r"window\.open\('([^']+)'\)", resp.text)
        if not m:
            raise ValueError(f"CSV URL not found in response: {resp.text[:200]}")
        return urljoin(f"{BASE_URL}/", m.group(1))

    def _ensure_authenticated(self, response, context):
        if self._looks_like_login_page(response.text):
            raise ValueError(f"Poona authentication failed while {context}")

    def _build_captcha_payload(self, html):
        selected_code = self._solve_captcha(html)
        logging.info("Captcha solution: %s", selected_code)

        token_fields, raw_fields, values, hashs = self._extract_captcha_script_data(html)

        # Look up the pre-computed server token for the selected option.
        # values[i] and hashs[i] are paired: hashs holds option codes, values holds their tokens.
        if selected_code and selected_code in hashs:
            token = values[hashs.index(selected_code)]
        else:
            logging.warning("Captcha code %r not in hashs, submitting empty token", selected_code)
            token = ""

        # captcha and raw_fields are set at page-load time by the JS before any selection,
        # so they're always empty when the form is submitted.
        payload = {"captcha": ""}
        for field in token_fields:
            payload[field] = token
        for field in raw_fields:
            payload[field] = ""
        return payload

    def _solve_captcha(self, html):
        soup = BeautifulSoup(html, "html.parser")
        label = soup.find(string=lambda t: t and "Image anti-robot" in t)
        if not label:
            return ""

        block = label.find_parent(class_="critere")
        if not block:
            return ""

        target_img = block.find("img")
        if not target_img or not target_img.get("src"):
            return ""
        logging.debug("Captcha target image: %s", target_img["src"])

        options = self._extract_captcha_options(soup)
        if not options:
            return ""

        option_labels = self._ocr_option_labels(options)
        return self._vision_match(target_img["src"], option_labels)

    def _extract_captcha_options(self, soup):
        options = []
        for img in soup.select("img.icone"):
            src = img.get("src")
            if not src:
                continue
            anchor = img.find_parent("a")
            value_node = anchor.find("span", class_="value") if anchor else None
            if not value_node:
                continue
            code = value_node.get_text(strip=True)
            if code:
                options.append((code, src))
        return options

    def _ocr_option_labels(self, options):
        result = []
        for code, src in options:
            ocr_result, _ = self._ocr(self._bytes_from_data_uri(src))
            text = " ".join(item[1] for item in ocr_result).strip() if ocr_result else ""
            result.append((code, text))
        return result

    def _vision_match(self, target_src, option_labels):
        # Generate list of options based on OCR result
        numbered = "\n".join(f"{i + 1}. {label}" for i, (_, label) in enumerate(option_labels) if label)
        prompt = (
            "Look at this captcha image. Which of the following descriptions best matches it?\n"
            f"{numbered}\n"
            "Reply with only the number of the best matching option."
        )
        logging.debug("Vision API prompt: %s", prompt)

        response = self._openai.responses.create(
            model="gpt-4o",
            input=[{
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {
                        "type": "input_image",
                        "image_url": target_src,
                        "detail": "low"
                    },
                ],
            }]
        )
        logging.debug("Vision API response: %s, used %d tokens", response.output_text, response.usage.total_tokens)
        raw = response.output_text.strip()
        m = re.search(r"\d+", raw)
        if not m:
            logging.warning("Vision API returned unexpected answer: %s", raw)
            return option_labels[0][0] if option_labels else ""

        idx = int(m.group()) - 1
        if 0 <= idx < len(option_labels):
            return option_labels[idx][0]

        logging.warning("Vision API index %d out of range for %d options", idx + 1, len(option_labels))
        return option_labels[0][0]

    @staticmethod
    def _bytes_from_data_uri(uri):
        if not uri.startswith("data:"):
            raise ValueError("Unsupported captcha image source")
        _, encoded = uri.split(",", 1)
        return base64.b64decode(encoded)

    def _extract_captcha_script_data(self, html):
        token_fields = []
        raw_fields = []
        values = []
        hashs = []
        for script_text in self._decode_obfuscated_captcha_scripts(html):
            values.extend(re.findall(r"values\.push\('([^']+)'\)", script_text))
            hashs.extend(re.findall(r"hashs\.push\('([^']+)'\)", script_text))
            token_fields.extend(re.findall(r"input\[name=([a-z]{5})\]\'\)\.val\(values\[position\]\)", script_text))
            raw_fields.extend(re.findall(r"input\[name=([a-z]{5})\]\'\)\.val\(getSelectedValue", script_text))
        return (
            list(dict.fromkeys(token_fields)),
            list(dict.fromkeys(raw_fields)),
            values,
            hashs,
        )

    def _decode_obfuscated_captcha_scripts(self, html):
        decoded = []
        for script in re.findall(r"<script[^>]*>([\s\S]*?)</script>", html):
            if "eval((function (r, a, n, t, e, s)" not in script:
                continue
            body = script.strip()
            try:
                decoded.append(self._decode_poona_obfuscated_script(body))
            except Exception:
                continue
        return decoded

    @staticmethod
    def _decode_poona_obfuscated_script(script):
        m = re.search(r"\}\)\('([^']*)',(\d+),'([^']*)',(\d+),(\d+),(\d+)\)\);?$", script)
        if not m:
            raise ValueError("Unsupported obfuscated script format")

        encoded = m.group(1)
        alphabet = m.group(3)
        t = int(m.group(4))
        base = int(m.group(5))

        delimiter = alphabet[base]
        output = []
        chunk = ""
        for ch in encoded:
            if ch != delimiter:
                chunk += ch
                continue

            normalized = chunk
            for idx, symbol in enumerate(alphabet):
                normalized = normalized.replace(symbol, str(idx))

            value = PoonaUpdate._decode_number_base(normalized, base)
            output.append(chr(value - t))
            chunk = ""

        return "".join(output)

    @staticmethod
    def _decode_number_base(text, base):
        charset = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ+/"
        valid = charset[:base]
        value = 0
        for power, ch in enumerate(reversed(text)):
            digit = valid.index(ch)
            value += digit * (base ** power)
        return value

    @staticmethod
    def _looks_like_login_page(html):
        return (
            "login_text_login" in html
            or "formControllerValidationPersonLogin" in html
            or "Image anti-robot" in html
        )

    @staticmethod
    def _looks_like_sms_page(html):
        lower = html.lower()
        return (
            "sms" in lower
            or "code de vérification" in lower
            or "code de verification" in lower
            or "double authentification" in lower
        ) and "login_text_login" not in html

    def _transform_rows(self, csv_content):
        text = csv_content.decode("utf-8-sig")
        rows = list(csv.reader(io.StringIO(text), delimiter=";"))
        if not rows:
            return []
        header = rows[0]
        nom_i = next((i for i, h in enumerate(header) if h.strip() == "Nom"), None)
        prenom_i = next((i for i, h in enumerate(header) if h.strip() in ("Prénom", "Prenom")), None)
        licence_i = next((i for i, h in enumerate(header) if h.strip() == "Licence"), None)
        naissance_i = next((i for i, h in enumerate(header) if "naissance" in h.lower()), None)
        email_i = next((i for i, h in enumerate(header) if h.strip() == "Email"), None)

        def _get(row, idx):
            return row[idx].strip() if idx is not None and idx < len(row) else ""

        result = [["Nom", "Licence", "Date de naissance", "Email"]]
        for row in rows[1:]:
            nom = _get(row, nom_i)
            prenom = _get(row, prenom_i)
            result.append([
                f"{nom} {prenom}".strip(),
                _get(row, licence_i),
                _get(row, naissance_i),
                _get(row, email_i),
            ])
        return result

    def _delete_tables(self, sheet_meta):
        requests = [
            {"deleteTable": {"tableId": t["tableId"]}}
            for t in sheet_meta.get("tables", [])
        ]
        if requests:
            self.sheet.spreadsheets().batchUpdate(
                spreadsheetId=self.sheet_id,
                body={"requests": requests},
            ).execute()

    def _add_table(self, sheet_id_num, num_rows):
        if num_rows <= 0:
            return
        self.sheet.spreadsheets().batchUpdate(
            spreadsheetId=self.sheet_id,
            body={"requests": [{
                "addTable": {
                    "table": {
                        "name": "Joueurs",
                        "range": {
                            "sheetId": sheet_id_num,
                            "startRowIndex": 0,
                            "endRowIndex": num_rows,
                            "startColumnIndex": 0,
                            "endColumnIndex": 4,
                        },
                    }
                }
            }]},
        ).execute()

    def _update_sheet(self, csv_content):
        rows = self._transform_rows(csv_content)

        spreadsheet = self.sheet.spreadsheets().get(spreadsheetId=self.sheet_id).execute()
        sheet_meta = next(
            s for s in spreadsheet["sheets"]
            if s["properties"]["title"] == self.sheet_name
        )
        sheet_id_num = sheet_meta["properties"]["sheetId"]

        api = self.sheet.spreadsheets().values()
        existing = api.get(spreadsheetId=self.sheet_id, range=self.sheet_name).execute()
        old_rows = existing.get("values", [])

        # Delete tables before clearing/writing — deleteTable wipes cell data,
        # so it must happen before we populate the sheet with new rows.
        self._delete_tables(sheet_meta)

        api.clear(spreadsheetId=self.sheet_id, range=self.sheet_name).execute()
        if rows:
            api.update(
                spreadsheetId=self.sheet_id,
                range=f"{self.sheet_name}!A1",
                valueInputOption="RAW",
                body={"values": rows},
            ).execute()

        self._add_table(sheet_id_num, len(rows))

        diff = self._compute_diff(old_rows, rows)
        logging.info(
            "Updated sheet %r with %d rows: %d added, %d removed",
            self.sheet_name, diff["total"], len(diff["added"]), len(diff["removed"]),
        )
        return diff

    def _compute_diff(self, old_rows, new_rows):
        if not new_rows:
            return {"total": 0, "added": [], "removed": []}

        header = new_rows[0]
        licence_col = next((i for i, h in enumerate(header) if "licence" in h.lower()), None)

        if licence_col is None or not old_rows:
            return {"total": max(0, len(new_rows) - 1), "added": [], "removed": []}

        old_header = old_rows[0]
        old_licence_col = next(
            (i for i, h in enumerate(old_header) if "licence" in h.lower()), licence_col
        )

        def _full_name(row, hdr):
            prenom_col = next((i for i, h in enumerate(hdr) if "prénom" in h.lower()), None)
            nom_col = next((i for i, h in enumerate(hdr) if "nom" in h.lower() and "prénom" not in h.lower()), None)
            parts = []
            if prenom_col is not None and prenom_col < len(row):
                parts.append(row[prenom_col])
            if nom_col is not None and nom_col < len(row):
                parts.append(row[nom_col])
            return " ".join(parts)

        old_licences = {
            row[old_licence_col] for row in old_rows[1:] if len(row) > old_licence_col
        }
        new_licences = {
            row[licence_col] for row in new_rows[1:] if len(row) > licence_col
        }

        added = [
            _full_name(row, header)
            for row in new_rows[1:]
            if len(row) > licence_col and row[licence_col] not in old_licences
        ]
        removed = [
            _full_name(row, old_header)
            for row in old_rows[1:]
            if len(row) > old_licence_col and row[old_licence_col] not in new_licences
        ]

        return {"total": len(new_licences), "added": added, "removed": removed}

    def stop(self):
        logging.info("Function stopping")

    def alive(self):
        return True, "Alive"

    def ready(self):
        if self._initialized:
            return True, "Ready"
        return False, "Not initialized"
