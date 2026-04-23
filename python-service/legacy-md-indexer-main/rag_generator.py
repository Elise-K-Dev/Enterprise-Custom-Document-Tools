"""
Phase 3 RAG Generator (BM25 + Kiwi Tokenizer)
- Kiwi 형태소 분석기 기반 BM25 본문 검색
- Stream response 처리 (vLLM 호환)
- Context Window(128k) 대응 및 사전 Truncate
"""
import logging
import requests
import configparser
import json
import time
from pathlib import Path
from typing import List, Dict, Any, Generator, Union
from rank_bm25 import BM25Okapi
from kiwipiepy import Kiwi

logger = logging.getLogger("RAG_GEN")
logger.setLevel(logging.INFO)
ch = logging.StreamHandler()
ch.setFormatter(logging.Formatter('[%(levelname)s] %(asctime)s - %(message)s', '%H:%M:%S'))
logger.addHandler(ch)

class RAGGenerator:
    def __init__(self, target_dir: str):
        self.target_dir = Path(target_dir)
        
        config = configparser.ConfigParser()
        config.read('setting.conf')
        self.model_id = config['DEFAULT'].get('MODEL_ID', 'gemma-4-31b-it')
        # Chat completions 엔드포인트 사용
        self.api_url = config['DEFAULT'].get('API_URL', 'http://hai-server:8000/v1/chat/completions')

        self.req_timeout = 600
        self.max_char_limit = 160000 
        
        self.top_k = 5

        logger.info("Kiwi 형태소 분석기 초기화")
        self.kiwi = Kiwi()

    def _tokenize(self, text: str) -> List[str]:
        if not text.strip():
            return []
        # 형태소 분석 후 조사(J) 및 기호(S) 제외
        tokens = self.kiwi.tokenize(text)
        return [t.form for t in tokens if not t.tag.startswith('J') and not t.tag.startswith('S')]

    def _retrieve_bm25(self, query: str, file_paths: List[str],
                       catalog: Dict = None, params: Dict = None) -> List[Dict[str, Any]]:
        docs = []
        valid_paths = []

        for path in file_paths:
            fpath = self.target_dir / path
            if fpath.exists():
                content = fpath.read_text(encoding='utf-8')
                if not content.strip():
                    continue
                docs.append(content)
                valid_paths.append(path)
            else:
                logger.warning(f"File not found: {path}")

        if not docs:
            return []

        logger.info(f"{len(docs)}개 문서 BM25 스코어 계산")
        tokenized_docs = [self._tokenize(doc) for doc in docs]
        bm25 = BM25Okapi(tokenized_docs)

        tokenized_query = self._tokenize(query)
        scores = bm25.get_scores(tokenized_query)

        # 날짜 매칭 문서 스코어 부스트
        if catalog and params:
            target_years = set(params.get("years") or [])
            target_months = set(params.get("months") or [])
            if target_years or target_months:
                for i, path in enumerate(valid_paths):
                    meta = catalog.get(path, {})
                    for d in meta.get("dates", []):
                        y, m = d.get("year"), d.get("month")
                        if (target_years and y in target_years) or \
                           (target_months and m in target_months):
                            scores[i] *= 1.3
                            break

        # desc 정렬
        top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:self.top_k]

        results = []
        for idx in top_indices:
            if scores[idx] > 0.0:
                results.append({
                    "file_path": valid_paths[idx],
                    "score": round(float(scores[idx]), 4)
                })

        logger.info(f"BM25 검색 완료. {len(results)}개 선택됨")
        return results

    def _load_context(self, target_files: List[Dict], catalog: Dict = None) -> str:
        context_blocks = []
        current_len = 0

        for item in target_files:
            file_path = item["file_path"]
            fpath = self.target_dir / file_path

            if not fpath.exists():
                continue

            text = fpath.read_text(encoding='utf-8')

            # 시계열 인식을 위한 날짜 메타 주입
            date_str = ""
            if catalog:
                meta = catalog.get(file_path, {})
                dates = meta.get("dates", [])
                valid = [f"{d['year']}-{d['month']:02d}" for d in dates
                         if d.get("year") and d.get("month")
                         and 1990 <= d["year"] <= 2030 and 1 <= d["month"] <= 12]
                if valid:
                    date_str = f" | Dates: {', '.join(valid)}"

            block = f"--- [Doc: {file_path}{date_str}] ---\n{text}\n\n"
            
            if current_len + len(block) > self.max_char_limit:
                allowed_len = self.max_char_limit - current_len
                if allowed_len > 100:
                    context_blocks.append(block[:allowed_len] + "\n...[Truncated]...")
                logger.warning(f"Context 제한 초과 ({self.max_char_limit} chars). Truncating.")
                break
            
            context_blocks.append(block)
            current_len += len(block)
                
        return "".join(context_blocks)

    def generate_stream(self, query: str, target_files: Union[List[str], List[Dict]],
                        search_query: str = None, catalog: Dict = None,
                        params: Dict = None,
                        chat_history: List[Dict] = None) -> Generator[Dict[str, Any], None, None]:
        if not target_files:
            yield {"answer": "조건에 부합하는 문서가 없어 답변할 수 없습니다.", "references": []}
            return

        bm25_targets = target_files
        if isinstance(target_files[0], str):
            bm25_query = search_query if search_query else query
            bm25_targets = self._retrieve_bm25(bm25_query, target_files,
                                                catalog=catalog, params=params)

        if not bm25_targets:
            yield {"answer": "본문 내에 일치하는 내용이 존재하지 않습니다.", "references": []}
            return

        context = self._load_context(bm25_targets, catalog=catalog)
        logger.info(f"Context 로드 완료. Length: {len(context)} chars")

        history_block = ""
        if chat_history:
            recent = chat_history[-10:]
            lines = []
            for msg in recent:
                role_label = "사용자" if msg["role"] == "user" else "시스템"
                lines.append(f"{role_label}: {msg['content']}")
            history_block = "\n[이전 대화]\n" + "\n".join(lines) + "\n"

        prompt = f"""다음 제공된 [Context] 문서들만 참고해서 [Query]에 대한 답변 작성.
Context에 없는 내용은 지어내지 말고, "해당 내용은 문서에서 확인할 수 없습니다"라고 할 것.
이전 대화가 있으면 맥락을 이어서 답변할 것. 사용자가 "다른건?", "더 없어?" 등 후속 질문을 하면 이전 대화 맥락을 참고할 것.
설명은 간결하고 핵심만.

[Context]
{context}
{history_block}
[Query]
{query}"""

        # vLLM (OpenAI 호환) 페이로드 구성
        payload = {
            "model": self.model_id,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
            "temperature": 0.6
        }

        logger.info("스트리밍 추론 요청")
        start_t = time.time()
        first_token_received = False

        try:
            with requests.post(self.api_url, json=payload, stream=True, timeout=self.req_timeout) as res:
                res.raise_for_status()
                
                full_answer = ""
                for line in res.iter_lines():
                    if line:
                        decoded = line.decode('utf-8').strip()
                        
                        if decoded.startswith("data:"):
                            data_str = decoded[5:].strip()
                            
                            if data_str == "[DONE]":
                                break
                            if not data_str:
                                continue
                                
                            try:
                                chunk = json.loads(data_str)
                                content = chunk['choices'][0]['delta'].get('content', '')
                                
                                if content:
                                    if not first_token_received:
                                        ttft = time.time() - start_t
                                        logger.info(f"TTFT: {ttft:.2f}s")
                                        first_token_received = True

                                    full_answer += content
                                    yield {
                                        "answer": full_answer,
                                        "references": bm25_targets
                                    }
                            except json.JSONDecodeError:
                                logger.debug(f"JSON Parse err: {data_str}")
                                continue
                            except KeyError:
                                continue
                                
        except requests.exceptions.RequestException as e:
            logger.error(f"API Error: {e}")
            yield {
                "answer": f"답변 생성 중 에러 발생: {e}",
                "references": bm25_targets
            }