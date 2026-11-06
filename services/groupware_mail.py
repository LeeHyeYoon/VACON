"""회사 메일 로그인 정보의 선택적 저장(Windows DPAPI 암호화). 메일 조회는 services/imap_mail.py가 담당한다."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def configured() -> bool:
    """로그인 정보 저장(DPAPI)은 Windows에서만 지원한다."""
    return sys.platform == "win32"


def _secret_path() -> Path:
    root = Path(os.getenv("LOCALAPPDATA") or (Path.home() / "AppData/Local"))
    return root / "VACON" / "groupware_credentials.bin"


def save_credentials(username: str, password: str) -> None:
    """Persist credentials only after explicit opt-in, protected by Windows DPAPI."""
    if not configured():
        raise RuntimeError("로그인 정보 저장은 Windows에서만 지원합니다.")
    try:
        import win32crypt
    except ImportError as exc:
        raise RuntimeError("Windows 계정 암호화 구성요소가 없습니다. requirements.txt를 설치하세요.") from exc
    payload = json.dumps({"username": username, "password": password}, ensure_ascii=False).encode("utf-8")
    protected = win32crypt.CryptProtectData(payload, "VACON groupware login", None, None, None, 0)
    path = _secret_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(protected)


def load_credentials() -> dict | None:
    path = _secret_path()
    if not path.is_file():
        return None
    try:
        import win32crypt
        _, raw = win32crypt.CryptUnprotectData(path.read_bytes(), None, None, None, 0)
        data = json.loads(raw.decode("utf-8"))
        if data.get("username") and data.get("password"):
            return {"username": data["username"], "password": data["password"]}
    except Exception as exc:
        raise RuntimeError("저장된 로그인 정보를 현재 Windows 계정으로 복호화하지 못했습니다. 저장 정보를 지우고 다시 로그인하세요.") from exc
    return None


def delete_credentials() -> None:
    _secret_path().unlink(missing_ok=True)


def has_saved_credentials() -> bool:
    return _secret_path().is_file()
