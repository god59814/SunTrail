import base64
import json
import re
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
from PIL import Image

# ---------------------------------------------------------------------------
# 常數
# ---------------------------------------------------------------------------
CONFIG_PATH = Path(r"config/login_info.json")
# 統一改用專案根目錄的 payload.json（由 momotest/xlsx_to_payload.py 產生）
PAYLOAD_PATH = Path(r"payload.json")
ZIP_PATH = Path(r"out/images.zip")

GOODS_SERVLET_PATH = "/GoodsServlet.do"
TIMEOUT = 60.0
MODE = "multipart"  # "json" or "multipart"
VERIFY_SSL = True


# ---------------------------------------------------------------------------
# 資料結構與設定載入
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class LoginInfo:
    entpID: str
    entpCode: str
    entpPwd: str
    otpBackNo: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "entpID": self.entpID,
            "entpCode": self.entpCode,
            "entpPwd": self.entpPwd,
            "otpBackNo": self.otpBackNo,
        }


def load_config() -> Dict[str, str]:
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    required = ["scm_domain", "entpCode", "entpID", "entpPwd", "otpBackNo"]
    for k in required:
        if k not in cfg or not str(cfg[k]).strip():
            raise ValueError(f"config missing key: {k}")
    return {
        "scm_domain": str(cfg["scm_domain"]).rstrip("/"),
        "entpCode": str(cfg["entpCode"]),
        "entpID": str(cfg["entpID"]),
        "entpPwd": str(cfg["entpPwd"]),
        "otpBackNo": str(cfg["otpBackNo"]),
    }


def load_payload() -> Dict[str, Any]:
    obj = json.loads(PAYLOAD_PATH.read_text(encoding="utf-8"))
    # multipart 模式不要讓 payload.json 自帶 zipFileData，避免後端去解析它
    if MODE == "multipart" and "zipFileData" in obj:
        obj.pop("zipFileData", None)
    return obj


# ---------------------------------------------------------------------------
# ZIP 工具
# ---------------------------------------------------------------------------
def read_zip_b64(zip_path: Path) -> str:
    return base64.b64encode(zip_path.read_bytes()).decode("ascii")


def make_spec_only_zip(src_zip: Path, dst_zip: Path, spec_names: list[str]) -> None:
    if dst_zip.exists():
        dst_zip.unlink()

    with zipfile.ZipFile(src_zip, "r") as zin:
        src_names = set(zin.namelist())
        missing = [n for n in spec_names if n not in src_names]
        if missing:
            raise FileNotFoundError(f"spec images not found in zip: {missing}")

        with zipfile.ZipFile(dst_zip, "w", compression=zipfile.ZIP_STORED) as zout:
            for name in spec_names:
                zout.writestr(name, zin.read(name))

    print(f"[ZIP] created spec-only zip: {dst_zip} (entries={len(spec_names)})")


def add_aliases_to_zip(zip_path: Path, src_name: str, alias_names: list[str]) -> None:
    tmp = zip_path.with_suffix(".tmp.zip")
    if tmp.exists():
        tmp.unlink()

    with zipfile.ZipFile(zip_path, "r") as zin:
        if src_name not in zin.namelist():
            raise FileNotFoundError(f"src_name not found in zip: {src_name}")

        src_bytes = zin.read(src_name)
        existing = set(zin.namelist())

        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_STORED) as zout:
            # copy original entries (STORED)
            for name in zin.namelist():
                zout.writestr(name, zin.read(name))

            # add aliases (same bytes)
            for a in alias_names:
                if a not in existing:
                    zout.writestr(a, src_bytes)

    zip_path.unlink()
    tmp.rename(zip_path)

    print(f"[ZIP] added aliases into {zip_path.name} (STORED)")
    for a in alias_names:
        print(" -", a)


def repack_zip_stored(src_zip: Path, dst_zip: Path) -> None:
    if dst_zip.exists():
        dst_zip.unlink()

    with zipfile.ZipFile(src_zip, "r") as zin, zipfile.ZipFile(
        dst_zip, "w", compression=zipfile.ZIP_STORED
    ) as zout:
        for name in zin.namelist():
            zout.writestr(name, zin.read(name))

    print(f"[ZIP] repacked STORED: {dst_zip.name}")


def print_zip_overview(zip_path: Path, limit: int = 50) -> None:
    print("=" * 80)
    print(f"[ZIP] path={zip_path} exists={zip_path.exists()}")
    if not zip_path.exists():
        print("=" * 80)
        return

    print(f"[ZIP] size={zip_path.stat().st_size/1024:.1f}KB")
    with zipfile.ZipFile(zip_path, "r") as z:
        names = z.namelist()
        print(
            f"[ZIP] entries={len(names)} has_subdirs={any('/' in n for n in names)} has___MACOSX={any(n.startswith('__MACOSX/') for n in names)}"
        )
        print(f"[ZIP] first {min(limit, len(names))} entries:")
        for n in sorted(names)[:limit]:
            info = z.getinfo(n)
            method = {0: "STORED", 8: "DEFLATED"}.get(
                info.compress_type, str(info.compress_type)
            )
            print(f"  - {n} ({info.file_size/1024:.1f}KB) compress={method}")
    print("=" * 80)


# ---------------------------------------------------------------------------
# 規格圖診斷與圖片檢查
# ---------------------------------------------------------------------------
def resolve_zip_entry(names: set[str], req_name: str) -> Optional[str]:
    """依 payload 檔名（可無副檔名）解析出 zip 內實際檔名。"""
    if req_name in names:
        return req_name
    for ext in (".jpg", ".jpeg", ".png", ".JPG", ".JPEG", ".PNG"):
        cand = req_name + ext
        if cand in names:
            return cand
    return None


def diagnose_spec_images(
    send_payload: dict, zip_path: Path, strict: bool = True
) -> None:
    print_zip_overview(zip_path)
    with zipfile.ZipFile(zip_path, "r") as z:
        names = set(z.namelist())

        print("[SPEC] checking payload -> zip mapping ...")
        problems = []

        for si in send_payload.get("sendInfoList", []):
            batch = si.get("batchSupNo", "")
            for item in si.get("singleItemList", []):
                sup_goodsdt = item.get("supGoodsdtCode", "")
                for k in ("specImg1", "specImg2"):
                    req_name = (item.get(k) or "").strip()
                    if not req_name:
                        print(
                            f"[SPEC] batch={batch} item={sup_goodsdt} {k}=<EMPTY> (skip)"
                        )
                        continue

                    found_name = resolve_zip_entry(names, req_name)
                    if not found_name:
                        print(
                            f"[SPEC] batch={batch} item={sup_goodsdt} {k}={req_name} -> NOT FOUND in zip (also tried .jpg/.jpeg/.png)"
                        )
                        problems.append(
                            f"[MISSING] batch={batch} item={sup_goodsdt} {k}={req_name}"
                        )
                        continue

                    b = z.read(found_name)
                    size_kb = len(b) / 1024.0
                    img = Image.open(BytesIO(b))
                    w, h = img.size
                    fmt = (img.format or "").upper()
                    print(
                        f"[SPEC] batch={batch} item={sup_goodsdt} {k}={req_name} -> using={found_name} fmt={fmt} dim={w}x{h} size={size_kb:.1f}KB"
                    )
        print("-" * 80)
        if problems:
            print("[SPEC] RESULT: FAIL")
            for p in problems:
                print(" -", p)
            print("-" * 80)
            if strict:
                raise ValueError("規格圖診斷未通過（上面已列出原因）")
            else:
                print(
                    "[SPEC] strict=False => continue anyway (for server-side experiment)"
                )
        else:
            print("[SPEC] RESULT: PASS")
        print("-" * 80)


# ---------------------------------------------------------------------------
# HTTP 客戶端
# ---------------------------------------------------------------------------
class SCMClient:
    def __init__(
        self,
        base_url: str,
        login: LoginInfo,
        verify_ssl: bool = True,
        timeout: float = 60.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.login = login
        self.verify_ssl = verify_ssl

        self.client = httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(
                timeout, connect=timeout, read=timeout, write=timeout, pool=timeout
            ),
            verify=verify_ssl,
            http2=False,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            headers={"Accept": "application/json", "User-Agent": "SCMClient/1.0"},
        )

    def close(self) -> None:
        self.client.close()

    def _raise_for_error(self, resp: httpx.Response) -> None:
        if resp.status_code >= 400:
            try:
                data = resp.json()
            except Exception:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:500]!r}")
            raise RuntimeError(f"HTTP {resp.status_code}: {data}")

    def post_goodsservlet_json(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        print("=" * 80)
        print("[HTTP] POST json", self.base_url + GOODS_SERVLET_PATH)
        resp = self.client.post(
            GOODS_SERVLET_PATH,
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        print(f"[HTTP] status_code={resp.status_code}")
        print(f"[HTTP] response text(first 500)={resp.text[:500]!r}")
        print("=" * 80)
        self._raise_for_error(resp)
        return resp.json()

    def post_goodsservlet_multipart(
        self, json_value: Dict[str, Any], zip_path: Path
    ) -> Dict[str, Any]:
        json_str = json.dumps(json_value, ensure_ascii=False)
        zip_bytes = zip_path.read_bytes()

        print("=" * 80)
        print("[HTTP] POST multipart", self.base_url + GOODS_SERVLET_PATH)
        print(f"[HTTP] jsonValue chars={len(json_str)}")
        print(
            f"[HTTP] zip_File name={zip_path.name} bytes={len(zip_bytes)} ({len(zip_bytes)/1024:.1f}KB)"
        )
        print(f"[HTTP] VERIFY_SSL={self.verify_ssl} http2=False")
        print("=" * 80)

        files = {
            "jsonValue": (None, json_str),
            "zip_File": (zip_path.name, zip_bytes, "application/zip"),
        }

        try:
            resp = self.client.post(GOODS_SERVLET_PATH, files=files)
        except httpx.HTTPError as e:
            print("[HTTP] EXCEPTION:", repr(e))
            raise

        print(f"[HTTP] status_code={resp.status_code}")
        print(f"[HTTP] response headers={dict(resp.headers)}")
        print(f"[HTTP] response text(first 500)={resp.text[:500]!r}")
        print("=" * 80)

        self._raise_for_error(resp)
        return resp.json()


# ---------------------------------------------------------------------------
# 請求組裝與送出
# ---------------------------------------------------------------------------
def build_report_request(
    do_action: str,
    login: LoginInfo,
    send_payload: Dict[str, Any],
    zip_b64: Optional[str] = None,
) -> Dict[str, Any]:
    req: Dict[str, Any] = {"doAction": do_action, "loginInfo": login.to_dict()}
    req.update(send_payload)
    if zip_b64 is not None:
        req["zipFileData"] = zip_b64
    return req


def safe_post_multipart_then_json(
    client: SCMClient,
    do_action: str,
    login: LoginInfo,
    send_payload: dict,
    zip_path: Path,
) -> Dict[str, Any]:
    req = build_report_request(do_action, login, send_payload, zip_b64=None)
    print("zipFileData_in_jsonValue =", "zipFileData" in req)
    try:
        return client.post_goodsservlet_multipart(req, zip_path)
    except httpx.HTTPError as e:
        print(
            f"[WARN] multipart failed ({type(e).__name__}), fallback to JSON(base64) ..."
        )
        zip_b64 = read_zip_b64(zip_path)
        req2 = build_report_request(do_action, login, send_payload, zip_b64=zip_b64)
        return client.post_goodsservlet_json(req2)


def inspect_image_in_zip(zip_path: Path, name: str) -> None:
    with zipfile.ZipFile(zip_path, "r") as z:
        b = z.read(name)
    img = Image.open(BytesIO(b))
    w, h = img.size
    fmt = (img.format or "").upper()
    mode = img.mode
    size_kb = len(b) / 1024
    print(f"[IMG] {name} fmt={fmt} mode={mode} dim={w}x{h} size={size_kb:.1f}KB")


def print_result_summary(resp_json: Dict[str, Any]) -> None:
    result = resp_json.get("resultInfo") if isinstance(resp_json, dict) else None
    if not isinstance(result, dict):
        print(json.dumps(resp_json, ensure_ascii=False, indent=2))
        return

    print(
        f"resultInfo: totalCnt={result.get('totalCnt')}, successCnt={result.get('successCnt')}, failCnt={result.get('failCnt')}"
    )
    fail_list = result.get("failList")
    if isinstance(fail_list, list) and fail_list:
        print("failList:")
        for line in fail_list:
            print(line)


# ---------------------------------------------------------------------------
# Payload 與除錯工具
# ---------------------------------------------------------------------------
def clone_and_set_specimg1(send_payload: dict, new_name: str) -> dict:
    cp = json.loads(json.dumps(send_payload, ensure_ascii=False))
    for si in cp.get("sendInfoList", []):
        for item in si.get("singleItemList", []):
            item["specImg1"] = new_name
    return cp


def try_specimg1_candidates(
    client: SCMClient,
    login: LoginInfo,
    base_payload: dict,
    zip_path: Path,
    candidates: list[str],
) -> None:
    print("=" * 80)
    print("[TEST] try specImg1 candidates ...")
    for name in candidates:
        test_payload = clone_and_set_specimg1(base_payload, name)
        print("-" * 80)
        print(f"[TEST] specImg1 -> {name}")
        resp = safe_post_multipart_then_json(
            client, "tempReportGoods", login, test_payload, zip_path
        )
        print_result_summary(resp)
    print("=" * 80)


def dump_image_fields(send_payload: dict) -> None:
    pat = re.compile(
        r"(img|image|pic|photo|banner|ad|廣告|賣場|主圖|B1|O)", re.IGNORECASE
    )

    def walk(obj, path=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                p = f"{path}.{k}" if path else k
                if pat.search(str(k)):
                    print(f"[IMGKEY] {p} = {v}")
                walk(v, p)
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                walk(v, f"{path}[{i}]")

    walk(send_payload)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main() -> None:
    cfg = load_config()
    login = LoginInfo(
        entpID=cfg["entpID"],
        entpCode=cfg["entpCode"],
        entpPwd=cfg["entpPwd"],
        otpBackNo=cfg["otpBackNo"],
    )
    base_url = cfg["scm_domain"]

    send_payload = load_payload()
    dump_image_fields(send_payload)

    if "sendInfoList" not in send_payload:
        raise ValueError("payload.json must contain key: sendInfoList")

    # 1) 強制把原 zip 重包成 STORED（不壓縮）
    stored_zip = ZIP_PATH.with_name(ZIP_PATH.stem + "_stored.zip")
    repack_zip_stored(ZIP_PATH, stored_zip)
    # 加入「無副檔名」alias（官方說法：specImg1 不含副檔名）
    # add_aliases_to_zip(stored_zip, "10001_01_001_B.jpg", ["10001_01_001_B"])
    # add_aliases_to_zip(stored_zip, "10001_B1.jpg", ["10001_B1"])
    # add_aliases_to_zip(stored_zip, "10001_O.jpg",  ["10001_O"])

    # 2) 本機診斷：確認 payload 需要的檔名在 stored_zip 裡、且全部 STORED
    diagnose_spec_images(send_payload, stored_zip, strict=False)

    # 3) 送出（只用 stored_zip，不要再用原 ZIP_PATH）
    client = SCMClient(
        base_url=base_url, login=login, verify_ssl=VERIFY_SSL, timeout=TIMEOUT
    )
    try:
        if MODE == "json":
            zip_b64 = read_zip_b64(stored_zip)

            resp1 = client.post_goodsservlet_json(
                build_report_request(
                    "tempReportGoods", login, send_payload, zip_b64=zip_b64
                )
            )
            print("tempReportGoods response:")
            print_result_summary(resp1)

            resp2 = client.post_goodsservlet_json(
                build_report_request(
                    "verifyReportGoods", login, send_payload, zip_b64=zip_b64
                )
            )
            print("verifyReportGoods response:")
            print_result_summary(resp2)

        elif MODE == "multipart":
            inspect_image_in_zip(stored_zip, "10001_B1.jpg")
            inspect_image_in_zip(stored_zip, "10001_O.jpg")
            inspect_image_in_zip(stored_zip, "10001_01_001_B.jpg")

            resp = safe_post_multipart_then_json(
                client,
                "tempReportGoods",
                login,
                send_payload,
                stored_zip,
            )
            print("tempReportGoods response:")
            print_result_summary(resp)

        else:
            raise ValueError("MODE must be 'json' or 'multipart'")
    finally:
        client.close()


if __name__ == "__main__":
    main()
