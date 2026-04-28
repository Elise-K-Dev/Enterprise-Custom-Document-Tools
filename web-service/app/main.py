from __future__ import annotations

import os
import secrets
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
import trafilatura
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from playwright.async_api import async_playwright
from pydantic import BaseModel, Field


app = FastAPI(title="web-service", version="0.1.0", openapi_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


CHROMIUM_EXECUTABLE_PATH = os.getenv("CHROMIUM_EXECUTABLE_PATH", "/usr/bin/chromium")
INTERNAL_TOKEN_HEADER = "x-port-project-internal-token"
OPEN_WEBUI_USER_EMAIL_HEADER = "x-openwebui-user-email"
OPEN_WEBUI_USER_ID_HEADER = "x-openwebui-user-id"

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 PortProjectWebService/0.1"
)

DEFAULT_FETCH_TIMEOUT = 15.0
PLAYWRIGHT_TIMEOUT_MS = 25_000
MIN_STATIC_TEXT_CHARS = 400
MAX_BODY_BYTES = 5 * 1024 * 1024


def configured_internal_token() -> str:
    token = os.getenv("PORT_PROJECT_INTERNAL_TOKEN", "").strip()
    if not token:
        raise HTTPException(status_code=500, detail="PORT_PROJECT_INTERNAL_TOKEN is not configured")
    return token


def require_internal_request(raw_request: Request) -> None:
    expected = configured_internal_token()
    supplied = (raw_request.headers.get(INTERNAL_TOKEN_HEADER) or "").strip()
    if not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=403, detail="invalid internal tool token")


def require_registered_tool_user(raw_request: Request) -> dict[str, str]:
    require_internal_request(raw_request)
    email = (raw_request.headers.get(OPEN_WEBUI_USER_EMAIL_HEADER) or "").strip().lower()
    user_id = (raw_request.headers.get(OPEN_WEBUI_USER_ID_HEADER) or "").strip()
    if not email or not user_id:
        raise HTTPException(status_code=401, detail="registered Open WebUI account is required")
    return {"email": email, "user_id": user_id}


def validate_url(url: str) -> str:
    cleaned = (url or "").strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="url is required")
    parsed = urlparse(cleaned)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="only http/https URLs are supported")
    if not parsed.netloc:
        raise HTTPException(status_code=400, detail="url is missing host")
    return cleaned


async def fetch_static(url: str) -> tuple[str, str]:
    """Return (final_url, html). Raises HTTPException on failure."""
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=DEFAULT_FETCH_TIMEOUT,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "ko,en;q=0.8"},
        ) as client:
            response = await client.get(url)
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail=f"timeout fetching {url}") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"network error fetching {url}: {exc}") from exc

    if response.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail=f"upstream returned {response.status_code} for {url}",
        )

    content_type = response.headers.get("content-type", "")
    if "html" not in content_type and "xml" not in content_type and "text" not in content_type:
        raise HTTPException(status_code=415, detail=f"unsupported content-type: {content_type}")

    body_bytes = response.content[:MAX_BODY_BYTES]
    return str(response.url), body_bytes.decode(response.encoding or "utf-8", errors="replace")


async def fetch_rendered(url: str) -> tuple[str, str]:
    """Render with Playwright (Chromium). Returns (final_url, html)."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            executable_path=CHROMIUM_EXECUTABLE_PATH if os.path.exists(CHROMIUM_EXECUTABLE_PATH) else None,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        try:
            context = await browser.new_context(user_agent=USER_AGENT, locale="ko-KR")
            page = await context.new_page()
            try:
                await page.goto(url, timeout=PLAYWRIGHT_TIMEOUT_MS, wait_until="networkidle")
            except Exception:
                await page.goto(url, timeout=PLAYWRIGHT_TIMEOUT_MS, wait_until="domcontentloaded")
            html = await page.content()
            final_url = page.url
            return final_url, html
        finally:
            await browser.close()


def extract_main_content(html: str, url: str) -> dict[str, Any]:
    markdown = trafilatura.extract(
        html,
        url=url,
        output_format="markdown",
        include_links=True,
        include_tables=True,
        favor_recall=True,
    ) or ""
    metadata = trafilatura.extract_metadata(html, default_url=url)
    title = ""
    author = ""
    published = ""
    if metadata is not None:
        title = (metadata.title or "").strip()
        author = (metadata.author or "").strip()
        published = (metadata.date or "").strip()
    return {
        "title": title,
        "author": author,
        "published": published,
        "markdown": markdown.strip(),
    }


async def fetch_and_extract(url: str, render: Literal["auto", "static", "js"]) -> dict[str, Any]:
    final_url = url
    html_body = ""
    used_renderer = "static"

    if render in ("auto", "static"):
        final_url, html_body = await fetch_static(url)
        extracted = extract_main_content(html_body, final_url)
        if render == "static":
            return {**extracted, "url": final_url, "renderer": "static"}
        if len(extracted["markdown"]) >= MIN_STATIC_TEXT_CHARS:
            return {**extracted, "url": final_url, "renderer": "static"}
        used_renderer = "js"

    if render == "js" or used_renderer == "js":
        final_url, html_body = await fetch_rendered(url)
        extracted = extract_main_content(html_body, final_url)
        return {**extracted, "url": final_url, "renderer": "js"}

    raise HTTPException(status_code=500, detail="unreachable fetch branch")


class FetchRequest(BaseModel):
    url: str = Field(..., description="대상 페이지 URL (http 또는 https)")
    render: Literal["auto", "static", "js"] = Field(
        "auto",
        description="auto: 정적 시도 후 본문이 짧으면 JS 렌더링. static: 정적만. js: Playwright 강제",
    )


class FetchResponse(BaseModel):
    url: str
    title: str
    author: str
    published: str
    markdown: str
    renderer: Literal["static", "js"]
    fetched_at: str


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/web/fetch", response_model=FetchResponse)
async def web_fetch(req: FetchRequest, raw_request: Request) -> FetchResponse:
    require_registered_tool_user(raw_request)
    url = validate_url(req.url)
    extracted = await fetch_and_extract(url, req.render)
    return FetchResponse(
        url=extracted["url"],
        title=extracted["title"],
        author=extracted["author"],
        published=extracted["published"],
        markdown=extracted["markdown"],
        renderer=extracted["renderer"],
        fetched_at=datetime.now(timezone.utc).isoformat(),
    )


@app.get("/openapi.json")
def openapi_spec() -> dict[str, Any]:
    return {
        "openapi": "3.0.0",
        "info": {
            "title": "Web Fetch",
            "version": "0.1.0",
            "description": (
                "사용자가 제공한 외부 웹 URL의 본문을 가져오는 도구. "
                "이 도구는 검색 기능을 제공하지 않는다. 사용자가 키워드 검색을 요청하면 "
                "'현재 인터넷 검색 기능은 제공하지 않습니다. 확인하실 페이지의 URL을 직접 알려주시면 내용을 가져와 드리겠습니다.'라고 안내한다. "
                "내부 사내 문서 검색에는 사용하지 않는다 (그 용도는 document_search)."
            ),
        },
        "servers": [{"url": "http://web-service:8004"}],
        "paths": {
            "/health": {
                "get": {
                    "operationId": "web_health_check",
                    "summary": "Health check",
                    "responses": {"200": {"description": "Healthy"}},
                }
            },
            "/web/fetch": {
                "post": {
                    "operationId": "fetch_web_page",
                    "summary": "단일 웹페이지 본문을 Markdown으로 가져오기",
                    "description": (
                        "사용자가 URL을 직접 제공하면 이 도구로 페이지를 가져와 본문 Markdown과 제목, 작성자, 작성일을 추출한다. "
                        "JavaScript 렌더링이 필요한 페이지는 자동으로 Playwright로 폴백된다. "
                        "사용자가 키워드로 인터넷 검색을 요청한 경우에는 이 도구를 호출하지 말고, "
                        "'현재 인터넷 검색 기능은 제공하지 않습니다. 확인하실 페이지의 URL을 직접 알려주시면 내용을 가져와 드리겠습니다.'라고 안내한다. "
                        "사내 문서나 레거시 자료에는 사용하지 않는다."
                    ),
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["url"],
                                    "properties": {
                                        "url": {
                                            "type": "string",
                                            "description": "가져올 페이지의 http/https URL",
                                        },
                                        "render": {
                                            "type": "string",
                                            "enum": ["auto", "static", "js"],
                                            "default": "auto",
                                            "description": "auto는 정적 시도 후 짧으면 JS 렌더링, static은 정적만, js는 Playwright 강제",
                                        },
                                    },
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "추출된 본문",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "url": {"type": "string"},
                                            "title": {"type": "string"},
                                            "author": {"type": "string"},
                                            "published": {"type": "string"},
                                            "markdown": {"type": "string"},
                                            "renderer": {"type": "string"},
                                            "fetched_at": {"type": "string"},
                                        },
                                    }
                                }
                            },
                        }
                    },
                }
            },
        },
    }
