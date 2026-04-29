import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# -----------------------------
# Load tree
# -----------------------------
def load_categories_tree(categories_path: Path) -> Any:
    obj = json.loads(categories_path.read_text(encoding="utf-8"))
    if isinstance(obj, dict) and "data" in obj:
        return obj["data"]
    return obj


# -----------------------------
# Query parsing
# -----------------------------
_SPLIT_QUERY_RE = re.compile(r"[>\|/,，、\s]+")


def parse_query_terms(query: str) -> List[str]:
    """
    Turn a user query like:
      - "吹風機"
      - "吹風機 家庭用"
      - "吹風機>家庭用"
    into list of non-empty terms.
    """
    q = (query or "").strip()
    if not q:
        return []
    parts = [p.strip() for p in _SPLIT_QUERY_RE.split(q) if p and p.strip()]
    # de-dup keep order
    seen = set()
    out: List[str] = []
    for p in parts:
        if p not in seen:
            out.append(p)
            seen.add(p)
    return out


# -----------------------------
# Tree flattening (same as your walk, but returns rows)
# -----------------------------
def _node_name(node: Dict[str, Any]) -> str:
    return str(
        node.get("name")
        or node.get("displayCategoryName")
        or node.get("displayItemCategoryName")
        or ""
    ).strip()


def _node_code(node: Dict[str, Any]) -> Optional[int]:
    code = node.get("displayCategoryCode") or node.get("displayItemCategoryCode") or node.get("code")
    if code is None:
        return None
    try:
        return int(code)
    except Exception:
        return None


def flatten_categories(tree: Any) -> List[Tuple[int, str]]:
    """
    Return list of (code, path_str) for every node that has a valid code.
    """
    rows: List[Tuple[int, str]] = []

    def walk(node: Any, path: List[str]) -> None:
        if isinstance(node, dict):
            name = _node_name(node)
            new_path = path + ([name] if name else [])
            code = _node_code(node)
            if code is not None and name:
                path_str = " > ".join([p for p in new_path if p])
                rows.append((code, path_str))

            child = node.get("child") or node.get("children") or []
            if isinstance(child, list):
                for ch in child:
                    walk(ch, new_path)

        elif isinstance(node, list):
            for it in node:
                walk(it, path)

    walk(tree, [])
    return rows


# -----------------------------
# Search
# -----------------------------
def find_category_candidates_query(
    tree: Any,
    query: str,
    *,
    match: str = "AND",   # "AND" or "OR"
) -> List[Tuple[int, str, int]]:
    """
    Search category paths by user query terms.
    Returns (code, path_str, hit_count)
    """
    terms = parse_query_terms(query)
    if not terms:
        return []

    match = (match or "AND").strip().upper()
    rows = flatten_categories(tree)

    candidates: List[Tuple[int, str, int]] = []
    for code, path_str in rows:
        hits = sum(1 for t in terms if t in path_str)

        if match == "AND":
            ok = (hits == len(terms))
        else:
            ok = (hits >= 1)

        if ok:
            candidates.append((code, path_str, hits))

    return candidates


def pick_best_category_query(
    candidates: List[Tuple[int, str, int]],
    *,
    strategy: str = "best",   # "best" | "deepest" | "first"
) -> Optional[int]:
    if not candidates:
        return None

    strategy = (strategy or "best").strip().lower()
    if strategy == "first":
        return candidates[0][0]

    if strategy == "deepest":
        best = max(candidates, key=lambda x: len(x[1].split(" > ")))
        return best[0]

    # strategy == "best": prefer more hits, then deeper path
    best = max(candidates, key=lambda x: (x[2], len(x[1].split(" > "))))
    return best[0]


# -----------------------------
# Public API
# -----------------------------
def resolve_category_code(
    categories_json_path: Path,
    fallback_code: int,
    *,
    query: str = "",
    match: str = "AND",
    strategy: str = "best",
) -> Tuple[int, Dict[str, Any]]:
    """
    Resolve category by user query search:
      - query="吹風機" or "吹風機 家庭用"
      - match="AND" (default) or "OR"
      - strategy="best" (default), "deepest", "first"

    Returns:
      (category_code, debug_info)
    """
    if not categories_json_path.exists():
        if fallback_code <= 0:
            raise FileNotFoundError(f"Missing categories file: {categories_json_path}")
        return int(fallback_code), {"mode": "fallback", "candidates": []}

    tree = load_categories_tree(categories_json_path)

    q = (query or "").strip()
    if q:
        cands = find_category_candidates_query(tree, query=q, match=match)
        code = pick_best_category_query(cands, strategy=strategy)
        if code is not None:
            # keep only top 50 for debug
            cands_sorted = sorted(cands, key=lambda x: (x[2], len(x[1].split(" > "))), reverse=True)[:50]
            return int(code), {
                "mode": "query",
                "query": q,
                "terms": parse_query_terms(q),
                "match": match,
                "strategy": strategy,
                "candidates": cands_sorted,
            }

    if fallback_code <= 0:
        raise ValueError("category not found and fallback_code is not set")
    return int(fallback_code), {"mode": "fallback", "candidates": []}