# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run locally (outside container, recommended for development)
func run --builder=host

# Run tests
pytest tests/

# Run a single test
pytest tests/test_func.py::test_login_submits_credentials_and_captcha

# Deploy to Knative cluster
func deploy --registry docker.io/jeremyalbrecht
```

Dependencies are declared in `pyproject.toml`. Install with `pip install -e .` or your preferred tool.

## Architecture

This is a **Knative Python HTTP function** that scrapes the Poona (FFBAD) member management platform and syncs the export to a Google Sheet.

### KNative lifecycle

The `function/func.py` module exposes `new()` which returns a `PoonaUpdate` instance. The KNative runtime calls:
- `start(cfg)` — receives env vars as a dict; initializes `httpx`, Google Sheets API, and OpenAI clients.
- `handle()` — triggered on every HTTP request; runs the full scrape-and-sync pipeline unconditionally.
- `stop()` / `alive()` / `ready()` — standard lifecycle/health hooks.

### Pipeline flow

`handle()` → `_fetch_csv()` → `_update_sheet()`

`_fetch_csv()` drives a multi-step authenticated session against `poona.ffbad.org`:
1. **Login** (`_login`): parses the login form, auto-solves the captcha, submits credentials. If persisted session cookies are valid (via `POONA_SESSION_COOKIES`), the login step is skipped.
2. **Load export template** (`_load_export_template`): POSTs to select the configured template ID.
3. **Select CSV format** (`_select_csv_format`): POSTs to choose UTF-8 CSV.
4. **Get download URL** (`_get_csv_url`): triggers async export generation via an AJAX endpoint, parses the `window.open(...)` URL from the response.

### Captcha solving

Poona uses a custom anti-robot widget with obfuscated JS. The solve pipeline in `_build_captcha_payload` / `_solve_captcha`:
1. **Deobfuscate** the inline `<script>` using `_decode_poona_obfuscated_script` to extract `values[]`/`hashs[]` arrays and the dynamic hidden field names.
2. **OCR** each option image via `ddddocr` (pure-Python ONNX, no system binary required).
3. **Vision match** the target image against OCR labels using `gpt-4o` via `openai.responses.create`.
4. Look up the precomputed server token for the winning option code and fill the dynamic hidden fields.

### Environment variables

Configured via `func.yaml` `run.envs`, read in `start(cfg)`:

| Variable | Purpose |
|---|---|
| `POONA_USERNAME` / `POONA_PASSWORD` | Login credentials |
| `POONA_SESSION_COOKIES` | JSON dict of persisted cookies to skip login |
| `POONA_EXPORT_TEMPLATE_ID` | Export template (default `26292`) |
| `GOOGLE_SHEETS_ID` | Target spreadsheet ID |
| `POONA_SHEET_NAME` | Sheet tab name (mapped to `GOOGLE_SHEET_NAME` in func.yaml) |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Full service account JSON payload |
| `OPENAI_API_KEY` | For GPT-4o vision captcha solving |

### Tests

Tests in `tests/test_func.py` use `pytest-asyncio` in strict mode. They test internal methods directly on `PoonaUpdate` instances using `FakeClient` (a simple async stub for `httpx.AsyncClient`) and `monkeypatch` for OCR/vision calls. No live network calls are made.
