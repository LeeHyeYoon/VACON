"""Read private Google Sheets through desktop OAuth; tokens are DPAPI protected on Windows."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


def _root() -> Path:
    return Path(os.getenv("LOCALAPPDATA") or (Path.home() / "AppData/Local")) / "VACON"


def _protect(raw: bytes) -> bytes:
    if sys.platform != "win32":
        raise RuntimeError("Google 계정 토큰의 로컬 보호는 Windows에서만 지원합니다.")
    try:
        import win32crypt
    except ImportError:
        raise RuntimeError("Windows DPAPI 모듈이 없습니다. requirements.txt를 설치하세요.") from None
    return win32crypt.CryptProtectData(raw, "VACON Google Sheets OAuth", None, None, None, 0)


def _unprotect(raw: bytes) -> bytes:
    import win32crypt
    _, data = win32crypt.CryptUnprotectData(raw, None, None, None, 0)
    return data


def _save_encrypted(name: str, payload: bytes) -> None:
    path = _root() / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_protect(payload))


def _load_encrypted(name: str) -> bytes | None:
    path = _root() / name
    if not path.is_file():
        return None
    try:
        return _unprotect(path.read_bytes())
    except Exception:
        raise RuntimeError("저장된 Google 연결 정보를 이 Windows 계정에서 복호화하지 못했습니다. 연결을 초기화하고 다시 승인하세요.") from None


def save_oauth_client(raw: bytes) -> None:
    try:
        config = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("Google OAuth client JSON 파일 형식이 올바르지 않습니다.") from None
    if not isinstance(config, dict) or not config.get("installed"):
        raise ValueError("Google Cloud에서 앱 유형을 'Desktop app'으로 만들어 다운로드한 OAuth JSON을 선택하세요.")
    _save_encrypted("google_sheets_client.bin", json.dumps(config).encode("utf-8"))


def has_client() -> bool:
    return (_root() / "google_sheets_client.bin").is_file()


def is_connected() -> bool:
    return (_root() / "google_sheets_token.bin").is_file()


def disconnect() -> None:
    (_root() / "google_sheets_token.bin").unlink(missing_ok=True)
    (_root() / "google_sheets_client.bin").unlink(missing_ok=True)


def credentials(*, authorize: bool = False, client_json: bytes | None = None):
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        raise RuntimeError("Google Sheets OAuth 패키지가 없습니다. requirements.txt를 설치하세요.") from None

    if client_json:
        save_oauth_client(client_json)
    client_raw = _load_encrypted("google_sheets_client.bin")
    token_raw = _load_encrypted("google_sheets_token.bin")
    creds = None
    if token_raw:
        try:
            creds = Credentials.from_authorized_user_info(json.loads(token_raw.decode("utf-8")), SCOPES)
        except Exception:
            creds = None
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_encrypted("google_sheets_token.bin", creds.to_json().encode("utf-8"))
            return creds
        except Exception:
            creds = None
    if not authorize:
        return None
    if not client_raw:
        raise RuntimeError("Google OAuth Desktop client JSON을 먼저 등록해야 합니다.")
    flow = InstalledAppFlow.from_client_config(json.loads(client_raw.decode("utf-8")), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True, authorization_prompt_message="브라우저에서 Google 계정 접근을 승인하세요.")
    _save_encrypted("google_sheets_token.bin", creds.to_json().encode("utf-8"))
    return creds


def authorize(client_json: bytes | None = None):
    return credentials(authorize=True, client_json=client_json)


def list_spreadsheet_tabs(ref: str) -> list[dict]:
    service = _service()
    spreadsheet_id = _sheet_id(ref)
    try:
        data = service.spreadsheets().get(spreadsheetId=spreadsheet_id,
                                          fields="properties(title),sheets(properties(sheetId,title,index,gridProperties(columnCount)))").execute()
    except Exception as exc:
        raise _api_error(exc) from None
    return [{"gid": str(sheet["properties"]["sheetId"]), "title": sheet["properties"]["title"],
             "index": sheet["properties"].get("index", 0),
             "column_count": int((sheet["properties"].get("gridProperties") or {}).get("columnCount") or 26)}
            for sheet in data.get("sheets", [])]


def read_tab(ref: str, tab: dict) -> list[list[str]]:
    service = _service()
    spreadsheet_id = _sheet_id(ref)
    title = str(tab["title"]).replace("'", "''")
    end_col = _column_letter(min(1000, int(tab.get("column_count") or 26)))
    try:
        data = service.spreadsheets().values().get(spreadsheetId=spreadsheet_id,
                                                   range=f"'{title}'!A:{end_col}",
                                                   valueRenderOption="FORMATTED_VALUE").execute()
    except Exception as exc:
        raise _api_error(exc) from None
    return [[str(value) for value in row] for row in data.get("values", [])]


def _service():
    try:
        from googleapiclient.discovery import build
    except ImportError:
        raise RuntimeError("Google Sheets API 패키지가 없습니다. requirements.txt를 설치하세요.") from None
    creds = credentials(authorize=False)
    if not creds:
        raise RuntimeError("Google Sheets에 연결되지 않았습니다. VACON에서 Google 계정 연결을 먼저 진행하세요.")
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _sheet_id(ref: str) -> str:
    from .sheet_sync import extract_sheet_id
    return extract_sheet_id(ref)


def _column_letter(number: int) -> str:
    out = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        out = chr(65 + remainder) + out
    return out or "A"


def _api_error(exc: Exception) -> RuntimeError:
    status = getattr(getattr(exc, "resp", None), "status", None)
    if status == 401:
        return RuntimeError("Google 인증이 만료됐습니다. 계정 연결을 다시 진행하세요.")
    if status == 403:
        return RuntimeError("연결한 Google 계정에 이 시트를 읽을 권한이 없거나 Sheets API가 활성화되지 않았습니다.")
    if status == 404:
        return RuntimeError("스프레드시트를 찾지 못했습니다. 링크와 계정 접근 권한을 확인하세요.")
    return RuntimeError(f"Google Sheets API 조회에 실패했습니다: {exc}")
