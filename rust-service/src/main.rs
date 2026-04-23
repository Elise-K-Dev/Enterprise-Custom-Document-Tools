use std::{
    collections::{BTreeMap, HashMap},
    fs,
    net::SocketAddr,
    path::{Path, PathBuf},
    str::FromStr,
    sync::Arc,
};

use axum::{
    extract::{Query, State},
    http::{
        header::{CONTENT_DISPOSITION, CONTENT_TYPE},
        HeaderValue, StatusCode,
    },
    response::IntoResponse,
    routing::{get, post},
    Json, Router,
};
use base64::{engine::general_purpose::STANDARD, Engine as _};
use chrono::Utc;
use regex::Regex;
use serde::{Deserialize, Serialize};
use tokio::sync::RwLock;
use tower_http::{
    cors::{Any, CorsLayer},
    trace::TraceLayer,
};
use tracing::info;
use uuid::Uuid;

mod legacy_engine;
mod legacy_port;

use legacy_port::{
    build_purchase_reason_text, decide_purchase_v2, load_named_templates, render_docx_bytes,
    select_template_for_row, template_dir_candidates, DocumentRow as LegacyDocumentRow,
    PurchaseDecision,
};

#[derive(Clone)]
struct AppState {
    templates: Arc<HashMap<String, TemplateDefinition>>,
    sessions: Arc<RwLock<HashMap<String, SessionState>>>,
    legacy_workdir: Option<PathBuf>,
    public_base_url: String,
}

#[derive(Debug, Clone)]
struct TemplateDefinition {
    id: &'static str,
    display_name: &'static str,
    required_fields: Vec<&'static str>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct SessionState {
    session_id: String,
    template_id: String,
    fields: BTreeMap<String, serde_json::Value>,
    missing_fields: Vec<String>,
}

#[derive(Debug, Deserialize)]
struct FillRequest {
    template_id: String,
    session_id: String,
    #[serde(default)]
    current_fields: BTreeMap<String, serde_json::Value>,
    user_message: String,
}

#[derive(Debug, Serialize, Deserialize)]
struct FillResponse {
    updated_fields: BTreeMap<String, serde_json::Value>,
    missing_fields: Vec<String>,
    next_question: Option<String>,
    preview_text: String,
    session: SessionState,
}

#[derive(Debug, Deserialize)]
struct CreateRequest {
    template_id: String,
    input_text: String,
}

#[derive(Debug, Serialize, Deserialize)]
struct CreateResponse {
    session_id: String,
    updated_fields: BTreeMap<String, serde_json::Value>,
    missing_fields: Vec<String>,
    next_question: Option<String>,
    preview_text: String,
}

#[derive(Debug, Deserialize)]
struct ExportRequest {
    template_id: String,
    fields: BTreeMap<String, serde_json::Value>,
    format: String,
}

#[derive(Debug, Serialize, Deserialize)]
struct ExportResponse {
    file_name: String,
    format: String,
    mime_type: String,
    content_base64: String,
    preview_text: String,
    generated_at: String,
}

#[derive(Debug, Serialize)]
struct HealthResponse {
    status: &'static str,
}

#[derive(Debug, Deserialize)]
struct LegacyRunRequest {
    #[serde(default = "default_true")]
    force: bool,
}

#[derive(Debug, Serialize, Deserialize)]
struct LegacyRunResponse {
    workdir: String,
    output_dir: String,
    generated_count: usize,
    generated_files: Vec<String>,
    snapshot_json_path: Option<String>,
    batch_report_path: Option<String>,
    stdout: String,
}

#[derive(Debug, Serialize, Deserialize)]
struct LegacyPackageResponse {
    generated_count: usize,
    zip_path: String,
    zip_file_name: String,
    download_path: String,
    download_url: String,
    message: String,
    assistant_summary: String,
    generated_files_preview: Vec<String>,
    batch_report_path: Option<String>,
    snapshot_json_path: Option<String>,
}

#[derive(Debug, Deserialize)]
struct LegacyDownloadQuery {
    path: String,
}

#[derive(Debug, Deserialize)]
struct LegacyShortagesQuery {
    query: Option<String>,
    limit: Option<usize>,
}

#[derive(Debug, Serialize)]
struct LegacyShortagesResponse {
    snapshot_date: Option<String>,
    total_count: usize,
    items: Vec<LegacyShortageItem>,
}

#[derive(Debug, Serialize, Clone)]
struct LegacyShortageItem {
    part_name: String,
    part_no: String,
    current_stock_before: f64,
    current_stock_updated: f64,
    inbound_qty_sum: f64,
    outbound_qty_sum: f64,
    outbound_count: usize,
    stock_status: String,
    summary: String,
    document_request_hint: String,
}

#[derive(Debug, Deserialize)]
struct LegacyItemContextQuery {
    part_name: Option<String>,
    part_no: Option<String>,
}

#[derive(Debug, Serialize)]
struct GuidedFieldSpec {
    field: String,
    label: String,
    prompt: String,
}

#[derive(Debug, Serialize)]
struct LegacyItemContextResponse {
    part_name: String,
    part_no: String,
    context: String,
    fields_seed: BTreeMap<String, serde_json::Value>,
    guided_fields: Vec<GuidedFieldSpec>,
    assistant_summary: String,
}

#[derive(Debug, Deserialize)]
struct LegacyItemExportRequest {
    part_name: Option<String>,
    part_no: Option<String>,
    fields: BTreeMap<String, serde_json::Value>,
}

#[derive(Debug, Serialize)]
struct LegacyItemExportResponse {
    output_path: String,
    download_path: String,
    download_url: String,
    file_name: String,
    assistant_summary: String,
}

#[derive(Debug, Deserialize)]
struct LegacyApproveRequest {
    part_name: Option<String>,
    part_no: Option<String>,
    #[serde(default)]
    fields: BTreeMap<String, serde_json::Value>,
    purchase_reason: Option<String>,
}

#[derive(Debug, Serialize)]
struct LegacyApproveResponse {
    pricing_policy_note: String,
    draft_preview: String,
    resolved_fields: BTreeMap<String, serde_json::Value>,
    output_path: String,
    download_path: String,
    download_url: String,
    file_name: String,
    assistant_summary: String,
}

fn default_true() -> bool {
    true
}

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .with_env_filter(
            std::env::var("RUST_LOG")
                .unwrap_or_else(|_| "document_service=debug,tower_http=info".into()),
        )
        .init();

    let app = app_router();
    let host = std::env::var("DOCUMENT_SERVICE_HOST").unwrap_or_else(|_| "0.0.0.0".to_string());
    let port = std::env::var("DOCUMENT_SERVICE_PORT")
        .ok()
        .and_then(|raw| u16::from_str(&raw).ok())
        .unwrap_or(8001);
    let addr = SocketAddr::from_str(&format!("{host}:{port}")).unwrap();
    info!("document service listening on {addr}");

    let listener = tokio::net::TcpListener::bind(addr).await.unwrap();
    axum::serve(listener, app).await.unwrap();
}

async fn health() -> Json<HealthResponse> {
    Json(HealthResponse { status: "ok" })
}

async fn openapi_spec() -> Json<serde_json::Value> {
    Json(serde_json::json!({
        "openapi": "3.0.0",
        "info": {
            "title": "Purchase Document Package Generator",
            "version": "1.0.0",
            "description": "구매 품의 문서 전체를 자동 생성하고 ZIP 다운로드 정보를 반환하는 도구 서버"
        },
        "servers": [
            { "url": "http://document-service:8001" }
        ],
        "paths": {
            "/health": {
                "get": {
                    "operationId": "health_check",
                    "summary": "Health check",
                    "responses": {
                        "200": {
                            "description": "Healthy",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "status": { "type": "string", "example": "ok" }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            },
            "/document/create": {
                "post": {
                    "operationId": "create_document",
                    "summary": "대화형 구매 품의 문서 채우기 세션 시작",
                    "description": "사용자의 최초 요청을 바탕으로 Rust 문서 채우기 세션을 만들고 누락 필드와 다음 질문을 반환한다.",
                    "requestBody": {
                        "required": true,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["template_id", "input_text"],
                                    "properties": {
                                        "template_id": { "type": "string", "example": "purchase_request" },
                                        "input_text": { "type": "string" }
                                    }
                                }
                            }
                        }
                    },
                    "responses": { "200": { "description": "Document filling session created" } }
                }
            },
            "/document/fill": {
                "post": {
                    "operationId": "fill_document",
                    "summary": "대화형 구매 품의 문서 필드 추가 채움",
                    "description": "이전 세션 상태와 현재 사용자 답변을 합쳐 필드를 갱신하고 다음으로 채울 칸을 반환한다. 사용자가 납품업체: 지정 협력사 처럼 말하면 해당 칸을 확정한다.",
                    "requestBody": {
                        "required": true,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["template_id", "session_id", "user_message"],
                                    "properties": {
                                        "template_id": { "type": "string", "example": "purchase_request" },
                                        "session_id": { "type": "string" },
                                        "current_fields": {
                                            "type": "object",
                                            "additionalProperties": true
                                        },
                                        "user_message": { "type": "string" }
                                    }
                                }
                            }
                        }
                    },
                    "responses": { "200": { "description": "Document fields updated" } }
                }
            },
            "/document/export": {
                "post": {
                    "operationId": "export_document",
                    "summary": "채워진 필드로 문서 내보내기",
                    "description": "대화형으로 채운 필드를 사용해 문서 파일 내용을 생성한다. docx 형식이면 Rust 레거시 DOCX 렌더러를 사용한다.",
                    "requestBody": {
                        "required": true,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["template_id", "fields", "format"],
                                    "properties": {
                                        "template_id": { "type": "string", "example": "purchase_request" },
                                        "fields": {
                                            "type": "object",
                                            "additionalProperties": true
                                        },
                                        "format": { "type": "string", "example": "docx" }
                                    }
                                }
                            }
                        }
                    },
                    "responses": { "200": { "description": "Document exported" } }
                }
            },
            "/document/legacy/package": {
                "post": {
                    "operationId": "generate_purchase_document_package",
                    "summary": "Run the full purchase document batch and return a ZIP package for download",
                    "description": "사용자가 구매 품의 문서 전체 작성, 일괄 생성, 전체 ZIP 다운로드를 요청하면 이 도구를 사용한다. 레거시 Rust 배치를 실행하고 생성된 DOCX 전체를 ZIP으로 묶은 뒤 다운로드 URL을 반환한다.",
                    "requestBody": {
                        "required": false,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "force": { "type": "boolean", "example": true }
                                    }
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {
                            "description": "ZIP package created",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "workdir": { "type": "string" },
                                            "output_dir": { "type": "string" },
                                            "generated_count": { "type": "integer" },
                                            "zip_path": { "type": "string" },
                                            "zip_file_name": { "type": "string" },
                                            "download_path": { "type": "string" },
                                            "download_url": {
                                                "type": "string",
                                                "description": "사용자에게 그대로 안내할 ZIP 다운로드 URL"
                                            },
                                            "message": {
                                                "type": "string",
                                                "description": "사용자에게 바로 보여줄 짧은 완료 메시지"
                                            },
                                            "assistant_summary": {
                                                "type": "string",
                                                "description": "모델이 그대로 사용자에게 안내해도 되는 자연어 요약"
                                            },
                                            "generated_files_preview": {
                                                "type": "array",
                                                "description": "생성된 파일 예시 일부",
                                                "items": { "type": "string" }
                                            },
                                            "batch_report_path": { "type": ["string", "null"] },
                                            "snapshot_json_path": { "type": ["string", "null"] },
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            },
            "/document/legacy/shortages": {
                "get": {
                    "operationId": "list_shortage_items",
                    "summary": "현재 재고 부족 또는 0 이하인 품목 조회",
                    "description": "사용자가 현재 재고가 없는 품목, 부족한 품목, 구매가 필요한 품목을 물으면 이 도구를 사용한다.",
                    "parameters": [
                        {
                            "name": "query",
                            "in": "query",
                            "required": false,
                            "schema": { "type": "string" },
                            "description": "품목명 또는 품번 필터"
                        },
                        {
                            "name": "limit",
                            "in": "query",
                            "required": false,
                            "schema": { "type": "integer", "default": 20 },
                            "description": "최대 반환 개수"
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Shortage items listed"
                        }
                    }
                }
            },
            "/document/legacy/item-context": {
                "get": {
                    "operationId": "get_item_document_context",
                    "summary": "선택한 품목의 문서 작성 컨텍스트 조회",
                    "description": "사용자가 특정 품목으로 구매 품의 문서를 작성하려 할 때 이 도구를 사용한다. 문서 채우기에 필요한 컨텍스트와 필드 seed, 한국어 guided field 목록을 반환한다.",
                    "parameters": [
                        {
                            "name": "part_name",
                            "in": "query",
                            "required": false,
                            "schema": { "type": "string" }
                        },
                        {
                            "name": "part_no",
                            "in": "query",
                            "required": false,
                            "schema": { "type": "string" }
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Item context resolved"
                        }
                    }
                }
            },
            "/document/legacy/item-export": {
                "post": {
                    "operationId": "export_single_item_document",
                    "summary": "선택 품목의 단건 구매 품의 문서 생성",
                    "description": "선택한 품목의 seed 필드와 대화형으로 채운 필드를 합쳐 단건 DOCX를 만들고 다운로드 URL을 반환한다.",
                    "requestBody": {
                        "required": true,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["fields"],
                                    "properties": {
                                        "part_name": { "type": "string" },
                                        "part_no": { "type": "string" },
                                        "fields": {
                                            "type": "object",
                                            "additionalProperties": true
                                        }
                                    }
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {
                            "description": "Single document exported"
                        }
                    }
                }
            },
            "/document/legacy/item-approve": {
                "post": {
                    "operationId": "approve_and_generate_item_document",
                    "summary": "승인/진행 의사 이후 단건 구매 품의 문서 최종 생성",
                    "description": "사용자가 승인해, 진행해 같은 긍정 의사를 보이면 이 도구를 사용한다. 가격 기준 문서 생성 방침을 적용하고, 일반적인 기본값을 채워 초안과 다운로드 URL을 함께 반환한다.",
                    "requestBody": {
                        "required": true,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "part_name": { "type": "string" },
                                        "part_no": { "type": "string" },
                                        "purchase_reason": { "type": "string" },
                                        "fields": {
                                            "type": "object",
                                            "additionalProperties": true
                                        }
                                    }
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {
                            "description": "Approved single document generated"
                        }
                    }
                }
            }
        }
    }))
}

fn app_router() -> Router {
    let cors = CorsLayer::new()
        .allow_origin(Any)
        .allow_methods(Any)
        .allow_headers(Any);

    Router::new()
        .route("/openapi.json", get(openapi_spec))
        .route("/health", get(health))
        .route("/document/create", post(create_document))
        .route("/document/fill", post(fill_document))
        .route("/document/export", post(export_document))
        .route("/document/legacy/run", post(run_legacy_document_batch))
        .route("/document/legacy/download", get(download_legacy_document))
        .route(
            "/document/legacy/package",
            post(generate_legacy_document_package),
        )
        .route("/document/legacy/shortages", get(list_legacy_shortages))
        .route(
            "/document/legacy/item-context",
            get(get_legacy_item_context),
        )
        .route(
            "/document/legacy/item-export",
            post(export_legacy_item_document),
        )
        .route(
            "/document/legacy/item-approve",
            post(approve_legacy_item_document),
        )
        .with_state(build_state())
        .layer(cors)
        .layer(TraceLayer::new_for_http())
}

fn build_state() -> AppState {
    let templates = HashMap::from([(
        "purchase_request".to_string(),
        TemplateDefinition {
            id: "purchase_request",
            display_name: "구매 품의서",
            required_fields: vec![
                "품명",
                "수량",
                "납품업체",
                "구매사유",
                "담당자 직접입력",
                "부품역할",
            ],
        },
    )]);

    AppState {
        templates: Arc::new(templates),
        sessions: Arc::new(RwLock::new(HashMap::new())),
        legacy_workdir: std::env::var("PORT_PROJECT_LEGACY_WORKDIR")
            .ok()
            .map(PathBuf::from)
            .or_else(|| Some(PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("DB")))
            .filter(|path| path.exists()),
        public_base_url: std::env::var("DOCUMENT_SERVICE_PUBLIC_BASE_URL")
            .unwrap_or_else(|_| "http://127.0.0.1:8001".to_string())
            .trim_end_matches('/')
            .to_string(),
    }
}

async fn create_document(
    State(state): State<AppState>,
    Json(req): Json<CreateRequest>,
) -> Result<Json<CreateResponse>, AppError> {
    let template = template_for(&state, &req.template_id)?;
    let session_id = Uuid::new_v4().to_string();

    let fields = extract_fields(template, &req.input_text);
    let missing_fields = compute_missing_fields(template, &fields);
    let next_question = missing_fields.first().map(|field| next_question_for(field));
    let preview_text = render_preview(template, &fields);

    let session = SessionState {
        session_id: session_id.clone(),
        template_id: template.id.to_string(),
        fields: fields.clone(),
        missing_fields: missing_fields.clone(),
    };

    state
        .sessions
        .write()
        .await
        .insert(session_id.clone(), session);

    Ok(Json(CreateResponse {
        session_id,
        updated_fields: fields,
        missing_fields,
        next_question,
        preview_text,
    }))
}

async fn fill_document(
    State(state): State<AppState>,
    Json(req): Json<FillRequest>,
) -> Result<Json<FillResponse>, AppError> {
    let template = template_for(&state, &req.template_id)?;
    let mut merged_fields = req.current_fields.clone();

    if let Some(existing) = state.sessions.read().await.get(&req.session_id).cloned() {
        for (key, value) in existing.fields {
            merged_fields.entry(key).or_insert(value);
        }
    }

    let extracted = extract_fields(template, &req.user_message);
    for (key, value) in extracted {
        merged_fields.insert(key, value);
    }

    let missing_fields = compute_missing_fields(template, &merged_fields);
    let next_question = missing_fields.first().map(|field| next_question_for(field));
    let preview_text = render_preview(template, &merged_fields);

    let session = SessionState {
        session_id: req.session_id.clone(),
        template_id: template.id.to_string(),
        fields: merged_fields.clone(),
        missing_fields: missing_fields.clone(),
    };

    state
        .sessions
        .write()
        .await
        .insert(req.session_id.clone(), session.clone());

    Ok(Json(FillResponse {
        updated_fields: merged_fields,
        missing_fields,
        next_question,
        preview_text,
        session,
    }))
}

async fn export_document(
    State(state): State<AppState>,
    Json(req): Json<ExportRequest>,
) -> Result<Json<ExportResponse>, AppError> {
    let template = template_for(&state, &req.template_id)?;
    let preview_text = render_preview(template, &req.fields);
    let format = req.format.clone();
    let file_name = format!("{}_{}.{}", template.id, Uuid::new_v4(), format);
    let mime_type = match format.as_str() {
        "docx" => "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "json" => "application/json",
        _ => "text/plain",
    }
    .to_string();

    if let Some((template_path, bytes)) =
        try_render_legacy_docx(&req.fields, &format).map_err(AppError::bad_request)?
    {
        return Ok(Json(ExportResponse {
            file_name,
            format,
            mime_type,
            content_base64: STANDARD.encode(bytes),
            preview_text: format!(
                "{preview_text}\n- export: legacy DOCX template applied from {template_path}"
            ),
            generated_at: Utc::now().to_rfc3339(),
        }));
    }

    let export_payload = serde_json::json!({
        "template_id": template.id,
        "display_name": template.display_name,
        "format": format,
        "fields": req.fields,
        "preview_text": preview_text.clone(),
        "note": "MVP stub. Legacy DOCX templates were not found in Port-Project templates directory."
    });

    Ok(Json(ExportResponse {
        file_name,
        format,
        mime_type,
        content_base64: STANDARD.encode(export_payload.to_string()),
        preview_text,
        generated_at: Utc::now().to_rfc3339(),
    }))
}

async fn run_legacy_document_batch(
    State(state): State<AppState>,
    payload: Option<Json<LegacyRunRequest>>,
) -> Result<Json<LegacyRunResponse>, AppError> {
    let workdir = state
        .legacy_workdir
        .clone()
        .ok_or_else(|| AppError::bad_request("legacy workdir is not configured".into()))?;

    let req = payload
        .map(|Json(body)| body)
        .unwrap_or(LegacyRunRequest { force: true });
    let response = run_legacy_batch_once(&workdir, req.force).await?;
    Ok(Json(response))
}

async fn download_legacy_document(
    State(state): State<AppState>,
    Query(query): Query<LegacyDownloadQuery>,
) -> Result<impl IntoResponse, AppError> {
    let workdir = state
        .legacy_workdir
        .clone()
        .ok_or_else(|| AppError::bad_request("legacy workdir is not configured".into()))?;
    let requested = workdir.join(&query.path);
    let canonical_db = workdir
        .canonicalize()
        .map_err(|err| AppError::bad_request(format!("invalid legacy DB path: {err}")))?;
    let canonical_file = requested
        .canonicalize()
        .map_err(|err| AppError::bad_request(format!("legacy file not found: {err}")))?;

    if !canonical_file.starts_with(&canonical_db) {
        return Err(AppError::bad_request(
            "requested file is outside the legacy DB directory".into(),
        ));
    }

    let bytes = fs::read(&canonical_file)
        .map_err(|err| AppError::bad_request(format!("failed to read legacy file: {err}")))?;
    let file_name = canonical_file
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or("download.bin");
    let mime_type = match canonical_file
        .extension()
        .and_then(|ext| ext.to_str())
        .unwrap_or("")
    {
        "docx" => "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "json" => "application/json",
        "zip" => "application/zip",
        "txt" | "log" => "text/plain; charset=utf-8",
        _ => "application/octet-stream",
    };

    Ok((
        [
            (CONTENT_TYPE, HeaderValue::from_static(mime_type)),
            (
                CONTENT_DISPOSITION,
                HeaderValue::from_str(&format!(
                    "attachment; filename*=UTF-8''{}",
                    url_encode_filename(file_name)
                ))
                .map_err(|err| {
                    AppError::bad_request(format!("failed to build content disposition: {err}"))
                })?,
            ),
        ],
        bytes,
    ))
}

async fn generate_legacy_document_package(
    State(state): State<AppState>,
    payload: Option<Json<LegacyRunRequest>>,
) -> Result<Json<LegacyPackageResponse>, AppError> {
    let workdir = state
        .legacy_workdir
        .clone()
        .ok_or_else(|| AppError::bad_request("legacy workdir is not configured".into()))?;
    let req = payload
        .map(|Json(body)| body)
        .unwrap_or(LegacyRunRequest { force: true });
    let run = run_legacy_batch_once(&workdir, req.force).await?;
    let workdir = PathBuf::from(&run.workdir);
    let zip_file_name = format!(
        "purchase_documents_{}.zip",
        Utc::now().format("%Y%m%d_%H%M%S")
    );
    let zip_relative = PathBuf::from("output")
        .join("packages")
        .join(&zip_file_name);
    let zip_absolute = workdir.join(&zip_relative);

    create_zip_bundle(&workdir, &zip_absolute, &run.generated_files)
        .map_err(|err| AppError::bad_request(format!("failed to create ZIP package: {err}")))?;

    let zip_path = zip_relative.to_string_lossy().to_string();
    let download_path = format!(
        "/document/legacy/download?path={}",
        url_encode_query_value(&zip_path)
    );
    let download_url = format!("{}{download_path}", state.public_base_url);
    let generated_files_preview = run
        .generated_files
        .iter()
        .take(5)
        .cloned()
        .collect::<Vec<_>>();
    let message = format!(
        "구매 품의 문서 {}건을 생성했고 ZIP 파일 {} 준비를 완료했습니다.",
        run.generated_count, zip_file_name
    );
    let assistant_summary = format!(
        "구매 품의 문서 전체 생성이 완료되었습니다. 총 {}건이 생성되었고, ZIP 다운로드 링크는 {} 입니다.",
        run.generated_count, download_url
    );

    Ok(Json(LegacyPackageResponse {
        generated_count: run.generated_count,
        zip_path: zip_path.clone(),
        zip_file_name,
        download_path,
        download_url,
        message,
        assistant_summary,
        generated_files_preview,
        batch_report_path: run.batch_report_path,
        snapshot_json_path: run.snapshot_json_path,
    }))
}

async fn list_legacy_shortages(
    State(state): State<AppState>,
    Query(query): Query<LegacyShortagesQuery>,
) -> Result<Json<LegacyShortagesResponse>, AppError> {
    let snapshot = load_latest_snapshot_json(&state)?;
    let snapshot_date = snapshot
        .get("meta")
        .and_then(|meta| meta.get("snapshot_date"))
        .and_then(|value| value.as_str())
        .map(|value| value.to_string());
    let parts = snapshot
        .get("parts")
        .and_then(|value| value.as_object())
        .ok_or_else(|| AppError::bad_request("invalid snapshot format: parts missing".into()))?;

    let needle = query.query.as_deref().map(normalize_lookup_text);
    let limit = query.limit.unwrap_or(20).clamp(1, 100);
    let mut items = parts
        .values()
        .filter_map(build_shortage_item)
        .filter(|item| {
            if let Some(needle) = &needle {
                let hay = normalize_lookup_text(&format!("{} {}", item.part_name, item.part_no));
                hay.contains(needle)
            } else {
                true
            }
        })
        .collect::<Vec<_>>();

    items.sort_by(|a, b| {
        a.current_stock_updated
            .partial_cmp(&b.current_stock_updated)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| {
                b.outbound_qty_sum
                    .partial_cmp(&a.outbound_qty_sum)
                    .unwrap_or(std::cmp::Ordering::Equal)
            })
            .then_with(|| a.part_name.cmp(&b.part_name))
    });
    let total_count = items.len();
    items.truncate(limit);

    Ok(Json(LegacyShortagesResponse {
        snapshot_date,
        total_count,
        items,
    }))
}

async fn get_legacy_item_context(
    State(state): State<AppState>,
    Query(query): Query<LegacyItemContextQuery>,
) -> Result<Json<LegacyItemContextResponse>, AppError> {
    let part = resolve_snapshot_part(&state, query.part_name.as_deref(), query.part_no.as_deref())?;
    let part_name = part
        .get("part_name")
        .and_then(|value| value.as_str())
        .unwrap_or("기록없음")
        .to_string();
    let part_no = part
        .get("part_no")
        .and_then(|value| value.as_str())
        .unwrap_or(&part_name)
        .to_string();
    let current_stock_before = part
        .get("current_stock_before")
        .and_then(|value| value.as_f64())
        .unwrap_or(0.0);
    let current_stock_updated = part
        .get("current_stock_updated")
        .and_then(|value| value.as_f64())
        .unwrap_or(current_stock_before);
    let inbound_qty_sum = part
        .get("inbound_qty_sum")
        .and_then(|value| value.as_f64())
        .unwrap_or(0.0);
    let outbound_qty_sum = part
        .get("outbound_qty_sum")
        .and_then(|value| value.as_f64())
        .unwrap_or(0.0);
    let outbound_count = part
        .get("outbound_count")
        .and_then(|value| value.as_u64())
        .unwrap_or(0) as usize;

    let context = format!(
        "품목명: {part_name}\n품번: {part_no}\n현재고(기준): {current_stock_before:.0}\n현재고(업데이트): {current_stock_updated:.0}\n입고 합계: {inbound_qty_sum:.0}\n출고 합계: {outbound_qty_sum:.0}\n출고 건수: {outbound_count}\n상태: {}",
        if current_stock_updated <= 0.0 { "재고 부족 또는 소진" } else { "재고 확인 필요" }
    );

    let mut fields_seed = BTreeMap::new();
    fields_seed.insert("품명".into(), serde_json::Value::String(part_name.clone()));
    fields_seed.insert("품번".into(), serde_json::Value::String(part_no.clone()));
    fields_seed.insert("현재고".into(), serde_json::json!(current_stock_updated));
    fields_seed.insert("수량".into(), serde_json::json!(1));
    fields_seed.insert(
        "부품역할".into(),
        serde_json::Value::String("(직접입력)".into()),
    );
    fields_seed.insert(
        "납품업체".into(),
        serde_json::Value::String("(직접입력)".into()),
    );

    let guided_fields = vec![
        GuidedFieldSpec {
            field: "구매사유".into(),
            label: "구매 사유".into(),
            prompt: "이 품목이 왜 지금 필요한지 재고 상황과 현장 위험을 포함해 한국어로 정리해줘."
                .into(),
        },
        GuidedFieldSpec {
            field: "담당자 직접입력".into(),
            label: "담당자 확인".into(),
            prompt: "이 문서를 검토하거나 설명할 담당자 정보를 한국어로 정리해줘.".into(),
        },
        GuidedFieldSpec {
            field: "납품업체".into(),
            label: "업체/거래처".into(),
            prompt: "구매 예정 업체나 공급업체 정보를 한국어로 정리해줘.".into(),
        },
        GuidedFieldSpec {
            field: "부품역할".into(),
            label: "부품 설명".into(),
            prompt:
                "이 부품의 핵심 기능과 실제 사용 목적을 구매 품의 문서 본문용 한국어로 정리해줘."
                    .into(),
        },
    ];

    let assistant_summary = format!(
        "{} ({}) 품목의 문서 작성 컨텍스트를 준비했습니다. 이제 guided_fields를 기준으로 대화형으로 값을 채운 뒤 단건 문서를 생성하면 됩니다.",
        part_name, part_no
    );

    Ok(Json(LegacyItemContextResponse {
        part_name,
        part_no,
        context,
        fields_seed,
        guided_fields,
        assistant_summary,
    }))
}

async fn export_legacy_item_document(
    State(state): State<AppState>,
    Json(req): Json<LegacyItemExportRequest>,
) -> Result<Json<LegacyItemExportResponse>, AppError> {
    let workdir = state
        .legacy_workdir
        .clone()
        .ok_or_else(|| AppError::bad_request("legacy workdir is not configured".into()))?;
    let mut fields = req.fields.clone();

    if fields.get("품명").is_none() || fields.get("품번").is_none() {
        let part = resolve_snapshot_part(&state, req.part_name.as_deref(), req.part_no.as_deref())?;
        if fields.get("품명").is_none() {
            if let Some(value) = part.get("part_name").and_then(|value| value.as_str()) {
                fields.insert("품명".into(), serde_json::Value::String(value.to_string()));
            }
        }
        if fields.get("품번").is_none() {
            if let Some(value) = part.get("part_no").and_then(|value| value.as_str()) {
                fields.insert("품번".into(), serde_json::Value::String(value.to_string()));
            }
        }
        if fields.get("현재고").is_none() {
            if let Some(value) = part
                .get("current_stock_updated")
                .and_then(|value| value.as_f64())
            {
                fields.insert("현재고".into(), serde_json::json!(value));
            }
        }
    }

    let (template_path, bytes) = try_render_legacy_docx(&fields, "docx")
        .map_err(AppError::bad_request)?
        .ok_or_else(|| {
            AppError::bad_request("legacy DOCX template could not be resolved".into())
        })?;

    let item_name = as_string(fields.get("품명"), "purchase_request");
    let file_name = format!(
        "single_{}_{}.docx",
        sanitize_filename_for_output(&item_name),
        Utc::now().format("%Y%m%d_%H%M%S")
    );
    let relative = PathBuf::from("output").join("single").join(file_name);
    let absolute = workdir.join(&relative);
    if let Some(parent) = absolute.parent() {
        fs::create_dir_all(parent).map_err(|err| {
            AppError::bad_request(format!("failed to create single output directory: {err}"))
        })?;
    }
    fs::write(&absolute, bytes)
        .map_err(|err| AppError::bad_request(format!("failed to write single document: {err}")))?;

    let output_path = relative.to_string_lossy().to_string();
    let download_path = format!(
        "/document/legacy/download?path={}",
        url_encode_query_value(&output_path)
    );
    let download_url = format!("{}{download_path}", state.public_base_url);
    let assistant_summary = format!(
        "단건 구매 품의 문서를 생성했습니다. 템플릿 경로는 {} 이고, 다운로드 링크는 {} 입니다.",
        template_path, download_url
    );

    Ok(Json(LegacyItemExportResponse {
        output_path,
        download_path,
        download_url,
        file_name: absolute
            .file_name()
            .and_then(|name| name.to_str())
            .unwrap_or("single.docx")
            .to_string(),
        assistant_summary,
    }))
}

async fn approve_legacy_item_document(
    State(state): State<AppState>,
    Json(req): Json<LegacyApproveRequest>,
) -> Result<Json<LegacyApproveResponse>, AppError> {
    let workdir = state
        .legacy_workdir
        .clone()
        .ok_or_else(|| AppError::bad_request("legacy workdir is not configured".into()))?;

    let part = resolve_snapshot_part(&state, req.part_name.as_deref(), req.part_no.as_deref())?;
    let part_name = part
        .get("part_name")
        .and_then(|value| value.as_str())
        .unwrap_or("기록없음")
        .to_string();
    let part_no = part
        .get("part_no")
        .and_then(|value| value.as_str())
        .unwrap_or(&part_name)
        .to_string();
    let current_stock_updated = part
        .get("current_stock_updated")
        .and_then(|value| value.as_f64())
        .unwrap_or(0.0);
    let outbound_qty_sum = part
        .get("outbound_qty_sum")
        .and_then(|value| value.as_f64())
        .unwrap_or(0.0);

    let mut fields = req.fields.clone();
    fields
        .entry("품명".into())
        .or_insert_with(|| serde_json::Value::String(part_name.clone()));
    fields
        .entry("품번".into())
        .or_insert_with(|| serde_json::Value::String(part_no.clone()));
    fields
        .entry("현재고".into())
        .or_insert_with(|| serde_json::json!(current_stock_updated));
    fields
        .entry("수량".into())
        .or_insert_with(|| serde_json::json!(1));
    fields
        .entry("납품업체".into())
        .or_insert_with(|| serde_json::Value::String("지정 협력사".into()));
    fields
        .entry("제조사".into())
        .or_insert_with(|| serde_json::Value::String("기존 등록 제조사 기준".into()));
    fields
        .entry("단위".into())
        .or_insert_with(|| serde_json::Value::String("EA".into()));
    fields
        .entry("담당자 직접입력".into())
        .or_insert_with(|| serde_json::Value::String("자재관리팀 담당자".into()));
    fields.entry("부품역할".into()).or_insert_with(|| {
        serde_json::Value::String(format!(
            "{}는 설비의 정상 운전을 유지하기 위해 필요한 핵심 부품으로, 현장 장비의 기능 유지와 예방 정비 목적에 사용됩니다. 적기 교체 및 확보를 통해 설비 고장 위험과 비가동 시간을 줄이는 데 활용됩니다.",
            part_name
        ))
    });

    let purchase_reason = req.purchase_reason.unwrap_or_else(|| {
        format!(
            "{} 품목은 현재 재고가 소진 또는 부족 상태이며 출고 누적 수량이 {:.0}건 반영된 상태입니다. 재고 소진으로 인한 설비 가동 중단 방지를 위해 우선 구매가 필요합니다.",
            part_name, outbound_qty_sum
        )
    });
    fields.insert(
        "구매사유".into(),
        serde_json::Value::String(purchase_reason),
    );

    let pricing_decision = decide_purchase_v2(
        as_f64(fields.get("필수재고량")),
        as_f64(fields.get("현재고")).unwrap_or(0.0),
        as_f64(fields.get("단가")),
    );
    let draft_preview = render_preview(
        &TemplateDefinition {
            id: "purchase_request",
            display_name: "구매 품의서",
            required_fields: vec!["품명", "수량", "납품업체"],
        },
        &fields,
    );

    let (template_path, bytes) = try_render_legacy_docx(&fields, "docx")
        .map_err(AppError::bad_request)?
        .ok_or_else(|| {
            AppError::bad_request("legacy DOCX template could not be resolved".into())
        })?;

    let file_name = format!(
        "approved_{}_{}.docx",
        sanitize_filename_for_output(&part_name),
        Utc::now().format("%Y%m%d_%H%M%S")
    );
    let relative = PathBuf::from("output").join("approved").join(file_name);
    let absolute = workdir.join(&relative);
    if let Some(parent) = absolute.parent() {
        fs::create_dir_all(parent).map_err(|err| {
            AppError::bad_request(format!("failed to create approved output directory: {err}"))
        })?;
    }
    fs::write(&absolute, bytes).map_err(|err| {
        AppError::bad_request(format!("failed to write approved document: {err}"))
    })?;

    let output_path = relative.to_string_lossy().to_string();
    let download_path = format!(
        "/document/legacy/download?path={}",
        url_encode_query_value(&output_path)
    );
    let download_url = format!("{}{download_path}", state.public_base_url);
    let assistant_summary = format!(
        "{} ({}) 품목에 대해 가격 기준 문서 생성 방침을 적용하여 최종 문서를 생성했습니다. 초안을 검토한 뒤 다운로드 링크 {} 에서 파일을 받을 수 있습니다.",
        part_name, part_no, download_url
    );

    Ok(Json(LegacyApproveResponse {
        pricing_policy_note: format!("{} | 템플릿 경로: {}", pricing_decision.note, template_path),
        draft_preview,
        resolved_fields: fields,
        output_path,
        download_path,
        download_url,
        file_name: absolute
            .file_name()
            .and_then(|name| name.to_str())
            .unwrap_or("approved.docx")
            .to_string(),
        assistant_summary,
    }))
}

fn template_for<'a>(
    state: &'a AppState,
    template_id: &str,
) -> Result<&'a TemplateDefinition, AppError> {
    state
        .templates
        .get(template_id)
        .ok_or_else(|| AppError::bad_request(format!("unknown template_id: {template_id}")))
}

fn compute_missing_fields(
    template: &TemplateDefinition,
    fields: &BTreeMap<String, serde_json::Value>,
) -> Vec<String> {
    template
        .required_fields
        .iter()
        .filter(|field| match fields.get(**field) {
            Some(serde_json::Value::Null) => true,
            Some(serde_json::Value::String(value)) => value.trim().is_empty(),
            Some(_) => false,
            None => true,
        })
        .map(|field| (*field).to_string())
        .collect()
}

fn extract_fields(
    template: &TemplateDefinition,
    input: &str,
) -> BTreeMap<String, serde_json::Value> {
    let mut fields = BTreeMap::new();

    if template.id == "purchase_request" {
        merge_explicit_field_values(&mut fields, input);
        let quantity_re = Regex::new(r"(\d+)\s*(개|EA|ea)?").unwrap();

        if !fields.contains_key("품명") && input.contains("SSD") {
            fields.insert("품명".into(), serde_json::Value::String("SSD".into()));
        }

        if !fields.contains_key("품명") && input.contains("HDD") {
            fields.insert("품명".into(), serde_json::Value::String("HDD".into()));
        }

        if !fields.contains_key("수량") {
            if let Some(captures) = quantity_re.captures(input) {
                if let Ok(quantity) = captures[1].parse::<u64>() {
                    fields.insert("수량".into(), serde_json::Value::Number(quantity.into()));
                }
            }
        }

        if !fields.contains_key("납품업체") {
            if let Some(vendor) = extract_vendor(input) {
                fields.insert("납품업체".into(), serde_json::Value::String(vendor));
            }
        }
    }

    fields
}

fn merge_explicit_field_values(fields: &mut BTreeMap<String, serde_json::Value>, input: &str) {
    if let Some(json_fields) = extract_json_field_values(input) {
        for (key, value) in json_fields {
            fields.insert(key, value);
        }
    }

    let known_fields = [
        "품명",
        "수량",
        "납품업체",
        "구매사유",
        "담당자 직접입력",
        "부품역할",
        "품번",
        "현재고",
        "필수재고량",
        "단가",
        "제조사",
        "단위",
    ];

    for raw_line in input.lines() {
        let line = raw_line
            .trim()
            .trim_start_matches(['-', '*', '•', ' '])
            .trim();
        let Some((field, value)) = split_field_line(line) else {
            continue;
        };
        if known_fields.contains(&field) && !value.trim().is_empty() {
            fields.insert(field.to_string(), parse_field_value(value.trim()));
        }
    }
}

fn extract_json_field_values(input: &str) -> Option<BTreeMap<String, serde_json::Value>> {
    let start = input.find('{')?;
    let end = input.rfind('}')?;
    if end <= start {
        return None;
    }
    let parsed = serde_json::from_str::<serde_json::Value>(&input[start..=end]).ok()?;
    let object = parsed
        .get("fields")
        .and_then(|value| value.as_object())
        .or_else(|| parsed.as_object())?;

    let mut out = BTreeMap::new();
    for (key, value) in object {
        if !key.trim().is_empty() && !value.is_null() {
            out.insert(key.trim().to_string(), value.clone());
        }
    }
    Some(out)
}

fn split_field_line(line: &str) -> Option<(&str, &str)> {
    for delimiter in [":", "=", "："] {
        if let Some((field, value)) = line.split_once(delimiter) {
            return Some((field.trim(), value.trim()));
        }
    }
    None
}

fn parse_field_value(value: &str) -> serde_json::Value {
    let normalized = value.replace(',', "");
    if let Ok(number) = normalized.parse::<i64>() {
        return serde_json::Value::Number(number.into());
    }
    if let Ok(number) = normalized.parse::<f64>() {
        if let Some(value) = serde_json::Number::from_f64(number) {
            return serde_json::Value::Number(value);
        }
    }
    serde_json::Value::String(value.to_string())
}

fn extract_vendor(input: &str) -> Option<String> {
    let patterns = [
        r"납품업체는\s*([^\s,.]+)",
        r"업체는\s*([^\s,.]+)",
        r"([A-Za-z0-9가-힣]+)\s*에서\s*구매",
    ];

    for pattern in patterns {
        let re = Regex::new(pattern).unwrap();
        if let Some(captures) = re.captures(input) {
            return Some(captures[1].trim().to_string());
        }
    }

    None
}

fn next_question_for(field: &str) -> String {
    match field {
        "품명" => "어떤 품목을 요청할까요?".into(),
        "수량" => "수량은 몇 개로 할까요?".into(),
        "납품업체" => "납품업체는 어디로 할까?".into(),
        "구매사유" => "구매사유는 어떻게 적을까요? 재고 부족, 설비 중단 위험, 사용 이력 중 확인된 근거를 알려주세요.".into(),
        "담당자 직접입력" => "담당자는 누구로 적을까요? 모르면 자재관리팀 담당자로 둘 수 있습니다.".into(),
        "부품역할" => "부품역할 칸에 넣을 설명이 필요합니다. 이 부품이 설비에서 하는 역할이나 사용 목적을 알려주세요.".into(),
        _ => format!("{field} 값을 알려주세요."),
    }
}

fn render_preview(
    template: &TemplateDefinition,
    fields: &BTreeMap<String, serde_json::Value>,
) -> String {
    let item = string_or_placeholder(fields.get("품명"));
    let quantity = string_or_placeholder(fields.get("수량"));
    let vendor = string_or_placeholder(fields.get("납품업체"));

    format!(
        "[{}]\n- 품명: {}\n- 수량: {}\n- 납품업체: {}\n{}",
        template.display_name,
        item,
        quantity,
        vendor,
        preview_purchase_note(fields)
    )
}

fn string_or_placeholder(value: Option<&serde_json::Value>) -> String {
    match value {
        Some(serde_json::Value::String(v)) if !v.is_empty() => v.clone(),
        Some(serde_json::Value::Number(v)) => v.to_string(),
        Some(other) if !other.is_null() => other.to_string(),
        _ => "(미입력)".into(),
    }
}

fn preview_purchase_note(fields: &BTreeMap<String, serde_json::Value>) -> String {
    let decision = decide_purchase_v2(
        as_f64(fields.get("필수재고량")),
        as_f64(fields.get("현재고")).unwrap_or(0.0),
        as_f64(fields.get("단가")),
    );
    let reason = build_purchase_reason_text(&build_legacy_row(fields, &decision));
    format!("- 구매판단: {}\n- 자동사유: {}\n", decision.note, reason)
}

fn as_f64(value: Option<&serde_json::Value>) -> Option<f64> {
    match value {
        Some(serde_json::Value::Number(n)) => n.as_f64(),
        Some(serde_json::Value::String(s)) => s.replace(',', "").trim().parse::<f64>().ok(),
        _ => None,
    }
}

fn as_string(value: Option<&serde_json::Value>, fallback: &str) -> String {
    match value {
        Some(serde_json::Value::String(s)) if !s.trim().is_empty() => s.clone(),
        Some(serde_json::Value::Number(n)) => n.to_string(),
        Some(v) if !v.is_null() => v.to_string(),
        _ => fallback.to_string(),
    }
}

fn build_legacy_row(
    fields: &BTreeMap<String, serde_json::Value>,
    decision: &PurchaseDecision,
) -> LegacyDocumentRow {
    let replacement_dates =
        std::array::from_fn(|idx| as_string(fields.get(format!("날짜{}", idx + 1).as_str()), ""));
    let replacement_qtys = std::array::from_fn(|idx| {
        as_string(fields.get(format!("교체수량{}", idx + 1).as_str()), "")
    });
    let replacement_hosts =
        std::array::from_fn(|idx| as_string(fields.get(format!("호기{}", idx + 1).as_str()), ""));
    let item_name = as_string(fields.get("품명"), "기록없음");
    let purchase_qty = as_f64(fields.get("수량")).unwrap_or(1.0).max(1.0);
    let has_replacement_history = fields
        .get("교체내역 유무")
        .and_then(|v| v.as_str())
        .map(|v| v == "유")
        .unwrap_or_else(|| replacement_dates.iter().any(|v| !v.is_empty()));

    LegacyDocumentRow {
        part_key: as_string(fields.get("파트키"), &item_name),
        part_no: as_string(fields.get("품번"), &item_name),
        part_name: item_name,
        received_date: as_string(fields.get("입고일"), "입고기록없음"),
        used_date_last: as_string(fields.get("사용일"), "출고기록없음"),
        used_where: as_string(fields.get("사용처"), "기록없음"),
        usage_reason: as_string(fields.get("문제점"), "기록없음"),
        replacement_reason: as_string(fields.get("교체사유"), "기록없음"),
        current_stock_before: as_f64(fields.get("현재고")).unwrap_or(0.0),
        required_stock: as_f64(fields.get("필수재고량")),
        purchase_qty,
        purchase_order_note: decision.note.clone(),
        issued_qty: as_string(fields.get("총 교체수량"), &format!("{purchase_qty:.0}")),
        replacement_dates,
        replacement_qtys,
        replacement_hosts,
        vendor_name: as_string(fields.get("납품업체"), "기록없음"),
        manufacturer_name: as_string(fields.get("제조사"), "기록없음"),
        unit: as_string(fields.get("단위"), "기록없음"),
        unit_price: as_string(fields.get("단가"), "기록없음"),
        part_role: as_string(fields.get("부품역할"), "(직접입력)"),
        template_kind: decision.template_kind,
        has_replacement_history,
    }
}

fn try_render_legacy_docx(
    fields: &BTreeMap<String, serde_json::Value>,
    format: &str,
) -> Result<Option<(String, Vec<u8>)>, String> {
    if format != "docx" {
        return Ok(None);
    }

    let decision = decide_purchase_v2(
        as_f64(fields.get("필수재고량")),
        as_f64(fields.get("현재고")).unwrap_or(0.0),
        as_f64(fields.get("단가")),
    );
    let row = build_legacy_row(fields, &decision);
    let service_root = Path::new(env!("CARGO_MANIFEST_DIR"));

    for candidate in template_dir_candidates(service_root) {
        if let Some(templates) = load_named_templates(&candidate)? {
            let selected = select_template_for_row(&row, &templates);
            let bytes = render_docx_bytes(selected, &row, 1)?;
            return Ok(Some((candidate.display().to_string(), bytes)));
        }
    }

    Ok(None)
}

#[derive(Debug)]
struct AppError {
    status: StatusCode,
    message: String,
}

impl AppError {
    fn bad_request(message: String) -> Self {
        Self {
            status: StatusCode::BAD_REQUEST,
            message,
        }
    }
}

async fn run_legacy_batch_once(workdir: &Path, force: bool) -> Result<LegacyRunResponse, AppError> {
    let workdir = workdir.to_path_buf();
    let summary = tokio::task::spawn_blocking(move || {
        legacy_engine::run_batch_once_from_workdir(&workdir, force, false)
    })
    .await
    .map_err(|err| AppError::bad_request(format!("failed to join legacy batch task: {err}")))?
    .map_err(|err| AppError::bad_request(format!("failed to run legacy batch: {err:#}")))?;

    Ok(LegacyRunResponse {
        workdir: summary.workdir.display().to_string(),
        output_dir: summary
            .output_dir
            .strip_prefix(&summary.workdir)
            .unwrap_or(&summary.output_dir)
            .to_string_lossy()
            .to_string(),
        generated_count: summary.generated_count,
        generated_files: summary.generated_files,
        snapshot_json_path: summary.snapshot_json_path,
        batch_report_path: summary.batch_report_path,
        stdout: summary.status_note,
    })
}

fn latest_legacy_output_dir(workdir: &Path) -> Option<PathBuf> {
    let output_root = workdir.join("output");
    let entries = fs::read_dir(output_root).ok()?;
    let mut dated_dirs = entries
        .filter_map(|entry| entry.ok().map(|e| e.path()))
        .filter(|path| path.is_dir())
        .collect::<Vec<_>>();
    dated_dirs.sort();
    dated_dirs.pop()
}

fn list_docx_files(root: &Path, workdir: &Path) -> Result<Vec<String>, std::io::Error> {
    let mut stack = vec![root.to_path_buf()];
    let mut files = Vec::new();

    while let Some(dir) = stack.pop() {
        for entry in fs::read_dir(&dir)? {
            let path = entry?.path();
            if path.is_dir() {
                stack.push(path);
            } else if path
                .extension()
                .and_then(|ext| ext.to_str())
                .map(|ext| ext.eq_ignore_ascii_case("docx"))
                .unwrap_or(false)
            {
                if let Ok(relative) = path.strip_prefix(workdir) {
                    files.push(relative.to_string_lossy().to_string());
                }
            }
        }
    }

    files.sort();
    Ok(files)
}

fn latest_matching_file(dir: &Path, prefix: &str, suffix: &str) -> Option<PathBuf> {
    let mut files = fs::read_dir(dir)
        .ok()?
        .filter_map(|entry| entry.ok().map(|e| e.path()))
        .filter(|path| path.is_file())
        .filter(|path| {
            path.file_name()
                .and_then(|name| name.to_str())
                .map(|name| name.starts_with(prefix) && name.ends_with(suffix))
                .unwrap_or(false)
        })
        .collect::<Vec<_>>();
    files.sort();
    files.pop()
}

fn load_latest_snapshot_json(state: &AppState) -> Result<serde_json::Value, AppError> {
    let workdir = state
        .legacy_workdir
        .clone()
        .ok_or_else(|| AppError::bad_request("legacy workdir is not configured".into()))?;
    let candidate = find_latest_snapshot_json(&workdir).or_else(|| {
        legacy_engine::run_batch_once_from_workdir(&workdir, true, false)
            .ok()
            .and_then(|summary| {
                summary
                    .snapshot_json_path
                    .map(|path| workdir.join(path))
                    .filter(|path| path.exists())
            })
            .or_else(|| find_latest_snapshot_json(&workdir))
    });
    let candidate =
        candidate.ok_or_else(|| AppError::bad_request("stock snapshot json not found".into()))?;
    let raw = fs::read_to_string(&candidate)
        .map_err(|err| AppError::bad_request(format!("failed to read snapshot json: {err}")))?;
    serde_json::from_str(&raw)
        .map_err(|err| AppError::bad_request(format!("failed to parse snapshot json: {err}")))
}

fn find_latest_snapshot_json(workdir: &Path) -> Option<PathBuf> {
    let direct = workdir.join("output").join("stock_in_out_monthly.json");
    if direct.exists() {
        return Some(direct);
    }

    latest_matching_file(&workdir.join("output"), "stock_in_out_monthly", ".json")
}

fn build_shortage_item(part: &serde_json::Value) -> Option<LegacyShortageItem> {
    let part_name = part.get("part_name")?.as_str()?.trim().to_string();
    let part_no = part
        .get("part_no")
        .and_then(|value| value.as_str())
        .unwrap_or(&part_name)
        .trim()
        .to_string();
    let current_stock_before = part
        .get("current_stock_before")
        .and_then(|value| value.as_f64())
        .unwrap_or(0.0);
    let current_stock_updated = part
        .get("current_stock_updated")
        .and_then(|value| value.as_f64())
        .unwrap_or(current_stock_before);
    let inbound_qty_sum = part
        .get("inbound_qty_sum")
        .and_then(|value| value.as_f64())
        .unwrap_or(0.0);
    let outbound_qty_sum = part
        .get("outbound_qty_sum")
        .and_then(|value| value.as_f64())
        .unwrap_or(0.0);
    let outbound_count = part
        .get("outbound_count")
        .and_then(|value| value.as_u64())
        .unwrap_or(0) as usize;

    if current_stock_updated > 0.0 && current_stock_before > 0.0 {
        return None;
    }

    let stock_status = if current_stock_updated <= 0.0 {
        "재고 없음".to_string()
    } else {
        "재고 부족".to_string()
    };
    let summary = format!(
        "{} ({})는 현재고 {:.0}, 업데이트 현재고 {:.0}, 출고합계 {:.0}로 구매 검토가 필요한 상태입니다.",
        part_name, part_no, current_stock_before, current_stock_updated, outbound_qty_sum
    );
    let document_request_hint = format!("{part_name} ({part_no}) 품목으로 구매 품의 문서 작성해줘");

    Some(LegacyShortageItem {
        part_name,
        part_no,
        current_stock_before,
        current_stock_updated,
        inbound_qty_sum,
        outbound_qty_sum,
        outbound_count,
        stock_status,
        summary,
        document_request_hint,
    })
}

fn resolve_snapshot_part(
    state: &AppState,
    part_name: Option<&str>,
    part_no: Option<&str>,
) -> Result<serde_json::Value, AppError> {
    let snapshot = load_latest_snapshot_json(state)?;
    let parts = snapshot
        .get("parts")
        .and_then(|value| value.as_object())
        .ok_or_else(|| AppError::bad_request("invalid snapshot format: parts missing".into()))?;

    let name_needle = part_name.map(normalize_lookup_text);
    let no_needle = part_no.map(normalize_lookup_text);

    for value in parts.values() {
        let name = value
            .get("part_name")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        let no = value.get("part_no").and_then(|v| v.as_str()).unwrap_or("");
        let normalized_name = normalize_lookup_text(name);
        let normalized_no = normalize_lookup_text(no);
        let name_match = name_needle
            .as_ref()
            .map(|needle| normalized_name.contains(needle))
            .unwrap_or(false);
        let no_match = no_needle
            .as_ref()
            .map(|needle| normalized_no.contains(needle))
            .unwrap_or(false);
        if (name_needle.is_some() && name_match) || (no_needle.is_some() && no_match) {
            return Ok(value.clone());
        }
    }

    Err(AppError::bad_request(
        "requested part was not found in the latest snapshot".into(),
    ))
}

fn normalize_lookup_text(input: &str) -> String {
    input
        .chars()
        .filter(|ch| ch.is_ascii_alphanumeric() || ('가'..='힣').contains(ch))
        .flat_map(|ch| ch.to_lowercase())
        .collect()
}

fn sanitize_filename_for_output(input: &str) -> String {
    let cleaned = input
        .chars()
        .map(|ch| match ch {
            '/' | '\\' | ':' | '*' | '?' | '"' | '<' | '>' | '|' => '_',
            '\n' | '\r' | '\t' => ' ',
            _ => ch,
        })
        .collect::<String>();
    let collapsed = cleaned.split_whitespace().collect::<Vec<_>>().join("_");
    if collapsed.is_empty() {
        "document".to_string()
    } else {
        collapsed
    }
}

fn create_zip_bundle(
    workdir: &Path,
    zip_absolute: &Path,
    generated_files: &[String],
) -> Result<(), String> {
    let parent = zip_absolute
        .parent()
        .ok_or_else(|| format!("invalid zip path: {}", zip_absolute.display()))?;
    fs::create_dir_all(parent).map_err(|err| err.to_string())?;
    let file = fs::File::create(zip_absolute).map_err(|err| err.to_string())?;
    let mut writer = zip::ZipWriter::new(file);

    for relative in generated_files {
        let absolute = workdir.join(relative);
        let bytes = fs::read(&absolute)
            .map_err(|err| format!("read {} failed: {err}", absolute.display()))?;
        writer
            .start_file(
                relative.replace('\\', "/"),
                zip::write::SimpleFileOptions::default()
                    .compression_method(zip::CompressionMethod::Deflated)
                    .unix_permissions(0o644),
            )
            .map_err(|err| err.to_string())?;
        use std::io::Write as _;
        writer.write_all(&bytes).map_err(|err| err.to_string())?;
    }

    writer.finish().map_err(|err| err.to_string())?;
    Ok(())
}

fn url_encode_filename(value: &str) -> String {
    let mut encoded = String::with_capacity(value.len());
    for byte in value.bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'.' | b'-' | b'_' => {
                encoded.push(byte as char)
            }
            b' ' => encoded.push_str("%20"),
            _ => encoded.push_str(&format!("%{byte:02X}")),
        }
    }
    encoded
}

fn url_encode_query_value(value: &str) -> String {
    let mut encoded = String::with_capacity(value.len());
    for byte in value.bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'.' | b'-' | b'_' | b'~' => {
                encoded.push(byte as char)
            }
            _ => encoded.push_str(&format!("%{byte:02X}")),
        }
    }
    encoded
}

impl IntoResponse for AppError {
    fn into_response(self) -> axum::response::Response {
        let body = Json(serde_json::json!({ "error": self.message }));
        (self.status, body).into_response()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::body::Body;
    use axum::http::{Request, StatusCode};
    use tower::ServiceExt;

    #[tokio::test]
    async fn create_document_extracts_fields_and_returns_missing_vendor() {
        let app = app_router();
        let request = Request::builder()
            .method("POST")
            .uri("/document/create")
            .header("content-type", "application/json")
            .body(Body::from(
                r#"{"template_id":"purchase_request","input_text":"구매 품의서 만들어줘, SSD 3개"}"#,
            ))
            .unwrap();

        let response = app.oneshot(request).await.unwrap();
        assert_eq!(response.status(), StatusCode::OK);

        let body = axum::body::to_bytes(response.into_body(), usize::MAX)
            .await
            .unwrap();
        let payload: CreateResponse = serde_json::from_slice(&body).unwrap();

        assert_eq!(
            payload.updated_fields.get("품명"),
            Some(&serde_json::Value::String("SSD".into()))
        );
        assert_eq!(
            payload.updated_fields.get("수량"),
            Some(&serde_json::Value::Number(3_u64.into()))
        );
        assert_eq!(payload.missing_fields, vec!["납품업체"]);
        assert_eq!(
            payload.next_question.as_deref(),
            Some("납품업체는 어디로 할까?")
        );
    }

    #[tokio::test]
    async fn fill_document_merges_session_fields_and_completes_template() {
        let app = app_router();
        let create_request = Request::builder()
            .method("POST")
            .uri("/document/create")
            .header("content-type", "application/json")
            .body(Body::from(
                r#"{"template_id":"purchase_request","input_text":"SSD 3개 구매 요청"}"#,
            ))
            .unwrap();

        let create_response = app.clone().oneshot(create_request).await.unwrap();
        let create_body = axum::body::to_bytes(create_response.into_body(), usize::MAX)
            .await
            .unwrap();
        let created: CreateResponse = serde_json::from_slice(&create_body).unwrap();

        let fill_body = serde_json::json!({
            "template_id": "purchase_request",
            "session_id": created.session_id,
            "current_fields": created.updated_fields,
            "user_message": "납품업체는 하이닉스야"
        });

        let fill_request = Request::builder()
            .method("POST")
            .uri("/document/fill")
            .header("content-type", "application/json")
            .body(Body::from(fill_body.to_string()))
            .unwrap();

        let fill_response = app.oneshot(fill_request).await.unwrap();
        assert_eq!(fill_response.status(), StatusCode::OK);

        let body = axum::body::to_bytes(fill_response.into_body(), usize::MAX)
            .await
            .unwrap();
        let payload: FillResponse = serde_json::from_slice(&body).unwrap();

        assert!(payload.missing_fields.is_empty());
        assert_eq!(payload.next_question, None);
        assert_eq!(
            payload.updated_fields.get("납품업체"),
            Some(&serde_json::Value::String("하이닉스야".into()))
        );
    }
}
