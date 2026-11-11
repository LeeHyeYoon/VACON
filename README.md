[Uploading README.md…]()
# VACON

## 검증 업무와 휴가 인수인계를 잇는 업무 운영 플랫폼

VACON은 휴가 일정, 업무 계획, 검증 실적을 한곳에서 관리하는 Streamlit 기반 업무 도구입니다. 과거 Google Sheets 기록과 VACON 내부 업무 데이터를 통합해 조회하고, 사내 **ALIRA AI**를 활용해 질의응답·대체 담당자 추천·인수인계 문서 작성을 지원합니다.

> 업무량 집계와 배정 수량은 VACON의 실제 계획·실적 데이터로 계산합니다. AI 추천은 검토를 돕는 참고 정보이며, 최종 배정은 PM이 승인합니다.

## 주요 메뉴

| 메뉴 | 주요 기능 |
|---|---|
| **대시보드** | 오늘의 부재, 예정 휴가, 대체 배정이 필요한 업무와 버전 진행 현황을 요약합니다. |
| **휴가 캘린더** | 주간·월간 휴가 현황을 확인하고 휴가를 등록·수정·삭제합니다. |
| **검증 업무 로그** | 버전·담당자·서브시스템·기간별 업무 기록을 통합 조회합니다. 업무 계획 일정과 실제 실적은 구분합니다. |
| **(PM) 검증 일정 관리** | T0800 이상 버전의 마감일, 서브시스템별 계획, 세부 일정을 관리하고 PM 검토 단계를 진행합니다. |
| **검증 업무 기록** | 요구사양서 작성과 동적 검증 실적을 각각 등록·수정·삭제합니다. |
| **대체 담당자** | 휴가와 겹치는 실제 업무를 확인하고 ALIRA 추천을 검토합니다. 승인한 재분배안만 업무 기록에 반영됩니다. |
| **인수인계** | 현재 진행 버전의 본인 진행 현황과 휴가 기간 일정을 바탕으로 문서를 작성하고 내려받습니다. |
| **메일 요약** | 회사 메일을 읽기 전용으로 가져와 ALIRA 브리핑을 생성합니다. |
| **ALIRA 비서** | 자연어 질문을 ALIRA에 전달하고 필요한 VACON 업무 데이터를 조회해 답변합니다. |
| **데이터 관리** | 팀원 정보와 Google Sheets 연동을 관리합니다. |

## 실행 방법

### Windows PowerShell

Python 3.10 이상을 설치한 뒤 프로젝트 폴더에서 실행합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```

가상환경을 활성화하지 않고 실행하려면 다음과 같이 사용할 수 있습니다.

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py
```

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

앱은 기본적으로 `http://localhost:8501`에서 열립니다. 처음 실행하면 `data/vacon.db`에 SQLite 데이터베이스가 생성됩니다.

## ALIRA 설정

ALIRA를 사용하는 기능은 사내 네트워크 연결과 API 설정이 필요합니다. 환경변수는 실행 중인 터미널에서 설정합니다.

```powershell
$env:ALIRA_API_BASE = "http://10.10.10.200:19640/v1"
$env:ALIRA_MODEL = "Qwen/Qwen3.6-27B"
streamlit run app.py
```

| 환경변수 | 설명 |
|---|---|
| `ALIRA_API_BASE` | 사내 ALIRA API의 OpenAI 호환 기본 주소 |
| `ALIRA_MODEL` | 서버에 등록된 모델 ID |
| `ALIRA_API_KEY` | API 서버가 인증 토큰을 요구하는 경우에만 설정 |
| `VACON_LLM_THINKING` | `1`로 설정하면 지원 모델의 thinking 모드를 사용합니다. 기본값은 비활성화입니다. |
| `VACON_DB` | SQLite 파일 경로를 변경할 때 사용합니다. 기본값은 `data/vacon.db`입니다. |
| `VACON_TODAY` | 시연·재현을 위해 기준 날짜를 `YYYY-MM-DD` 형식으로 고정합니다. |

ALIRA 설정이 없어도 기본 업무 화면은 사용할 수 있습니다. 다만 AI 비서, AI 추천, ALIRA 문서 작성 기능은 사용할 수 없습니다.

## 업무 데이터 관리

### 과거 버전과 신규 버전

| 대상 | 원본 | 관리 방식 |
|---|---|---|
| T0020~T0700 | 기존 Google Sheets의 `2. Dynamic_Verification`, `3. Req_Creation_WBS` | 자동 분석·가져오기 후 조회 전용으로 사용 |
| T0800 이상 | VACON SQLite DB | PM이 일정·계획을 관리하고 담당자가 실적을 기록 |

데이터 관리의 시트 연동에서 Google Sheets를 연결하거나 Excel 파일을 제공하면 ALIRA가 헤더와 데이터 구성을 분석해 VACON 항목에 맞게 분류합니다. 반영 전에 미리보기를 확인하며, 원본에 없는 업무량이나 숙련도 값은 만들어내지 않습니다.

요구사양서 작성 실적과 동적 검증 실적은 별도로 저장·집계합니다. PM 일정은 예정 업무이며 실제 완료 실적이나 진척률을 자동으로 늘리지 않습니다. 요구사양서 PM 검토를 완료하면 동적 검증 단계가 열립니다.

## ALIRA와 데이터 보호

- AI 비서와 AI 추천은 사내 ALIRA API를 사용합니다. 외부 AI 서비스로 업무 데이터를 보내지 않습니다.
- AI 비서의 조회 도구는 VACON 데이터를 읽기만 하며, 휴가·일정·업무 기록을 변경하지 않습니다.
- 대체 업무 추천 결과와 인수인계 문서는 PM 또는 사용자가 확인하고 승인·저장합니다.
- 메일은 IMAP/SSL 읽기 전용으로 가져오며 메일함의 읽음 상태는 바꾸지 않습니다. 원문은 VACON DB에 저장하지 않고 현재 앱 세션에서 처리합니다. ALIRA 브리핑을 요청하면 메일 내용이 사내 ALIRA 서버로 전달됩니다.
- 메일 로그인 정보 저장을 선택하면 Windows 계정 기반으로 암호화해 이 PC에 보관합니다.
- SQLite 업무 데이터는 기본적으로 프로젝트의 `data/vacon.db`에 저장됩니다. `VACON_DB`를 설정하면 저장 위치를 바꿀 수 있습니다.

## 개발 참고

```text
app.py       앱 진입점과 사이드바 메뉴
views/       Streamlit 페이지
services/    업무 규칙, 추천, ALIRA·메일 연동 및 계산 로직
data/        SQLite 저장소와 초기 데이터
```

테스트가 필요할 때 프로젝트 가상환경에서 실행합니다.

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## 참고 사항

- 업무·휴가 데이터는 로컬 SQLite에 저장되므로 실행 PC와 DB 파일을 함께 관리해야 합니다.
- Google Sheets 데이터는 사용자가 연동·가져오기를 실행할 때 갱신됩니다. 실시간 동기화는 아닙니다.
- 대체 담당자와 완료 가능성은 기록된 업무 범위·실적·일정에 기반한 참고값입니다. PM이 내용을 확인한 뒤 최종 승인해야 합니다.
