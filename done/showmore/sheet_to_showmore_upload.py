from __future__ import annotations

import argparse
import base64
import csv
import json
import sys
import time
from html import escape as escape_html
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import gspread
import requests
from google.oauth2.service_account import Credentials
from playwright.sync_api import sync_playwright

_SHARED_LIB = Path(__file__).resolve().parent.parent / "shared"
if str(_SHARED_LIB) not in sys.path:
    sys.path.insert(0, str(_SHARED_LIB))
from work_paths import (
    load_gsheet_pm_source,
    resolve_cred_path,
    shared_assets_dir,
    work_root_from,
)


# =========================
# config
# =========================

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

CONFIG_PATH = Path(__file__).resolve().parent / "config" / "login_info.json"
OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
ASSETS_DIR = shared_assets_dir(_WR)
IMAGE_HOSTING_JSON = Path(__file__).resolve().parent / "image_hosting.json"

SHOWMORE_BASE = "https://showmore-api.showmore.cc"
SHOWMORE_BACKEND = "https://ecplus.showmore.cc/"
API_VERSION = "v2.67"


# =========================
# Google Sheet reader
# =========================
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


def _normalize_header(h: Any) -> str:
    s = "" if h is None else str(h)
    return s.strip().replace("\n", " ")


def _dedupe_headers(raw_headers: List[Any]) -> Tuple[List[str], List[Tuple[int, str, str]]]:
    used: Dict[str, int] = {}
    headers: List[str] = []
    mapping: List[Tuple[int, str, str]] = []

    for i, h in enumerate(raw_headers, start=1):
        orig = _normalize_header(h)
        base = orig if orig else f"__col{i}"

        if base not in used:
            used[base] = 1
            final = base
        else:
            used[base] += 1
            final = f"{base}__{used[base]}"

        headers.append(final)
        mapping.append((i, orig, final))

    return headers, mapping


def read_sheet_records(
    credentials_path: str,
    spreadsheet_id: str,
    worksheet_name: str,
    header_row: int = 2,
    data_start_row: int | None = None,
    data_row_count: int | None = None,
    keep_empty_rows: bool = False,
) -> Tuple[List[Dict[str, Any]], List[Tuple[int, str, str]]]:
    if data_start_row is None:
        data_start_row = header_row + 1
    if data_start_row <= header_row:
        raise ValueError("data_start_row must be > header_row")

    creds = Credentials.from_service_account_file(credentials_path, scopes=SCOPES)
    client = gspread.authorize(creds)
    ws = client.open_by_key(spreadsheet_id).worksheet(worksheet_name)

    all_values = ws.get_all_values()
    if len(all_values) < header_row:
        return [], []

    raw_headers = all_values[header_row - 1]
    headers, header_mapping = _dedupe_headers(raw_headers)

    records: List[Dict[str, Any]] = []

    for r in range(data_start_row - 1, len(all_values)):
        if data_row_count is not None and data_row_count > 0 and len(records) >= data_row_count:
            break

        row = all_values[r]

        if len(row) < len(headers):
            row = row + [""] * (len(headers) - len(row))
        else:
            row = row[: len(headers)]

        if not keep_empty_rows and not any(str(x).strip() for x in row):
            continue

        records.append(dict(zip(headers, row)))

    return records, header_mapping


# =========================
# Image resolver
# =========================
def load_img_base_url() -> str:
    if not IMAGE_HOSTING_JSON.exists():
        raise RuntimeError("找不到 image_hosting.json，請先執行 start_image_hosting.py")
    data = json.loads(IMAGE_HOSTING_JSON.read_text(encoding="utf-8"))
    base_url = str(data.get("img_base_url", "")).rstrip("/")
    if not base_url:
        raise RuntimeError("image_hosting.json 缺少 img_base_url")
    return base_url

def safe_name(value: Any) -> str:
    return "" if value is None else str(value).strip()

def build_public_url(base_url: str, path: Path, assets_dir: Path) -> str:
    rel = path.resolve().relative_to(assets_dir.resolve()).as_posix()
    return f"{base_url}/{rel}"

def pick_first_image(folder: Path) -> Path | None:
    if not folder.exists() or not folder.is_dir():
        return None
    exts = {".jpg", ".jpeg", ".png", ".webp"}
    files = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in exts]
    files.sort(key=lambda p: p.name.lower())
    return files[0] if files else None


def pick_all_images(folder: Path) -> List[Path]:
    if not folder.exists() or not folder.is_dir():
        return []
    exts = {".jpg", ".jpeg", ".png", ".webp"}
    files = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in exts]
    files.sort(key=lambda p: p.name.lower())
    return files


def pick_nth_image(folder: Path, index: int) -> Path | None:
    """在 main 資料夾內依檔名挑選 main_01 ~ main_03（支援 .jpg/.jpeg/.png/.webp）。"""
    if not folder.exists() or not folder.is_dir() or index < 1:
        return None
    stem = f"main_{index:02d}"
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        p = folder / f"{stem}{ext}"
        if p.is_file():
            return p
    return None


def upload_csv_in_page(
    page,
    shop_id: str,
    csv_path: Path,
    auth: str,
    api_version: str = API_VERSION,
) -> Dict[str, Any]:
    csv_b64 = base64.b64encode(csv_path.read_bytes()).decode("ascii")

    return page.evaluate(
        """async ({ shopId, apiVersion, fileName, csvB64, auth }) => {
            const url = `https://showmore-api.showmore.cc/api/v1/shop/product_upload?shop_id=${encodeURIComponent(shopId)}`;

            const binary = atob(csvB64);
            const bytes = new Uint8Array(binary.length);
            for (let i = 0; i < binary.length; i++) {
                bytes[i] = binary.charCodeAt(i);
            }

            const blob = new Blob([bytes], { type: "text/csv" });
            const file = new File([blob], fileName, { type: "text/csv" });

            const formData = new FormData();
            formData.append("upload", file);

            const resp = await fetch(url, {
                method: "POST",
                body: formData,
                credentials: "include",
                headers: {
                    "accept": "application/json, text/plain, */*",
                    "api-version": apiVersion,
                    "authorization": auth,
                },
            });

            const text = await resp.text();
            return {
                status: resp.status,
                text,
            };
        }""",
        {
            "shopId": shop_id,
            "apiVersion": api_version,
            "fileName": csv_path.name,
            "csvB64": csv_b64,
            "auth": auth,
        },
    )
    
def get_progress_in_page(
    page,
    shop_id: str,
    api_version: str = API_VERSION,
) -> Dict[str, Any]:
    return page.evaluate(
        """async ({ shopId, apiVersion }) => {
            const url = `https://showmore-api.showmore.cc/api/v1/shop/product_upload/progress?shop_id=${encodeURIComponent(shopId)}`;

            const resp = await fetch(url, {
                method: "GET",
                credentials: "include",
                headers: {
                    "accept": "application/json, text/plain, */*",
                    "api-version": apiVersion,
                },
            });

            const text = await resp.text();
            return {
                status: resp.status,
                text,
            };
        }""",
        {
            "shopId": shop_id,
            "apiVersion": api_version,
        },
    )

def resolve_desc_images(record: Dict[str, Any], assets_dir: Path) -> List[Path]:
    folder_candidates = [
        safe_name(record.get("sale_product_name")),
        safe_name(record.get("erp_product_name")),
    ]
    folder_candidates = [x for x in folder_candidates if x]

    sku = normalize_sku(record.get("erp_sku"))

    for product_name in folder_candidates:
        product_dir = assets_dir / product_name
        if not product_dir.exists():
            continue

        sku_dir = product_dir / sku if sku else None
        common_dir = product_dir / "common"

        # 先找 SKU/desc，再 fallback common/desc
        if sku_dir and (sku_dir / "desc").exists():
            imgs = pick_all_images(sku_dir / "desc")
            if imgs:
                return imgs

        if (common_dir / "desc").exists():
            imgs = pick_all_images(common_dir / "desc")
            if imgs:
                return imgs

    return []

def build_desc_image_html(
    record: Dict[str, Any],
    assets_dir: Path,
    base_url: str,
) -> str:
    desc_imgs = resolve_desc_images(record, assets_dir)
    if not desc_imgs:
        return ""

    parts = []
    for img in desc_imgs:
        img_url = build_public_url(base_url, img, assets_dir)
        parts.append(
            f'<p><img src="{img_url}" style="max-width:100%;height:auto;" /></p>'
        )

    return "".join(parts)
    
def poll_upload_progress(
    page,
    shop_id: str,
    api_version: str = API_VERSION,
    max_attempts: int = 20,
    sleep_sec: float = 2.0,
) -> Dict[str, Any]:
    last_resp: Dict[str, Any] = {"status": -1, "text": ""}

    for i in range(max_attempts):
        resp = get_progress_in_page(
            page=page,
            shop_id=shop_id,
            api_version=api_version,
        )
        last_resp = resp

        print(f"[progress {i+1}/{max_attempts}] status={resp['status']}")
        print(resp["text"][:1000])

        if resp["status"] != 200:
            time.sleep(sleep_sec)
            continue

        try:
            data = json.loads(resp["text"])
        except Exception:
            time.sleep(sleep_sec)
            continue

        # API 成功但沒有進一步資料時，直接視為結束
        if data.get("code") == 20000 and data.get("data") is None:
            return resp

        progress_data = data.get("data")
        if isinstance(progress_data, dict):
            progress = progress_data.get("progress")
            percent = progress_data.get("percent")
            status = str(progress_data.get("status", "")).lower()

            if progress == 100 or percent == 100 or status in {"success", "completed", "done"}:
                return resp

        time.sleep(sleep_sec)

    return last_resp
def resolve_product_image_paths(
    record: Dict[str, Any],
    assets_dir: Path,
) -> Tuple[Path | None, List[Path], Path | None]:
    folder_candidates = [
        safe_name(record.get("sale_product_name")),
        safe_name(record.get("erp_product_name")),
    ]
    folder_candidates = [x for x in folder_candidates if x]

    sku = normalize_sku(record.get("erp_sku"))

    for product_name in folder_candidates:
        product_dir = assets_dir / product_name
        if not product_dir.exists():
            continue

        sku_dir = product_dir / sku if sku else None
        common_dir = product_dir / "common"

        search_roots: List[Path] = []
        if sku_dir and sku_dir.exists():
            search_roots.append(sku_dir)
        if common_dir.exists():
            search_roots.append(common_dir)

        main_img = None
        detail_imgs: List[Path] = []
        style_img = None

        for root in search_roots:
            if main_img is None:
                main_img = pick_first_image(root / "main")
            if not detail_imgs:
                main_folder = root / "main"
                if main_folder.exists():
                    detail_imgs = []
                    for i in [2, 3]:
                        img = pick_nth_image(main_folder, i)
                        if img:
                            detail_imgs.append(img)
            # 先處理 style（只做一次）
            if sku_dir and (sku_dir / "main").exists():
                style_img = pick_main_03(sku_dir / "main")

            # fallback
            if style_img is None:
                for root in search_roots:
                    style_img = pick_first_image(root / "desc")
                    if style_img:
                        break

        if main_img or detail_imgs or style_img:
            return main_img, detail_imgs, style_img

    return None, [], None


def build_image_urls_for_record(
    record: Dict[str, Any],
    assets_dir: Path,
    base_url: str,
) -> Tuple[str, str, str]:
    main_img, detail_imgs, style_img = resolve_product_image_paths(record, assets_dir)

    main_url = build_public_url(base_url, main_img, assets_dir) if main_img else ""
    carousel_urls = " ".join(
        build_public_url(base_url, p, assets_dir) for p in detail_imgs
    ) if detail_imgs else ""
    style_url = build_public_url(base_url, style_img, assets_dir) if style_img else ""

    return main_url, carousel_urls, style_url


# =========================
# config / login
# =========================
def load_login_config() -> dict:
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    for k in ["email", "password", "shop_id"]:
        if k not in cfg or not str(cfg[k]).strip():
            raise ValueError(f"config missing key: {k}")
    return cfg


def auto_login(page, email: str, password: str, timeout_sec: int = 120) -> None:
    page.goto(SHOWMORE_BACKEND, wait_until="domcontentloaded")
    page.wait_for_timeout(2000)

    # 先判斷是否已經登入，不要硬找登入框
    logged_in_indicators = [
        "text=商品",
        "text=訂單",
        "text=上傳格式說明",
        "text=批量上傳",
        "text=登出",
    ]
    for sel in logged_in_indicators:
        try:
            loc = page.locator(sel)
            if loc.count() > 0:
                print("[AUTO] already logged in, skip login")
                return
        except Exception:
            pass

    email_locators = [
        "input[type='email']",
        "input[name='email']",
        "input[autocomplete='email']",
        "input[placeholder*='mail' i]",
        "input[placeholder*='Email' i]",
    ]
    pass_locators = [
        "input[type='password']",
        "input[name='password']",
        "input[autocomplete='current-password']",
        "input[placeholder*='Password' i]",
        "input[placeholder*='密碼']",
    ]

    email_input = None
    for sel in email_locators:
        loc = page.locator(sel)
        if loc.count() > 0:
            email_input = loc.first
            break

    pass_input = None
    for sel in pass_locators:
        loc = page.locator(sel)
        if loc.count() > 0:
            pass_input = loc.first
            break

    # 如果兩個都沒有，印目前網址幫 debug
    if email_input is None or pass_input is None:
        print("[AUTO] current url:", page.url)
        print("[AUTO] page title:", page.title())
        raise RuntimeError("找不到登入表單，可能已登入或頁面不是登入頁")

    email_input.fill(email)
    pass_input.fill(password)

    btn_selectors = [
        "button[type='submit']",
        "button:has-text('登入')",
        "button:has-text('Log in')",
        "button:has-text('Sign in')",
        "text=登入",
    ]
    clicked = False
    for sel in btn_selectors:
        loc = page.locator(sel)
        if loc.count() > 0:
            loc.first.click()
            clicked = True
            break

    if not clicked:
        pass_input.press("Enter")

    page.wait_for_timeout(2000)


def ensure_auth(page, timeout_sec: int = 60) -> str:
    page.add_init_script(
        """() => {
            window.__sm_auth = window.__sm_auth || null;
            const origFetch = window.fetch;
            window.fetch = async function(input, init) {
                try {
                    const headers = (init && init.headers) ? init.headers : null;
                    let auth = null;
                    if (headers) {
                        if (headers instanceof Headers) auth = headers.get('authorization') || headers.get('Authorization');
                        else if (Array.isArray(headers)) {
                            for (const [k, v] of headers) {
                                if (String(k).toLowerCase() === 'authorization') auth = v;
                            }
                        } else {
                            auth = headers['authorization'] || headers['Authorization'];
                        }
                    }
                    if (auth && typeof auth === 'string' && auth.startsWith('Bearer ') && !window.__sm_auth) {
                        window.__sm_auth = auth;
                    }
                } catch (e) {}
                return origFetch.apply(this, arguments);
            };
        }"""
    )

    def read_auth() -> Optional[str]:
        return page.evaluate(
            """() => {
                const candidates = [];
                const tryPush = (v) => {
                    if (!v) return;
                    if (typeof v === 'string' && v.startsWith('Bearer ')) candidates.push(v);
                    try {
                        const obj = JSON.parse(v);
                        const walk = (x) => {
                            if (!x) return;
                            if (typeof x === 'string' && x.startsWith('Bearer ')) candidates.push(x);
                            if (typeof x === 'object') {
                                for (const kk in x) walk(x[kk]);
                            }
                        };
                        walk(obj);
                    } catch(e) {}
                };

                for (let i = 0; i < localStorage.length; i++) {
                    tryPush(localStorage.getItem(localStorage.key(i)));
                }
                for (let i = 0; i < sessionStorage.length; i++) {
                    tryPush(sessionStorage.getItem(sessionStorage.key(i)));
                }

                return candidates[0] || window.__sm_auth || null;
            }"""
        )

    deadline = time.time() + timeout_sec
    auth = read_auth()
    while not auth and time.time() < deadline:
        time.sleep(0.25)
        auth = read_auth()

    if not auth:
        raise RuntimeError("Cannot capture Bearer token within timeout.")

    return auth


# =========================
# CSV
# =========================
def normalize_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def normalize_number(value: Any, default: str = "") -> str:
    s = normalize_text(value)
    return s if s else default


def normalize_sku(value: Any) -> str:
    return "".join(str(value or "").split())


def normalize_temperature_type(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""

    mapping = {
        "normal": "常溫",
        "room": "常溫",
        "ambient": "常溫",
        "chilled": "冷藏",
        "cold": "冷藏",
        "frozen": "冷凍",
        "常溫": "常溫",
        "冷藏": "冷藏",
        "冷凍": "冷凍",
    }

    tokens = [x.strip() for x in raw.replace(",", " ").split() if x.strip()]
    out: List[str] = []
    for token in tokens:
        v = mapping.get(token.lower()) or mapping.get(token)
        if v and v not in out:
            out.append(v)
    return " ".join(out)


def build_description_html(
    record: Dict[str, Any],
    assets_dir: Path,
    base_url: str,
) -> str:
    parts: List[str] = []

    # slogan / feature 僅放「商品簡述」，不寫入商品介紹 HTML
    desc = normalize_text(record.get("product_description"))

    # 追加 desc 圖片到商品介紹
    desc_image_html = build_desc_image_html(record, assets_dir, base_url)
    if desc_image_html:
        parts.append("<hr>")
        parts.append(desc_image_html)
        
    if desc:
        desc_lines = [x.strip() for x in desc.splitlines() if x.strip()]
        for line in desc_lines:
            parts.append(f"<p>{escape_html(line)}</p>")
    return "".join(parts)


def pick_main_03(folder: Path) -> Path | None:
    target = folder / "main_03.jpg"
    return target if target.exists() else None


def map_to_showmore(
    record: Dict[str, Any],
    assets_dir: Path,
    base_url: str,
    main_image_url: str = "",
    carousel_urls: str = "",
    style_image_url: str = "",
) -> Dict[str, str]:
    spec_name_1 = normalize_text(record.get("spec_name_1")) or "規格"
    spec_value_1 = normalize_text(record.get("spec_value_1")) or "預設"

    spec_name_2 = normalize_text(record.get("spec_name_2"))
    spec_value_2 = normalize_text(record.get("spec_value_2"))

    sale_price = normalize_number(record.get("sale_price"), "0")

    slogan = normalize_text(record.get("slogan"))
    feature = normalize_text(record.get("feature"))
    short_desc = "\n".join([x for x in [slogan, feature] if x])

    return {
        "商品名稱*": normalize_text(record.get("erp_product_name")),
        "商品簡述": short_desc,
        "商品介紹": build_description_html(record, assets_dir, base_url),
        "配送限定": normalize_temperature_type(record.get("temperature_type")),
        "商品編號(sku)": normalize_sku(record.get("erp_sku")),

        "第一層樣式名稱": spec_name_1,
        "第一層樣式*": spec_value_1,
        "第二層樣式名稱": spec_name_2,
        "第二層樣式": spec_value_2,
        "第三層樣式名稱": "",
        "第三層樣式": "",

        "原價": normalize_number(record.get("list_price")),
        "售價*": sale_price,
        "成本": normalize_number(record.get("cost_price")),
        "官網庫存*": normalize_number(record.get("stock_qty"), "0"),
        "重量(kg)*": normalize_number(record.get("weight_kg"), "0.1"),
        "VIP價格": "",
        "主要圖片*": main_image_url,
        "廣告圖": style_image_url,
        "商品圖片": carousel_urls,
        "商品樣式圖片": style_image_url,
    }
    
def write_records_to_csv(records: List[Dict[str, Any]], csv_path: Path) -> Path:
    if not records:
        raise ValueError("沒有資料可以寫入 CSV")

    headers: List[str] = []
    seen = set()
    for rec in records:
        for k in rec.keys():
            if k not in seen:
                seen.add(k)
                headers.append(k)

    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        for rec in records:
            row = {h: "" if rec.get(h) is None else str(rec.get(h)) for h in headers}
            writer.writerow(row)

    return csv_path

# =========================
# orchestration
# =========================
def capture_browser_session(email: str, password: str):
    p = sync_playwright().start()
    browser = p.chromium.launch(
        headless=False,
        channel="chrome",
        args=["--disable-blink-features=AutomationControlled"],
    )
    context = browser.new_context()
    page = context.new_page()

    page.goto(SHOWMORE_BACKEND, wait_until="domcontentloaded")
    auto_login(page, email, password)

    # 若有 OTP/驗證碼可在這裡手動完成
    # input("若有 OTP / 驗證碼，請手動完成後按 Enter ...")

    auth = ensure_auth(page)
    return p, browser, context, page, auth


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="從 Google Sheet 讀取資料、組 CSV 並上傳至 Showmore。",
    )
    p.add_argument(
        "--data-start-row",
        type=int,
        default=None,
        help="試算表資料起始列（1-based，須大於 header-row）。預設：腳本內 GSHEET_DATA_START_ROW",
    )
    p.add_argument(
        "--data-row-count",
        type=int,
        default=None,
        help="要讀取的資料筆數；0 表示不限制（讀到表尾）。預設：腳本內 GSHEET_DATA_ROW_COUNT",
    )
    p.add_argument(
        "--header-row",
        type=int,
        default=None,
        help="標題列（1-based）。預設：腳本內 GSHEET_HEADER_ROW",
    )
    return p


def main() -> None:
    args = build_arg_parser().parse_args()

    header_row = (
        args.header_row if args.header_row is not None else GSHEET_HEADER_ROW
    )
    data_start_row = (
        args.data_start_row
        if args.data_start_row is not None
        else GSHEET_DATA_START_ROW
    )
    if args.data_row_count is not None:
        data_row_count = (
            None if args.data_row_count <= 0 else args.data_row_count
        )
    else:
        data_row_count = (
            None if GSHEET_DATA_ROW_COUNT <= 0 else GSHEET_DATA_ROW_COUNT
        )

    cfg = load_login_config()

    print("[1/5] 讀取 Google Sheet ...")
    print(
        f"    header_row={header_row} data_start_row={data_start_row} "
        f"data_row_count={data_row_count!r}"
    )
    records, header_mapping = read_sheet_records(
        credentials_path=str(GSHEET_CRED_PATH),
        spreadsheet_id=GSHEET_SPREADSHEET_ID,
        worksheet_name=GSHEET_WORKSHEET_NAME,
        header_row=header_row,
        data_start_row=data_start_row,
        data_row_count=data_row_count,
        keep_empty_rows=False,
    )

    print(f"讀到 {len(records)} 筆資料")
    print("header mapping:")
    for col_idx, orig, final in header_mapping:
        print(f"  col{col_idx}: {orig!r} -> {final!r}")

    img_base_url = load_img_base_url()

    mapped_records = []
    for rec in records:
        main_image_url, carousel_urls, style_image_url = build_image_urls_for_record(
            rec,
            assets_dir=ASSETS_DIR,
            base_url=img_base_url,
        )

        print("---- image debug ----")
        print("erp_product_name:", rec.get("erp_product_name"))
        print("erp_sku:", rec.get("erp_sku"))
        print("main_image_url:", main_image_url)
        print("carousel_urls:", carousel_urls)
        print("style_image_url:", style_image_url)

        mapped_records.append(
            map_to_showmore(
                rec,
                assets_dir=ASSETS_DIR,
                base_url=img_base_url,
                main_image_url=main_image_url,
                carousel_urls=carousel_urls,
                style_image_url=style_image_url,
            )
        )

    print(f"已轉換為 Showmore 欄位：{len(mapped_records)} 筆")

    print("[2/5] 寫入 CSV ...")
    csv_path = OUTPUT_DIR / f"showmore_upload_{int(time.time())}.csv"
    write_records_to_csv(mapped_records, csv_path)
    print(f"CSV 已輸出：{csv_path}")

    print("[3/5] 登入 Showmore 並抓 Bearer token ...")
    p, browser, context, page, auth = capture_browser_session(
        cfg["email"],
        cfg["password"],
    )
    print("[OK] token captured")

    try:
        print("[4/5] 上傳 CSV 到 Showmore ...")
        resp = upload_csv_in_page(
            page=page,
            shop_id=cfg["shop_id"],
            csv_path=csv_path,
            auth=auth,
        )
        print("status:", resp["status"])
        print("response:", resp["text"][:2000])

        print("[5/5] 查詢 progress ...")
        progress_resp = poll_upload_progress(
            page=page,
            shop_id=cfg["shop_id"],
            max_attempts=20,
            sleep_sec=2.0,
        )
        print("final progress status:", progress_resp["status"])
        print("final progress response:", progress_resp["text"][:2000])

    finally:
        browser.close()
        p.stop()


if __name__ == "__main__":
    main()