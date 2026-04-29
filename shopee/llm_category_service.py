from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests

_SHARED_LIB = Path(__file__).resolve().parents[1] / "done" / "shared"
if str(_SHARED_LIB) not in sys.path:
    sys.path.insert(0, str(_SHARED_LIB))

from llm_core.ollama_client import OllamaClassifier
from llm_core.parse_utils import (
    parse_llm_result_json_first,
    sanitize_reason,
)

logger = logging.getLogger(__name__)

CLASSIFIER_VERSION = "1.0"
_CACHE_DIR = Path(__file__).resolve().parent / "cache"
_CATEGORIES_CACHE_JSON = _CACHE_DIR / "shopee_categories.json"
_CATEGORY_EMBEDDINGS_NPY = _CACHE_DIR / "shopee_category_embeddings.npy"
_CACHE_META_JSON = _CACHE_DIR / "shopee_cache_meta.json"
_RAG_MEMORY_INDEX_JSON = _CACHE_DIR / "shopee_rag_memory_index.json"
_RAG_MEMORY_LOG_JSONL = _CACHE_DIR / "shopee_rag_memory_log.jsonl"

_CLIENT = OllamaClassifier()
_INDEX_READY = False
_CATEGORIES: list[dict[str, Any]] | None = None
_EMBEDDINGS: np.ndarray | None = None
_OLLAMA_START_ATTEMPTED = False
_RAG_MEMORY_READY = False
_RAG_MEMORY_INDEX: dict[str, dict[str, Any]] = {}


def _norm(s: str) -> str:
    s = str(s or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


def _ollama_tags_url() -> str:
    base = _CLIENT.ollama_base_url.rstrip("/")
    if base.endswith("/api"):
        return f"{base}/tags"
    return f"{base}/api/tags"


def ensure_ollama_ready(*, auto_start: bool = True, timeout_sec: float = 90.0) -> None:
    global _OLLAMA_START_ATTEMPTED
    tags_url = _ollama_tags_url()

    def _is_ready() -> bool:
        try:
            r = requests.get(tags_url, timeout=2.0)
            return r.status_code == 200
        except requests.RequestException:
            return False

    if _is_ready():
        return

    if auto_start and not _OLLAMA_START_ATTEMPTED:
        _OLLAMA_START_ATTEMPTED = True
        ollama_bin = shutil.which("ollama")
        if ollama_bin:
            kwargs: dict[str, Any] = {
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
            }
            if sys.platform.startswith("win"):
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            subprocess.Popen([ollama_bin, "serve"], **kwargs)

    deadline = time.time() + max(1.0, timeout_sec)
    while time.time() < deadline:
        if _is_ready():
            return
        time.sleep(1.0)

    raise RuntimeError(
        "Ollama is not ready. Please start it manually (e.g. `ollama serve`) "
        "or increase --ollama-ready-timeout."
    )


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _memory_key(product_text: str, category_xls_sha256: str) -> str:
    raw = f"{_norm(product_text)}\n{category_xls_sha256}\n{_CLIENT.embed_model}\n{_CLIENT.chat_model}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _load_rag_memory_index() -> None:
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


def _save_rag_memory_index() -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _RAG_MEMORY_INDEX_JSON.write_text(
        json.dumps(_RAG_MEMORY_INDEX, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _persist_rag_result(
    *,
    key: str,
    product: dict[str, Any],
    product_text: str,
    category_xls: Path,
    category_xls_sha256: str,
    result: dict[str, Any],
) -> None:
    _load_rag_memory_index()
    ts = time.time()
    final_result = dict(result.get("final_result") or {})
    top_k_candidates = result.get("top_k_candidates") or []
    product_snapshot = dict(product)
    log_rec = {
        "ts": ts,
        "key": key,
        "category_xls_path": str(category_xls.resolve()),
        "category_xls_sha256": category_xls_sha256,
        "embed_model": _CLIENT.embed_model,
        "chat_model": _CLIENT.chat_model,
        "product": product_snapshot,
        "product_text": product_text,
        "final_result": final_result,
        "top_k_candidates": top_k_candidates[:10],
    }
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with _RAG_MEMORY_LOG_JSONL.open("a", encoding="utf-8") as f:
        f.write(json.dumps(log_rec, ensure_ascii=False) + "\n")

    _RAG_MEMORY_INDEX[key] = {
        "ts": ts,
        "category_xls_sha256": category_xls_sha256,
        "product_text": product_text,
        "final_result": final_result,
        "top_k_candidates": top_k_candidates[:10],
    }
    _save_rag_memory_index()


def _read_categories_from_xls(category_xls: Path) -> list[dict[str, Any]]:
    try:
        df = pd.read_excel(category_xls)
    except ImportError as e:
        raise RuntimeError("Reading .xls requires xlrd. Please run: pip install xlrd") from e

    def col(name: str) -> str:
        for c in df.columns:
            if str(c).strip() == name:
                return c
        raise KeyError(f"column not found: {name}. available={list(df.columns)}")

    c1 = col("Level 1 Category")
    c2 = col("Level 2 Category")
    c3 = col("Level 3 Category")
    c4 = col("Level 4 Category")
    c5 = col("Level 5 Category")
    cid = col("Category ID")

    out: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        levels: list[str] = []
        for key in (c1, c2, c3, c4, c5):
            v = row.get(key)
            if v is None:
                continue
            if isinstance(v, float) and math.isnan(v):
                continue
            s = str(v).strip()
            if s:
                levels.append(s)

        if not levels:
            continue

        v_id = row.get(cid)
        if v_id is None or (isinstance(v_id, float) and math.isnan(v_id)):
            continue

        if isinstance(v_id, float) and float(v_id).is_integer():
            cat_id = str(int(v_id))
        else:
            cat_id = str(v_id).strip()

        out.append(
            {
                "cat_id": cat_id,
                "path": " > ".join(levels),
                "leaf": levels[-1],
            }
        )
    return out


def _build_category_text(category: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"分類代碼：{category.get('cat_id', '')}",
            f"分類路徑：{category.get('path', '')}",
            f"葉節點：{category.get('leaf', '')}",
        ]
    )


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)


def _build_product_text(product: dict[str, Any]) -> str:
    fields = [
        ("商品分類查詢", product.get("category_query", "")),
        ("商品名稱", product.get("name", "")),
        ("商品特色標語", product.get("slogan", "")),
        ("商品特色", product.get("feature", "")),
        ("商品描述", product.get("description", "")),
        ("品牌", product.get("brand", "")),
        ("ERP品號", product.get("erp_sku", "")),
        ("規格1", product.get("spec_1", "")),
        ("規格2", product.get("spec_2", "")),
    ]
    lines: list[str] = []
    for k, v in fields:
        t = str(v).strip()
        if t:
            lines.append(f"{k}：{t}")
    return "\n".join(lines)


def _write_cache_meta(category_xls: Path) -> None:
    meta = {
        "classifier_version": CLASSIFIER_VERSION,
        "embed_model": _CLIENT.embed_model,
        "chat_model": _CLIENT.chat_model,
        "category_xls_path": str(category_xls.resolve()),
        "category_xls_sha256": _file_sha256(category_xls),
    }
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _CACHE_META_JSON.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def _cache_needs_rebuild(category_xls: Path) -> bool:
    if not _CATEGORIES_CACHE_JSON.exists() or not _CATEGORY_EMBEDDINGS_NPY.exists():
        return True
    if not _CACHE_META_JSON.exists():
        return True
    try:
        meta = json.loads(_CACHE_META_JSON.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return True
    if meta.get("classifier_version") != CLASSIFIER_VERSION:
        return True
    if meta.get("embed_model") != _CLIENT.embed_model or meta.get("chat_model") != _CLIENT.chat_model:
        return True
    if meta.get("category_xls_sha256") != _file_sha256(category_xls):
        return True
    return False


def _build_category_index(category_xls: Path) -> None:
    categories = _read_categories_from_xls(category_xls)
    texts = [_build_category_text(c) for c in categories]
    embs = _CLIENT.embed_texts(texts)
    matrix = np.vstack(embs).astype(np.float32)
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _CATEGORIES_CACHE_JSON.write_text(json.dumps(categories, ensure_ascii=False, indent=2), encoding="utf-8")
    np.save(_CATEGORY_EMBEDDINGS_NPY, matrix)
    _write_cache_meta(category_xls)


def _load_category_index() -> tuple[list[dict[str, Any]], np.ndarray]:
    categories = json.loads(_CATEGORIES_CACHE_JSON.read_text(encoding="utf-8"))
    embeddings = np.load(_CATEGORY_EMBEDDINGS_NPY).astype(np.float32)
    if len(categories) != len(embeddings):
        raise ValueError("categories and embeddings count mismatch")
    return categories, embeddings


def init_classifier(category_xls: Path, *, force_rebuild: bool = False) -> None:
    global _INDEX_READY, _CATEGORIES, _EMBEDDINGS
    if force_rebuild or _cache_needs_rebuild(category_xls):
        logger.info("building shopee category embedding index...")
        _build_category_index(category_xls)
    if _INDEX_READY and _CATEGORIES is not None and _EMBEDDINGS is not None:
        return
    _CATEGORIES, _EMBEDDINGS = _load_category_index()
    _INDEX_READY = True


def _retrieve_top_k(product_text: str, top_k: int) -> list[dict[str, Any]]:
    assert _CATEGORIES is not None and _EMBEDDINGS is not None
    product_emb = _CLIENT.embed_texts([product_text])[0]
    scored: list[dict[str, Any]] = []
    for c, emb in zip(_CATEGORIES, _EMBEDDINGS):
        item = dict(c)
        item["retrieval_score"] = _cosine_similarity(product_emb, emb)
        scored.append(item)
    scored.sort(key=lambda x: x["retrieval_score"], reverse=True)
    return scored[:top_k]


def _rerank_with_llm(product_text: str, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    candidate_lines: list[str] = []
    allowed_codes: list[str] = []
    for i, c in enumerate(candidates, start=1):
        code = str(c.get("cat_id", "")).strip()
        if code:
            allowed_codes.append(code)
        candidate_lines.append(
            "\n".join(
                [
                    f"{i}. cat_id={code}",
                    f"   path={c.get('path', '')}",
                    f"   leaf={c.get('leaf', '')}",
                    f"   retrieval_score={float(c.get('retrieval_score', 0.0) or 0.0):.4f}",
                ]
            )
        )

    system_prompt = """
你是 Shopee 商品分類助手。請根據商品資訊，從候選分類中選最適合的一筆。
cat_id 必須完全等於候選清單中的某一筆，不可發明，不可輸出占位符。

你只能輸出一個 JSON 物件（不要 markdown）：
{
  "category_code": "<候選中的 cat_id>",
  "category_path": "候選中的 path",
  "reason": "<20字內理由>",
  "confidence": 0.0
}
若不確定，仍必須從候選中選最接近的一筆。confidence 範圍是 0 到 1。
""".strip()

    user_prompt = f"""
允許的 category_code（必須擇一）：{", ".join(allowed_codes)}

商品資訊：
{product_text}

候選分類：
{chr(10).join(candidate_lines)}
""".strip()

    raw = _CLIENT.chat_json(system_prompt, user_prompt, schema={})["raw_output"]
    parsed, parse_mode = parse_llm_result_json_first(raw)
    parsed["parse_mode"] = parse_mode
    parsed["raw_output"] = raw

    if parsed.get("category_code") not in allowed_codes and candidates:
        parsed["category_code"] = str(candidates[0].get("cat_id", ""))
        parsed["category_path"] = str(candidates[0].get("path", ""))
        parsed["reason"] = sanitize_reason(
            str(parsed.get("reason", "") or ""),
            "fallback: llm code not in candidates",
        )
        parsed["used_code_fallback"] = True
    else:
        parsed["reason"] = sanitize_reason(str(parsed.get("reason", "") or ""))
        parsed["used_code_fallback"] = False
    return parsed


def _blend_final_confidence(model_confidence: float, top1_retrieval: float, gap_12: float) -> float:
    norm_ret = min(1.0, max(0.0, top1_retrieval) / 0.35) if top1_retrieval > 0 else 0.0
    norm_gap = min(1.0, max(0.0, gap_12) * 50.0)
    return float(0.50 * model_confidence + 0.35 * norm_ret + 0.15 * norm_gap)


def predict_category(
    product: dict[str, Any],
    *,
    category_xls: Path,
    top_k: int = 10,
    min_confidence: float = 0.55,
    min_retrieval_score: float = 0.12,
    min_retrieval_gap: float = 0.005,
    force_rebuild: bool = False,
    auto_start_ollama: bool = True,
    ollama_ready_timeout_sec: float = 90.0,
    use_result_memory: bool = True,
    reuse_needs_review_memory: bool = False,
) -> dict[str, Any]:
    ensure_ollama_ready(auto_start=auto_start_ollama, timeout_sec=ollama_ready_timeout_sec)
    init_classifier(category_xls, force_rebuild=force_rebuild)
    product_text = _build_product_text(product)
    category_sha = _file_sha256(category_xls)
    key = _memory_key(product_text, category_sha)

    if use_result_memory:
        _load_rag_memory_index()
        cached = _RAG_MEMORY_INDEX.get(key)
        if isinstance(cached, dict):
            cached_final = dict(cached.get("final_result") or {})
            cached_needs_review = bool(cached_final.get("needs_review", False))
            if reuse_needs_review_memory or not cached_needs_review:
                return {
                    "product_text": str(cached.get("product_text", product_text)),
                    "top_k_candidates": list(cached.get("top_k_candidates") or []),
                    "final_result": cached_final,
                    "memory_hit": True,
                    "memory_key": key,
                }

    candidates = _retrieve_top_k(product_text, top_k=top_k)
    final_result = _rerank_with_llm(product_text, candidates)

    top1 = float(candidates[0].get("retrieval_score", 0.0) or 0.0) if candidates else 0.0
    top2 = float(candidates[1].get("retrieval_score", 0.0) or 0.0) if len(candidates) > 1 else 0.0
    gap_12 = top1 - top2

    model_conf = float(final_result.get("confidence", 0.0) or 0.0)
    final_conf = _blend_final_confidence(model_conf, top1, gap_12)

    needs_review = False
    review_reasons: list[str] = []

    if final_conf < min_confidence:
        needs_review = True
        review_reasons.append(f"final_confidence<{min_confidence}")
    if top1 < min_retrieval_score:
        needs_review = True
        review_reasons.append(f"top1_retrieval<{min_retrieval_score}")
    if len(candidates) >= 2 and gap_12 < min_retrieval_gap and final_conf < 0.75:
        needs_review = True
        review_reasons.append(f"retrieval_gap_12<{min_retrieval_gap}")
    if bool(final_result.get("used_code_fallback", False)):
        needs_review = True
        review_reasons.append("llm_code_fallback")

    final_result["model_confidence"] = model_conf
    final_result["final_confidence"] = round(final_conf, 4)
    final_result["top1_retrieval_score"] = round(top1, 6)
    final_result["retrieval_gap_12"] = round(gap_12, 6)
    final_result["needs_review"] = needs_review
    final_result["review_reasons"] = review_reasons

    result = {
        "product_text": product_text,
        "top_k_candidates": candidates,
        "final_result": final_result,
        "memory_hit": False,
        "memory_key": key,
    }
    if use_result_memory:
        _persist_rag_result(
            key=key,
            product=product,
            product_text=product_text,
            category_xls=category_xls,
            category_xls_sha256=category_sha,
            result=result,
        )
    return result


def preload_index(
    category_xls: Path,
    *,
    force_rebuild: bool = False,
    auto_start_ollama: bool = True,
    ollama_ready_timeout_sec: float = 90.0,
) -> None:
    ensure_ollama_ready(auto_start=auto_start_ollama, timeout_sec=ollama_ready_timeout_sec)
    init_classifier(category_xls, force_rebuild=force_rebuild)
