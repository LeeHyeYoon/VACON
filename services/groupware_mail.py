"""Read received mail from the company groupware web UI (read-only)."""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path

from services.mail_digest import parse_date

MAIL_URL = "https://gw.suresofttech.com/app/mail"
HOME_URL = "https://gw.suresofttech.com/"
MAX_MESSAGES = 500
_SHORT_DATE_RE = re.compile(r"(?<!\d)(\d{1,2})\s*(?:월|[./-])\s*(\d{1,2})\s*(?:일)?")


def configured() -> bool:
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


def _driver():
    try:
        from selenium import webdriver
    except ImportError as exc:
        raise RuntimeError("그룹웨어 메일 연동에는 Selenium이 필요합니다. pip install -r requirements.txt를 실행하세요.") from exc
    options = webdriver.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1440,1200")
    try:
        return webdriver.Chrome(options=options)
    except Exception as exc:
        raise RuntimeError("Chrome을 실행하지 못했습니다. Chrome 설치 상태와 사내망 접속을 확인하세요. 처음 실행 시 Selenium이 드라이버를 준비할 네트워크 연결이 필요할 수 있습니다.") from exc


def _visible_text(element) -> str:
    try:
        return " ".join((element.text or "").split())
    except Exception:
        return ""


def _context_paths(driver, max_depth: int = 4) -> list[tuple[int, ...]]:
    """Return the main document and nested iframe paths, restoring default context."""
    from selenium.webdriver.common.by import By
    paths: list[tuple[int, ...]] = [()]

    def visit(path: tuple[int, ...]) -> None:
        if len(path) >= max_depth:
            return
        driver.switch_to.default_content()
        for index in path:
            frames = driver.find_elements(By.TAG_NAME, "iframe")
            if index >= len(frames):
                return
            driver.switch_to.frame(frames[index])
        count = len(driver.find_elements(By.TAG_NAME, "iframe"))
        for index in range(count):
            child = path + (index,)
            paths.append(child)
            visit(child)

    visit(())
    driver.switch_to.default_content()
    return paths


def _switch_context(driver, path: tuple[int, ...]) -> None:
    from selenium.webdriver.common.by import By
    driver.switch_to.default_content()
    for index in path:
        frames = driver.find_elements(By.TAG_NAME, "iframe")
        if index >= len(frames):
            raise RuntimeError("그룹웨어 메일 프레임이 다시 로드되어 내용을 찾지 못했습니다.")
        driver.switch_to.frame(frames[index])


def _parse_mail_date(value: str, start: date, end: date) -> date | None:
    """Parse full dates and yearless dates shown by the groupware list."""
    parsed = parse_date(value)
    if parsed:
        return parsed if start <= parsed <= end else None
    text = str(value or "")
    if "오늘" in text:
        candidate = date.today()
        return candidate if start <= candidate <= end else None
    if "어제" in text:
        candidate = date.today() - timedelta(days=1)
        return candidate if start <= candidate <= end else None
    matches = list(_SHORT_DATE_RE.finditer(text))
    for match in matches:
        month, day = int(match.group(1)), int(match.group(2))
        for year in range(start.year, end.year + 1):
            try:
                candidate = date(year, month, day)
            except ValueError:
                continue
            if start <= candidate <= end:
                return candidate
    return None


def _login(driver, username: str, password: str, wait) -> None:
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys

    driver.get(HOME_URL)
    wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
    login_url = driver.current_url
    user_selectors = ("input[name='userId']", "input[name='userid']", "input[name='user_id']",
                      "input[name='username']", "input[name='loginId']", "input[name='login_id']",
                      "input[name='id']", "input[type='email']", "input[type='text']")

    def find_login_fields():
        for path in _context_paths(driver):
            try:
                _switch_context(driver, path)
            except Exception:
                continue
            user_field = next((el for selector in user_selectors for el in driver.find_elements(By.CSS_SELECTOR, selector)
                               if el.is_displayed() and el.is_enabled()), None)
            pass_field = next((el for el in driver.find_elements(By.CSS_SELECTOR, "input[type='password']")
                               if el.is_displayed() and el.is_enabled()), None)
            if user_field and pass_field:
                return user_field, pass_field
        driver.switch_to.default_content()
        return None, None

    try:
        user_field, pass_field = wait.until(
            lambda _driver: (fields if all(fields := find_login_fields()) else False)
        )
    except Exception as exc:
        raise RuntimeError("로그인 입력칸을 찾지 못했습니다. 그룹웨어 페이지 구조나 iframe 로그인을 확인하세요.") from exc
    user_field.clear(); user_field.send_keys(username)
    pass_field.clear(); pass_field.send_keys(password)
    clicked = False
    for selector in ("button[type='submit']", "input[type='submit']", "button.login", "button[id*='login']", "input[id*='login']"):
        buttons = [el for el in driver.find_elements(By.CSS_SELECTOR, selector) if el.is_displayed() and el.is_enabled()]
        if buttons:
            buttons[0].click(); clicked = True; break
    if not clicked:
        for xpath in ("//button[contains(normalize-space(.), '로그인')]", "//a[contains(normalize-space(.), '로그인')]",
                      "//button[contains(translate(normalize-space(.), 'LOGIN', 'login'), 'login')]"):
            buttons = [el for el in driver.find_elements(By.XPATH, xpath) if el.is_displayed() and el.is_enabled()]
            if buttons:
                buttons[0].click(); clicked = True; break
    if not clicked:
        pass_field.send_keys(Keys.ENTER)
    try:
        wait.until(lambda d: d.current_url != login_url)
    except Exception as exc:
        raise RuntimeError("로그인 후 화면이 바뀌지 않았습니다. 아이디·비밀번호, 추가 인증 또는 계정 잠금 상태를 확인하세요.") from exc
    driver.switch_to.default_content()
    if any(el.is_displayed() for el in driver.find_elements(By.CSS_SELECTOR, "input[type='password']")):
        raise RuntimeError("그룹웨어 로그인이 완료되지 않았습니다. 아이디·비밀번호와 계정 상태를 확인하세요.")


def _mail_rows(driver):
    """Collect likely message rows and their visible fields without changing messages."""
    from selenium.webdriver.common.by import By
    row_selectors = ("table tbody tr", "[role='row']", "[role='listitem']", "[class*='mail-item']",
                     "[class*='mail_item']", "[class*='message-item']", "[class*='message_item']",
                     "[class*='mail-row']", "[class*='mail_row']")
    seen, result = set(), []
    subject_selectors = ("[data-subject]", "[data-testid*='subject']", "[class*='mail-subject']",
                         "[class*='mail_subject']", "[class*='mail-title']", "[class*='mail_title']",
                         "[class*='subject']", "[class*='Subject']", "td.subject", "a.subject")
    driver.switch_to.default_content()
    for frame_index, path in enumerate(_context_paths(driver)):
        try:
            _switch_context(driver, path)
        except Exception:
            continue
        # First collect subject-specific nodes; some groupware builds do not use table/list row tags.
        for selector in subject_selectors:
            for subject_el in driver.find_elements(By.CSS_SELECTOR, selector):
                if not subject_el.is_displayed():
                    continue
                subject = " ".join((subject_el.get_attribute("data-subject") or subject_el.get_attribute("aria-label") or
                                    subject_el.get_attribute("title") or _visible_text(subject_el)).split())
                ignored = {"inbox", "받은메일함", "받은 메일함", "수신함", "메일쓰기", "메일 작성",
                           "보낸메일함", "스팸메일함", "임시보관함", "휴지통", "제목", "날짜", "보낸 사람"}
                if (not subject or len(subject) > 300 or subject.lower() in ignored or
                        subject.isnumeric() or len(subject) < 2):
                    continue
                row_text = driver.execute_script("return arguments[0].closest('tr,[role=row],[role=listitem],[class*=mail-item],[class*=message-item]')?.innerText || arguments[0].innerText || ''", subject_el)
                row_text = " ".join((row_text or "").split())
                if len(subject) < 2 or (subject.isnumeric()):
                    continue
                sender = ""
                key = (frame_index, subject, row_text)
                if key not in seen:
                    seen.add(key)
                    result.append({"frame_index": frame_index, "subject": subject, "row_text": row_text or subject, "sender": sender})
        rows = [row for selector in row_selectors for row in driver.find_elements(By.CSS_SELECTOR, selector)]
        for row in rows:
            if not row.is_displayed():
                continue
            row_text = _visible_text(row)
            if not row_text or len(row_text) > 3000:
                continue
            possible = [_visible_text(el) for selector in subject_selectors for el in row.find_elements(By.CSS_SELECTOR, selector)]
            possible += [_visible_text(el) for el in row.find_elements(By.CSS_SELECTOR, "a, [role='link'], [title]")]
            subject = max((text for text in possible if text and len(text) < 500), key=len, default="")
            if not subject:
                continue
            cells = [_visible_text(el) for el in row.find_elements(By.CSS_SELECTOR, "td,[role='gridcell']")]
            date_text = next((cell for cell in cells if cell and
                              (parse_date(cell) is not None or _SHORT_DATE_RE.search(cell) or "오늘" in cell or "어제" in cell)), row_text)
            sender = next((cell for cell in cells if cell and subject not in cell and
                           not (parse_date(cell) is not None or _SHORT_DATE_RE.search(cell) or "오늘" in cell or "어제" in cell)), "")
            key = (frame_index, subject, row_text)
            if key not in seen:
                seen.add(key)
                result.append({"frame_index": frame_index, "subject": subject, "row_text": row_text,
                               "date_text": date_text, "sender": sender})
    driver.switch_to.default_content()
    return result


def _click_inbox(driver) -> bool:
    from selenium.webdriver.common.by import By
    labels = ("inbox", "받은 메일함", "수신함", "받은메일함")
    driver.switch_to.default_content()
    for path in _context_paths(driver):
        try:
            _switch_context(driver, path)
        except Exception:
            continue
        for el in driver.find_elements(By.CSS_SELECTOR, "a,button,[role='tab'],[role='treeitem'],[role='link']"):
            if el.is_displayed() and any(label in _visible_text(el).lower() for label in labels):
                el.click()
                driver.switch_to.default_content()
                return True
    driver.switch_to.default_content()
    return False


def _click_subject(driver, subject: str) -> bool:
    from selenium.webdriver.common.by import By
    selectors = ("[data-subject]", "[data-testid*='subject']", "[class*='mail-subject']", "[class*='mail_subject']",
                 "[class*='mail-title']", "[class*='mail_title']", "[class*='subject']", "[class*='Subject']",
                 "table tbody tr a", "table tbody tr", "[role='row'] a", "[role='row']", "[role='listitem']",
                 "[class*='mail-item'] a", "[class*='mail-item']", "[class*='message-item'] a", "[class*='message-item']",
                 "[class*='mail-row']")
    driver.switch_to.default_content()
    for path in _context_paths(driver):
        try:
            _switch_context(driver, path)
        except Exception:
            continue
        for selector in selectors:
            for el in driver.find_elements(By.CSS_SELECTOR, selector):
                label = (el.get_attribute("data-subject") or el.get_attribute("aria-label") or
                         el.get_attribute("title") or _visible_text(el)).strip()
                if el.is_displayed() and label == subject.strip():
                    el.click()
                    driver.switch_to.default_content()
                    return True
    driver.switch_to.default_content()
    return False


def _click_next_page(driver, wait) -> bool:
    """Advance the groupware inbox pagination when a next-page control exists."""
    from selenium.webdriver.common.by import By
    previous = tuple(item["subject"] for item in _mail_rows(driver)[:3])
    labels = ("다음 페이지", "다음", "next", "older", "›", "»", ">")
    for path in _context_paths(driver):
        try:
            _switch_context(driver, path)
        except Exception:
            continue
        for element in driver.find_elements(By.CSS_SELECTOR, "button,a,[role='button']"):
            if not element.is_displayed() or not element.is_enabled():
                continue
            label = " ".join((element.get_attribute("aria-label") or "", element.get_attribute("title") or "",
                              _visible_text(element))).strip().lower()
            disabled = (element.get_attribute("aria-disabled") == "true" or
                        "disabled" in (element.get_attribute("class") or "").lower())
            if disabled or not any(token in label for token in labels):
                continue
            try:
                element.click()
                driver.switch_to.default_content()
                try:
                    wait.until(lambda d: tuple(item["subject"] for item in _mail_rows(d)[:3]) != previous)
                except Exception:
                    return False
                return True
            except Exception:
                driver.switch_to.default_content()
                continue
    driver.switch_to.default_content()
    return False


def _advance_to_page(driver, wait, page_index: int) -> bool:
    for _ in range(page_index):
        if not _click_next_page(driver, wait):
            return False
    return True


def _extract_body(driver) -> str:
    from selenium.webdriver.common.by import By
    selectors = ("[data-testid*='message-body']", "[data-testid*='mail-body']", "[data-testid='body']",
                 "#mailBody", "#messageBody", "[class*='mail-body']", "[class*='mail_body']",
                 "[class*='message-body']", "[class*='message_body']", "[class*='mail-view-content']",
                 "[class*='mail_view_content']", "[class*='message-view-content']", "[class*='message_view_content']",
                 "[class*='view-body']", "[class*='read-body']", "[class*='read-content']", "[role='article']", "article")
    candidates = []
    driver.switch_to.default_content()
    for path in _context_paths(driver):
        try:
            _switch_context(driver, path)
        except Exception:
            continue
        for selector in selectors:
            for el in driver.find_elements(By.CSS_SELECTOR, selector):
                if el.is_displayed():
                    text = _visible_text(el)
                    if len(text) > 40 and not any(k in text for k in ("받은메일함", "보낸메일함", "휴지통", "메일쓰기")):
                        candidates.append(text)
    driver.switch_to.default_content()
    return max(candidates, key=len, default="")


def _open_mail_view(driver, wait) -> list:
    """Open the mail route and verify authentication using the confirmed groupware flow."""
    from selenium.webdriver.common.by import By
    driver.get(MAIL_URL)
    def authenticated_mail_route(d):
        if "/app/mail" not in d.current_url:
            return False
        d.switch_to.default_content()
        for path in _context_paths(d):
            try:
                _switch_context(d, path)
            except Exception:
                continue
            if any(el.is_displayed() for el in d.find_elements(By.CSS_SELECTOR, "input[type='password']")):
                d.switch_to.default_content()
                return False
        d.switch_to.default_content()
        return True
    try:
        wait.until(authenticated_mail_route)
    except Exception as exc:
        raise RuntimeError("그룹웨어 메일 페이지에 접근하지 못했습니다. 로그인 정보나 추가 인증 상태를 확인하세요.") from exc
    _click_inbox(driver)
    try:
        wait.until(lambda d: bool(_mail_rows(d)) or _has_empty_inbox(d))
    except Exception as exc:
        raise RuntimeError("로그인은 됐지만 받은메일 목록이 로드되지 않았습니다. 메일 페이지의 목록 구조나 사내망 상태를 확인하세요.") from exc
    return _mail_rows(driver)


def _has_empty_inbox(driver) -> bool:
    from selenium.webdriver.common.by import By
    empty_markers = ("메일이 없습니다", "조회된 결과가 없습니다", "검색 결과가 없습니다", "데이터가 없습니다", "메일 없음")
    driver.switch_to.default_content()
    for path in _context_paths(driver):
        try:
            _switch_context(driver, path)
        except Exception:
            continue
        body = driver.find_elements(By.TAG_NAME, "body")
        text = _visible_text(body[0]) if body else ""
        if any(marker in text for marker in empty_markers):
            driver.switch_to.default_content()
            return True
    driver.switch_to.default_content()
    return False


def check_login(username: str, password: str) -> str:
    """Perform a fresh sign-in and verify that the authenticated inbox is accessible."""
    if not username.strip() or not password:
        raise ValueError("그룹웨어 아이디와 비밀번호를 입력하세요.")
    try:
        from selenium.webdriver.support.ui import WebDriverWait
    except ImportError as exc:
        raise RuntimeError("Selenium이 설치되지 않았습니다. pip install -r requirements.txt를 실행하세요.") from exc
    driver = _driver()
    try:
        wait = WebDriverWait(driver, 30)
        _login(driver, username, password, wait)
        _open_mail_view(driver, wait)
        return "로그인 확인됨 · 받은메일함 접근 가능"
    finally:
        driver.quit()


def fetch_received_mail(start: date, end: date, username: str, password: str) -> tuple[list[dict], bool]:
    """Sign in, read visible received-mail rows and bodies, then close the browser."""
    if end < start:
        raise ValueError("조회 종료일은 시작일보다 빠를 수 없습니다.")
    if not username.strip() or not password:
        raise ValueError("그룹웨어 아이디와 비밀번호를 입력하세요.")
    try:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
    except ImportError as exc:
        raise RuntimeError("Selenium이 설치되지 않았습니다. pip install -r requirements.txt를 실행하세요.") from exc
    driver = _driver()
    mails, truncated = [], False
    try:
        wait = WebDriverWait(driver, 30)
        _login(driver, username, password, wait)
        entries = []
        page_signatures = set()
        for page_index in range(20):
            page_entries = _open_mail_view(driver, wait) if page_index == 0 else _mail_rows(driver)
            signature = tuple(item["subject"] for item in page_entries[:5])
            if not page_entries or signature in page_signatures:
                break
            page_signatures.add(signature)
            entries.extend({**entry, "page_index": page_index} for entry in page_entries)
            if len(entries) >= MAX_MESSAGES:
                entries = entries[:MAX_MESSAGES]
                truncated = True
                break
            if not _click_next_page(driver, wait):
                break
        else:
            truncated = True

        # Reopen the page containing each matching message before opening its detail view.
        for entry in entries:
            if len(mails) >= MAX_MESSAGES:
                truncated = True
                break
            # Date parsing uses the same date parser as file imports; undated mail is excluded.
            subject = entry["subject"]
            row_text = entry["row_text"]
            date_text = entry.get("date_text") or row_text
            full_date = parse_date(date_text)
            if full_date is not None and not (start <= full_date <= end):
                continue
            mail_date = full_date or _parse_mail_date(date_text, start, end)
            if mail_date is None:
                # Keep the subject visible so the UI can report that dates were not parseable.
                mails.append({"date": None, "sender": entry.get("sender", ""),
                              "subject": subject, "body": ""})
                continue
            sender = entry.get("sender", "")
            _open_mail_view(driver, wait)
            if not _advance_to_page(driver, wait, entry.get("page_index", 0)):
                continue
            if not _click_subject(driver, subject):
                _open_mail_view(driver, wait)
                _advance_to_page(driver, wait, entry.get("page_index", 0))
                wait.until(lambda d: any(x["subject"] == subject for x in _mail_rows(d)))
                if not _click_subject(driver, subject):
                    continue
            try:
                wait.until(lambda d: bool(_extract_body(d)))
            except Exception:
                pass
            body = _extract_body(driver)
            mails.append({"date": mail_date, "sender": sender, "subject": subject, "body": body})
            # The next record may belong to a different page; it will be reopened above.
        return mails, truncated or len(entries) >= MAX_MESSAGES
    except Exception as exc:
        if isinstance(exc, RuntimeError):
            raise
        detail = getattr(exc, "msg", "") or str(exc).splitlines()[0]
        raise RuntimeError(f"그룹웨어 메일을 읽지 못했습니다 ({type(exc).__name__}): {detail[:240]}") from exc
    finally:
        driver.quit()

