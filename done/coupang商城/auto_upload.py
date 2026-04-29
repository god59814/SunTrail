import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

_SHARED = Path(__file__).resolve().parent.parent / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))
from rename_assets_images import main as rename_assets_main


BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config" / "login_info.json"
MAPPING_PATH = BASE_DIR / "mapping.json"
IMAGE_HOSTING_PATH = BASE_DIR / "image_hosting.json"
OUT_DIR = BASE_DIR / "out"


def _mapping_input_format() -> str:
    """回傳 mapping.json 的 input.format（預設 xlsx）。"""
    if not MAPPING_PATH.exists():
        return "xlsx"
    try:
        mp = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
        inp = mp.get("input") or {}
        return str(inp.get("format", "xlsx")).strip().lower()
    except Exception:
        return "xlsx"


def _load_image_hosting() -> dict:
    """
    讀取由 start_image_hosting.py 產生的 image_hosting.json。
    若不存在，提示使用者先在另一個終端機啟動圖片伺服器。
    """
    if not IMAGE_HOSTING_PATH.exists():
        raise SystemExit(
            "[ERROR] 找不到 image_hosting.json。\n"
            "請先在另一個終端機執行：\n"
            "  python start_image_hosting.py\n"
            "啟動圖片伺服器與 cloudflared 之後，再回來執行 auto_upload.py。"
        )

    try:
        return json.loads(IMAGE_HOSTING_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        raise SystemExit(f"[ERROR] 讀取 image_hosting.json 失敗: {e}")


def _auto_detect_image_folder(assets_dir: Path) -> str:
    """
    嘗試自動推斷要使用的 image-folder：
    - 若 assets_dir 底下只有一個子資料夾，就用它。
    - 否則要求使用者用 --image-folder 指定。
    """
    subdirs = [p for p in assets_dir.iterdir() if p.is_dir()]
    if not subdirs:
        raise SystemExit(
            f"[ERROR] 在 {assets_dir} 底下找不到任何商品資料夾。請先放入圖片。"
        )

    if len(subdirs) == 1:
        return subdirs[0].name

    names = ", ".join(p.name for p in subdirs)
    raise SystemExit(
        "[ERROR] 發現多個商品資料夾，無法自動決定 image-folder。\n"
        f"目前偵測到：{names}\n"
        "請在執行時加上參數，例如：\n"
        '  python auto_upload.py --image-folder "高速BLDC正負離子吹風機-小甜筒-兩色可選(S1)"'
    )


def main() -> None:
    ap = argparse.ArgumentParser(
        description="整理 assets 圖片後呼叫 create_from_xlsx.py 上傳 Coupang商城"
    )
    ap.add_argument(
        "--image-folder",
        default="",
        help="強制整批使用同一圖片子資料夾（未指定且為 gsheet 時改依每組欄位）",
    )
    ap.add_argument(
        "--gsheet-header-row",
        type=int,
        default=None,
        metavar="N",
        help="覆寫試算表標題列（如 2），僅 input.format=gsheet 時有效",
    )
    ap.add_argument(
        "--gsheet-data-start-row",
        type=int,
        default=None,
        metavar="N",
        help="覆寫資料起始列（如 4）",
    )
    ap.add_argument(
        "--gsheet-data-row-count",
        type=int,
        default=None,
        metavar="N",
        help="本次要讀取的資料筆數（覆寫 mapping 的 data_row_count，且忽略 data_end_row）",
    )
    args = ap.parse_args()
    image_folder_arg = str(args.image_folder or "").strip()

    if not CONFIG_PATH.exists():
        raise SystemExit(f"[ERROR] 找不到 {CONFIG_PATH}")
    if not MAPPING_PATH.exists():
        raise SystemExit(f"[ERROR] 找不到 {MAPPING_PATH}")

    hosting = _load_image_hosting()
    img_base_url = str(hosting.get("img_base_url", "")).strip()
    assets_dir_name = str(hosting.get("assets_dir", "") or "assets").strip()
    # 防禦性：若寫進 image_hosting.json 的 assets_dir 不小心是 "--assets-dir" 這種誤值，改回預設 "assets"
    if not assets_dir_name or assets_dir_name.startswith("-"):
        assets_dir_name = "assets"
    if not img_base_url:
        raise SystemExit(
            "[ERROR] image_hosting.json 裡沒有 img_base_url，請重新執行 start_image_hosting.py"
        )

    assets_path = str(hosting.get("assets_path", "") or "").strip()
    if assets_path:
        assets_dir = Path(assets_path)
    else:
        assets_dir = BASE_DIR / assets_dir_name
    if not assets_dir.exists():
        raise SystemExit(f"[ERROR] assets 目錄不存在：{assets_dir}")

    os.environ["WORK_ASSETS_DIR"] = str(assets_dir.resolve())

    # 先整理一次 assets 底下的圖片命名（main_01/desc_01/detail_01）
    print("[INFO] 重新整理 assets 圖片命名...")
    rename_assets_main()

    # 決定 image-folder：可手動指定；Google Sheet 模式下可省略，改由每組資料的 sale_product_name（見 mapping image_folder_column）對應 assets 子資料夾
    use_per_group_folders = False
    if image_folder_arg:
        image_folder = image_folder_arg
        print(f"[INFO] 使用 image-folder: {image_folder}")
    else:
        if _mapping_input_format() == "gsheet":
            use_per_group_folders = True
            print(
                "[INFO] Google Sheet 模式且未指定 --image-folder："
                "將依 mapping 讀取試算表範圍，並以每個分組的圖片欄位對應 assets 子資料夾（預設欄 sale_product_name）。"
            )
        else:
            image_folder = _auto_detect_image_folder(assets_dir)
            print(f"[INFO] 使用 image-folder: {image_folder}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        "create_from_xlsx.py",
        "--img-base-url",
        img_base_url,
        "--assets-dir",
        str(assets_dir.resolve()),
    ]
    if not use_per_group_folders:
        cmd.extend(["--image-folder", image_folder])

    if args.gsheet_header_row is not None:
        cmd.extend(["--gsheet-header-row", str(args.gsheet_header_row)])
    if args.gsheet_data_start_row is not None:
        cmd.extend(["--gsheet-data-start-row", str(args.gsheet_data_start_row)])
    if args.gsheet_data_row_count is not None:
        cmd.extend(["--gsheet-data-row-count", str(args.gsheet_data_row_count)])

    print("[INFO] 開始執行自動上傳流程：")
    print("      " + " ".join(cmd))

    result = subprocess.run(cmd, cwd=str(BASE_DIR))
    if result.returncode != 0:
        raise SystemExit(
            f"[ERROR] create_from_xlsx.py 執行失敗，returncode={result.returncode}"
        )

    report = OUT_DIR / "upload_report.csv"
    if report.exists():
        print(f"[OK] 上傳流程結束，請查看報表：{report}")
    else:
        print("[WARN] 找不到 upload_report.csv，請手動確認 out/ 內容是否正確。")


if __name__ == "__main__":
    main()
