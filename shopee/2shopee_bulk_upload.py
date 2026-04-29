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
    s = re.sub(r"/login(?:/.*)?$", "", s)  # 支援你填到 /login/login 的狀況
    s = re.sub(r"/+$", "", s)
    return s


def is_bad_backend_url(url: str) -> bool:
    u = url or ""
    return ("/login" in u) or ("/v2/404" in u) or (re.search(r"/404(?:\b|/)", u) is not None)


def open_mass_upload_modal(page) -> None:
    # 先點 Mass Upload（中英都支援）
    try:
        page.get_by_role("button", name=re.compile(r"(Mass Upload|大量上傳)", re.I)).click()
    except Exception:
        page.get_by_text(re.compile(r"(Mass Upload|大量上傳)", re.I)).click()

    # 用「拖曳上傳區」當成 modal 已開的可靠判斷（比 role=dialog / 標題文字穩）
    drop_area = page.locator("div.ssc-upload-drag").first
    drop_area.wait_for(state="visible", timeout=20_000)


def find_mass_upload_root(page):
    """
    找到 Mass Upload modal 的根容器：
    以拖曳區 div.ssc-upload-drag 當 anchor 往上找，
    找到同時包含：
      - 拖曳區 ssc-upload-drag
      - 真正的 input.ssc-upload-input[type=file]
    """
    # 這個 selector 會抓到「包含 upload-drag 且包含 upload-input」的最外層 div
    root = page.locator(
        "xpath=//div[.//div[contains(@class,'ssc-upload-drag')] and .//input[contains(@class,'ssc-upload-input') and @type='file']]"
    ).first
    root.wait_for(state="attached", timeout=20_000)
    return root


def count_record_rows(root):
    """
    Upload Record 的 DOM 不一定是 <table>，所以做一個比較寬鬆的 row selector：
    - 有 table 就抓 tr
    - 沒 table 就抓常見的 ssc-table row 類別
    """
    candidates = [
        root.locator("css=table tbody tr"),
        root.locator("css=table tr"),
        root.locator("css=div.ssc-table tbody tr"),
        root.locator("css=div.ssc-table tr"),
        root.locator("css=div[class*='table'] tr"),
        # 最後保底：抓所有可能像 row 的容器（避免 0）
        root.locator("xpath=.//*[self::tr or contains(@class,'row') or contains(@class,'Row')]"),
    ]

    best = None
    best_n = -1
    for loc in candidates:
        try:
            n = loc.count()
            if n > best_n:
                best_n = n
                best = loc
        except Exception:
            continue

    if best is None:
        return None, 0
    return best, best_n


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

            page.goto(f"{base}/v2/application/list", wait_until="domcontentloaded")
            try:
                page.wait_for_load_state("networkidle", timeout=30_000)
            except Exception:
                pass

            cur = page.url
            print(f"[DEBUG] after goto url={cur}")
            if is_bad_backend_url(cur):
                page.screenshot(path="out/save_login_failed.png", full_page=True)
                raise RuntimeError(f"Save-login failed (url={cur}). Saved screenshot: out/save_login_failed.png")

            context.storage_state(path=str(args.storage))
            print(f"[OK] saved storage state: {args.storage}")
            print("done")
            return

        # -------------------------
        # Upload flow
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

        # 1) 開 modal
        open_mass_upload_modal(page)

        # 2) 找 modal root（用 upload-drag + upload-input 兩個條件鎖定）
        root = find_mass_upload_root(page)

        # 3) 記錄 Upload Record 現有 rows 數量（可能是 0）
        rows_locator, before = count_record_rows(root)
        print(f"[DEBUG] upload_record_rows(before)={before}")

        # 4) 找真正的 file input（它通常是 hidden，但 set_input_files 仍可用）
        file_input = root.locator("input.ssc-upload-input[type='file']").first
        if file_input.count() == 0:
            # fallback：只要是 type=file
            file_input = root.locator("input[type='file']").first

        if file_input.count() == 0:
            page.screenshot(path="out/no_file_input.png", full_page=True)
            raise RuntimeError("Cannot find <input type='file'> in Mass Upload modal. Saved screenshot: out/no_file_input.png")

        file_input.wait_for(state="attached", timeout=20_000)

        # 5) 直接上傳（不需要點 Select Files，也不需要 expect_file_chooser）
        file_input.set_input_files(str(upload_file))
        print(f"[DEBUG] set_input_files ok: {upload_file.name}")

        # 6) 等「有反應」：rows 增加 或 record 區塊文字更新
        deadline = time.time() + 180
        changed = False
        fname = upload_file.name
        key = (Path(fname).stem[:8]) if len(Path(fname).stem) >= 8 else Path(fname).stem

        while time.time() < deadline and not changed:
            try:
                # 重新抓一次 rows（有些 UI 會整個 table 重建）
                rows_locator, now = count_record_rows(root)
                if now > before:
                    changed = True
                    break

                # 沒新增列也可能更新最上面那列：看 root 的文字裡是否出現 key
                txt = root.inner_text(timeout=2_000)
                if key and key in txt:
                    changed = True
                    break
            except Exception:
                pass
            time.sleep(1.0)

        if not changed:
            page.screenshot(path="out/upload_record_not_changed.png", full_page=True)
            raise RuntimeError("Upload maybe not triggered or record not refreshed within 180s. Saved screenshot: out/upload_record_not_changed.png")

        print("[DEBUG] upload record changed, waiting process status...")

        # 7) 等結果狀態：2/2 或 Download Results（在 root 內找即可）
        try:
            progress_loc = root.get_by_text(re.compile(r"\b\d+/\d+\b")).first
            progress_loc.wait_for(timeout=300_000)
            progress_text = (progress_loc.inner_text(timeout=2_000) or "").strip()
            if not progress_text:
                progress_text = "unknown"
            print(f"[OK] uploaded and status appeared ({progress_text}): {fname}")
        except PWTimeout:
            try:
                root.get_by_text(re.compile(r"(Download Results|下載結果報告|Download)", re.I)).first.wait_for(timeout=120_000)
                print(f"[OK] uploaded and result link appeared: {fname}")
            except PWTimeout:
                page.screenshot(path="out/upload_status_unknown.png", full_page=True)
                print(f"[WARN] uploaded but status not confirmed in time: {fname} (saved out/upload_status_unknown.png)")

        print("done")


if __name__ == "__main__":
    main()
