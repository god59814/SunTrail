from __future__ import annotations

import hashlib
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

import numpy as np
import requests

_SHARED_LIB = Path(__file__).resolve().parent.parent.parent / "shared"
if str(_SHARED_LIB) not in sys.path:
    sys.path.insert(0, str(_SHARED_LIB))

from llm_core.ollama_client import (
    CHAT_MODEL,
    EMBED_MODEL,
    OllamaClassifier,
)
from llm_core.parse_utils import (
    parse_llm_result_json_first as shared_parse_llm_result_json_first,
    sanitize_reason as shared_sanitize_reason,
)

logger = logging.getLogger(__name__)

# 與 cache_meta 一併寫入；變更時舊 cache 會失效（對齊 momo 專案做法）
CLASSIFIER_VERSION = "1.1-coupang"

_DEFAULT_CATEGORIES_JSON = (
    Path(__file__).resolve().parent.parent / "results" / "display_categories_TW.json"
)

_CACHE_DIR = Path(__file__).resolve().parent / "cache"
_CATEGORIES_CACHE_JSON = _CACHE_DIR / "categories.json"
_CATEGORY_EMBEDDINGS_NPY = _CACHE_DIR / "category_embeddings.npy"
_CACHE_META_JSON = _CACHE_DIR / "cache_meta.json"


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)


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

    lines: list[str] = []
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
    path_l = str(path).lower()
    include_terms: list[str] = []
    exclude_terms: list[str] = []
    hint_rules: list[tuple[str, list[str], list[str]]] = [
        ("吹風機", ["吹風機", "烘髮", "負離子", "美髮"], ["風扇", "水冷扇"]),
        ("風扇", ["循環扇", "立扇", "桌扇", "dc扇"], ["吹風機", "空調"]),
        ("空氣清淨", ["空氣清淨機", "除甲醛", "濾網", "pm2.5"], ["手機", "冷氣"]),
        ("智慧型手機", ["手機", "5g", "ios", "android"], ["空氣清淨機", "配件"]),
        ("冰箱", ["冰箱", "冷藏", "冷凍", "變頻"], ["飲料", "食品"]),
    ]
    for needle, inc, exc in hint_rules:
        if needle.lower() in path_l:
            include_terms.extend(inc)
            exclude_terms.extend(exc)
    if include_terms:
        uniq_inc = list(dict.fromkeys(include_terms))[:8]
        parts.append(f"適用關鍵字：{', '.join(uniq_inc)}")
    if exclude_terms:
        uniq_exc = list(dict.fromkeys(exclude_terms))[:8]
        parts.append(f"排除關鍵字：{', '.join(uniq_exc)}")
    return "\n".join(parts)


def _apply_simple_path_prefilter(
    product_text: str,
    categories: list[dict[str, Any]],
    category_embeddings: np.ndarray,
) -> tuple[list[dict[str, Any]], np.ndarray]:
    """
    依商品關鍵字限縮候選分類路徑（momo 同款邏輯），減少跨大類干擾。
    未命中規則或子集過小時回傳原樣。
    """
    blob = product_text.lower()
    rules: list[tuple[list[str], list[str]]] = [
        (
            [
                "吹風機",
                "負離子",
                "奈米水離子",
                "美髮",
                "hair dryer",
                "dryer",
            ],
            ["吹風機", "美髮家電", "美容保健", "美髮", "造型器"],
        ),
        (
            ["風扇", "循環扇", "立扇", "桌扇", "水冷扇", "dc扇", "dc 扇"],
            ["風扇", "季節家電"],
        ),
        (
            ["空氣淨化器", "空氣清淨機", "清淨機", "除甲醛", "pm2.5", "pm 2.5"],
            ["清淨機", "空氣清淨", "家電"],
        ),
        (
            ["冰箱", "電冰箱", "雙門", "對開", "門中門", "變頻冰箱", "冷櫃"],
            ["冰箱", "冷櫃", "大型家電", "家電"],
        ),
        (
            ["iphone", "手機", "智慧型手機", "ios"],
            ["智慧型手機", "iphone", "手機", "通訊"],
        ),
        (
            ["嬰兒", "寶寶", "奶瓶", "孕婦", "童"],
            ["母嬰", "嬰", "童", "孕"],
        ),
        (
            ["票券", "住宿", "旅遊", "飯店"],
            ["票券", "旅遊", "住宿"],
        ),
    ]
    for kws, path_needles in rules:
        if any(k.lower() in blob for k in kws):
            needles_l = [n.lower() for n in path_needles]
            idxs: list[int] = []
            for i, c in enumerate(categories):
                p = str(c.get("path", "")).lower()
                if any(n in p for n in needles_l):
                    idxs.append(i)
            if len(idxs) >= 8:
                sub_c = [categories[i] for i in idxs]
                sub_e = np.stack([category_embeddings[i] for i in idxs]).astype(
                    np.float32
                )
                logger.info(
                    "category prefilter: %s -> %s leaves",
                    kws[0],
                    len(sub_c),
                )
                return sub_c, sub_e
            break
    return categories, category_embeddings


def retrieve_top_k_categories(
    client: OllamaClassifier,
    product_text: str,
    categories: list[dict[str, Any]],
    category_embeddings: np.ndarray,
    top_k: int = 10,
    *,
    apply_prefilter: bool = True,
) -> list[dict[str, Any]]:
    cats, embs = categories, category_embeddings
    if apply_prefilter:
        cats, embs = _apply_simple_path_prefilter(
            product_text, categories, category_embeddings
        )

    product_emb = client.embed_texts([product_text])[0]
    scored: list[dict[str, Any]] = []
    for category, emb in zip(cats, embs):
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
    candidate_lines: list[str] = []
    allowed_codes: list[str] = []
    for idx, c in enumerate(candidates, start=1):
        code = str(c.get("code", "")).strip()
        if code:
            allowed_codes.append(code)
        candidate_lines.append(
            "\n".join(
                [
                    f"{idx}. code={code}",
                    f"   path={c.get('path', '')}",
                    f"   description={c.get('description', '')}",
                    f"   retrieval_score={c.get('retrieval_score', 0):.4f}",
                ]
            )
        )

    allowed_str = ", ".join(allowed_codes)

    system_prompt = """
你是商品分類助手。請依商品資訊，從「候選分類」中選最適合的一筆。
category_code 必須完全等於候選清單中某一筆的 code，不可發明、不可填占位字或說明文字。

請只輸出一個 JSON 物件（不要 markdown、不要其他說明），格式如下：
{
  "category_code": "<從候選擇一實際代碼>",
  "category_path": "與該候選一致的路徑文字",
  "reason": "<最多 20 字，說明為何選此類；勿複製本提示、勿填「簡短原因」等占位詞>",
  "confidence": 0.0
}
若難以判斷，仍須從候選中選最接近的一個 category_code，不可留空、不可輸出「必須為候選之一」這類示意字。
禁止輸出以下內容：<code>、string、...、最多20字、簡短原因、short reason。
confidence 為 0 到 1 的小數，表示你對此選擇的信心。
""".strip()

    user_prompt = f"""
允許的 category_code（必須擇一）：{allowed_str}

商品資訊：
{product_text}

候選分類：
{chr(10).join(candidate_lines)}
""".strip()

    result = client.chat_json(system_prompt, user_prompt, schema={})
    raw = result["raw_output"]
    parsed, parse_mode = shared_parse_llm_result_json_first(raw)
    parsed["parse_mode"] = parse_mode
    parsed["raw_output"] = raw

    if parsed.get("category_code") not in allowed_codes and candidates:
        logger.warning(
            "LLM category_code 不在候選內，改採 retrieval top-1: %s",
            parsed.get("category_code"),
        )
        top1 = float(candidates[0].get("retrieval_score", 0.0) or 0.0)
        top2 = (
            float(candidates[1].get("retrieval_score", 0.0) or 0.0)
            if len(candidates) > 1
            else 0.0
        )
        gap_12 = top1 - top2
        stable_top1 = top1 >= 0.17 and gap_12 >= 0.008
        parsed["category_code"] = str(candidates[0].get("code", ""))
        parsed["category_path"] = str(candidates[0].get("path", ""))
        parsed["reason"] = shared_sanitize_reason(
            str(parsed.get("reason", "") or ""),
            "fallback: llm code not in candidates",
        )
        parsed["confidence"] = max(float(parsed.get("confidence", 0.0)), 0.0)
        parsed["parse_mode"] = parse_mode + "+code_fallback"
        parsed["used_code_fallback"] = True
        parsed["fallback_on_unstable_candidates"] = not stable_top1
    else:
        parsed["reason"] = shared_sanitize_reason(str(parsed.get("reason", "") or ""))
        parsed["used_code_fallback"] = False
        parsed["fallback_on_unstable_candidates"] = False

    return parsed


def classify_product(
    client: OllamaClassifier,
    product: dict[str, Any],
    categories: list[dict[str, Any]],
    category_embeddings: np.ndarray,
    top_k: int = 10,
    *,
    apply_prefilter: bool = True,
) -> dict[str, Any]:
    product_text = build_product_text(product)
    candidates = retrieve_top_k_categories(
        client,
        product_text,
        categories,
        category_embeddings,
        top_k=top_k,
        apply_prefilter=apply_prefilter,
    )
    final_result = rerank_with_llm(client, product_text, candidates)

    top1 = float(candidates[0]["retrieval_score"]) if candidates else 0.0
    top2 = float(candidates[1]["retrieval_score"]) if len(candidates) > 1 else 0.0
    gap_12 = top1 - top2

    final_result.setdefault("top1_retrieval_score", top1)
    final_result.setdefault("retrieval_gap_12", gap_12)
    final_result.setdefault(
        "model_confidence", float(final_result.get("confidence", 0.0) or 0.0)
    )

    return {
        "product_text": product_text,
        "top_k_candidates": candidates,
        "final_result": final_result,
        "retrieval_metadata": {
            "top1_retrieval_score": top1,
            "top2_retrieval_score": top2,
            "retrieval_gap_12": gap_12,
        },
    }


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


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_cache_meta(category_json_path: Path) -> None:
    meta = {
        "classifier_version": CLASSIFIER_VERSION,
        "embed_model": EMBED_MODEL,
        "chat_model": CHAT_MODEL,
        "category_json_path": str(category_json_path.resolve()),
        "category_json_sha256": _file_sha256(category_json_path),
        "category_json_mtime_ns": category_json_path.stat().st_mtime_ns,
    }
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _CACHE_META_JSON.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def cache_needs_rebuild(category_json_path: Path) -> bool:
    if not _CATEGORIES_CACHE_JSON.exists() or not _CATEGORY_EMBEDDINGS_NPY.exists():
        return True
    if not _CACHE_META_JSON.exists():
        logger.warning("cache_meta 缺失，將重建 embedding 快取")
        return True
    try:
        meta = json.loads(_CACHE_META_JSON.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return True
    if meta.get("classifier_version") != CLASSIFIER_VERSION:
        logger.info("classifier_version 變更，重建快取")
        return True
    if meta.get("embed_model") != EMBED_MODEL or meta.get("chat_model") != CHAT_MODEL:
        logger.info("ollama 模型設定變更，重建快取")
        return True
    if not category_json_path.is_file():
        return True
    current_sha = _file_sha256(category_json_path)
    if meta.get("category_json_sha256") != current_sha:
        logger.info("分類 JSON 內容變更，重建快取")
        return True
    return False


def build_category_index(
    client: OllamaClassifier,
    categories: list[dict[str, Any]],
    *,
    category_source_path: Path | None = None,
) -> None:
    _CACHE_DIR.mkdir(exist_ok=True)

    category_texts = [build_category_text(c) for c in categories]
    category_embs = client.embed_texts(category_texts)
    matrix = np.vstack(category_embs).astype(np.float32)

    _CATEGORIES_CACHE_JSON.write_text(
        json.dumps(categories, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    np.save(_CATEGORY_EMBEDDINGS_NPY, matrix)
    if category_source_path and category_source_path.is_file():
        write_cache_meta(category_source_path)
    else:
        write_cache_meta(_DEFAULT_CATEGORIES_JSON)


def load_category_index() -> tuple[list[dict[str, Any]], np.ndarray]:
    categories = json.loads(_CATEGORIES_CACHE_JSON.read_text(encoding="utf-8"))
    embeddings = np.load(_CATEGORY_EMBEDDINGS_NPY).astype(np.float32)

    if len(categories) != len(embeddings):
        raise ValueError("categories 與 embeddings 數量不一致。")

    return categories, embeddings


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    client = OllamaClassifier()

    if cache_needs_rebuild(_DEFAULT_CATEGORIES_JSON):
        categories = load_categories_from_json(_DEFAULT_CATEGORIES_JSON)
        build_category_index(client, categories, category_source_path=_DEFAULT_CATEGORIES_JSON)

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
