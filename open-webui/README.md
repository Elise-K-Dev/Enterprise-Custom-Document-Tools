# Open WebUI Tool Imports

이 디렉터리에는 BrainLess 전용 Open WebUI 도구 설정만 둡니다.

## Files

- `openwebui-pink-sphinx-tools.json`
- `openwebui-suno-tools.json`
- `openwebui-speaki-tools.json`

각 JSON의 `REPLACE_WITH_PORT_PROJECT_INTERNAL_TOKEN` 값은 배포 환경의 `PORT_PROJECT_INTERNAL_TOKEN`으로 교체해야 합니다.

Pink Sphinx는 `elise`와 `Sock` 계정만 접근하도록 운영 환경에서 사용자 ID 기반 access grant를 추가하는 것을 권장합니다.
