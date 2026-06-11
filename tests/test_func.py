import base64
import io
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from function.func import PoonaUpdate


class FakeResponse:
    def __init__(self, text):
        self.text = text


class FakeClient:
    def __init__(self, get_responses=None, post_responses=None):
        self.get_responses = list(get_responses or [])
        self.post_responses = list(post_responses or [])
        self.post_calls = []

    async def get(self, url):
        if not self.get_responses:
            raise AssertionError(f"Unexpected GET call to {url}")
        return self.get_responses.pop(0)

    async def post(self, url, data=None):
        self.post_calls.append({"url": url, "data": data})
        if not self.post_responses:
            raise AssertionError(f"Unexpected POST call to {url}")
        return self.post_responses.pop(0)


def _make_data_uri(size=(32, 32), color=128):
    img = Image.new("RGB", size, color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _fake_openai_client(answer: str):
    response = MagicMock()
    response.output_text = answer
    client = MagicMock()
    client.responses.create.return_value = response
    return client


@pytest.mark.asyncio
async def test_login_submits_credentials_and_captcha():
    updater = PoonaUpdate()
    updater.username = "alice"
    updater.password = "secret"

    login_page = """
    <html><body>
    <form action="/">
      <input type="hidden" name="requestForm" value="formControllerValidationPersonLoginForm123" />
      <input type="text" name="login_text_login" value="" />
      <input type="password" name="login_text_password" value="" />
      <input type="text" name="captcha" value="" />
      <input type="hidden" name="_csrf_token" value="token123" />
    </form>
    </body></html>
    """
    logged_page = "<html><body>POONA - ESPACE DIRIGEANT</body></html>"
    client = FakeClient(
        get_responses=[FakeResponse(login_page)],
        post_responses=[FakeResponse(logged_page)],
    )

    await updater._login(client)

    assert len(client.post_calls) == 1
    sent_data = client.post_calls[0]["data"]
    assert sent_data["login_text_login"] == "alice"
    assert sent_data["login_text_password"] == "secret"
    assert "captcha" in sent_data


@pytest.mark.asyncio
async def test_login_raises_when_page_still_looks_like_login_form():
    updater = PoonaUpdate()
    updater.username = "alice"
    updater.password = "secret"

    login_page = """
    <html><body>
    <form action="/">
      <input type="text" name="login_text_login" value="" />
      <input type="password" name="login_text_password" value="" />
      <input type="text" name="captcha" value="" />
    </form>
    </body></html>
    """
    failed_response = "<html><body><span>Image anti-robot</span><input name='login_text_login' /></body></html>"
    client = FakeClient(
        get_responses=[FakeResponse(login_page)],
        post_responses=[FakeResponse(failed_response)],
    )

    with pytest.raises(ValueError, match="authentication failed"):
        await updater._login(client)


@pytest.mark.asyncio
async def test_get_csrf_raises_when_not_authenticated():
    updater = PoonaUpdate()
    client = FakeClient(get_responses=[FakeResponse("<html><input name='login_text_login' /></html>")])

    with pytest.raises(ValueError, match="authentication failed"):
        await updater._get_csrf(client, "https://poona.ffbad.org/page.php?P=bo/adherent/adherents/export/index")


def test_extract_captcha_script_data(monkeypatch):
    updater = PoonaUpdate()

    def fake_decoder(_html):
        return [
            "values.push('token_a');"
            "values.push('token_b');"
            "hashs.push('CodeX');"
            "hashs.push('CodeY');"
            "$('input[name=abcde]').val(values[position]);"
            "$('input[name=fghij]').val(values[position]);"
            "$('input[name=klmno]').val(getSelectedValue(\"x\"));"
        ]

    monkeypatch.setattr(updater, "_decode_obfuscated_captcha_scripts", fake_decoder)
    token_fields, raw_fields, values, hashs = updater._extract_captcha_script_data("<html></html>")
    assert token_fields == ["abcde", "fghij"]
    assert raw_fields == ["klmno"]
    assert values == ["token_a", "token_b"]
    assert hashs == ["CodeX", "CodeY"]


def test_build_captcha_payload_uses_precomputed_token(monkeypatch):
    updater = PoonaUpdate()
    monkeypatch.setattr(updater, "_solve_captcha", lambda _: "CodeX")
    monkeypatch.setattr(
        updater,
        "_extract_captcha_script_data",
        lambda _: (["abcde", "fghij"], ["klmno"], ["token_a", "token_b"], ["CodeX", "CodeY"]),
    )

    payload = updater._build_captcha_payload("<html></html>")
    assert payload["abcde"] == "token_a"
    assert payload["fghij"] == "token_a"
    assert payload["captcha"] == ""
    assert payload["klmno"] == ""


def test_solve_captcha_returns_empty_when_no_captcha_widget():
    updater = PoonaUpdate()
    result = updater._solve_captcha("<html><body>No captcha here</body></html>")
    assert result == ""


def _fake_ocr(labels):
    it = iter(labels)
    fake = MagicMock()
    fake.classification.side_effect = lambda _bytes: next(it)
    return fake


def test_solve_captcha_uses_ocr_and_vision_to_pick_correct_code():
    updater = PoonaUpdate()
    updater._openai = _fake_openai_client("1")  # Vision API picks option 1
    updater._ocr = _fake_ocr(["Un nuage", "Un bateau"])

    target = _make_data_uri(color=100)
    opt_nuage = _make_data_uri(color=200)
    opt_bateau = _make_data_uri(color=50)

    html = f"""
    <html><body>
      <div class="critere"><span>Image anti-robot :</span><img src="{target}" /></div>
      <ul>
        <li><a href="#"><img class="icone" src="{opt_nuage}" /><span class="value">CODE_NUAGE</span></a></li>
        <li><a href="#"><img class="icone" src="{opt_bateau}" /><span class="value">CODE_BATEAU</span></a></li>
      </ul>
    </body></html>
    """

    result = updater._solve_captcha(html)
    assert result == "CODE_NUAGE"


def test_solve_captcha_vision_picks_second_option():
    updater = PoonaUpdate()
    updater._openai = _fake_openai_client("2")  # Vision API picks option 2
    updater._ocr = _fake_ocr(["Un nuage", "Un bateau"])

    target = _make_data_uri(color=100)
    opt_nuage = _make_data_uri(color=200)
    opt_bateau = _make_data_uri(color=50)

    html = f"""
    <html><body>
      <div class="critere"><span>Image anti-robot :</span><img src="{target}" /></div>
      <ul>
        <li><a href="#"><img class="icone" src="{opt_nuage}" /><span class="value">CODE_NUAGE</span></a></li>
        <li><a href="#"><img class="icone" src="{opt_bateau}" /><span class="value">CODE_BATEAU</span></a></li>
      </ul>
    </body></html>
    """

    result = updater._solve_captcha(html)
    assert result == "CODE_BATEAU"


def test_transform_rows_concatenates_nom_prenom_and_selects_columns():
    updater = PoonaUpdate()
    csv_content = "Nom;Prénom;Licence;Date naissance;Email\nDupont;Alice;123456;01/01/1990;alice@example.com\n".encode("utf-8")
    rows = updater._transform_rows(csv_content)
    assert rows[0] == ["Nom", "Licence", "Date de naissance", "Email"]
    assert rows[1] == ["Dupont Alice", "123456", "01/01/1990", "alice@example.com"]


def test_transform_rows_empty_csv():
    updater = PoonaUpdate()
    assert updater._transform_rows(b"") == []


def test_transform_rows_handles_missing_prenom():
    updater = PoonaUpdate()
    csv_content = "Nom;Licence;Date naissance;Email\nDupont;123456;01/01/1990;alice@example.com\n".encode("utf-8")
    rows = updater._transform_rows(csv_content)
    assert rows[1][0] == "Dupont"
