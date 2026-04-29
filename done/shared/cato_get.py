import argparse
import csv
import hmac
import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import httpx

from work_paths import work_root_from


_WORK_ROOT = work_root_from(Path(__file__))
CONFIG_PATH = _WORK_ROOT / "coupang商城" / "config" / "login_info.json"
OUT_DIR = _WORK_ROOT / "coupang商城" / "results"
OUT_CSV = OUT_DIR / "category_availability.csv"

# 查看品類列表 (Display Categories)
PATH = "/v2/providers/seller_api/apis/api/v1/marketplace/meta/display-categories"


@dataclass(frozen=True)
class CoupangConfig:
    access_key: str
    secret_key: str
    vendor_id: str
    base_url: str


def load_config(path: Path = CONFIG_PATH) -> CoupangConfig:
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path.resolve()}")

    obj = json.loads(path.read_text(encoding="utf-8"))
    access_key = str(obj.get("access_key", "")).strip()
    secret_key = str(obj.get("secret_key", "")).strip()
    vendor_id = str(obj.get("vendor_id", "")).strip()
    base_url = str(obj.get("base_url", "https://api-gateway.coupang.com")).strip()

    if not access_key or not secret_key:
        raise ValueError("config/login_info.json missing access_key or secret_key")

    return CoupangConfig(
        access_key=access_key,
        secret_key=secret_key,
        vendor_id=vendor_id,
        base_url=base_url,
    )


def utc_signed_date() -> str:
    # yyMMdd'T'HHmmss'Z'
    return time.strftime("%y%m%dT%H%M%SZ", time.gmtime())


def build_authorization(
    method: str,
    path: str,
    query: str,
    access_key: str,
    secret_key: str,
) -> str:
    signed_date = utc_signed_date()
    msg = f"{signed_date}{method.upper()}{path}{query}"
    signature = hmac.new(
        secret_key.encode("utf-8"),
        msg.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return (
        "CEA algorithm=HmacSHA256, "
        f"access-key={access_key}, "
        f"signed-date={signed_date}, "
        f"signature={signature}"
    )


def safe_json_loads(text: str) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(text)
    except Exception:
        return None


def extract_category_count(payload: Dict[str, Any]) -> Optional[int]:
    """
    盡量從回傳 JSON 抓出品類筆數。
    常見是 payload['data'] 為 list；若不是，就回傳 None。
    """
    data = payload.get("data")
    if isinstance(data, list):
        return len(data)
    return None


def summarize_error(payload: Optional[Dict[str, Any]], raw_text: str) -> str:
    """
    優先從 JSON 裡找錯誤訊息，否則截取 raw_text 前 200 字。
    """
    if isinstance(payload, dict):
        for k in ("message", "error", "errorMessage", "msg"):
            v = payload.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()[:200]
        dumped = json.dumps(payload, ensure_ascii=False)
        return dumped[:200]
    return raw_text.strip().replace("\n", " ")[:200]


def call_display_categories(
    cfg: CoupangConfig,
    market: str,
    timeout: float = 30.0,
) -> Tuple[int, str]:
    method = "GET"
    query = ""

    auth = build_authorization(
        method=method,
        path=PATH,
        query=query,
        access_key=cfg.access_key,
        secret_key=cfg.secret_key,
    )

    headers = {
        "Authorization": auth,
        "Content-Type": "application/json;charset=UTF-8",
        "X-MARKET": market,  # "TW" or "KR"
    }
    if cfg.vendor_id:
        headers["X-Requested-By"] = cfg.vendor_id

    url = f"{cfg.base_url}{PATH}"
    with httpx.Client(timeout=timeout) as client:
        r = client.get(url, headers=headers)
        return r.status_code, r.text


def write_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "market",
        "usable",
        "http_status",
        "category_count",
        "summary",
        "saved_json",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in rows:
            w.writerow(row)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch Coupang display categories")
    parser.add_argument(
        "--config",
        default=str(CONFIG_PATH),
        help="Path to login_info.json",
    )
    parser.add_argument(
        "--out-dir",
        default=str(OUT_DIR),
        help="Output directory for JSON and CSV",
    )
    args = parser.parse_args()

    config_path = Path(args.config).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_csv = out_dir / "category_availability.csv"

    cfg = load_config(config_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []

    for market in ("TW", "KR"):
        status, text = call_display_categories(cfg, market=market, timeout=30.0)
        ok = 200 <= status < 300

        payload = safe_json_loads(text)
        category_count = extract_category_count(payload) if (ok and payload) else None

        saved_json_path = ""
        if payload is not None:
            saved_json_file = out_dir / f"display_categories_{market}.json"
            saved_json_file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            saved_json_path = str(saved_json_file)

        summary = ""
        if ok:
            summary = "success" + (
                f", categories={category_count}" if category_count is not None else ""
            )
        else:
            summary = summarize_error(payload, text)

        rows.append(
            {
                "market": market,
                "usable": "YES" if ok else "NO",
                "http_status": status,
                "category_count": category_count if category_count is not None else "",
                "summary": summary,
                "saved_json": saved_json_path,
            }
        )

    write_csv(rows, out_csv)

    print(f"[OK] Wrote CSV: {out_csv.resolve()}")
    for r in rows:
        print(
            f"- {r['market']}: {r['usable']} (HTTP {r['http_status']}) {r['summary']}"
        )


if __name__ == "__main__":
    main()
