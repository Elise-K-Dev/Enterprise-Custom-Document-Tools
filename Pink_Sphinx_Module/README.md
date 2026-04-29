# Pink_Sphinx_Module

OpenWebUI에서 호출할 수 있는 계획서/보고서/아이디어 검토 도구입니다.

판정은 LLM이 하지 않습니다. `purpose`, `scope`, `feasibility`, `validation`, `deliverable`, `logic` 점수와 `judgement`, `weakest_area`는 코드가 계산합니다. Gemma 4는 교수 스타일 코멘트와 정리 문장만 생성합니다.

## 실행

```bash
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8010
```

## 환경 변수

```env
GEMMA_API_URL=http://192.168.100.13:8000/v1/chat/completions
GEMMA_MODEL=gemma-4-31b-it
GEMMA_API_KEY=
GEMMA_TIMEOUT_SECONDS=30
PORT_PROJECT_INTERNAL_TOKEN=
WON_CONFIRM_ALLOWED_EMAILS=elise@local.dev,sock@local.dev
WON_CONFIRM_ALLOWED_NAMES=elise,Sock
WON_CONFIRM_ALLOWED_USER_IDS=
```

`GEMMA_API_URL`과 `GEMMA_MODEL`이 없으면 기존 프로젝트의 `DOCUMENT_FILLER_API_URL`, `DOCUMENT_FILLER_MODEL_ID`도 읽습니다.

허용되지 않은 계정 또는 직접 호출자는 `POST /tools/won-confirm`에서 `404 Not Found`를 받습니다. OpenWebUI 런타임 동기화는 `WON_CONFIRM_ALLOWED_EMAILS`와 `WON_CONFIRM_ALLOWED_NAMES`에 해당하는 사용자 ID만 도구 서버 access grant에 넣습니다.

## API

- `GET /health`
- `POST /tools/won-confirm`
- `GET /openapi.json`

## 예시

```bash
curl -X POST http://127.0.0.1:8010/tools/won-confirm \
  -H "Content-Type: application/json" \
  -d "{\"text\":\"좋은 챗봇을 만들고 싶다.\",\"mode\":\"savage\",\"rewrite\":true}"
```
