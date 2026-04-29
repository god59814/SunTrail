"""
Coupang 上架用的 LLM 分類服務層（對齊 momo/momotest/llm_category_service 設計）。

- 封裝索引初始化（含分類 JSON 變更時重建）
- `predict_category`：混合檢索分數與 LLM 信心、needs_review 標記

主流程可直接用 `classify_product`；若要與 momo 相同的決策欄位，請用 `predict_category`。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ollama_test.classify_product import (
    OllamaClassifier,
    _CACHE_DIR,
    _CACHE_META_JSON,
    _CATEGORIES_CACHE_JSON,
    _CATEGORY_EMBEDDINGS_NPY,
    build_category_index,
    cache_needs_rebuild,
    classify_product,
    load_categories_from_json,
    load_category_index,
)

logger = logging.getLogger(__name__)

_CLIENT = OllamaClassifier()

_INDEX_READY = False
_CATEGORIES: list[dict[str, Any]] | None = None
_EMBEDDINGS: Any = None
_ACTIVE_CATEGORY_PATH: Path | None = None


def _clear_cache_files() -> None:
    for p in (_CATEGORIES_CACHE_JSON, _CATEGORY_EMBEDDINGS_NPY, _CACHE_META_JSON):
        try:
            if p.exists():
                p.unlink()
        except OSError as e:
            logger.warning("could not remove %s: %s", p, e)
    try:
        if _CACHE_DIR.exists() and not any(_CACHE_DIR.iterdir()):
            _CACHE_DIR.rmdir()
    except OSError:
        pass


def init_classifier(
    category_json_path: Path,
    *,
    force_rebuild: bool = False,
) -> None:
    """
    初始化分類索引。`category_json_path` 應與 mapping 的 categories_json_path 一致。
    """
    global _INDEX_READY, _CATEGORIES, _EMBEDDINGS, _ACTIVE_CATEGORY_PATH

    path = category_json_path.resolve()
    if _ACTIVE_CATEGORY_PATH is not None and _ACTIVE_CATEGORY_PATH != path:
        logger.info(
            "category json path changed (%s -> %s); resetting index state",
            _ACTIVE_CATEGORY_PATH,
            path,
        )
        _INDEX_READY = False
        _CATEGORIES = None
        _EMBEDDINGS = None

    _ACTIVE_CATEGORY_PATH = path

    if force_rebuild:
        logger.warning("force_rebuild: clearing embedding cache")
        _clear_cache_files()
        _INDEX_READY = False
        _CATEGORIES = None
        _EMBEDDINGS = None

    if _INDEX_READY and _CATEGORIES is not None and _EMBEDDINGS is not None:
        return

    if not path.is_file():
        raise FileNotFoundError(f"category json not found: {path}")

    if cache_needs_rebuild(path) or force_rebuild:
        logger.info("building category embedding index...")
        categories = load_categories_from_json(path)
        build_category_index(_CLIENT, categories, category_source_path=path)

    _CATEGORIES, _EMBEDDINGS = load_category_index()
    _INDEX_READY = True
    logger.info("category index ready: %s leaves", len(_CATEGORIES))


def _blend_final_confidence(
    model_confidence: float,
    top1_retrieval: float,
    gap_12: float,
) -> float:
    """混合 LLM 信心與檢索分數（僅供決策參考，非機率校準）。"""
    norm_ret = min(1.0, max(0.0, top1_retrieval) / 0.35) if top1_retrieval > 0 else 0.0
    norm_ret = min(1.0, norm_ret)
    norm_gap = min(1.0, max(0.0, gap_12) * 50.0)
    return float(
        0.50 * model_confidence
        + 0.35 * norm_ret
        + 0.15 * norm_gap
    )


def predict_category(
    product: dict[str, Any],
    *,
    category_json_path: Path,
    top_k: int = 10,
    min_confidence: float = 0.55,
    min_retrieval_score: float = 0.12,
    min_retrieval_gap: float = 0.005,
    fallback_code: str = "",
    fallback_path: str = "",
    apply_prefilter: bool = True,
    force_rebuild: bool = False,
) -> dict[str, Any]:
    """
    回傳結構與 `classify_product` 相同，但 `final_result` 額外含：
    model_confidence、final_confidence、used_fallback、needs_review、review_reasons 等。
    """
    init_classifier(category_json_path, force_rebuild=force_rebuild)

    assert _CATEGORIES is not None and _EMBEDDINGS is not None

    result = classify_product(
        client=_CLIENT,
        product=product,
        categories=_CATEGORIES,
        category_embeddings=_EMBEDDINGS,
        top_k=top_k,
        apply_prefilter=apply_prefilter,
    )

    rm = result.get("retrieval_metadata") or {}
    top1 = float(rm.get("top1_retrieval_score", 0.0) or 0.0)
    gap_12 = float(rm.get("retrieval_gap_12", 0.0) or 0.0)

    final_result: dict[str, Any] = dict(result.get("final_result") or {})
    model_conf = float(
        final_result.get("model_confidence", final_result.get("confidence", 0.0)) or 0.0
    )
    final_result["model_confidence"] = model_conf

    final_conf = _blend_final_confidence(model_conf, top1, gap_12)
    final_result["final_confidence"] = round(final_conf, 4)
    final_result["top1_retrieval_score"] = round(top1, 6)
    final_result["retrieval_gap_12"] = round(gap_12, 6)
    final_result["used_code_fallback"] = bool(
        final_result.get("used_code_fallback", False)
    )
    final_result["fallback_on_unstable_candidates"] = bool(
        final_result.get("fallback_on_unstable_candidates", False)
    )

    used_fallback = False
    fallback_reason = ""

    if model_conf < min_confidence and fallback_code:
        used_fallback = True
        fallback_reason = f"model_confidence {model_conf:.3f} < {min_confidence}"
        final_result["category_code"] = fallback_code
        final_result["category_path"] = fallback_path
        final_result["reason"] = (
            f"{final_result.get('reason', '')} [{fallback_reason}]"
        ).strip()
        final_result["final_confidence"] = min(
            float(final_result.get("final_confidence", 0.0)), model_conf
        )

    needs_review = False
    review_reasons: list[str] = []

    if final_result.get("final_confidence", 0.0) < min_confidence:
        needs_review = True
        review_reasons.append(f"final_confidence<{min_confidence}")

    if top1 < min_retrieval_score:
        needs_review = True
        review_reasons.append(f"top1_retrieval<{min_retrieval_score}")

    _GAP_REVIEW_MAX_CONFIDENCE = 0.75
    cands = result.get("top_k_candidates") or []
    if len(cands) >= 2 and gap_12 < min_retrieval_gap:
        fc_gap = float(final_result.get("final_confidence", 0.0) or 0.0)
        if fc_gap < _GAP_REVIEW_MAX_CONFIDENCE:
            needs_review = True
            review_reasons.append(
                f"retrieval_gap_12<{min_retrieval_gap} and "
                f"final_confidence<{_GAP_REVIEW_MAX_CONFIDENCE}"
            )

    if used_fallback:
        needs_review = True
        if fallback_reason:
            review_reasons.append("used_fallback")
    if final_result.get("used_code_fallback", False):
        needs_review = True
        review_reasons.append("llm_code_fallback")
    if final_result.get("fallback_on_unstable_candidates", False):
        needs_review = True
        review_reasons.append("fallback_on_unstable_candidates")

    final_result["used_fallback"] = used_fallback
    final_result["fallback_reason"] = fallback_reason if used_fallback else ""
    final_result["needs_review"] = needs_review
    final_result["review_reasons"] = review_reasons

    result["final_result"] = final_result
    return result


def preload_index(
    category_json_path: Path,
    *,
    force_rebuild: bool = False,
) -> None:
    """批次流程開始時呼叫一次即可。"""
    init_classifier(category_json_path, force_rebuild=force_rebuild)
