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
- `GET /document/legacy/shortages`
- `GET /document/legacy/item-context`
- `POST /document/legacy/item-export`
- `POST /document/legacy/item-approve`
- `POST /document/legacy/run`
- `POST /parser/to-md`
- `POST /document/fill-fields`
- `POST /search/query`

## Legacy Stock Snapshot Rules

Rust 레거시 문서 작성기는 `rust-service/DB/output/stock_in_out_monthly.json` 스냅샷을 기준으로 동작합니다.

- 원천 Excel(`입고/재고/출고`)은 배치 시 JSON 스냅샷 생성에만 사용합니다.
- 문서 `create/fill/export`와 품목 컨텍스트 조회는 원천 Excel을 직접 현재 조회 기준으로 사용하지 않습니다.
- `현재고`는 재고 파일 원값(`current_stock_before`)만 사용합니다.
- `movement_net_qty`와 `current_stock_updated`는 이동 이력/추정 잔량용 보조 값으로 유지되며, 실제 현재고 표시값으로 쓰지 않습니다.
- 재고 행이 없는 품목은 `inventory_confirmed=false`와 `inventory_match_status`로 분리되어 `재고 미확인`으로 취급합니다.

부족재고 조회 응답 기준:

- 확인된 부족 품목은 `현재고`, `필수재고`, `부족수량(shortage_quantity)` 기준으로 정렬/설명합니다.
- `shortage_gap`는 내부 계산 필드로 남기되, 사용자 응답은 `현재고 X개, 필수재고 Y개로 Z개 부족` 형식으로 설명합니다.
- `/document/legacy/shortages` 응답에는 Open WebUI가 그대로 사용할 수 있는 `markdown_table`와 `unverified_markdown_table`가 포함됩니다.

배치 재생성:

```bash
curl -X POST http://127.0.0.1:8001/document/legacy/run \
  -H 'Content-Type: application/json' \
  -d '{"force": true}'
```

## Local Run

Rust service:

```bash
cd rust-service
DOCUMENT_SERVICE_HOST=0.0.0.0 DOCUMENT_SERVICE_PORT=8001 cargo run
```

테스트:

```bash
PATH='/home/elise/.rustup/toolchains/stable-x86_64-unknown-linux-gnu/bin':$PATH \
cargo test --manifest-path Cargo.toml
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
