# -*- coding: utf-8 -*-
"""
category_matcher.py

用途：
1. 從商品名稱（sale_product_name / erp_product_name / brand / spec）做關鍵字抽取
2. 對 momo_category_paths.csv 做全站通用分類召回
3. 只在候選群內啟用 family-specific rerank
4. 回傳最終分類候選與自動選擇結果

建議：
- 先只用這支做 mainEcCategoryCode
- 等分類穩定後，再做 ecIndex -> indexList 自動補值
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd


# ---------------------------------------------------------------------------
# 基礎設定
# ---------------------------------------------------------------------------

STOPWORDS = {
    "可選", "任選", "兩色", "三色", "四色", "五色", "多色", "單一規格", "組合", "套組",
    "禮盒", "限量", "熱銷", "推薦", "人氣", "旗艦", "升級版", "新版", "經典款", "豪華版",
    "公司貨", "台灣公司貨", "原廠", "原廠公司貨", "正貨", "現貨", "福利品", "促銷",
    "加碼", "專用", "適用", "通用", "家用", "商用", "款", "台", "入", "組", "盒", "包",
    "顆", "支", "條", "件", "個", "枚",
}

COLOR_WORDS = {
    "黑", "白", "紅", "藍", "綠", "黃", "紫", "粉", "灰", "銀", "金", "棕", "橘",
    "黑色", "白色", "紅色", "藍色", "綠色", "黃色", "紫色", "粉色", "灰色", "銀色", "金色",
    "奶油白", "起司黃", "曜石黑", "星空灰", "霧面黑", "珍珠白", "櫻花粉",
}

BRAND_LOW_WEIGHT = {
    # 這裡不是要完全排除品牌，而是降權
    # 你可以依實際常見品牌慢慢補
    "直白", "dyson", "philips", "panasonic", "tescom", "xiaomi", "apple", "samsung",
    "sony", "jbl", "anker", "tp-link", "asus", "logitech",
}

# 常見中英同義詞，可逐步擴充
SYNONYM_GROUPS = [
    ("吹風機", ["吹風機", "吹風", "吹髮", "美髮吹風", "hair dryer", "dryer"]),
    ("負離子", ["負離子", "離子", "雙離子", "正負離子", "ionic"]),
    ("高速", ["高速", "高速馬達", "bldc", "無刷", "無刷馬達"]),
    ("耳機", ["耳機", "藍牙耳機", "真無線耳機", "headphone", "earphone", "earbuds"]),
    ("真無線", ["真無線", "tws", "wireless earbuds"]),
    ("降噪", ["降噪", "anc", "主動降噪", "noise cancelling"]),
    ("行動電源", ["行動電源", "充電寶", "power bank"]),
    ("手機殼", ["手機殼", "保護殼", "手機保護殼", "case"]),
    ("風扇", ["風扇", "電風扇", "循環扇", "桌扇", "立扇"]),
    ("吸塵器", ["吸塵器", "無線吸塵器", "手持吸塵器", "吸拖", "vacuum"]),
    ("除濕機", ["除濕機", "除濕"]),
    ("空氣清淨機", ["空氣清淨機", "清淨機", "air purifier"]),
    ("氣炸鍋", ["氣炸鍋", "air fryer"]),
    ("電鍋", ["電鍋", "電子鍋", "ih電子鍋", "rice cooker"]),
    ("滑鼠", ["滑鼠", "mouse"]),
    ("鍵盤", ["鍵盤", "keyboard"]),
]

# 這裡定義「候選群」判斷關鍵字
FAMILY_RULES = {
    "hair_dryer": {
        "family_keywords": ["吹風機", "吹風", "吹髮", "美髮", "hair dryer", "dryer"],
        "boost_keywords": ["負離子", "雙離子", "正負離子", "高速", "bldc", "無刷", "溫控", "冷熱風"],
        "negative_keywords": ["吹風機架", "掛架", "收納架", "置物架", "烘罩配件", "吹嘴配件"],
    },
    "earphone": {
        "family_keywords": ["耳機", "藍牙耳機", "真無線", "tws", "headphone", "earbuds"],
        "boost_keywords": ["降噪", "anc", "入耳", "頭戴", "無線", "藍牙", "通話"],
        "negative_keywords": ["耳機殼", "保護殼", "收納包", "替換耳塞", "耳罩配件"],
    },
    "fan": {
        "family_keywords": ["風扇", "電風扇", "循環扇", "立扇", "桌扇"],
        "boost_keywords": ["dc", "循環", "遙控", "靜音", "渦流", "立扇", "桌扇", "吊扇"],
        "negative_keywords": ["風扇罩", "風扇網", "扇葉配件"],
    },
    "vacuum": {
        "family_keywords": ["吸塵器", "吸拖", "手持吸塵器", "無線吸塵器", "vacuum"],
        "boost_keywords": ["手持", "無線", "濕拖", "除蟎", "集塵", "吸拖"],
        "negative_keywords": ["集塵袋", "濾網", "吸頭配件", "延長管"],
    },
    "power_bank": {
        "family_keywords": ["行動電源", "充電寶", "power bank"],
        "boost_keywords": ["磁吸", "快充", "pd", "qc", "自帶線", "magsafe"],
        "negative_keywords": ["收納包", "保護套", "支架"],
    },
    "phone_case": {
        "family_keywords": ["手機殼", "保護殼", "手機保護殼", "case"],
        "boost_keywords": ["防摔", "透明", "磁吸", "支架", "鏡頭保護"],
        "negative_keywords": ["手機", "整機", "充電器", "保護貼"],
    },
}

# 品牌別名，用於品牌一致性判斷
BRAND_ALIASES: Dict[str, List[str]] = {
    "直白": ["直白", "zhibai"],
    "dyson": ["dyson", "戴森"],
    "philips": ["philips", "飛利浦"],
    "panasonic": ["panasonic", "國際牌"],
    "tescom": ["tescom"],
    "solac": ["solac"],
    "iris ohyama": ["iris ohyama", "iris", "愛麗思"],
}


# ---------------------------------------------------------------------------
# 資料結構
# ---------------------------------------------------------------------------

@dataclass
class CategoryCandidate:
    code: str
    breadcrumb: str
    leaf_name: str
    level_1_name: str
    level_2_name: str
    level_3_name: str
    level_4_name: str
    base_score: float
    family_score: float
    final_score: float
    family: str
    reasons: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# 文字工具
# ---------------------------------------------------------------------------

def normalize_text(text: Any) -> str:
    s = unicodedata.normalize("NFKC", str(text or ""))
    s = s.replace("（", "(").replace("）", ")")
    s = s.replace("【", " ").replace("】", " ")
    s = s.replace("/", " ")
    s = s.replace("\\", " ")
    s = re.sub(r"[\[\]{}()\-_,.+|:;\"'`~!@#$%^&*=<>?，。、：；／]", " ", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


def contains_cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text))


def is_model_token(token: str) -> bool:
    """
    常見型號:
    S1 / A23 / XR-100 / WH1000XM5 / K10 / M5 / X200
    """
    t = token.strip()
    if not t:
        return False
    if re.fullmatch(r"[a-z]{0,4}\d{1,6}[a-z0-9\-]{0,10}", t, flags=re.I):
        return True
    if re.fullmatch(r"\d{2,6}[a-z]{1,4}", t, flags=re.I):
        return True
    return False


def is_size_or_unit_token(token: str) -> bool:
    return bool(re.fullmatch(r"\d+(\.\d+)?(w|v|kg|g|mg|ml|l|cm|mm|mah|吋|寸|公升|瓦)", token, flags=re.I))


def tokenize_text(*texts: Any) -> List[str]:
    raw = " ".join(str(x or "") for x in texts)
    norm = normalize_text(raw)
    if not norm:
        return []

    parts = norm.split()
    tokens: List[str] = []

    for p in parts:
        p = p.strip()
        if not p:
            continue
        if p in STOPWORDS:
            continue
        if p in COLOR_WORDS:
            continue
        if is_model_token(p):
            continue
        if is_size_or_unit_token(p):
            continue
        if p in BRAND_LOW_WEIGHT:
            # 品牌不完全丟掉，後面用低權重處理
            tokens.append(p)
            continue
        if len(p) == 1 and not re.search(r"[a-z0-9]", p):
            continue
        tokens.append(p)

    # 同義詞展開
    joined = " ".join(tokens)
    expanded = set(tokens)
    for canonical, aliases in SYNONYM_GROUPS:
        for alias in aliases:
            if normalize_text(alias) in joined:
                expanded.add(normalize_text(canonical))

    return sorted(expanded)


def tokenize_category_text(text: Any) -> List[str]:
    norm = normalize_text(text)
    if not norm:
        return []
    parts = norm.split()
    return [p for p in parts if p]


def _detect_brand_from_text(text: str) -> Optional[str]:
    """
    從文字中偵測品牌（使用 BRAND_ALIASES），回傳 canonical brand key。
    """
    t = normalize_text(text)
    for brand_key, aliases in BRAND_ALIASES.items():
        for alias in aliases:
            if normalize_text(alias) in t:
                return brand_key
    return None


# ---------------------------------------------------------------------------
# 分類器主體
# ---------------------------------------------------------------------------

class MomoCategoryMatcher:
    def __init__(self, csv_path: str | Path):
        self.csv_path = Path(csv_path)

        if self.csv_path.suffix.lower() == ".json":
            # 直接從 queryEcCategory.scm 的 category.json 建立分類 DataFrame
            self.df = self._load_from_category_json(self.csv_path)
        else:
            # 相容舊的 CSV 格式
            self.df = pd.read_csv(self.csv_path).fillna("")

        # 兼容不同欄位命名
        self._ensure_column("leaf_code")
        self._ensure_column("leaf_name")
        self._ensure_column("breadcrumb")
        self._ensure_column("level_1_name")
        self._ensure_column("level_2_name")
        self._ensure_column("level_3_name")
        self._ensure_column("level_4_name")

        # 正規化文字欄位
        for col in [
            "leaf_name",
            "breadcrumb",
            "level_1_name",
            "level_2_name",
            "level_3_name",
            "level_4_name",
        ]:
            self.df[f"__norm_{col}"] = self.df[col].astype(str).map(normalize_text)

        self.df["__tokens_leaf"] = self.df["leaf_name"].astype(str).map(tokenize_category_text)
        self.df["__tokens_l4"] = self.df["level_4_name"].astype(str).map(tokenize_category_text)
        self.df["__tokens_l3"] = self.df["level_3_name"].astype(str).map(tokenize_category_text)
        self.df["__tokens_breadcrumb"] = self.df["breadcrumb"].astype(str).map(tokenize_category_text)

        # 觀察目前 leaf 數量，方便除錯
        print(f"[CATEGORY_MATCHER] loaded leaves = {len(self.df)} from {self.csv_path}")

    # -----------------------------------------------------------------------
    # 公開方法
    # -----------------------------------------------------------------------

    def match(
        self,
        sale_product_name: str = "",
        erp_product_name: str = "",
        brand: str = "",
        spec_texts: Optional[List[str]] = None,
        top_k: int = 10,
    ) -> List[CategoryCandidate]:
        product_text = self._join_product_text(
            sale_product_name=sale_product_name,
            erp_product_name=erp_product_name,
            brand=brand,
            spec_texts=spec_texts or [],
        )
        product_tokens = tokenize_text(product_text)
        if not product_tokens:
            return []

        # 嘗試從商品文字偵測實際品牌（而不是只當低權重 token）
        product_brand = _detect_brand_from_text(product_text)

        # 1) 全站通用召回
        base_candidates = self._global_match(
            product_text=product_text,
            product_tokens=product_tokens,
            top_k=max(top_k * 3, 30),
        )
        if not base_candidates:
            return []

        # 2) 從候選判斷 family
        detected_family = self._detect_family_from_candidates(base_candidates, product_text, product_tokens)

        # 3) family-specific rerank
        reranked = self._rerank_with_family_rules(
            base_candidates=base_candidates,
            product_text=product_text,
            product_tokens=product_tokens,
            detected_family=detected_family,
            product_brand=product_brand,
        )

        reranked.sort(key=lambda x: x.final_score, reverse=True)
        return reranked[:top_k]

    def auto_pick(
        self,
        sale_product_name: str = "",
        erp_product_name: str = "",
        brand: str = "",
        spec_texts: Optional[List[str]] = None,
        top_k: int = 5,
        min_score: float = 14.0,
        min_gap: float = 4.0,
    ) -> Dict[str, Any]:
        candidates = self.match(
            sale_product_name=sale_product_name,
            erp_product_name=erp_product_name,
            brand=brand,
            spec_texts=spec_texts or [],
            top_k=top_k,
        )

        if not candidates:
            return {
                "picked_code": None,
                "picked_path": None,
                "picked_leaf_name": None,
                "need_review": True,
                "family": "unknown",
                "candidates": [],
            }

        top1 = candidates[0]
        top2_score = candidates[1].final_score if len(candidates) > 1 else -999.0
        gap = top1.final_score - top2_score

        auto_ok = (top1.final_score >= min_score) and (gap >= min_gap)

        return {
            "picked_code": top1.code if auto_ok else None,
            "picked_path": top1.breadcrumb if auto_ok else None,
            "picked_leaf_name": top1.leaf_name if auto_ok else None,
            "need_review": not auto_ok,
            "family": top1.family or "unknown",
            "candidates": [c.to_dict() for c in candidates],
        }

    # -----------------------------------------------------------------------
    # 核心流程
    # -----------------------------------------------------------------------

    def _global_match(
        self,
        product_text: str,
        product_tokens: List[str],
        top_k: int,
    ) -> List[CategoryCandidate]:
        results: List[CategoryCandidate] = []

        product_token_set = set(product_tokens)
        product_norm = normalize_text(product_text)

        for _, row in self.df.iterrows():
            score = 0.0
            reasons: List[str] = []

            leaf_norm = row["__norm_leaf_name"]
            l4_norm = row["__norm_level_4_name"]
            l3_norm = row["__norm_level_3_name"]
            l2_norm = row["__norm_level_2_name"]
            bc_norm = row["__norm_breadcrumb"]

            leaf_tokens = set(row["__tokens_leaf"])
            l4_tokens = set(row["__tokens_l4"])
            l3_tokens = set(row["__tokens_l3"])
            bc_tokens = set(row["__tokens_breadcrumb"])

            # A. token overlap
            overlap_leaf = product_token_set & leaf_tokens
            overlap_l4 = product_token_set & l4_tokens
            overlap_l3 = product_token_set & l3_tokens
            overlap_bc = product_token_set & bc_tokens

            if overlap_leaf:
                val = 10 + len(overlap_leaf) * 2.5
                score += val
                reasons.append(f"leaf_overlap:{sorted(overlap_leaf)}(+{val:.1f})")

            if overlap_l4:
                val = 7 + len(overlap_l4) * 1.8
                score += val
                reasons.append(f"l4_overlap:{sorted(overlap_l4)}(+{val:.1f})")

            if overlap_l3:
                val = 4 + len(overlap_l3) * 1.2
                score += val
                reasons.append(f"l3_overlap:{sorted(overlap_l3)}(+{val:.1f})")

            if overlap_bc:
                val = min(6.0, len(overlap_bc) * 0.8)
                score += val
                reasons.append(f"bc_overlap:{sorted(overlap_bc)}(+{val:.1f})")

            # B. substring hit
            for token in product_tokens:
                if not token:
                    continue

                if token in BRAND_LOW_WEIGHT:
                    # 品牌低權重
                    if token in bc_norm:
                        score += 0.5
                        reasons.append(f"brand_hit:{token}(+0.5)")
                    continue

                if token == leaf_norm:
                    score += 12
                    reasons.append(f"leaf_exact:{token}(+12)")
                elif token in leaf_norm:
                    score += 5
                    reasons.append(f"leaf_contains:{token}(+5)")

                if token == l4_norm:
                    score += 8
                    reasons.append(f"l4_exact:{token}(+8)")
                elif token in l4_norm:
                    score += 4
                    reasons.append(f"l4_contains:{token}(+4)")

                if token == l3_norm:
                    score += 5
                    reasons.append(f"l3_exact:{token}(+5)")
                elif token in l3_norm:
                    score += 2
                    reasons.append(f"l3_contains:{token}(+2)")

            # C. breadcrumb 文字連續命中
            if leaf_norm and leaf_norm in product_norm:
                score += 6
                reasons.append(f"product_contains_leaf:{leaf_norm}(+6)")

            # D. 輕微深度偏好：偏向較細的葉節點
            depth_bonus = 0.0
            non_empty_levels = sum(
                1 for x in [row["level_1_name"], row["level_2_name"], row["level_3_name"], row["level_4_name"]]
                if str(x).strip()
            )
            if non_empty_levels >= 4:
                depth_bonus = 0.8
            elif non_empty_levels >= 3:
                depth_bonus = 0.4

            score += depth_bonus
            if depth_bonus:
                reasons.append(f"depth_bonus:+{depth_bonus}")

            # F. 品牌專屬路徑輕微懲罰（之後若品牌匹配會在 family rerank 裡補回來）
            brand_specific_keywords = ["dyson", "戴森", "philips", "飛利浦", "solac", "tescom", "iris"]
            if any(k in bc_norm for k in brand_specific_keywords):
                score -= 4.0
                reasons.append("brand_specific_path_penalty(-4.0)")

            # E. 若完全沒有任何交集，不留
            if score <= 0:
                continue

            results.append(
                CategoryCandidate(
                    code=str(row["leaf_code"]),
                    breadcrumb=str(row["breadcrumb"]),
                    leaf_name=str(row["leaf_name"]),
                    level_1_name=str(row["level_1_name"]),
                    level_2_name=str(row["level_2_name"]),
                    level_3_name=str(row["level_3_name"]),
                    level_4_name=str(row["level_4_name"]),
                    base_score=round(score, 3),
                    family_score=0.0,
                    final_score=round(score, 3),
                    family="unknown",
                    reasons=reasons[:20],
                )
            )

        results.sort(key=lambda x: x.base_score, reverse=True)
        return results[:top_k]

    def _detect_family_from_candidates(
        self,
        base_candidates: List[CategoryCandidate],
        product_text: str,
        product_tokens: List[str],
    ) -> str:
        if not base_candidates:
            return "unknown"

        product_norm = normalize_text(product_text)
        family_scores: Dict[str, float] = {}

        # 先看商品文字
        for family, rule in FAMILY_RULES.items():
            score = 0.0
            for kw in rule["family_keywords"]:
                kw_norm = normalize_text(kw)
                if kw_norm in product_norm:
                    score += 3.0
            family_scores[family] = score

        # 再看 top 候選 breadcrumb 是否集中
        for cand in base_candidates[:8]:
            bc_norm = normalize_text(cand.breadcrumb)
            for family, rule in FAMILY_RULES.items():
                for kw in rule["family_keywords"]:
                    kw_norm = normalize_text(kw)
                    if kw_norm in bc_norm:
                        family_scores[family] = family_scores.get(family, 0.0) + 2.0

        if not family_scores:
            return "unknown"

        best_family, best_score = max(family_scores.items(), key=lambda x: x[1])
        if best_score < 3.0:
            return "unknown"
        return best_family

    def _rerank_with_family_rules(
        self,
        base_candidates: List[CategoryCandidate],
        product_text: str,
        product_tokens: List[str],
        detected_family: str,
        product_brand: Optional[str] = None,
    ) -> List[CategoryCandidate]:
        if detected_family == "unknown":
            return base_candidates

        product_norm = normalize_text(product_text)
        rule = FAMILY_RULES.get(detected_family)
        if not rule:
            return base_candidates

        reranked: List[CategoryCandidate] = []

        for cand in base_candidates:
            family_score = 0.0
            reasons = list(cand.reasons)
            bc_norm = normalize_text(cand.breadcrumb)
            leaf_norm = normalize_text(cand.leaf_name)

            # 品牌一致性：商品文字品牌 vs 類目 breadcrumb/葉節點中的品牌
            candidate_brand = _detect_brand_from_text(f"{cand.breadcrumb} {cand.leaf_name}")
            if product_brand and candidate_brand:
                if product_brand == candidate_brand:
                    family_score += 8.0
                    reasons.append(f"brand_match:{product_brand}(+8.0)")
                else:
                    family_score -= 25.0
                    reasons.append(f"brand_mismatch:{product_brand}!={candidate_brand}(-25.0)")

                    # 品牌館或品牌專屬節點，品牌又不符時再額外扣分
                    if ("品牌旗艦" in bc_norm) or ("dyson" in bc_norm) or ("戴森" in bc_norm):
                        family_score -= 10.0
                        reasons.append("wrong_brand_bucket(-10.0)")

            # 只有候選本身也和該 family 有關時才加權
            candidate_family_related = False
            for kw in rule["family_keywords"]:
                kw_norm = normalize_text(kw)
                if kw_norm in bc_norm or kw_norm in leaf_norm:
                    candidate_family_related = True
                    break

            if candidate_family_related:
                family_score += 3.0
                reasons.append(f"family_related:{detected_family}(+3.0)")

                for kw in rule["boost_keywords"]:
                    kw_norm = normalize_text(kw)
                    if kw_norm in product_norm and (kw_norm in bc_norm or kw_norm in leaf_norm):
                        family_score += 2.5
                        reasons.append(f"family_boost:{kw}(+2.5)")
                    elif kw_norm in product_norm:
                        family_score += 0.8
                        reasons.append(f"family_hint:{kw}(+0.8)")
            else:
                # 已判定屬於某個 family，但候選本身與 family 不相干，給予懲罰
                family_score -= 10.0
                reasons.append(f"not_family_related:{detected_family}(-10.0)")

            # 若候選帶有負向字，扣分
            for neg in rule["negative_keywords"]:
                neg_norm = normalize_text(neg)
                if neg_norm and (neg_norm in bc_norm or neg_norm in leaf_norm):
                    family_score -= 6.0
                    reasons.append(f"family_negative:{neg}(-6.0)")

            final_score = cand.base_score + family_score

            reranked.append(
                CategoryCandidate(
                    code=cand.code,
                    breadcrumb=cand.breadcrumb,
                    leaf_name=cand.leaf_name,
                    level_1_name=cand.level_1_name,
                    level_2_name=cand.level_2_name,
                    level_3_name=cand.level_3_name,
                    level_4_name=cand.level_4_name,
                    base_score=round(cand.base_score, 3),
                    family_score=round(family_score, 3),
                    final_score=round(final_score, 3),
                    family=detected_family,
                    reasons=reasons[:30],
                )
            )

        return reranked

    # -----------------------------------------------------------------------
    # 工具方法
    # -----------------------------------------------------------------------

    def _load_from_category_json(self, json_path: Path) -> pd.DataFrame:
        """
        從 queryEcCategory.scm 取得的 category.json 展平為 leaf 清單。
        只保留 END_YN == "1" 的節點，並組出最多 4 層的路徑。
        """
        with json_path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        rtn = data.get("rtnData") or {}

        rows: List[Dict[str, Any]] = []

        def dfs(node_code: str, node: Dict[str, Any], ancestors: List[Tuple[str, str]]) -> None:
            title = str(node.get("title", "")).strip()
            end_yn = str(node.get("END_YN", "")).strip()
            children = node.get("content") or {}

            path_chain = ancestors + [(node_code, title)]
            names = [t[1] for t in path_chain if t[1]]

            # 只收 leaf 節點
            if end_yn == "1":
                leaf_code = node_code
                leaf_name = title or (names[-1] if names else "")

                level_1_name = names[0] if len(names) >= 1 else ""
                level_2_name = names[1] if len(names) >= 2 else ""
                level_3_name = names[2] if len(names) >= 3 else ""
                level_4_name = names[3] if len(names) >= 4 else ""

                breadcrumb = " > ".join(names)

                rows.append(
                    {
                        "leaf_code": leaf_code,
                        "leaf_name": leaf_name,
                        "breadcrumb": breadcrumb,
                        "level_1_name": level_1_name,
                        "level_2_name": level_2_name,
                        "level_3_name": level_3_name,
                        "level_4_name": level_4_name,
                    }
                )

            # 繼續往下走（即使 END_YN == "1"，實務上還是防呆掃一下 children）
            if isinstance(children, dict):
                for child_code, child_node in children.items():
                    if isinstance(child_node, dict):
                        dfs(str(child_code), child_node, path_chain)

        for top_code, top_node in rtn.items():
            if isinstance(top_node, dict):
                dfs(str(top_code), top_node, [])

        df = pd.DataFrame(rows)
        if not df.empty:
            df = df.fillna("")
        return df

    def _join_product_text(
        self,
        sale_product_name: str = "",
        erp_product_name: str = "",
        brand: str = "",
        spec_texts: Optional[List[str]] = None,
    ) -> str:
        parts = [
            str(sale_product_name or ""),
            str(erp_product_name or ""),
            str(brand or ""),
        ]
        parts.extend([str(x or "") for x in (spec_texts or [])])
        return " ".join(parts)

    def _ensure_column(self, col: str) -> None:
        if col in self.df.columns:
            return

        fallback_map = {
            "leaf_code": ["category_code", "code", "leafCategoryCode", "ecCategoryCode"],
            "leaf_name": ["category_name", "name", "leafCategoryName"],
            "breadcrumb": ["path", "category_path", "full_path"],
            "level_1_name": ["l1_name"],
            "level_2_name": ["l2_name"],
            "level_3_name": ["l3_name"],
            "level_4_name": ["l4_name"],
        }

        for candidate in fallback_map.get(col, []):
            if candidate in self.df.columns:
                self.df[col] = self.df[candidate]
                return

        self.df[col] = ""


# ---------------------------------------------------------------------------
# 可直接執行測試
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    base_dir = Path(__file__).resolve().parents[1]
    json_path = base_dir / "momotest" / "category.json"
    matcher = MomoCategoryMatcher(json_path)

    samples = [
        {
            "sale_product_name": "高速BLDC正負離子吹風機-小甜筒-兩色可選(S1)",
            "erp_product_name": "直白雙離子高速吹風機S1(黃)",
            "brand": "直白",
            "spec_texts": ["電壓 110V", "功率 1600W"],
        },
        {
            "sale_product_name": "真無線藍牙降噪耳機 ANC 長續航",
            "erp_product_name": "藍牙耳機 黑色",
            "brand": "SoundX",
            "spec_texts": ["藍牙5.3", "主動降噪"],
        },
        {
            "sale_product_name": "20W 磁吸行動電源 10000mAh",
            "erp_product_name": "自帶線快充行動電源",
            "brand": "PowerGo",
            "spec_texts": ["容量 10000mAh", "PD快充"],
        },
    ]

    for i, s in enumerate(samples, start=1):
        result = matcher.auto_pick(
            sale_product_name=s["sale_product_name"],
            erp_product_name=s["erp_product_name"],
            brand=s["brand"],
            spec_texts=s["spec_texts"],
            top_k=5,
            min_score=14.0,
            min_gap=4.0,
        )

        print("=" * 100)
        print(f"[TEST {i}]")
        print(json.dumps(result, ensure_ascii=False, indent=2))