# Open WebUI Integration Notes

Open WebUI는 UI와 tool call orchestration만 맡고, 실제 비즈니스 로직은 외부 서비스로 분리합니다. 현재는 `Port-Project`의 단일 Docker Compose 안에서 `open-webui`, `document-service`, `parser-service`가 함께 실행됩니다.

실행 기준:

- 모든 런타임은 `Port-Project` 안에서 시작합니다.
- 필요한 HAI-H 자산은 `Port-Project` 루트 안으로 복사해 둔 로컬 디렉터리를 사용합니다.

## Tool Routing

- `create_document` -> `POST http://document-service:8001/document/create`
- `fill_document` -> `POST http://document-service:8001/document/fill`
- `export_document` -> `POST http://document-service:8001/document/export`
- `parse_to_md` -> `POST http://parser-service:8002/parser/to-md`
- `fill_document_fields_ko` -> `POST http://parser-service:8002/document/fill-fields`
- `approve_and_generate_item_document` -> `POST http://document-service:8001/document/legacy/item-approve`

## Docker Network Mode

Open WebUI는 `port-project` Docker 네트워크에 함께 붙어 있으므로, 호스트 IP 대신 서비스명으로 연결합니다.

- `document-service` -> `http://document-service:8001/openapi.json`
- `parser-service` -> `http://parser-service:8002/openapi.json`

Import 가능한 설정 파일:

- [openwebui-rust-tools.json](/home/elise/Desktop/2026%20Dev/Port-Project/open-webui/openwebui-rust-tools.json)
- [openwebui-python-tools.json](/home/elise/Desktop/2026%20Dev/Port-Project/open-webui/openwebui-python-tools.json)

구성 원칙:

- Rust 계열 도구는 `openwebui-rust-tools.json` 하나로 묶음
- Python 계열 도구는 `openwebui-python-tools.json` 하나로 묶음
- 포트별 세부 분리 파일은 제거

## Model Backend

- `vLLM (OpenAI-compatible)` -> `http://192.168.100.13:8000/v1`

## One Command

```bash
cd "/home/elise/Desktop/2026 Dev/Port-Project"
sudo bash scripts/start_openwebui_with_vllm.sh
```

## System Prompt Direction

LLM에는 아래 원칙을 주는 편이 안전합니다.

- 문서 생성 자체를 직접 수행하지 말 것
- 어떤 tool을 호출할지 결정할 것
- 누락 필드가 있으면 `fill_document`를 우선 사용할 것
- 파일/RAW 변환은 `parse_to_md`로 보낼 것
- 문서 채우기 초안은 `fill_document_fields_ko`로 보낼 것
- 사용자가 `승인해`, `진행해`처럼 긍정 의사를 보이면 `approve_and_generate_item_document`를 우선 사용할 것

## Example Flow

1. 사용자가 "구매 품의서 만들어줘, SSD 3개"라고 요청
2. Open WebUI가 `create_document` 호출
3. 응답에 `missing_fields=["납품업체"]`가 오면 모델이 사용자에게 후속 질문
4. 사용자가 업체를 답하면 `fill_document` 호출
5. 모든 필드가 채워지면 `export_document` 호출
