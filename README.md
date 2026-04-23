# Enterprise Custom Document Tools

Open WebUI를 프런트로 두고, 문서 생성과 내보내기는 Rust 서비스에, 문서 파싱과 레거시 문서 검색 및 필드 채우기 보조는 Python 서비스에 분리한 사내 문서 자동화 워크스페이스입니다.

## Overview

```text
User -> Open WebUI -> vLLM
                    -> Tool Calls
                       -> Rust Service   (:8001 /document/create, /document/fill, /document/export)
                       -> Python Service (:8002 /parser/to-md, /document/fill-fields, /search/query)
```

구성 요소:

- `open-webui`: 사용자 UI, 툴 서버 연결 설정, 실행 환경 변수
- `rust-service`: 문서 생성, 필드 채우기, 내보내기 API
- `python-service`: RAW 문서 Markdown 변환, 레거시 검색, 문서 필드 보조 API
- `scripts`: 로컬 실행, Docker 실행, Open WebUI 동기화 스크립트
- `docs`: 운영 메모와 연결 문서

## Key APIs

- `POST /document/create`
- `POST /document/fill`
- `POST /document/export`
- `POST /parser/to-md`
- `POST /document/fill-fields`
- `POST /search/query`

## Local Run

Rust service:

```bash
cd rust-service
DOCUMENT_SERVICE_HOST=0.0.0.0 DOCUMENT_SERVICE_PORT=8001 cargo run
```

Python service:

```bash
cd python-service
python -m venv .venv
. .venv/bin/activate
pip install -e .
PARSER_SERVICE_HOST=0.0.0.0 PARSER_SERVICE_PORT=8002 uvicorn app.main:app --host 0.0.0.0 --port 8002
```

Open WebUI:

```bash
bash scripts/start_openwebui_with_vllm.sh
```

Creator services:

```bash
bash scripts/start_creator_services.sh
```

Parser service:

```bash
bash scripts/start_parser_service.sh
```

## Docker Compose

```bash
cd /home/elise/Desktop/2026\ Dev/Port-Project
docker compose up -d --build
```

구성 요약:

- `document-service`는 `./rust-service/templates`를 `/app/templates`로 read-only 마운트합니다.
- `parser-service`는 레거시 검색용 Python 서비스를 함께 기동합니다.
- Open WebUI는 같은 Docker 네트워크에서 서비스명으로 각 API를 호출합니다.

서비스 엔드포인트:

```text
vLLM              -> http://192.168.100.13:8000/v1
Rust creator API  -> http://192.168.100.13:8001
Python parser API -> http://192.168.100.13:8002
Open WebUI        -> http://127.0.0.1:3000
document-service  -> http://document-service:8001/openapi.json
parser-service    -> http://parser-service:8002/openapi.json
```

Open WebUI import 예시:

- `open-webui/openwebui-rust-tools.json`
- `open-webui/openwebui-python-tools.json`

## Repository Notes

- 대용량 인덱스 산출물과 로컬 빌드 폴더는 저장소에서 제외합니다.
- 실제 운영용 계정 정보와 `.env` 파일은 커밋하지 않습니다.
- `python-service/legacy-md-indexer-main` 아래 문서 카탈로그와 검색 코드는 보존하되, 생성된 `processed_md` 데이터는 제외합니다.
