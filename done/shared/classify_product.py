from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import requests

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from work_paths import shared_data_dir, work_root_from


OLLAMA_BASE_URL = "http://localhost:11434/api"
EMBED_MODEL = "qwen3-embedding:0.6b"
CHAT_MODEL = "qwen3:4b"
# 全部分類一次 embed 會超過逾時；改為分批請求
EMBED_BATCH_SIZE = 128
EMBED_REQUEST_TIMEOUT = 300.0
OLLAMA_KEEP_ALIVE = "30m"
OLLAMA_READY_TIMEOUT = 90.0

# 通路：coupang / momo（之後可擴充）。可改環境變數 CLASSIFY_CHANNEL。
_CLASSIFY_CHANNEL = (
    os.environ.get("CLASSIFY_CHANNEL") or "coupang"
).strip() or "coupang"

_WORK_ROOT = work_root_from(Path(__file__))
_SHARED_DATA = shared_data_dir(_WORK_ROOT)

# 可覆寫完整路徑；否則 <work>/shared/data/<channel>/display_categories_TW.json
_env_cat = (os.environ.get("CLASSIFY_CATEGORIES_JSON") or "").strip()
if _env_cat:
    _DEFAULT_CATEGORIES_JSON = Path(_env_cat).expanduser().resolve()
else:
    _DEFAULT_CATEGORIES_JSON = (
        _SHARED_DATA / _CLASSIFY_CHANNEL / "display_categories_TW.json"
    )

_CACHE_DIR = _SHARED_DATA / _CLASSIFY_CHANNEL / "classify_cache"
_CATEGORIES_CACHE_JSON = _CACHE_DIR / "categories.json"
_CATEGORY_EMBEDDINGS_NPY = _CACHE_DIR / "category_embeddings.npy"
_RAG_MEMORY_INDEX_JSON = _CACHE_DIR / "rag_result_memory_index.json"
_RAG_MEMORY_LOG_JSONL = _CACHE_DIR / "rag_result_memory_log.jsonl"

_RAG_MEMORY_READY = False
_RAG_MEMORY_INDEX: dict[str, dict[str, Any]] = {}


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)


def _norm_text(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


def _categories_signature(categories: list[dict[str, Any]]) -> str:
    payload = json.dumps(categories, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _memory_key(
    product_text: str,
    categories_signature: str,
    embed_model: str,
    chat_model: str,
) -> str:
    raw = f"{_norm_text(product_text)}\n{categories_signature}\n{embed_model}\n{chat_model}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _load_rag_memory() -> None:
    global _RAG_MEMORY_READY, _RAG_MEMORY_INDEX
    if _RAG_MEMORY_READY:
        return
    if _RAG_MEMORY_INDEX_JSON.exists():
        try:
            data = json.loads(_RAG_MEMORY_INDEX_JSON.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                _RAG_MEMORY_INDEX = data
        except json.JSONDecodeError:
            _RAG_MEMORY_INDEX = {}
    _RAG_MEMORY_READY = True


def _save_rag_memory() -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _RAG_MEMORY_INDEX_JSON.write_text(
        json.dumps(_RAG_MEMORY_INDEX, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _persist_rag_result(
    *,
    key: str,
    categories_signature: str,
    product: dict[str, Any],
    product_text: str,
    result: dict[str, Any],
    client: "OllamaClassifier",
) -> None:
    _load_rag_memory()
    ts = time.time()
    rec = {
        "ts": ts,
        "key": key,
        "channel": _CLASSIFY_CHANNEL,
        "categories_signature": categories_signature,
        "embed_model": client.embed_model,
        "chat_model": client.chat_model,
        "product": dict(product),
        "product_text": product_text,
        "final_result": dict(result.get("final_result") or {}),
        "top_k_candidates": list(result.get("top_k_candidates") or [])[:10],
    }
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with _RAG_MEMORY_LOG_JSONL.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    _RAG_MEMORY_INDEX[key] = {
        "ts": ts,
        "categories_signature": categories_signature,
        "product_text": product_text,
        "final_result": rec["final_result"],
        "top_k_candidates": rec["top_k_candidates"],
    }
    _save_rag_memory()


class OllamaClassifier:
    def __init__(
        self,
        ollama_base_url: str = OLLAMA_BASE_URL,
        embed_model: str = EMBED_MODEL,
        chat_model: str = CHAT_MODEL,
        keep_alive: str = OLLAMA_KEEP_ALIVE,
    ) -> None:
        self.ollama_base_url = ollama_base_url.rstrip("/")
        self.embed_model = embed_model
        self.chat_model = chat_model
        self.keep_alive = keep_alive
        self.auto_start_ollama = (os.environ.get("AUTO_START_OLLAMA", "1").strip() != "0")
        self.ollama_ready_timeout = float(
            os.environ.get("OLLAMA_READY_TIMEOUT", str(OLLAMA_READY_TIMEOUT)).strip()
            or OLLAMA_READY_TIMEOUT
        )
        self._ollama_start_attempted = False

    def _tags_url(self) -> str:
        if self.ollama_base_url.endswith("/api"):
            return f"{self.ollama_base_url}/tags"
        return f"{self.ollama_base_url}/api/tags"

    def ensure_ollama_ready(self) -> None:
        tags_url = self._tags_url()

        def _is_ready() -> bool:
            try:
                r = requests.get(tags_url, timeout=2.0)
                return r.status_code == 200
            except requests.RequestException:
                return False

        if _is_ready():
            return

        if self.auto_start_ollama and not self._ollama_start_attempted:
            self._ollama_start_attempted = True
            ollama_bin = shutil.which("ollama")
            if ollama_bin:
                kwargs: dict[str, Any] = {
                    "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.DEVNULL,
                }
                if sys.platform.startswith("win"):
                    kwargs["creationflags"] = (
                        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                        | getattr(subprocess, "DETACHED_PROCESS", 0)
                    )
                subprocess.Popen([ollama_bin, "serve"], **kwargs)

        deadline = time.time() + max(1.0, self.ollama_ready_timeout)
        while time.time() < deadline:
            if _is_ready():
                return
            time.sleep(1.0)

        raise RuntimeError(
            "Ollama is not ready. Please start it manually with `ollama serve` "
            "or increase OLLAMA_READY_TIMEOUT."
        )

    def embed_texts(
        self,
        texts: list[str],
        *,
        batch_size: int = EMBED_BATCH_SIZE,
        timeout: float = EMBED_REQUEST_TIMEOUT,
    ) -> list[np.ndarray]:
        self.ensure_ollama_ready()
        if not texts:
            return []
        out: list[np.ndarray] = []
        for start in range(0, len(texts), batch_size):
            chunk = texts[start : start + batch_size]
            resp = requests.post(
                f"{self.ollama_base_url}/embed",
                json={
                    "model": self.embed_model,
                    "input": chunk,
                    "keep_alive": self.keep_alive,
                },
                timeout=timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            embeddings = data.get("embeddings", [])
            if len(embeddings) != len(chunk):
                raise RuntimeError(
                    f"embed 回傳筆數不符：預期 {len(chunk)}，實際 {len(embeddings)}"
                )
            out.extend(np.array(vec, dtype=np.float32) for vec in embeddings)
        return out

    def chat_json(
        self, system_prompt: str, user_prompt: str, schema: dict[str, Any]
    ) -> dict[str, Any]:
        self.ensure_ollama_ready()
        del schema  # 保留參數供之後恢復 JSON mode；目前不送 format
        resp = requests.post(
            f"{self.ollama_base_url}/chat",
            json={
                "model": self.chat_model,
                "stream": False,
                "think": False,
                "keep_alive": self.keep_alive,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            },
            timeout=180,
        )
        resp.raise_for_status()
        data = resp.json()

        content = data["message"]["content"]
        return {"raw_output": content}


def _last_regex_match(pattern: str, text: str) -> re.Match[str] | None:
    matches = list(re.finditer(pattern, text))
    return matches[-1] if matches else None


def parse_llm_result(raw_output: str) -> dict[str, Any]:
    text = raw_output.strip()
    text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.DOTALL).strip()

    # 模型常先複述 prompt 裡的「category_code: 分類代碼」範例，最終答案多在文末，取最後一次匹配
    code_match = _last_regex_match(r"category_code\s*:\s*(.+)", text)
    path_match = _last_regex_match(r"category_path\s*:\s*(.+)", text)
    reason_match = _last_regex_match(r"reason\s*:\s*(.+)", text)
    conf_match = _last_regex_match(r"confidence\s*:\s*([0-9.]+)", text)

    return {
        "category_code": code_match.group(1).strip() if code_match else "",
        "category_path": path_match.group(1).strip() if path_match else "",
        "reason": reason_match.group(1).strip() if reason_match else "",
        "confidence": float(conf_match.group(1)) if conf_match else 0.0,
    }


def build_product_text(product: dict[str, Any]) -> str:
    fields = [
        ("商品名稱", product.get("sale_product_name", "")),
        ("ERP商品名", product.get("erp_product_name", "")),
        ("品牌", product.get("brand", "")),
        ("標語", product.get("slogan", "")),
        ("特色", product.get("feature", "")),
        ("商品描述", product.get("product_description", "")),
        ("型號", product.get("model_no", "")),
        (
            "規格1",
            f"{product.get('spec_name_1', '')} = {product.get('spec_value_1', '')}",
        ),
        (
            "規格2",
            f"{product.get('spec_name_2', '')} = {product.get('spec_value_2', '')}",
        ),
    ]

    lines = []
    for key, value in fields:
        text = str(value).strip()
        if text and text != "=":
            lines.append(f"{key}：{text}")
    return "\n".join(lines)


def build_category_text(category: dict[str, Any]) -> str:
    code = category.get("code", "")
    path = category.get("path", "")
    desc = category.get("description", "")
    parts = [
        f"分類代碼：{code}",
        f"分類路徑：{path}",
    ]
    if str(desc).strip():
        parts.append(f"分類說明：{desc}")
    return "\n".join(parts)


def retrieve_top_k_categories(
    client: OllamaClassifier,
    product_text: str,
    categories: list[dict[str, Any]],
    category_embeddings: np.ndarray,
    top_k: int = 10,
) -> list[dict[str, Any]]:
    product_emb = client.embed_texts([product_text])[0]

    scored: list[dict[str, Any]] = []
    for category, emb in zip(categories, category_embeddings):
        path = str(category.get("path", ""))
        if "吹風機" in product_text and "吹風機" not in path:
            continue

        score = cosine_similarity(product_emb, emb)
        item = dict(category)
        item["retrieval_score"] = score
        scored.append(item)

    scored.sort(key=lambda x: x["retrieval_score"], reverse=True)
    return scored[:top_k]


def rerank_with_llm(
    client: OllamaClassifier,
    product_text: str,
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    candidate_lines = []
    for idx, c in enumerate(candidates, start=1):
        candidate_lines.append(
            "\n".join(
                [
                    f"{idx}. code={c.get('code', '')}",
                    f"   path={c.get('path', '')}",
                    f"   description={c.get('description', '')}",
                    f"   retrieval_score={c.get('retrieval_score', 0):.4f}",
                ]
            )
        )

    system_prompt = """
你是商品分類助手。
請根據商品資訊，從候選分類中選出最適合的一個。

只能從候選分類中選，不可發明新分類。
請直接回答這個格式：

category_code: 分類代碼
category_path: 分類路徑
reason: 原因
confidence: 0到1之間的小數
""".strip()

    user_prompt = f"""
商品資訊：
{product_text}

候選分類：
{chr(10).join(candidate_lines)}
""".strip()

    schema: dict[str, Any] = {}
    result = client.chat_json(system_prompt, user_prompt, schema)
    return parse_llm_result(result["raw_output"])


def classify_product(
    client: OllamaClassifier,
    product: dict[str, Any],
    categories: list[dict[str, Any]],
    category_embeddings: np.ndarray,
    top_k: int = 10,
    *,
    use_result_memory: bool = True,
    reuse_low_confidence_memory: bool = False,
    min_memory_confidence: float = 0.55,
) -> dict[str, Any]:
    product_text = build_product_text(product)
    cat_sig = _categories_signature(categories)
    key = _memory_key(product_text, cat_sig, client.embed_model, client.chat_model)

    if use_result_memory:
        _load_rag_memory()
        cached = _RAG_MEMORY_INDEX.get(key)
        if isinstance(cached, dict):
            cached_final = dict(cached.get("final_result") or {})
            cached_conf = float(cached_final.get("confidence", 0.0) or 0.0)
            if reuse_low_confidence_memory or cached_conf >= min_memory_confidence:
                return {
                    "product_text": str(cached.get("product_text", product_text)),
                    "top_k_candidates": list(cached.get("top_k_candidates") or []),
                    "final_result": cached_final,
                    "memory_hit": True,
                    "memory_key": key,
                }

    candidates = retrieve_top_k_categories(
        client, product_text, categories, category_embeddings, top_k=top_k
    )
    final_result = rerank_with_llm(client, product_text, candidates)
    out = {
        "product_text": product_text,
        "top_k_candidates": candidates,
        "final_result": final_result,
        "memory_hit": False,
        "memory_key": key,
    }
    if use_result_memory:
        _persist_rag_result(
            key=key,
            categories_signature=cat_sig,
            product=product,
            product_text=product_text,
            result=out,
            client=client,
        )
    return out


def _flatten_display_category_tree(
    node: dict[str, Any],
    path_parts: list[str],
) -> list[dict[str, Any]]:
    """將 Coupang display categories API 的巢狀 child 樹展平成葉節點列表。"""
    name = str(node.get("name", "")).strip()
    code = node.get("displayItemCategoryCode")
    children = node.get("child")
    if not isinstance(children, list):
        children = []

    new_parts = path_parts
    if name and name != "ROOT":
        new_parts = path_parts + [name]

    if not children:
        path_str = " > ".join(new_parts)
        code_str = str(code) if code is not None else ""
        if code_str or path_str:
            return [{"code": code_str, "path": path_str, "description": ""}]
        return []

    out: list[dict[str, Any]] = []
    for ch in children:
        if isinstance(ch, dict):
            out.extend(_flatten_display_category_tree(ch, new_parts))
    return out


def load_categories_from_json(path: str | Path) -> list[dict[str, Any]]:
    raw: Any = json.loads(Path(path).read_text(encoding="utf-8"))

    if isinstance(raw, dict):
        api_data = raw.get("data")
        if isinstance(api_data, dict) and (
            "displayItemCategoryCode" in api_data or "child" in api_data
        ):
            categories = _flatten_display_category_tree(api_data, [])
            if categories:
                return categories

    data = raw
    # 你可以依自己的 JSON 結構調整這段
    # 預設支援：
    # 1. [{"code":"123","path":"A > B","description":"..."}]
    # 2. {"categories":[...]}
    if isinstance(data, dict) and "categories" in data:
        data = data["categories"]

    if not isinstance(data, list):
        raise ValueError("分類 JSON 格式錯誤，應為 list 或包含 categories 的 dict。")

    categories: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue

        code = str(
            item.get("code") or item.get("category_code") or item.get("id") or ""
        ).strip()

        path = str(
            item.get("path")
            or item.get("full_path")
            or item.get("category_path")
            or item.get("label")
            or ""
        ).strip()

        description = str(item.get("description") or "").strip()

        if code or path:
            categories.append(
                {
                    "code": code,
                    "path": path,
                    "description": description,
                }
            )

    if not categories:
        raise ValueError("分類 JSON 讀不到有效資料。")

    return categories


def build_category_index(
    client: OllamaClassifier,
    categories: list[dict[str, Any]],
) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)

    category_texts = [build_category_text(c) for c in categories]
    category_embs = client.embed_texts(category_texts)
    matrix = np.vstack(category_embs).astype(np.float32)

    _CATEGORIES_CACHE_JSON.write_text(
        json.dumps(categories, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    np.save(_CATEGORY_EMBEDDINGS_NPY, matrix)


def load_category_index() -> tuple[list[dict[str, Any]], np.ndarray]:
    categories = json.loads(_CATEGORIES_CACHE_JSON.read_text(encoding="utf-8"))
    embeddings = np.load(_CATEGORY_EMBEDDINGS_NPY).astype(np.float32)

    if len(categories) != len(embeddings):
        raise ValueError("categories 與 embeddings 數量不一致。")

    return categories, embeddings


if __name__ == "__main__":
    client = OllamaClassifier()

    if not _CATEGORIES_CACHE_JSON.exists() or not _CATEGORY_EMBEDDINGS_NPY.exists():
        categories = load_categories_from_json(_DEFAULT_CATEGORIES_JSON)
        build_category_index(client, categories)

    categories, category_embeddings = load_category_index()

    product = {
        "sale_product_name": "高速BLDC負離子吹風機",
        "erp_product_name": "高速BLDC正負離子吹風機-小甜筒-兩色可選(S1)",
        "brand": "直白",
        "slogan": "高轉速速乾，輕量護髮",
        "feature": "負離子、低噪音、家用、兩段風速、輕量",
        "product_description": "適合一般家庭日常吹髮使用，主打快速吹乾與低噪音體驗。",
        "model_no": "S1",
        "spec_name_1": "顏色",
        "spec_value_1": "白色",
        "spec_name_2": "用途",
        "spec_value_2": "家庭用",
    }

    result = classify_product(
        client,
        product,
        categories,
        category_embeddings,
        top_k=10,
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))
