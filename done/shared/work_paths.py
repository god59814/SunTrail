from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


def work_root_from(start_file: Path) -> Path:
    """自任一腳本路径往上尋找 work 根目錄（需存在 shared/config/gsheet_pm_source.json）。"""
    p = start_file.resolve()
    if p.is_file():
        p = p.parent
    for d in [p, *p.parents]:
        marker = d / "shared" / "config" / "gsheet_pm_source.json"
        if marker.is_file():
            return d
    raise RuntimeError(
        "找不到 work 根目錄（缺少 shared/config/gsheet_pm_source.json），"
        f"從 {start_file} 往上尋找失敗。"
    )


def shared_config_dir(work_root: Path) -> Path:
    return work_root / "shared" / "config"


def shared_assets_dir(work_root: Path) -> Path:
    return work_root / "shared" / "assets"


def shared_data_dir(work_root: Path) -> Path:
    """共用資料（各通路分類 JSON、快取等）：<work>/shared/data。"""
    return work_root / "shared" / "data"


def load_gsheet_pm_source(work_root: Path) -> Dict[str, Any]:
    path = shared_config_dir(work_root) / "gsheet_pm_source.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_cred_path(work_root: Path, cred_path: str) -> str:
    s = str(cred_path or "").strip()
    if not s:
        return ""
    p = Path(s)
    if p.is_absolute():
        return str(p.resolve())
    return str((work_root / p).resolve())
