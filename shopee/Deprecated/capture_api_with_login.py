import argparse
import json
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright


def load_login(cfg_path: Path) -> dict:
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    for k in ("base_url", "username", "password"):
        if k not in cfg or not str(cfg[k]).strip():
            raise ValueError(f"config missing key: {k}")

    acct = str(cfg.get("account_type", "supplier")).strip().lower()
    if acct not in ("supplier", "shopee"):
        raise ValueError("account_type must be 'supplier' or 'shopee'")
    cfg["account_type"] = acct
    return cfg


def same_origin(url: str, base_origin: str) -> bool:
    try:
        u = urlparse(url)
        return f"{u.scheme}://{u.netloc}" == base_origin
    except Exception:
        return False


def is_api(resource_type: str, url: str) -> bool:
    if resource_type in ("xhr", "fetch"):
        return True
    return "/api/" in url or "/v" in url


def handle_account_type_selection(page, account_type: str) -> None:
    supplier_btn = page.get_by_role("button", name="Supplier Staff")
    shopee_btn = page.get_by_role("button", name="Shopee Staff")

    # 有出現才處理（避免在其他頁誤點）
    if supplier_btn.count() > 0 or shopee_btn.count() > 0:
        if account_type == "supplier" and supplier_btn.count() > 0:
            supplier_btn.first.click()
        elif account_type == "shopee" and shopee_btn.count() > 0:
            shopee_btn.first.click()

        # 點完通常會導向下一頁（SSO/帳密頁）
        page.wait_for_load_state("domcontentloaded")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--login-cfg", default="config/login_info.json")
    p.add_argument("--target-url", required=True, help="After login, go to this page to capture APIs")
    p.add_argument("--out", default="out_capture", help="Output directory")
    p.add_argument("--duration", type=int, default=60, help="Capture seconds after load")
    p.add_argument("--headless", action="store_true")
    p.add_argument("--only-same-origin", action="store_true")
    p.add_argument("--storage-state", default="storage_state.json", help="Save/load login session state")
    p.add_argument("--force-login", action="store_true", help="Ignore saved session and login again")
    args = p.parse_args()

    login_cfg = load_login(Path(args.login_cfg))
    base_url = str(login_cfg["base_url"]).rstrip("/")
    username = str(login_cfg["username"])
    password = str(login_cfg["password"])
    account_type = str(login_cfg["account_type"])

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    all_path = out_dir / "all_requests.jsonl"
    api_path = out_dir / "api_calls.jsonl"
    summary_path = out_dir / "api_calls_summary.json"

    storage_state_path = Path(args.storage_state)

    summary = {}

    def add_summary(method: str, url: str, status: int | None):
        key = f"{method} {url}"
        ent = summary.get(key) or {"count": 0, "statuses": {}}
        ent["count"] += 1
        if status is not None:
            ent["statuses"][str(status)] = ent["statuses"].get(str(status), 0) + 1
        summary[key] = ent

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=args.headless)

        context_kwargs = {}
        if storage_state_path.exists() and not args.force_login:
            context_kwargs["storage_state"] = str(storage_state_path)

        context = browser.new_context(**context_kwargs)
        page = context.new_page()

        # ---- network capture ----
        try:
            pu = urlparse(base_url)
            base_origin = f"{pu.scheme}://{pu.netloc}"
        except Exception:
            base_origin = ""

        req_info = {}
        all_f = all_path.open("w", encoding="utf-8")
        api_f = api_path.open("w", encoding="utf-8")
        is_stopping = False

        def safe_write(f, line: str):
            try:
                if not is_stopping and (not f.closed):
                    f.write(line)
            except Exception:
                pass

        def on_request(request):
            nonlocal is_stopping
            if is_stopping:
                return
            try:
                url = request.url
                if args.only_same_origin and base_origin and not same_origin(url, base_origin):
                    return

                rtype = request.resource_type
                method = request.method
                headers = request.headers
                try:
                    post_data = request.post_data
                except Exception:
                    post_data = None

                rec = {
                    "ts": time.time(),
                    "type": "request",
                    "resource_type": rtype,
                    "method": method,
                    "url": url,
                    "headers": headers,
                    "post_data": post_data,
                }
                req_info[request] = {"resource_type": rtype, "method": method, "url": url}

                safe_write(all_f, json.dumps(rec, ensure_ascii=False) + "\n")

                if is_api(rtype, url):
                    safe_write(api_f, json.dumps(rec, ensure_ascii=False) + "\n")
                    add_summary(method, url, None)
            except Exception:
                return

        def on_response(response):
            nonlocal is_stopping
            if is_stopping:
                return
            try:
                request = response.request
                base = req_info.get(request) or {}
                url = response.url
                if args.only_same_origin and base_origin and not same_origin(url, base_origin):
                    return

                status = response.status
                headers = response.headers
                body_text = None
                ctype = headers.get("content-type", "")

                try:
                    if "application/json" in ctype or ctype.startswith("text/"):
                        body_text = response.text()
                        if body_text and len(body_text) > 200000:
                            body_text = body_text[:200000] + "\n...[truncated]"
                except Exception:
                    body_text = None

                rec = {
                    "ts": time.time(),
                    "type": "response",
                    "resource_type": base.get("resource_type"),
                    "method": base.get("method"),
                    "url": url,
                    "status": status,
                    "headers": headers,
                    "body": body_text,
                }

                safe_write(all_f, json.dumps(rec, ensure_ascii=False) + "\n")

                if is_api(base.get("resource_type") or "", url):
                    safe_write(api_f, json.dumps(rec, ensure_ascii=False) + "\n")
                    add_summary(base.get("method") or "GET", url, status)
            except Exception:
                return

        page.on("request", on_request)
        page.on("response", on_response)

        # ---- login helpers ----
        def is_on_account_type_page() -> bool:
            return (
                page.get_by_role("button", name="Supplier Staff").count() > 0
                or page.get_by_role("button", name="Shopee Staff").count() > 0
            )

        def is_on_login_form() -> bool:
            try:
                return page.locator('input[type="password"]').count() > 0
            except Exception:
                return False

        def is_logged_in() -> bool:
            # 不要只靠 URL，改用頁面特徵判斷
            if is_on_account_type_page():
                return False
            if is_on_login_form():
                return False
            # 這裡代表至少不在登入頁/分流頁
            return True

        def first_existing(sel_list):
            for s in sel_list:
                try:
                    if page.locator(s).count() > 0:
                        return s
                except Exception:
                    pass
            return None

        try:
            # 1) go base_url
            page.goto(base_url, wait_until="domcontentloaded")
            page.wait_for_timeout(800)

            # 2) 先處理身分選擇頁（關鍵）
            handle_account_type_selection(page, account_type)
            page.wait_for_timeout(800)

            # 3) 若還沒登入，嘗試自動填表登入（SSO/OTP 可能要你人工做一次）
            if args.force_login or (not is_logged_in()):
                # 可能又被導回分流頁，再處理一次
                handle_account_type_selection(page, account_type)
                page.wait_for_timeout(800)

                user_sel_candidates = [
                    'input[name="username"]',
                    'input[name="email"]',
                    'input[type="email"]',
                    'input[autocomplete="username"]',
                    'input[placeholder*="Email"]',
                    'input[placeholder*="email"]',
                    'input[placeholder*="帳號"]',
                    'input[placeholder*="信箱"]',
                ]
                pass_sel_candidates = [
                    'input[name="password"]',
                    'input[type="password"]',
                    'input[autocomplete="current-password"]',
                    'input[placeholder*="Password"]',
                    'input[placeholder*="password"]',
                    'input[placeholder*="密碼"]',
                ]

                user_sel = first_existing(user_sel_candidates)
                pass_sel = first_existing(pass_sel_candidates)

                if not user_sel or not pass_sel:
                    raise RuntimeError(
                        "Cannot find login inputs on current page. "
                        "Likely SSO/OTP step or different selectors. "
                        "Run with --headless=false and complete SSO/OTP once, then reuse storage_state."
                    )

                page.fill(user_sel, username)
                page.fill(pass_sel, password)

                if page.locator('button[type="submit"]').count() > 0:
                    page.click('button[type="submit"]')
                else:
                    page.press(pass_sel, "Enter")

                page.wait_for_load_state("networkidle", timeout=120000)

            # 4) Save session for next time
            context.storage_state(path=str(storage_state_path))

            # 5) Go target and capture
            page.goto(args.target_url, wait_until="networkidle")
            time.sleep(args.duration)

            summary_path.write_text(
                json.dumps(summary, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        finally:
            is_stopping = True
            try:
                context.close()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass
            try:
                api_f.close()
            except Exception:
                pass
            try:
                all_f.close()
            except Exception:
                pass

    print(f"Saved: {all_path}")
    print(f"Saved: {api_path}")
    print(f"Saved: {summary_path}")
    print(f"Saved session: {storage_state_path}")


if __name__ == "__main__":
    main()
