import argparse
import json
import re
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout


def load_cfg(p: Path) -> dict:
    cfg = json.loads(p.read_text(encoding="utf-8"))
    for k in ["base_url", "username", "password"]:
        if k not in cfg or not str(cfg[k]).strip():
            raise ValueError(f"config missing key: {k}")
    return cfg


def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def normalize_base_url(raw: str) -> str:
    s = str(raw).strip()
    s = re.sub(r"/+$", "", s)
    # 你可能填成 https://e-procurement.shopee.com/login 或 /login/login
    s = re.sub(r"/login(?:/.*)?$", "", s)
    s = re.sub(r"/+$", "", s)
    return s


def is_bad_backend_url(url: str) -> bool:
    u = url or ""
    return ("/login" in u) or ("/v2/404" in u) or (re.search(r"/404(?:\b|/)", u) is not None)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", type=Path, default=Path("config/login_info.json"))
    ap.add_argument("--file", type=Path, required=True, help="要上傳的 xlsx 檔案路徑")
    ap.add_argument("--storage", type=Path, default=Path("storage_state.json"), help="登入狀態保存檔")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--timeout-ms", type=int, default=60_000)
    ap.add_argument("--save-login", action="store_true")
    args = ap.parse_args()

    cfg = load_cfg(args.config)

    base = normalize_base_url(cfg["base_url"])
    if not base.startswith("http"):
        raise ValueError(f"base_url looks invalid: {cfg['base_url']}")
    print(f"[DEBUG] base_url(normalized) = {base}")

    upload_file = args.file.resolve()
    if not upload_file.exists():
        raise FileNotFoundError(upload_file)

    ensure_dir(Path("out"))

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless)
        need_save_login = args.save_login or (not args.storage.exists())

        if need_save_login:
            context = browser.new_context(accept_downloads=True)
        else:
            context = browser.new_context(storage_state=str(args.storage), accept_downloads=True)

        page = context.new_page()
        page.set_default_timeout(args.timeout_ms)

        # -------------------------
        # Save-login flow
        # -------------------------
        if need_save_login:
            # 先到登入入口（用 /login 或 /login/login 都行，這裡走 /login）
            page.goto(f"{base}/login", wait_until="domcontentloaded")

            # 盡量點 Supplier Staff（點不到就算了）
            for _ in range(3):
                try:
                    page.get_by_role("button", name=re.compile(r"Supplier Staff", re.I)).click()
                    break
                except Exception:
                    pass
                try:
                    page.get_by_text(re.compile(r"Supplier Staff", re.I)).click()
                    break
                except Exception:
                    pass
                time.sleep(0.5)

            # 嘗試填帳密（失敗沒關係）
            try:
                u = page.locator(
                    "input[type='text'], input[type='email'], input[name*='user' i], "
                    "input[placeholder*='帳號'], input[placeholder*='Email'], input[placeholder*='Username']"
                ).first
                if u.count() > 0:
                    u.fill(str(cfg["username"]))

                pw = page.locator(
                    "input[type='password'], input[name*='pass' i], input[placeholder*='密碼']"
                ).first
                if pw.count() > 0:
                    pw.fill(str(cfg["password"]))

                btn = page.get_by_role("button", name=re.compile(r"(登入|Login|Sign in)", re.I))
                if btn.count() > 0:
                    btn.first.click()
            except Exception:
                pass

            print("[ACTION] 請在瀏覽器手動完成登入（含 CAPTCHA/OTP）。")
            print("[ACTION] 完成後不要關閉瀏覽器，回來按 Enter。")
            input("[ACTION] 按 Enter 後我會用同一個視窗導到 /v2/application/list 並保存 session...")

            # 直接用同一個 page 去後台頁（不要靠 context pages）
            page.goto(f"{base}/v2/application/list", wait_until="domcontentloaded")
            try:
                page.wait_for_load_state("networkidle", timeout=30_000)
            except Exception:
                pass

            cur = page.url
            print(f"[DEBUG] after goto url={cur}")

            if is_bad_backend_url(cur):
                page.screenshot(path="out/save_login_failed.png", full_page=True)
                raise RuntimeError(
                    f"Save-login failed: cannot reach backend page (url={cur}). "
                    "Saved screenshot: out/save_login_failed.png"
                )

            # 用 UI 字樣確認（中英都支援）
            try:
                page.get_by_role("button", name=re.compile(r"(Mass Upload|大量上傳)", re.I)).wait_for(timeout=15_000)
            except Exception:
                try:
                    page.get_by_text(re.compile(r"(Product Listing Request|商品申請)", re.I)).wait_for(timeout=15_000)
                except Exception:
                    page.screenshot(path="out/save_login_ui_not_ready.png", full_page=True)
                    raise RuntimeError(
                        f"Save-login failed: reached url but UI not ready (url={cur}). "
                        "Saved screenshot: out/save_login_ui_not_ready.png"
                    )

            context.storage_state(path=str(args.storage))
            print(f"[OK] saved storage state: {args.storage}")
            print("done")
            return

        # -------------------------
        # Upload flow (storage exists)
        # -------------------------
        page.goto(f"{base}/v2/application/list", wait_until="domcontentloaded")
        try:
            page.wait_for_load_state("networkidle", timeout=30_000)
        except Exception:
            pass

        cur = page.url
        if is_bad_backend_url(cur):
            page.screenshot(path="out/not_logged_in.png", full_page=True)
            raise RuntimeError(f"Not logged in (url={cur}). Saved screenshot: out/not_logged_in.png")

        # 1) 開 Mass Upload modal
        try:
            page.get_by_role("button", name=re.compile(r"(Mass Upload|大量上傳)", re.I)).click()
        except Exception:
            page.get_by_text(re.compile(r"(Mass Upload|大量上傳)", re.I)).click()

        # 2) 等彈窗標題出現（不要靠 role=dialog）
        #    你的 UI 是英文 "Mass Upload"，也同時支援中文
        modal_title = page.get_by_text(re.compile(r"^(Mass Upload|大量上傳)$", re.I)).first
        modal_title.wait_for(timeout=20_000)

        # 3) 從標題往上找「彈窗容器」
        #    這裡用 XPath ancestor 找最近的容器，常見 class 會有 modal/dialog/mask 等字眼
        dialog = modal_title.locator(
            "xpath=ancestor::*[contains(@class,'modal') or contains(@class,'dialog') or contains(@class,'Modal') "
            "or contains(@class,'Dialog') or contains(@class,'ssc') or contains(@class,'ant')][1]"
        )

        # 如果上面抓不到（某些站不靠 class），再退一步：往上找有 Upload Record 的區塊
        if dialog.count() == 0:
            dialog = page.locator("xpath=//*[.//text()[contains(., 'Upload Record') or contains(., '上傳記錄')]][1]")

        dialog.first.wait_for(state="attached", timeout=20_000)
        dialog = dialog.first


        # 3) 記錄目前 Upload Record 的列數（之後用來判斷是否新增一筆）
        rows = dialog.locator("table tbody tr")
        before = rows.count()
        print(f"[DEBUG] upload_record_rows(before)={before}")

        # 4) 找 input[type=file]（只要 attached，不要等 visible）
        # 4) 用 file chooser 上傳（不用找 input[type=file]）
        #    你截圖內有 "Select Files" 連結
        try:
            with page.expect_file_chooser(timeout=20_000) as fc:
                # 優先點 dialog 裡的 Select Files（英文/中文都試）
                try:
                    dialog.get_by_text(re.compile(r"Select Files|選擇檔案", re.I)).click()
                except Exception:
                    # 有時是 link 或 span，退一步用全頁點
                    page.get_by_text(re.compile(r"Select Files|選擇檔案", re.I)).click()

            chooser = fc.value
            chooser.set_files(str(upload_file))
            print(f"[DEBUG] file chooser set_files ok: {upload_file.name}")
        except PWTimeout:
            page.screenshot(path="out/no_file_chooser.png", full_page=True)
            raise RuntimeError(
                "Cannot open file chooser by clicking 'Select Files'. "
                "Saved screenshot: out/no_file_chooser.png"
            )


        print(f"[DEBUG] set_input_files ok: {upload_file.name}")

        # 6) 等待「有反應」：列數變多 OR 第一列文字變動（避免檔名截斷/重複 strict mode）
        deadline = time.time() + 180  # 3 minutes
        changed = False

        # 用一個短 key（避免截斷），但不拿它做 strict selector
        fname = upload_file.name
        key = fname[:8] if len(fname) >= 8 else fname

        while time.time() < deadline and not changed:
            try:
                now = rows.count()
                if now > before:
                    changed = True
                    break

                # 有些情況不新增列，而是更新最上面那列
                if rows.count() > 0:
                    first_text = rows.first.inner_text(timeout=2_000)
                    if key in first_text:
                        changed = True
                        break
            except Exception:
                pass

            time.sleep(1.0)

        if not changed:
            page.screenshot(path="out/upload_record_not_changed.png", full_page=True)
            raise RuntimeError(
                "Upload maybe not triggered or record not refreshed within 180s. "
                "Saved screenshot: out/upload_record_not_changed.png"
            )

        print("[DEBUG] upload record changed, waiting process status...")

        # 7) 只看最新列（通常是第一列）等待狀態出現
        newest = rows.first
        try:
            newest.get_by_text(re.compile(r"\b\d+/\d+\b")).wait_for(timeout=300_000)
            print(f"[OK] uploaded and status appeared (x/x): {fname}")
        except PWTimeout:
            # 也可能是 Download Results
            try:
                newest.get_by_text(re.compile(r"(Download Results|下載結果報告|Download)", re.I)).wait_for(timeout=120_000)
                print(f"[OK] uploaded and result link appeared: {fname}")
            except PWTimeout:
                page.screenshot(path="out/upload_status_unknown.png", full_page=True)
                print(f"[WARN] uploaded but status not confirmed in time: {fname} (saved out/upload_status_unknown.png)")

        print("done")

if __name__ == "__main__":
    main()
