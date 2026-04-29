from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from subprocess import Popen, PIPE, STDOUT, DEVNULL

BASE_DIR = Path(__file__).resolve().parent
WORK_ROOT = BASE_DIR.parent
SHARED_ASSETS = WORK_ROOT / "shared" / "assets"
IMAGE_HOSTING_PATH = BASE_DIR / "image_hosting.json"


def start_http_server(assets_dir: Path, port: int = 8000) -> Popen:
    """
    在 assets_dir 底下啟動 python http.server。
    """
    assets_dir.mkdir(parents=True, exist_ok=True)
    proc = Popen(
        [sys.executable, "-m", "http.server", str(port)],
        cwd=str(assets_dir),
        stdout=DEVNULL,   # ✅ 不要 PIPE
        stderr=STDOUT,
        text=True,
        encoding="utf-8",
    )
    return proc


def start_cloudflared(port: int = 8000) -> tuple[Popen | None, str]:
    """
    啟動 cloudflared 並解析 trycloudflare URL。
    重點：只啟動一次，拿到 URL 後不重啟，避免 URL 失效（Error 1033）。
    同時用背景 thread 持續 drain stdout，避免 PIPE 塞爆。
    """
    import threading

    CLOUDFLARED_CMD = [
        "cloudflared",
        "tunnel",
        "--no-autoupdate",
        "--url",
        f"http://localhost:{port}",
    ]

    proc = Popen(
        CLOUDFLARED_CMD,
        stdout=PIPE,
        stderr=STDOUT,
        text=True,
        encoding="utf-8",
        bufsize=1,
    )

    public_url = ""
    stdout_io = proc.stdout
    if stdout_io is not None:
        url_re = re.compile(r"https://[^\s]+trycloudflare\.com", re.IGNORECASE)
        deadline = time.time() + 30.0

        # 先同步讀到 URL
        while time.time() < deadline:
            if proc.poll() is not None:
                print("[ERROR] cloudflared 程序已結束。")
                break
            line = stdout_io.readline()
            if not line:
                time.sleep(0.1)
                continue
            m = url_re.search(line)
            if m:
                public_url = m.group(0).rstrip("/")
                break

        if not public_url:
            print("[ERROR] 取得 cloudflared URL 逾時（30 秒）。")

        # 拿到 URL 後，開 thread 持續把 stdout 讀乾淨，避免 PIPE 塞爆
        def _drain_stdout() -> None:
            try:
                while True:
                    if proc.poll() is not None:
                        break
                    line = stdout_io.readline()
                    if not line:
                        time.sleep(0.1)
            except Exception:
                pass

        t = threading.Thread(target=_drain_stdout, daemon=True)
        t.start()

    if not public_url:
        # 沒拿到 URL 就收掉
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        return None, ""

    return proc, public_url

def main() -> None:
    """
    自動化圖片架設：
    - 在 assets (或指定資料夾) 啟動 python http.server 8000
    - 啟動 cloudflared tunnel，取得 trycloudflare 公網網址
    - 印出給 create_from_xlsx.py 使用的 --img-base-url / --assets-dir 指令樣板
    """
    # 簡單支援兩種寫法：
    #   python start_image_hosting.py
    #   python start_image_hosting.py assets
    #   python start_image_hosting.py --assets-dir assets
    if len(sys.argv) > 1:
        if sys.argv[1] == "--assets-dir" and len(sys.argv) > 2:
            assets_arg = sys.argv[2]
        elif not sys.argv[1].startswith("-"):
            assets_arg = sys.argv[1]
        else:
            assets_arg = str(SHARED_ASSETS)
    else:
        assets_arg = str(SHARED_ASSETS)

    assets_dir = Path(assets_arg)
    if not assets_dir.is_absolute():
        assets_dir = BASE_DIR / assets_dir
    assets_dir = assets_dir.resolve()

    port = 8000

    print(f"[INFO] assets 資料夾: {assets_dir}")
    print(f"[INFO] 啟動本機 HTTP 伺服器: http://localhost:{port}")
    http_proc = start_http_server(assets_dir, port=port)

    print("[INFO] 啟動 cloudflared tunnel，請稍候取得公網網址...")
    cf_proc, public_url = start_cloudflared(port=port)

    if not public_url or cf_proc is None:
        print("[ERROR] 無法從 cloudflared log 解析出 trycloudflare 公網網址。")
        print("        請確認已安裝 cloudflared，並手動檢查其輸出。")
        try:
            http_proc.terminate()
            http_proc.wait(timeout=3)
        except Exception:
            try:
                http_proc.kill()
            except Exception:
                pass
        return

    print(f"[OK] 圖片公網網址 (img-base-url): {public_url}")

    # 將結果寫入 image_hosting.json，方便其他程式自動帶入
    data = {
        "img_base_url": public_url,
        "assets_dir": assets_dir.name,
        "assets_path": str(assets_dir),
    }
    try:
        IMAGE_HOSTING_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[OK] 已寫入 {IMAGE_HOSTING_PATH.name}，create_from_xlsx.py / UI 會自動讀取 img-base-url。")
    except Exception as e:
        print(f"[WARN] 寫入 {IMAGE_HOSTING_PATH.name} 失敗：{e}")

    print()
    print("你可以用下列指令上傳（範例）：")
    print()
    print("  python create_from_xlsx.py \\")
    print(f"    --img-base-url {public_url} \\")
    print(f"    --assets-dir {assets_dir.name} \\")
    print(f"    --fallback-main-image {public_url}/<你的主圖檔名>")
    print()
    print("若有使用 image-folder，例如 '10001'，可再加上：")
    print("    --image-folder 10001")
    print()

    print("[INFO] HTTP server 與 cloudflared 目前在背景執行。")
    print("      若不再需要，請在此視窗按 Ctrl+C 結束兩個程序。")
    print("      若任一程序異常結束，這個程式會偵測到並自動清除 image_hosting.json 後結束。")

    try:
        # 監控子程序狀態：若 http.server 或 cloudflared 任一結束，就主動結束自己並清除 image_hosting.json
        while True:
            time.sleep(5.0)
            http_rc = http_proc.poll()
            cf_rc = cf_proc.poll()
            if http_rc is not None or cf_rc is not None:
                print("\n[ERROR] 檢測到圖片伺服器或 cloudflared 已結束。")
                print(f"        http.server returncode={http_rc}, cloudflared returncode={cf_rc}")
                break
    except KeyboardInterrupt:
        print("\n[INFO] 收到 Ctrl+C，關閉 server 與 cloudflared...")
    finally:
        for proc in (http_proc, cf_proc):
            if proc is None:
                continue
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

        # 清掉 image_hosting.json，避免下次誤用已失效的 URL
        try:
            if IMAGE_HOSTING_PATH.exists():
                IMAGE_HOSTING_PATH.unlink()
                print(f"[INFO] 已刪除 {IMAGE_HOSTING_PATH.name}，下次將重新取得 img-base-url。")
        except Exception as e:
            print(f"[WARN] 刪除 {IMAGE_HOSTING_PATH.name} 失敗：{e}")


if __name__ == "__main__":
    main()

