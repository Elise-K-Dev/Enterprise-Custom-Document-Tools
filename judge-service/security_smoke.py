from __future__ import annotations

from fastapi.testclient import TestClient

from app import app


def main() -> None:
    client = TestClient(app)
    payload = {"text": "good chatbot", "mode": "savage", "rewrite": True}

    no_token = client.post("/tools/judge", json=payload)
    no_token_openapi = client.get("/openapi.json")
    internal_openapi = client.get(
        "/openapi.json",
        headers={"X-Port-Project-Internal-Token": "test-token"},
    )
    normal = client.post(
        "/tools/judge",
        json=payload,
        headers={
            "X-Port-Project-Internal-Token": "test-token",
            "X-OpenWebUI-User-Email": "user@gmail.com",
            "X-OpenWebUI-User-Id": "user-id",
            "X-OpenWebUI-User-Name": "user",
        },
    )
    elise = client.post(
        "/tools/judge",
        json=payload,
        headers={
            "X-Port-Project-Internal-Token": "test-token",
            "X-OpenWebUI-User-Email": "elise@local.dev",
            "X-OpenWebUI-User-Id": "elise-id",
            "X-OpenWebUI-User-Name": "elise",
        },
    )
    sock = client.post(
        "/tools/judge",
        json=payload,
        headers={
            "X-Port-Project-Internal-Token": "test-token",
            "X-OpenWebUI-User-Email": "sock@gmail.com",
            "X-OpenWebUI-User-Id": "sock-id",
            "X-OpenWebUI-User-Name": "Sock",
        },
    )

    print(f"no_token={no_token.status_code}")
    print(f"no_token_openapi={no_token_openapi.status_code}")
    print(f"internal_openapi={internal_openapi.status_code}")
    print(f"normal_user={normal.status_code}")
    print(f"elise={elise.status_code}:{elise.json().get('judgement')}")
    print(f"sock={sock.status_code}:{sock.json().get('judgement')}")

    assert no_token.status_code == 404
    assert no_token_openapi.status_code == 404
    assert internal_openapi.status_code == 200
    assert normal.status_code == 404
    assert elise.status_code == 200
    assert sock.status_code == 200


if __name__ == "__main__":
    main()
