import argparse
import hmac
import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

import httpx


CONFIG_PATH = Path("config/login_info.json")

# Logistics APIs
OUTBOUND_LIST_PATH = "/v2/providers/marketplace_openapi/apis/api/v2/vendor/shipping-place/outbound"
RETURN_CENTER_LIST_PATH_TMPL = "/v2/providers/openapi/apis/api/v5/vendors/{vendorId}/returnShippingCenters"


@dataclass(frozen=True)
class CoupangConfig:
    access_key: str
    secret_key: str
    vendor_id: str
    base_url: str


def load_config() -> CoupangConfig:
    obj = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    access_key = str(obj.get("access_key", "")).strip()
    secret_key = str(obj.get("secret_key", "")).strip()
    vendor_id = str(obj.get("vendor_id", "")).strip()
    base_url = str(obj.get("base_url", "https://api-gateway.coupang.com")).strip()

    if not access_key or not secret_key:
        raise ValueError("config/login_info.json missing access_key or secret_key")
    if not vendor_id:
        raise ValueError("config/login_info.json missing vendor_id")

    return CoupangConfig(access_key=access_key, secret_key=secret_key, vendor_id=vendor_id, base_url=base_url)


def utc_signed_date() -> str:
    # Coupang official examples use yymmddTHHMMSSZ (2-digit year)
    return time.strftime("%y%m%dT%H%M%SZ", time.gmtime())




def build_authorization(method: str, path: str, query_str: str, access_key: str, secret_key: str) -> str:
    signed_date = utc_signed_date()
    message = f"{signed_date}{method.upper()}{path}{query_str}"

    signature = hmac.new(
        secret_key.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return (
        "CEA algorithm=HmacSHA256, "
        f"access-key={access_key}, "
        f"signed-date={signed_date}, "
        f"signature={signature}"
    )


def http_get(cfg: CoupangConfig, market: str, path: str, query_params: Dict[str, Any], timeout: float = 30.0) -> Any:
    # signature message uses query WITHOUT leading "?"
    query_str = ""
    if query_params:
        query_str = urlencode(query_params, doseq=True)

    auth = build_authorization("GET", path, query_str, cfg.access_key, cfg.secret_key)

    headers = {
        "Authorization": auth,
        "Content-Type": "application/json;charset=UTF-8",
        "X-MARKET": market,
        "X-Requested-By": cfg.vendor_id,
    }

    url = f"{cfg.base_url}{path}"
    if query_str:
        url += "?" + query_str

    with httpx.Client(timeout=timeout) as client:
        r = client.get(url, headers=headers)
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text}")
        return r.json()



def list_outbound_shipping_places(cfg: CoupangConfig, market: str, page_size: int = 50) -> List[Dict[str, Any]]:
    all_rows: List[Dict[str, Any]] = []
    page_num = 1

    while True:
        data = http_get(
            cfg=cfg,
            market=market,
            path=OUTBOUND_LIST_PATH,
            query_params={"pageSize": page_size, "pageNum": page_num},
        )

        content = data.get("content") or []
        pagination = data.get("pagination") or {}
        all_rows.extend([x for x in content if isinstance(x, dict)])

        total_pages = int(pagination.get("totalPages") or page_num)
        if page_num >= total_pages:
            break
        page_num += 1

    return all_rows


def list_return_centers(cfg: CoupangConfig, market: str, page_size: int = 50) -> List[Dict[str, Any]]:
    all_rows: List[Dict[str, Any]] = []
    page_num = 1
    path = RETURN_CENTER_LIST_PATH_TMPL.format(vendorId=cfg.vendor_id)

    while True:
        resp = http_get(
            cfg=cfg,
            market=market,
            path=path,
            query_params={"pageNum": page_num, "pageSize": page_size},
        )

        # 常見回傳格式：{"code":200,"message":"SUCCESS","data":{"content":[...], "pagination":{...}}}
        data = resp.get("data") if isinstance(resp, dict) else None
        content: List[Dict[str, Any]] = []

        if isinstance(data, dict) and isinstance(data.get("content"), list):
            content = [x for x in data["content"] if isinstance(x, dict)]
        elif isinstance(resp, dict) and isinstance(resp.get("content"), list):
            content = [x for x in resp["content"] if isinstance(x, dict)]
        elif isinstance(resp, dict) and isinstance(resp.get("data"), list):
            content = [x for x in resp["data"] if isinstance(x, dict)]
        elif isinstance(resp, list):
            content = [x for x in resp if isinstance(x, dict)]

        all_rows.extend(content)

        # pagination：有就用，沒有就用筆數判斷
        pagination = data.get("pagination") if isinstance(data, dict) else None
        if isinstance(pagination, dict) and pagination.get("totalPages") is not None:
            total_pages = int(pagination.get("totalPages") or page_num)
            if page_num >= total_pages:
                break
        else:
            if len(content) < page_size:
                break

        page_num += 1

    return all_rows

def _safe_get(d: Dict[str, Any], *keys: str, default: str = "") -> Any:
    cur: Any = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
    return cur if cur is not None else default


def print_outbound_places(rows: List[Dict[str, Any]]) -> None:
    print("=" * 80)
    print(f"[OUTBOUND SHIPPING PLACES] count={len(rows)}")
    print("=" * 80)

    for i, r in enumerate(rows, start=1):
        code = r.get("outboundShippingPlaceCode")
        name = r.get("shippingPlaceName")
        usable = r.get("usable")
        create_date = r.get("createDate")

        addr0 = (r.get("placeAddresses") or [{}])[0] if isinstance(r.get("placeAddresses"), list) else {}
        addr = _safe_get(addr0, "returnAddress", default="")
        addr_detail = _safe_get(addr0, "returnAddressDetail", default="")
        zip_code = _safe_get(addr0, "returnZipCode", default="")
        phone = _safe_get(addr0, "companyContactNumber", default="")

        remote0 = (r.get("remoteInfos") or [{}])[0] if isinstance(r.get("remoteInfos"), list) else {}
        remote_info_id = _safe_get(remote0, "remoteInfoId", default="")
        delivery_code = _safe_get(remote0, "deliveryCode", default="")
        jeju_fee = remote0.get("jeju") if isinstance(remote0, dict) else ""
        not_jeju_fee = remote0.get("notJeju") if isinstance(remote0, dict) else ""

        print(f"{i:02d}. code={code}  name={name}  usable={usable}  createDate={create_date}")
        print(f"    zip={zip_code}  phone={phone}")
        print(f"    addr={addr} {addr_detail}".rstrip())
        if remote_info_id or delivery_code:
            print(f"    remoteInfoId={remote_info_id}  deliveryCode={delivery_code}  jejuFee={jeju_fee}  notJejuFee={not_jeju_fee}")
        print("-" * 80)

def print_return_centers(rows: List[Dict[str, Any]]) -> None:
    print("=" * 80)
    print(f"[RETURN SHIPPING CENTERS] count={len(rows)}")
    print("=" * 80)

    for i, r in enumerate(rows, start=1):
        code = r.get("returnCenterCode")
        name = r.get("shippingPlaceName")
        deliver_code = r.get("deliverCode")
        deliver_name = r.get("deliverName")
        status = r.get("goodsflowStatus")
        usable = r.get("usable")
        created = r.get("createdAt")
        err = r.get("errorMessage")

        # placeAddresses 通常是 list
        addr0 = (r.get("placeAddresses") or [{}])[0] if isinstance(r.get("placeAddresses"), list) else {}
        addr = _safe_get(addr0, "returnAddress", default="")
        addr_detail = _safe_get(addr0, "returnAddressDetail", default="")
        zip_code = _safe_get(addr0, "returnZipCode", default="")
        phone = _safe_get(addr0, "companyContactNumber", default="")

        print(f"{i:02d}. returnCenterCode={code}  name={name}  usable={usable}  status={status}  createAt={created}")
        print(f"    deliver={deliver_name}({deliver_code})")
        print(f"    zip={zip_code}  phone={phone}")
        print(f"    addr={addr} {addr_detail}".rstrip())
        if err:
            print(f"    errorMessage={err}")
        print("-" * 80)

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="TW", help="Market header, e.g. TW")
    ap.add_argument("--page-size", type=int, default=50, help="Max 50 per API spec")
    args = ap.parse_args()

    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Missing {CONFIG_PATH.resolve()}")

    cfg = load_config()
    market = str(args.market).strip().upper()
    page_size = int(args.page_size)

    outbound = list_outbound_shipping_places(cfg, market=market, page_size=page_size)
    returns = list_return_centers(cfg, market=market, page_size=page_size)

    print_outbound_places(outbound)
    print_return_centers(returns)

if __name__ == "__main__":
    main()
