# Judge Service

Judge Service reviews short plans, report drafts, and project ideas with fixed scoring rules.

The service computes `judgement`, `score`, and `weakest_area` in code, then asks an LLM to produce a concise Korean review comment. If the LLM call fails, it returns a deterministic fallback response.

## Endpoint

- `POST /tools/judge`
- `GET /openapi.json`
- `GET /health`

## Access

Use Open WebUI user headers and `JUDGE_ALLOWED_EMAILS`, `JUDGE_ALLOWED_NAMES`, or `JUDGE_ALLOWED_USER_IDS` to limit access.
