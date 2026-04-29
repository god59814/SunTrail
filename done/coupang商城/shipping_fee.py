import math
from typing import Any, Dict, List, Optional, Tuple


# 長+寬+高 (cm) 上限 -> 運費 (NT)。依 Coupang 台灣宅配級距。
SHIPPING_TIERS: List[Tuple[int, int]] = [
    (60, 65),
    (90, 70),
    (120, 90),
    (140, 105),
    (160, 135),
    (180, 180),
    (200, 285),
    (240, 365),
]


def _to_float(v: Any, default: float = 0.0) -> float:
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "")
    if not s:
        return default
    try:
        return float(s)
    except Exception:
        return default


def fee_from_dimension_sum(dimension_sum_cm: float) -> int:
    """
    依長+寬+高 (cm) 總和回傳對應運費。
    - 先對總和做無條件進位到整數 cm（避免 65.6 → 65 被看成 0–60 級距）。
    - 超過 240 則固定取最高級距 365。
    """
    dim_int = math.ceil(dimension_sum_cm)
    for max_cm, fee in SHIPPING_TIERS:
        if dim_int <= max_cm:
            return fee
    return 365


def get_dimension_sum(row: Dict[str, Any], field_map: Dict[str, str]) -> Optional[float]:
    """
    從一列資料取得「長+寬+高」(cm)，保留小數再相加。
    field_map 可設 dimension_sum_cm（單一欄）或 length_cm, width_cm, height_cm（三欄），
    對應 gsheet 欄位名稱。
    """
    sum_col = str((field_map.get("dimension_sum_cm") or "").strip())
    if sum_col:
        v = row.get(sum_col, None)
        n = _to_float(v, -1.0)
        return n if n >= 0 else None

    l_col = str((field_map.get("length_cm") or "")).strip()
    w_col = str((field_map.get("width_cm") or "")).strip()
    h_col = str((field_map.get("height_cm") or "")).strip()
    if not (l_col and w_col and h_col):
        return None

    l_ = _to_float(row.get(l_col), -1.0)
    w_ = _to_float(row.get(w_col), -1.0)
    h_ = _to_float(row.get(h_col), -1.0)
    if l_ < 0 or w_ < 0 or h_ < 0:
        return None

    return l_ + w_ + h_
