import requests
import json
from pathlib import Path

URL = "https://scmapi.momoshop.com.tw/api/v1/goods/basic_code/web_brand/D1102.scm"

LOGIN_INFO_PATH = Path(__file__).resolve().parents[1] / "config" / "login_info.json"


def load_login_info(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


login_info = load_login_info(LOGIN_INFO_PATH)

# -------------------------
# cache
# -------------------------

BRAND_CACHE: dict[tuple[str, str], str | None] = {}

# -------------------------
# API
# -------------------------

def _search_brand(payload_key: str, value: str) -> str | None:
    payload = {
        "loginInfo": login_info,
        payload_key: value,
    }

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0",
    }

    resp = requests.post(
        URL,
        headers=headers,
        json=payload,
        timeout=30,
    )

    if resp.status_code != 200:
        print(
            f"[WARN] brand API status={resp.status_code}, "
            f"key={payload_key}, value={value!r}"
        )
        return None

    data = resp.json()

    # 部分回應是 dict(data=[...])，有些則直接是 list
    if isinstance(data, dict):
        items = data.get("data")
    else:
        items = data

    if not items:
        print(f"[WARN] brand not found: key={payload_key}, value={value!r}")
        return None

    first = items[0]
    if not isinstance(first, dict):
        print(f"[WARN] unexpected brand item type: {type(first)!r}")
        return None

    return first.get("WEB_BRAND_NO") or first.get("webBrandNo")


def search_brand_chi(name: str) -> str | None:
    return _search_brand("brandChi", name)


def search_brand_eng(name: str) -> str | None:
    return _search_brand("brandEng", name)


# -------------------------
# 統一查詢（含 cache）
# -------------------------

def search_brand(chi: str | None, eng: str | None) -> str | None:
    chi = (chi or "").strip()
    eng = (eng or "").strip()

    cache_key = (chi, eng)

    # 命中 cache 直接回傳
    if cache_key in BRAND_CACHE:
        return BRAND_CACHE[cache_key]

    brand_no: str | None = None

    # 先用中文查
    if chi:
        brand_no = search_brand_chi(chi)

    # 中文沒找到再用英文查
    if not brand_no and eng:
        brand_no = search_brand_eng(eng)

    # 存到 cache（即使是 None，下次就不用再打 API）
    BRAND_CACHE[cache_key] = brand_no

    return brand_no