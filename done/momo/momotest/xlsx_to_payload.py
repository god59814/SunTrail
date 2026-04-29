import re
import argparse
import sys
from urllib.parse import urlparse, parse_qs
import unicodedata
import base64
import json
import numpy as np
import pandas as pd

from pathlib import Path
from typing import Any, Dict, List, cast, Optional

_SHARED_LIB = Path(__file__).resolve().parent.parent.parent / "shared"
if str(_SHARED_LIB) not in sys.path:
    sys.path.insert(0, str(_SHARED_LIB))
from work_paths import load_gsheet_pm_source, resolve_cred_path, work_root_from

from column_map import COLUMN_MAP
from momo_allowlist import (
    LOGININFO_KEYS,
    SENDINFO_KEYS,
    INDEXLIST_ITEM_KEYS,
    MOBILE_DETAIL_KEYS,
    SINGLE_ITEM_KEYS,
)
from gsheet_reader import read_sheet_records

from category_matcher import MomoCategoryMatcher, normalize_text as _cat_normalize_text
from momo_api_product_code import MomoProductCodeAPI
from llm_category_service import predict_category, preload_index

# from web_brand import search_brand_chi, search_brand_eng
from web_brand import search_brand

BASE_DIR = Path(__file__).resolve().parents[1]
LOGIN_INFO_PATH = BASE_DIR / "config" / "login_info.json"
CATEGORY_JSON_PATH = BASE_DIR / "momotest" / "category.json"
XLSX_PATH = r"C:\Users\user\Desktop\brian\momotest\test.xlsx"
ZIP_PATH = r"C:\Users\user\Desktop\brian\momotest\6971016545882.zip"

DO_ACTION = "tempReportGoods"  # tempReportGoods / verifyReportGoods

DROP_EMPTY_VALUE = True
INCLUDE_ZIP = False  # 先跳過zip；之後要加再改 True

# ---------------------------------------------------------------------------
# Google Sheet 相關設定（與 Coupang 共用同一份）
# ---------------------------------------------------------------------------
# 若要改回只讀本地 Excel，將 USE_GSHEET 改為 False 即可
USE_GSHEET = True

_WR = work_root_from(Path(__file__))
_GS = load_gsheet_pm_source(_WR)
if not _GS:
    raise RuntimeError(f"缺少 {_WR / 'shared' / 'config' / 'gsheet_pm_source.json'}")
GSHEET_CRED_PATH = Path(
    resolve_cred_path(_WR, str(_GS.get("cred_path", "") or ""))
)
GSHEET_SPREADSHEET_ID = str(_GS.get("spreadsheet_id", "") or "")
GSHEET_WORKSHEET_NAME = str(_GS.get("worksheet_name", "") or "")
GSHEET_HEADER_ROW = int(_GS.get("header_row") or 2)
GSHEET_DATA_START_ROW = int(_GS.get("data_start_row") or 4)
GSHEET_DATA_ROW_COUNT = 1

# ---------------------------------------------------------------------------
# LLM 分類設定（Ollama）
# ---------------------------------------------------------------------------
USE_LLM_CATEGORY = True
LLM_FALLBACK_TO_MATCHER_ON_ERROR = True
LLM_SKIP_ROW_ON_REVIEW = False

SPEC_CODE_MAP = {
    "尺寸": "001",
    "容量": "002",
    "顏色": "004",
    "口味": "005",
    "規格": "006",
    "款式": "007",
    "效期": "008",
    "色號": "014",
    "香調": "015",
    "大小": "018",
    "贈品": "024",
    "瓦斯類型": "026",
    "造型": "027",
    "型號": "028",
    "名稱": "029",
    "組合": "030",
    "出貨日": "031",
}

ORIGIN_CODE_JSON_PATH = BASE_DIR / "config" / "origin_code_map.json"

with ORIGIN_CODE_JSON_PATH.open("r", encoding="utf-8") as _f_origin:
    ORIGIN_CODE_MAP = json.load(_f_origin)

KEEP_EMPTY_KEYS = {
    "expDays",
    "colSeq2",
    "specImg2",
    "asDays",
    "asNote",
    "giftDesc",
    "saleNotice",
    "detailInfo",
    "outplaceSeq",
    "outplaceSeqRtn",
    "content",  # 給 mobileDetailInfo.content 用
}


def load_login_info(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("login_info.json must be a JSON object")
    return data


# ---------------------------------------------------------------------------
# 分類 + 屬性自動選擇設定
# ---------------------------------------------------------------------------

# 初始化分類匹配器與屬性 API client（全檔共用一份，避免重複載入 category.json）
_CATEGORY_MATCHER: Optional[MomoCategoryMatcher] = None
_MOMO_API: Optional[MomoProductCodeAPI] = None


def _get_category_matcher() -> MomoCategoryMatcher:
    global _CATEGORY_MATCHER
    if _CATEGORY_MATCHER is None:
        print(f"[CATEGORY] loading category tree from {CATEGORY_JSON_PATH}")
        _CATEGORY_MATCHER = MomoCategoryMatcher(CATEGORY_JSON_PATH)
    return _CATEGORY_MATCHER


def _get_momo_api() -> MomoProductCodeAPI:
    global _MOMO_API
    if _MOMO_API is None:
        _MOMO_API = MomoProductCodeAPI(config_path=str(LOGIN_INFO_PATH))
    return _MOMO_API


def read_zip_base64(zip_path: str) -> str:
    with open(zip_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def extract_youtube_id(value: str) -> str:
    s = str(value or "").strip()
    if not s:
        return ""

    # 直接就是 11 碼 ID
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", s):
        return s

    # youtu.be/xxxxxxxxxxx
    m = re.search(r"youtu\.be/([A-Za-z0-9_-]{11})", s)
    if m:
        return m.group(1)

    # youtube.com/watch?v=xxxxxxxxxxx
    try:
        parsed = urlparse(s)
        qs = parse_qs(parsed.query)
        v = qs.get("v", [""])[0]
        if re.fullmatch(r"[A-Za-z0-9_-]{11}", v):
            return v
    except Exception:
        pass

    return ""

def _normalize_text_for_match(text: Any) -> str:
    return _cat_normalize_text(text or "")

def _build_spec_texts_for_match(row: Dict[str, Any]) -> List[str]:
    vals: List[str] = []
    for key in [
        "slogan",
        "feature",
        "spec_value_1",
        "spec_value_2",
        "spec_value_3",
        "spec_value_4",
    ]:
        v = row.get(key, "")
        if _is_empty(v):
            continue
        vals.append(str(v).strip())
    return vals


def _flatten_index_row(item: Dict[str, Any]) -> Dict[str, Any]:
    index_no = str(item.get("INDEX_NO", "")).strip()
    index_name = str(item.get("INDEX_NAME", "")).strip()
    check_yn = str(item.get("CHECK_YN", "")).strip()

    item_content_raw = str(item.get("ITEM_CONTENT", "") or "")
    index_item_no_raw = str(item.get("INDEX_ITEM_NO", "") or "")

    value_names = [x.strip() for x in item_content_raw.split("\t") if x.strip()]
    value_codes = [x.strip() for x in index_item_no_raw.split("\t") if x.strip()]

    options: List[Dict[str, str]] = []
    max_len = max(len(value_names), len(value_codes))
    for i in range(max_len):
        options.append(
            {
                "name": value_names[i] if i < len(value_names) else "",
                "code": value_codes[i] if i < len(value_codes) else "",
            }
        )

    is_multi = check_yn == "1"

    return {
        "indexNo": index_no,
        "indexName": index_name,
        "checkYn": check_yn,
        "isMulti": is_multi,
        "options": options,
    }


def _rank_index_options_by_text(
    index_name: str, options: List[Dict[str, str]], product_text: str
) -> List[Dict[str, Any]]:
    """
    根據商品文字對每個選項打分，回傳依 score 由高到低排序的列表。
    分數邏輯與 test_category_and_index_from_gsheet.py 的 try_match_option_from_text 一致。
    """
    text = _normalize_text_for_match(product_text).lower()
    ranked: List[Dict[str, Any]] = []

    for opt in options:
        name = _normalize_text_for_match(opt.get("name", "")).lower()
        code = str(opt.get("code", "")).strip()
        if not name:
            continue

        score = 0
        if name in text:
            score += 10

        # 一些通用補強
        if index_name in ["有線/無線", "有線無線"]:
            if "無線" in text and "無線" in name:
                score += 20
            if "有線" in text and "有線" in name:
                score += 20

        elif index_name == "電壓":
            if "110v" in text and "110" in name:
                score += 20
            if "220v" in text and "220" in name:
                score += 20

        elif index_name == "消耗功率":
            watt_match = re.search(r"(\d{3,4})\s*w", text)
            if watt_match and watt_match.group(1) in name:
                score += 20

        if score > 0:
            ranked.append(
                {
                    "code": code,
                    "name": opt.get("name", ""),
                    "score": score,
                }
            )

    ranked.sort(key=lambda x: x["score"], reverse=True)
    return ranked


def _is_empty(v) -> bool:
    if v is None:
        return True
    if isinstance(v, str) and v.strip() == "":
        return True
    return False


def _filter_kv(d: dict, allow_keys: set[str]) -> dict:
    out = {}
    for k, v in d.items():
        if k not in allow_keys:
            continue

        if DROP_EMPTY_VALUE and _is_empty(v):
            if k in KEEP_EMPTY_KEYS:
                out[k] = ""
            continue

        if (not DROP_EMPTY_VALUE) and v is None:
            continue

        out[k] = v
    return out


def _filter_nested(send: dict) -> dict:
    send = _filter_kv(send, SENDINFO_KEYS)

    if "indexList" in send and isinstance(send["indexList"], list):
        send["indexList"] = [
            _filter_kv(x, INDEXLIST_ITEM_KEYS)
            for x in send["indexList"]
            if isinstance(x, dict)
        ]
        send["indexList"] = [x for x in send["indexList"] if x]

    if "singleItemList" in send and isinstance(send["singleItemList"], list):
        send["singleItemList"] = [
            _filter_kv(x, SINGLE_ITEM_KEYS)
            for x in send["singleItemList"]
            if isinstance(x, dict)
        ]
        send["singleItemList"] = [x for x in send["singleItemList"] if x]

    if "mobileDetailInfo" in send and isinstance(send["mobileDetailInfo"], dict):
        send["mobileDetailInfo"] = _filter_kv(
            send["mobileDetailInfo"], MOBILE_DETAIL_KEYS
        )
        if not send["mobileDetailInfo"]:
            send.pop("mobileDetailInfo", None)

    return send


def _move_key_to_end(d: dict, key: str) -> None:
    if key in d:
        v = d.pop(key)
        d[key] = v


# def _ensure_parent_batch_sup_no(base: Dict[str, Any]) -> None:
#     # 若商品層沒有 batchSupNo，就用第一個 SKU 的 batchSupNo 補上
#     if not _is_empty(base.get("batchSupNo")):
#         return
#     single_list = base.get("singleItemList")
#     if not isinstance(single_list, list) or len(single_list) == 0:
#         return
#     first = single_list[0]
#     if isinstance(first, dict):
#         v = first.get("batchSupNo")
#         if not _is_empty(v):
#             base["batchSupNo"] = str(v).strip()
def _ensure_parent_batch_sup_no(base: Dict[str, Any]) -> None:
    if _is_empty(base.get("batchSupNo")):
        base["batchSupNo"] = "10001"


def _load_raw_df(
    *,
    data_start_row: int | None = None,
    data_row_count: int | None = None,
) -> pd.DataFrame:
    """
    載入原始商品資料：
    - 若 USE_GSHEET=True：從 Google Sheet 讀取（與 Coupang 相同設定）
    - 否則：從本地 Excel 檔案讀取
    """
    if USE_GSHEET:
        _data_start_row = (
            data_start_row if data_start_row is not None else GSHEET_DATA_START_ROW
        )
        _data_row_count = (
            data_row_count if data_row_count is not None else GSHEET_DATA_ROW_COUNT
        )
        records, header_mapping = read_sheet_records(
            credentials_path=str(GSHEET_CRED_PATH),
            spreadsheet_id=GSHEET_SPREADSHEET_ID,
            worksheet_name=GSHEET_WORKSHEET_NAME,
            header_row=GSHEET_HEADER_ROW,
            data_start_row=_data_start_row,
        )

        # 若有指定 GSHEET_DATA_ROW_COUNT，就只取前 N 列做測試
        if _data_row_count and _data_row_count > 0:
            records = records[:_data_row_count]

        print("[GSHEET] rows =", len(records))
        print("[GSHEET] header mapping (col: 原始 -> 正規化):")
        for col_idx, orig, final in header_mapping:
            print(f"  col{col_idx}: {orig!r} -> {final!r}")

        return pd.DataFrame(records)

    # 備援：改回用本地 Excel
    return pd.read_excel(
        XLSX_PATH,
        header=0,
        skiprows=[1],
        dtype=str,  # 你說讀出來正常，那就維持 str
    )


def _build_mobile_detail(row):
    url = row.get("youtube_url")
    if _is_empty(url):
        return None

    return {"youtubeUrl": [str(url).strip()]}


def _overlay_from_sheet(df: pd.DataFrame, target_col: str, source_col: str) -> None:
    if source_col not in df.columns:
        return
    s = df[source_col].fillna("").astype(str).str.strip()
    df[target_col] = s.where(s != "", df[target_col])


def _apply_gsheet_to_momo_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    if "platform" in df.columns:
        uniques = sorted(set(str(x).strip() for x in df["platform"]))
        print("[GSHEET] platform unique values:", uniques)

    # 固定預設值
    df["isPrompt"] = "否"
    df["isGift"] = "否"

    # 暫時固定預設值
    df["batchSupNo"] = "10001"
    df["hasAs"] = "有"
    df["asDays"] = "365"
    df["asNote"] = "保固說明"
    df["mainEcCategoryCode"] = "1000500001"
    df["buyPrice"] = "1200"
    df["goodsType"] = "01"

    df["isECWarehouse"] = "否"
    df["isCommission"] = "否"
    df["isAcceptTravelCard"] = "否"
    df["isIncludeInstall"] = "否"
    df["isPointReachDate"] = "否"

    #  327桃園市新屋區東明里埔工路336號(中法鑫-東明倉)
    df["outplaceSeq"] = "000009"
    df["outplaceSeqRtn"] = "000009"

    df["saleUnit"] = "無"
    df["expDays"] = ""

    df["colSeq2"] = ""
    df["displaySpecImg"] = "規格1"
    df["liveStreamYn"] = "1"

    # 預設 indexList
    df["indexList"] = [[] for _ in range(len(df))]

    # 預設 mobileDetailInfo.content
    df["mobileDetailInfo"] = [{"youtubeUrl": [], "content": ""} for _ in range(len(df))]

    # 商品名稱
    sale_col = df.get("sale_product_name")
    erp_col = df.get("erp_product_name")

    if sale_col is not None and erp_col is not None:
        df["supGoodsName_salePoint"] = sale_col.where(
            sale_col.astype(str).str.strip() != "", erp_col
        )
    elif sale_col is not None:
        df["supGoodsName_salePoint"] = sale_col
    elif erp_col is not None:
        df["supGoodsName_salePoint"] = erp_col

    # 先用 ERP 名稱當作原始 supGoodsName
    df["supGoodsName"] = df["erp_product_name"].fillna("").astype(str).str.strip()

    # 品牌 / 標語 / 賣點 / 說明
    if "brand" in df.columns:
        brand_split = df["brand"].apply(split_brand_name)
        df["brand_chi"] = brand_split.apply(lambda x: x[0])
        df["brand_eng"] = brand_split.apply(lambda x: x[1])
        df["supGoodsName_brand"] = df["brand_chi"].fillna(df["brand_eng"])

        # 把商品名稱開頭重複的品牌字樣拿掉（通常在前面幾個字）
        def _strip_brand_prefix(row):
            name = str(row.get("supGoodsName") or "").strip()
            brand = str(row.get("supGoodsName_brand") or "").strip()
            if not name or not brand:
                return name

            # 常見分隔符號
            seps = [" ", "　", "-", "－", "_", "／", "/", "|"]

            for sep in seps:
                prefix = brand + sep
                if name.startswith(prefix):
                    return name[len(prefix) :].lstrip()

            # 品牌剛好等於整個前綴，不帶分隔符
            if name.startswith(brand):
                return name[len(brand) :].lstrip()

            return name

        df["supGoodsName"] = df.apply(_strip_brand_prefix, axis=1)

    if "slogan" in df.columns:
        df["headline"] = df["slogan"]

    if "feature" in df.columns:
        df["goodsSpec"] = df["product_description"]

    if "product_description" in df.columns:
        df["content"] = df["feature"]

    # youtube 有值時，覆蓋 mobileDetailInfo
    if "youtube_url" in df.columns:

        def _build_mobile_detail_with_default(row):
            url = row.get("youtube_url")
            if _is_empty(url):
                return {"youtubeUrl": [], "content": ""}

            video_id = extract_youtube_id(str(url))
            if not video_id:
                return {"youtubeUrl": [], "content": ""}

            return {"youtubeUrl": [video_id], "content": ""}

        df["mobileDetailInfo"] = df.apply(_build_mobile_detail_with_default, axis=1)

    # 價格
    if "sale_price" in df.columns:
        df["salePrice"] = df["sale_price"]

    if "list_price" in df.columns:
        df["custPrice"] = df["list_price"]

    if "cost_price" in df.columns:
        cost = df["cost_price"].astype(str).str.strip()
        df["buyPrice"] = cost.where(cost != "", "1200")

    # 尺寸 / 重量
    if "length_cm" in df.columns:
        df["length"] = df["length_cm"]
    if "width_cm" in df.columns:
        df["width"] = df["width_cm"]
    if "height_cm" in df.columns:
        df["height"] = df["height_cm"]
    if "weight_kg" in df.columns:
        df["weight"] = df["weight_kg"]

    # 產地、溫層、保固
    if "origin_country" in df.columns:
        df["originCode"] = df["origin_country"].apply(_map_origin_code)

    if "temperature_type" in df.columns:
        df["temperatureType"] = df["temperature_type"]

    if "warranty_days" in df.columns:
        warranty = df["warranty_days"].fillna("").astype(str).str.strip()
        df["asDays"] = warranty.where(warranty != "", df["asDays"])

    # 規格欄位
    if "spec_name_1" in df.columns:
        df["colSeq1"] = df["spec_name_1"].apply(_map_spec_code)

    if "spec_name_2" in df.columns:
        spec2_name = df["spec_name_2"].fillna("").astype(str).str.strip()
        mapped = spec2_name.apply(_map_spec_code)
        df["colSeq2"] = mapped.where(spec2_name != "", "006")
    else:
        spec2_name = pd.Series([""] * len(df), index=df.index)
        df["colSeq2"] = "006"

    if "spec_value_1" in df.columns:
        df["colDetail1"] = df["spec_value_1"]

    if "spec_value_2" in df.columns:
        spec2_value = df["spec_value_2"].fillna("").astype(str).str.strip()
        df["colDetail2"] = spec2_value.where(spec2_name != "", "單一規格")
    else:
        df["colDetail2"] = pd.Series(
            ["單一規格" if v == "" else "" for v in spec2_name], index=df.index
        )

    # SKU & 庫存
    if "erp_sku" in df.columns:
        df["entpGoodsNo"] = df["erp_sku"]

    if "barcode" in df.columns:
        df["internationalNo"] = df["barcode"]

    if "stock_qty" in df.columns:
        df["prepareQty"] = df["stock_qty"]

    # if "model_no" in df.columns:
    #     df["entpGoodsNo"] = df["model_no"]

    return df


def _map_spec_code(name):
    if _is_empty(name):
        return None

    key = str(name).strip()

    # Unicode 正規化，處理像 顏⾊ 這種相容字/部件字
    key = unicodedata.normalize("NFKC", key)

    # 清理常見空白
    key = key.replace("　", "").replace(" ", "")

    code = SPEC_CODE_MAP.get(key)

    if code is None:
        print(f"[WARN] unknown spec name: raw={repr(name)} normalized={repr(key)}")

    return code


def _map_origin_code(v):
    if _is_empty(v):
        return None

    key = str(v).strip().replace("　", "")

    code = ORIGIN_CODE_MAP.get(key, key)

    if code == key and not key.isdigit():
        print(f"[WARN] unknown origin country: {repr(key)}")

    return code


def split_brand_name(raw: str) -> tuple[str | None, str | None]:
    s = str(raw).strip()
    if not s:
        return None, None

    parts = s.split()

    chi_parts = [p for p in parts if any("\u4e00" <= ch <= "\u9fff" for ch in p)]
    eng_parts = [p for p in parts if p.isascii()]

    chi = " ".join(chi_parts) or None
    eng = " ".join(eng_parts) or None

    return chi, eng


def _annotate_category_and_indexes_for_records(records: List[Dict[str, Any]]) -> None:
    """
    對每筆原始記錄自動：
    1) 選 mainEcCategoryCode（使用 MomoCategoryMatcher + category.json）
    2) 用 ecIndex 查該分類的屬性
    3) 依商品文字自動選擇屬性值：
       - 單選：選 1 個
       - 複選：最多選 3 個，chosenItemNo 用 `\t` 串接
    4) 將結果寫回 record["mainEcCategoryCode"] / record["indexList"]
    """
    matcher: Optional[MomoCategoryMatcher] = None
    if not USE_LLM_CATEGORY or LLM_FALLBACK_TO_MATCHER_ON_ERROR:
        matcher = _get_category_matcher()
    api = _get_momo_api()

    for i, row in enumerate(records, start=1):
        sale_name = str(row.get("sale_product_name", "") or "")
        erp_name = str(row.get("erp_product_name", "") or "")
        brand = str(row.get("brand", "") or "")
        spec_texts = _build_spec_texts_for_match(row)

        product_text = " ".join([sale_name, erp_name, brand] + spec_texts)

        print("=" * 120)
        print(f"[AUTO-CAT ROW {i}]")
        print("sale_product_name =", sale_name)
        print("erp_product_name  =", erp_name)
        print("brand             =", brand)
        print("spec_texts        =", spec_texts)

        picked_code = ""
        picked_path = ""
        need_review = False

        if USE_LLM_CATEGORY:
            try:
                llm_result = predict_category(
                    {
                        "sale_product_name": sale_name,
                        "erp_product_name": erp_name,
                        "brand": brand,
                        "feature": str(row.get("feature", "") or ""),
                        "product_description": str(
                            row.get("product_description", "") or ""
                        ),
                        "model_no": str(row.get("model_no", "") or ""),
                        "spec_name_1": str(row.get("spec_name_1", "") or ""),
                        "spec_value_1": str(row.get("spec_value_1", "") or ""),
                        "spec_name_2": str(row.get("spec_name_2", "") or ""),
                        "spec_value_2": str(row.get("spec_value_2", "") or ""),
                    },
                    top_k=10,
                    min_confidence=0.55,
                    fallback_code="1000500001",
                    fallback_path="家電 > 美髮家電 > 吹風機",
                    apply_prefilter=True,
                )
                final = llm_result.get("final_result") or {}
                top_k_candidates = llm_result.get("top_k_candidates") or []
                picked_code = str(final.get("category_code", "") or "").strip()
                picked_path = str(final.get("category_path", "") or "").strip()
                need_review = bool(final.get("needs_review", False))

                row["llmCategoryPath"] = picked_path
                row["llmNeedsReview"] = "Y" if need_review else "N"
                row["llmFinalConfidence"] = str(
                    final.get("final_confidence", "") or ""
                )
                row["llmModelConfidence"] = str(
                    final.get("model_confidence", "") or ""
                )
                row["llmUsedFallback"] = (
                    "Y" if final.get("used_fallback", False) else "N"
                )
                reasons = final.get("review_reasons") or []
                row["llmReviewReasons"] = (
                    "\t".join(str(x) for x in reasons) if reasons else ""
                )
                row["llmCategorySource"] = "llm"
                if LLM_SKIP_ROW_ON_REVIEW and need_review:
                    row["skipUpload"] = "Y"

                print("[CATEGORY][LLM] picked_code =", picked_code)
                print("[CATEGORY][LLM] picked_path =", picked_path)
                print("[CATEGORY][LLM] needs_review =", need_review)
                print("[CATEGORY][LLM] parse_mode =", final.get("parse_mode", ""))
                print("[CATEGORY][LLM] reason =", final.get("reason", ""))
                print("[CATEGORY][LLM] confidence =", final.get("confidence", ""))
                raw_output = str(final.get("raw_output", "") or "").strip()
                # if raw_output:
                #     print("[CATEGORY][LLM] raw_output:")
                #     print(raw_output)

                if isinstance(top_k_candidates, list) and top_k_candidates:
                    print("[CATEGORY][LLM] top_k_candidates:")
                    for j, c in enumerate(top_k_candidates[:10], start=1):
                        if isinstance(c, dict):
                            print(
                                "  "
                                f"{j}. code={c.get('code', '')} "
                                f"score={c.get('retrieval_score', 0)} "
                                f"path={c.get('path', '')}"
                            )
            except Exception as e:
                print("[LLM] predict_category failed:", repr(e))
                if LLM_FALLBACK_TO_MATCHER_ON_ERROR and matcher is not None:
                    category_result = matcher.auto_pick(
                        sale_product_name=sale_name,
                        erp_product_name=erp_name,
                        brand=brand,
                        spec_texts=spec_texts,
                        top_k=5,
                        min_score=22.0,
                        min_gap=6.0,
                    )
                    picked_code = category_result.get("picked_code") or ""
                    picked_path = str(category_result.get("picked_path") or "")
                    need_review = bool(category_result.get("need_review"))
                    row["llmCategorySource"] = "matcher_fallback"
                else:
                    raise
        elif matcher is not None:
            category_result = matcher.auto_pick(
                sale_product_name=sale_name,
                erp_product_name=erp_name,
                brand=brand,
                spec_texts=spec_texts,
                top_k=5,
                min_score=22.0,
                min_gap=6.0,
            )
            picked_code = category_result.get("picked_code") or ""
            picked_path = str(category_result.get("picked_path") or "")
            need_review = bool(category_result.get("need_review"))
            row["llmCategorySource"] = "matcher"

        # 寫回主分類（若沒有足夠信心，就先留原值）
        if picked_code:
            row["mainEcCategoryCode"] = picked_code

        index_list: List[Dict[str, str]] = []

        if picked_code:
            try:
                ec_index_resp = api.ecIndex({"ecCategoryCode": picked_code})
                print(
                    "[INDEX] raw response (略過詳細內容，僅顯示型別):",
                    type(ec_index_resp),
                )

                index_rows_raw: List[Dict[str, Any]] = []
                if isinstance(ec_index_resp, list):
                    index_rows_raw = [x for x in ec_index_resp if isinstance(x, dict)]
                elif isinstance(ec_index_resp, dict):
                    for key in ["dataList", "data", "resultData", "indexList"]:
                        val = ec_index_resp.get(key)
                        if isinstance(val, list):
                            index_rows_raw = [x for x in val if isinstance(x, dict)]
                            break
                    if not index_rows_raw and isinstance(
                        ec_index_resp.get("dataList"), dict
                    ):
                        inner = ec_index_resp["dataList"]
                        for key in ["dataList", "data", "resultData", "indexList"]:
                            val = inner.get(key)
                            if isinstance(val, list):
                                index_rows_raw = [x for x in val if isinstance(x, dict)]
                                break

                index_rows = [_flatten_index_row(x) for x in index_rows_raw]
                print("[INDEX] required count =", len(index_rows))

                for item in index_rows:
                    index_no = item["indexNo"]
                    index_name = item["indexName"]
                    options = item["options"]
                    is_multi = item.get("isMulti", False)
                    select_type = "複選" if is_multi else "單選"

                    print(
                        f"  - {index_name} ({index_no}) {select_type} options={len(options)}"
                    )

                    ranked = _rank_index_options_by_text(
                        index_name=index_name,
                        options=options,
                        product_text=product_text,
                    )
                    # 若有明確匹配結果就依分數選；否則退而選擇 options 的前幾個作為 fallback
                    if ranked:
                        if is_multi:
                            top_codes = [r["code"] for r in ranked[:3] if r.get("code")]
                            if not top_codes:
                                continue
                            chosen_item_no = "\t".join(top_codes)
                        else:
                            code = ranked[0].get("code")
                            if not code:
                                continue
                            chosen_item_no = code
                    else:
                        # 沒有文字命中時的保底策略：仍然選出一組值，避免必填屬性留空
                        raw_codes = [o.get("code", "") for o in options]
                        raw_codes = [c for c in raw_codes if c]
                        if not raw_codes:
                            continue
                        if is_multi:
                            chosen_item_no = "\t".join(raw_codes[:3])
                        else:
                            chosen_item_no = raw_codes[0]

                    index_list.append(
                        {
                            "indexNo": index_no,
                            "chosenItemNo": chosen_item_no,
                        }
                    )

            except Exception as e:
                print("[INDEX] ecIndex failed:", repr(e))

        if index_list:
            row["indexList"] = index_list


def read_send_info_list(
    xlsx_path: str,
    *,
    data_start_row: int | None = None,
    data_row_count: int | None = None,
) -> list[dict]:
    # 來源統一改由 _load_raw_df 控制（可切換 gsheet / xlsx）
    df = _load_raw_df(
        data_start_row=data_start_row,
        data_row_count=data_row_count,
    )

    # 若是從 Google Sheet 來，先把欄位名轉成 momo 版本
    if USE_GSHEET:
        df = _apply_gsheet_to_momo_columns(df)

    df = df.dropna(how="all")

    if COLUMN_MAP:
        df = df.rename(columns=COLUMN_MAP)

    df = df.replace({pd.NA: None, np.nan: None})
    df = df.loc[:, [c for c in df.columns if c and not str(c).startswith("Unnamed")]]

    print(
        "[DEBUG] sale_product_name =",
        (
            df["sale_product_name"].tolist()
            if "sale_product_name" in df.columns
            else "NO sale_product_name"
        ),
    )
    print(
        "[DEBUG] erp_product_name =",
        (
            df["erp_product_name"].tolist()
            if "erp_product_name" in df.columns
            else "NO erp_product_name"
        ),
    )
    print(
        "[DEBUG] supGoodsName_salePoint =",
        (
            df["supGoodsName_salePoint"].tolist()
            if "supGoodsName_salePoint" in df.columns
            else "NO supGoodsName_salePoint"
        ),
    )
    raw_records = df.to_dict(orient="records")

    print(f"[MOMO] records after mapping = {len(raw_records)}")

    # 明確化型別，避免 Pylance 把 key 推成 Hashable
    records: List[Dict[str, Any]] = [
        {str(k): v for k, v in cast(Dict[Any, Any], r).items()}
        for r in cast(List[Dict[Any, Any]], raw_records)
    ]
    for i, r in enumerate(records[:5], start=1):
        print(f"[DEBUG] record {i} group_key =", repr(r.get("supGoodsName_salePoint")))

    if USE_LLM_CATEGORY:
        try:
            preload_index()
        except Exception as e:
            print("[LLM] preload_index failed:", repr(e))
            if not LLM_FALLBACK_TO_MATCHER_ON_ERROR:
                raise

    # 先對每筆原始記錄進行「分類 + 屬性」自動選擇，填入 mainEcCategoryCode / indexList
    _annotate_category_and_indexes_for_records(records)

    group_key = "supGoodsName_salePoint"

    # 這些是 SKU 層（singleItemList）欄位
    sku_keys = {
        "internationalNo",
        "prepareQty",
        "colDetail1",
        "colDetail2",
        "colSeq1",
        "colSeq2",
        "entpGoodsNo",
    }

    grouped: Dict[str, Dict[str, Any]] = {}

    for r in records:
        if str(r.get("skipUpload", "") or "").strip().upper() == "Y":
            print("[SKIP] skipUpload=Y, sale_point=", repr(r.get(group_key)))
            continue

        raw_k = r.get(group_key)
        if _is_empty(raw_k):
            continue

        sale_point = str(raw_k).strip()
        if sale_point == "":
            continue

        if sale_point not in grouped:
            grouped[sale_point] = {"singleItemList": []}

        base = grouped[sale_point]

        if "webBrandNo" not in base:
            chi = r.get("brand_chi")
            eng = r.get("brand_eng")

            brand_no = search_brand(chi, eng)

            if brand_no:
                base["webBrandNo"] = brand_no
            else:
                print(f"[WARN] no brand found for {sale_point}: {chi} {eng}")

        # 1) 賣場共用欄位：取第一筆為主，後面有值才補
        for kk, vv in r.items():
            if kk in sku_keys:
                continue
            if _is_empty(vv):
                continue
            if kk not in base or _is_empty(base.get(kk)):
                base[kk] = vv

        # 2) 規格名稱放在賣場層：colSeq1/colSeq2
        #    來源：同賣場內通常相同，取第一筆即可
        v = r.get("colSeq1")
        if not _is_empty(v):
            v = str(v).strip()
            if "colSeq1" not in base:
                base["colSeq1"] = v
            elif base["colSeq1"] != v:
                print(
                    f"[WARN] inconsistent colSeq1 for {sale_point}: {base['colSeq1']} -> {v}"
                )

        v = r.get("colSeq2")
        if not _is_empty(v):
            v = str(v).strip()
            if "colSeq2" not in base:
                base["colSeq2"] = v
            elif base["colSeq2"] != v:
                print(
                    f"[WARN] inconsistent colSeq2 for {sale_point}: {base['colSeq2']} -> {v}"
                )

        # 3) SKU 明細（每個顏色一筆）
        item: Dict[str, str] = {}

        v = r.get("entpGoodsNo")
        if not _is_empty(v):
            item["entpGoodsNo"] = str(v).strip()

        v = r.get("internationalNo")
        if not _is_empty(v):
            item["internationalNo"] = str(v).strip()

        v = r.get("prepareQty")
        if not _is_empty(v):
            item["prepareQty"] = str(v).strip()

        v = r.get("colDetail1")
        if not _is_empty(v):
            item["colDetail1"] = str(v).strip()

        v = r.get("colDetail2")
        if not _is_empty(v):
            item["colDetail2"] = str(v).strip()

        v = r.get("entpGoodsNo")
        if not _is_empty(v):
            item["entpGoodsNo"] = str(v).strip()

        # 產生 supGoodsdtCode：同一賣場第幾個 SKU 就用 001, 002, 003...
        single_list = base.get("singleItemList")
        if isinstance(single_list, list):
            next_idx = len(single_list) + 1
            item["supGoodsdtCode"] = f"{next_idx:03d}"

        # 只有有規格一(colSeq1)時，才產 specImg1
        parent_batch = str(base.get("batchSupNo") or sku_batch).strip()
        if parent_batch and base.get("colSeq1") and item.get("supGoodsdtCode"):
            item["specImg1"] = f"{parent_batch}_01_{item['supGoodsdtCode']}_B"

        # 若未使用第二規格，就留空；有第二規格再補
        item["specImg2"] = ""

        # 如果這列連 batchSupNo/internationalNo 都沒有，就不要塞（避免空 SKU）
        if _is_empty(item.get("entpGoodsNo")) and _is_empty(
            item.get("internationalNo")
        ):
            continue

        if isinstance(single_list, list):
            single_list.append(item)

        # 如果這列連 batchSupNo/internationalNo 都沒有，就不要塞（避免空 SKU）
        # if _is_empty(item.get("batchSupNo")) and _is_empty(item.get("internationalNo")):
        #     continue

        # single_list = base.get("singleItemList")
        # if isinstance(single_list, list):
        #     single_list.append(item)

    # 4) 輸出前：過 allowlist + 移除空 singleItemList
    send_list: list[dict] = []

    for base in grouped.values():
        single_list = base.get("singleItemList")
        if isinstance(single_list, list) and len(single_list) == 0:
            base.pop("singleItemList", None)

        # 先過 allowlist 再調順序（避免被濾掉）
        _ensure_parent_batch_sup_no(base)

        filtered = _filter_nested(base)

        # 讓 singleItemList 排最後（可讀性）
        _move_key_to_end(filtered, "indexList")
        _move_key_to_end(filtered, "singleItemList")
        _move_key_to_end(filtered, "mobileDetailInfo")

        send_list.append(filtered)

    print(f"[MOMO] grouped sale_point count = {len(grouped)}")
    print(f"[MOMO] sendInfoList length = {len(send_list)}")

    return send_list


def main():
    parser = argparse.ArgumentParser(
        description="Generate payload.json from Google Sheet / Excel"
    )
    parser.add_argument(
        "--data-start-row",
        type=int,
        default=None,
        help="Override GSHEET_DATA_START_ROW (1-based sheet row index).",
    )
    parser.add_argument(
        "--data-row-count",
        type=int,
        default=None,
        help="Override GSHEET_DATA_ROW_COUNT (how many records to take).",
    )
    args = parser.parse_args()

    login_info_raw = load_login_info(LOGIN_INFO_PATH)
    login_info = _filter_kv(login_info_raw, LOGININFO_KEYS)

    send_info_list = read_send_info_list(
        XLSX_PATH,
        data_start_row=args.data_start_row,
        data_row_count=args.data_row_count,
    )

    # 對 report_goods.py 而言，payload.json 只需要 sendInfoList；
    # doAction / loginInfo 會在送出前由 report_goods.py 動態組裝。
    payload: Dict[str, Any] = {
        "sendInfoList": send_info_list,
    }

    if INCLUDE_ZIP:
        payload["zipFileData"] = read_zip_base64(ZIP_PATH)

    # 輸出到專案根目錄的 payload.json，給 momo_auto_pack.py 等後續流程統一使用
    output_path = BASE_DIR / "payload.json"
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print("done")


if __name__ == "__main__":
    main()

