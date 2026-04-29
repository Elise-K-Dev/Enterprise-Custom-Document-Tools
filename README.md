# BrainLess

BrainLess는 Open WebUI에 붙여 쓰는 별도 도구 모듈 묶음입니다.

이 저장소는 엔터프라이즈 문서 생성/검색 산출물과 분리되어 있습니다. 전처리 문서, 입력 데이터, 문서 템플릿, 고객 데이터는 포함하지 않습니다.

## Modules

- `judge-service`: 계획, 보고서, 아이디어 검토 도구
- `suno-service`: Suno 가사 및 스타일 프롬프트 도구
- `speaki-service`: Speaki 응답 도구
- `open-webui`: Open WebUI 도구 가져오기용 JSON

## Environment

`.env` 예시:

```bash
PORT_PROJECT_INTERNAL_TOKEN=replace-with-local-token
SUNO_LLM_API_URL=http://127.0.0.1:8000/v1/chat/completions
SUNO_LLM_MODEL_ID=gemma-4-31b-it
GEMMA_API_URL=http://127.0.0.1:8000/v1/chat/completions
GEMMA_MODEL=gemma-4-31b-it
JUDGE_ALLOWED_EMAILS=elise@local.dev,sock@gmail.com
JUDGE_ALLOWED_NAMES=elise,Sock
```

## Run

```bash
docker compose up -d --build
```

Host network mode:

```bash
docker compose -f docker-compose.yml -f docker-compose.host.yml up -d --build
```

## Open WebUI

Import these files from `open-webui/`:

- `openwebui-judge-tools.json`
- `openwebui-suno-tools.json`
- `openwebui-speaki-tools.json`

Replace placeholder user IDs and the internal token before importing.

<img width="480" height="480" alt="image" src="https://github.com/user-attachments/assets/bf182107-1c35-489f-a71f-f9a93e65be84" />
<img width="1200" height="848" alt="image" src="https://github.com/user-attachments/assets/478eae24-7516-49f0-84c7-d436bcd02092" />
