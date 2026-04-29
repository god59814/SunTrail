"""
整理共用 assets 底下圖片檔名（main_01、desc_01、detail_01…）。
預設根目錄：<work>/shared/assets。
可覆寫：環境變數 WORK_ASSETS_DIR。
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import List

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from work_paths import shared_assets_dir, work_root_from


def _resolve_assets_root() -> Path:
    env = (os.environ.get("WORK_ASSETS_DIR") or "").strip()
    if env:
        return Path(env).resolve()
    return shared_assets_dir(work_root_from(Path(__file__)))


def natural_sort_key(p: Path):
    """自然排序：img2.jpg < img10.jpg"""
    parts = re.split(r"(\d+)", p.stem)
    return [int(x) if x.isdigit() else x.lower() for x in parts]


def _rename_in_folder(folder: Path, prefix: str) -> None:
    if not folder.exists() or not folder.is_dir():
        return

    files: List[Path] = [
        p
        for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in [".jpg", ".jpeg", ".png", ".webp"]
    ]

    if not files:
        return

    files = sorted(files, key=natural_sort_key)

    tmp_files: List[Path] = []
    for idx, f in enumerate(files, start=1):
        tmp_name = f"__tmp_renaming_{idx}{f.suffix.lower()}"
        tmp_path = folder / tmp_name

        print(f"[TMP] {f.name} → {tmp_name}")
        f.rename(tmp_path)
        tmp_files.append(tmp_path)

    for idx, f in enumerate(sorted(tmp_files), start=1):
        new_name = f"{prefix}_{idx:02d}{f.suffix.lower()}"
        target = folder / new_name

        print(f"[RENAME] {f.name} → {new_name}")
        f.rename(target)


def _process_product_folder(product_dir: Path) -> None:
    if not product_dir.is_dir():
        return

    for sub in product_dir.iterdir():
        if not sub.is_dir():
            continue

        for name, prefix in (
            ("main", "main"),
            ("desc", "desc"),
            ("detail", "detail"),
        ):
            folder = sub / name
            _rename_in_folder(folder, prefix)


def main() -> None:
    root = _resolve_assets_root()
    if not root.exists() or not root.is_dir():
        print(f"[ERROR] assets 根目錄不存在: {root}")
        return

    print(f"[INFO] 開始整理圖片命名，根目錄: {root}")

    for product_dir in root.iterdir():
        if not product_dir.is_dir():
            continue

        print(f"[INFO] 處理商品資料夾: {product_dir.name}")
        _process_product_folder(product_dir)

    print("[OK] 全部商品圖片命名整理完成。")


if __name__ == "__main__":
    main()
