from __future__ import annotations

import logging
import importlib.util
import sys
from pathlib import Path
from typing import Any

_LOCAL_CLASSIFIER_PATH = Path(__file__).resolve().parent / "classify_product.py"
_SPEC = importlib.util.spec_from_file_location(
    "_momo_llm_classify_product_local", _LOCAL_CLASSIFIER_PATH
)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError(f"Cannot load local classify_product module: {_LOCAL_CLASSIFIER_PATH}")
_MOD = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MOD
_SPEC.loader.exec_module(_MOD)

OllamaClassifier = _MOD.OllamaClassifier
build_category_index = _MOD.build_category_index
cache_needs_rebuild = _MOD.cache_needs_rebuild
classify_product = _MOD.classify_product
load_categories_from_json = _MOD.load_categories_from_json
load_category_index = _MOD.load_category_index

logger = logging.getLogger(__name__)

_CLIENT = OllamaClassifier()
_CATEGORY_JSON_PATH = Path(__file__).resolve().parent / "category.json"
_CACHE_DIR = Path(__file__).resolve().parent / "cache"
_CACHE_JSON = _CACHE_DIR / "categories.json"
_CACHE_NPY = _CACHE_DIR / "category_embeddings.npy"

# 全域快取：批次跑多筆時只載入一次
_INDEX_READY = False
_CATEGORIES: list[dict[str, Any]] | None = None
_EMBEDDINGS: Any = None


def _clear_cache_files() -> None:
    for p in (_CACHE_JSON, _CACHE_NPY, _CACHE_DIR / "cache_meta.json"):
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


def init_classifier(*, force_rebuild: bool = False) -> None:
    """
    初始化分類索引：category.json 變更或 cache 過期時自動重建。
    """
    global _INDEX_READY, _CATEGORIES, _EMBEDDINGS

    if force_rebuild:
        logger.warning("force_rebuild: clearing embedding cache")
        _clear_cache_files()
        _INDEX_READY = False
        _CATEGORIES = None
        _EMBEDDINGS = None

    if _INDEX_READY and _CATEGORIES is not None and _EMBEDDINGS is not None:
        return

    if not _CATEGORY_JSON_PATH.is_file():
        raise FileNotFoundError(f"category.json not found: {_CATEGORY_JSON_PATH}")

    if cache_needs_rebuild(_CATEGORY_JSON_PATH) or force_rebuild:
        logger.info("building category embedding index...")
        categories = load_categories_from_json(_CATEGORY_JSON_PATH)
        build_category_index(
            _CLIENT,
            categories,
            category_source_path=_CATEGORY_JSON_PATH,
        )

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
    回傳的 final_result 含：
    - category_code / category_path / reason
    - model_confidence：LLM 自報
    - final_confidence：混合分數
    - used_fallback / fallback_reason
    - needs_review
    - top1_retrieval_score / retrieval_gap_12
    """
    init_classifier(force_rebuild=force_rebuild)

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

    # 僅 top1/top2 接近時不一定代表錯誤；若 final_confidence 已高，可不必因 gap 強制 review
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


def preload_index(*, force_rebuild: bool = False) -> None:
    """批次流程開始時呼叫一次即可。"""
    init_classifier(force_rebuild=force_rebuild)
