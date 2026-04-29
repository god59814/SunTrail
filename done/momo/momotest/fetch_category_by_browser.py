from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


TARGET_URL = "https://scm.momoshop.com.tw/D1101Servlet.do"
CATEGORY_API_URL = "https://scm.momoshop.com.tw/api/queryEcCategory.scm"


def _fetch_category_raw(page) -> str:
    return page.evaluate(
        """
        async ({ apiUrl }) => {
            const resp = await fetch(apiUrl, {
                method: "POST",
                credentials: "include",
                headers: {
                    "accept": "application/json, text/javascript, */*; q=0.01",
                    "content-type": "application/json",
                    "x-requested-with": "XMLHttpRequest"
                },
                body: JSON.stringify({
                    level1: "",
                    level2: "",
                    level3: "",
                    type: "0"
                })
            });
            return await resp.text();
        }
        """,
        {"apiUrl": CATEGORY_API_URL},
    )


def _save_json_text(raw_text: str, output_path: Path) -> None:
    parsed = json.loads(raw_text)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(parsed, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> None:
    base_dir = Path(__file__).resolve().parent
    default_output = base_dir / "category.json"
    default_state = base_dir / "momo_storage_state.json"

    parser = argparse.ArgumentParser(
        description="用已登入 momo SCM 瀏覽器 session 抓分類 category.json"
    )
    parser.add_argument(
        "--output",
        default=str(default_output),
        help="分類 JSON 輸出路徑（預設 momo/momotest/category.json）",
    )
    parser.add_argument(
        "--storage-state",
        default=str(default_state),
        help="Playwright storage state 路徑（可重用登入）",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="無頭模式（通常不建議，手動登入時請勿使用）",
    )
    args = parser.parse_args()

    output_path = Path(args.output).expanduser().resolve()
    state_path = Path(args.storage_state).expanduser().resolve()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=bool(args.headless))
        context_kwargs = {}
        if state_path.exists():
            context_kwargs["storage_state"] = str(state_path)
            print(f"[INFO] 載入既有登入狀態：{state_path}")

        context = browser.new_context(**context_kwargs)
        page = context.new_page()

        try:
            page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=90000)
        except PlaywrightTimeoutError:
            print("[WARN] 首次進入頁面逾時，仍可手動在視窗完成登入後繼續。")

        print("請在瀏覽器完成 momo SCM 登入。")
        input("登入完成後按 Enter 繼續抓分類...")

        try:
            page.goto(TARGET_URL, wait_until="networkidle", timeout=90000)
        except PlaywrightTimeoutError:
            print("[WARN] 回到後台頁面等待逾時，將直接嘗試呼叫分類 API。")

        raw_text = _fetch_category_raw(page)

        try:
            _save_json_text(raw_text, output_path)
        except json.JSONDecodeError:
            print("[ERROR] 抓到的內容不是 JSON，可能 session 失效或尚未登入成功。")
            print(raw_text[:1000])
            context.storage_state(path=str(state_path))
            browser.close()
            raise SystemExit(1)

        context.storage_state(path=str(state_path))
        print(f"[OK] 分類已寫入：{output_path}")
        print(f"[OK] 登入狀態已保存：{state_path}")

        browser.close()


if __name__ == "__main__":
    main()
