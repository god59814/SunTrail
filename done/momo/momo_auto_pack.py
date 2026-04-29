from __future__ import annotations

import argparse
import json
import re
import shutil
import zipfile
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image, ImageOps


# =========================================================
# 設定
# =========================================================

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
TEXT_EXTS = {".txt", ".md", ".html", ".htm"}

# 主圖 / 廣告圖 / 規格圖
MAIN_SIZE = (1000, 1000)
AD_SIZE = (1000, 1000)
SPEC_SIZE = (1000, 1000)

MAIN_MIN_KB = 200
MAIN_MAX_KB = 1000
SPEC_MIN_KB = 200
SPEC_MAX_KB = 1000
AD_MAX_KB = 1000

# 手機版專推
MOBILE_MIN_WIDTH = 750
MOBILE_MAX_WIDTH = 1000
MOBILE_MAX_HEIGHT = 1500
MOBILE_MIN_KB = 40
MOBILE_MAX_KB = 500
MOBILE_TOTAL_MAX_KB = 5 * 1024  # 5MB

MAX_MAIN_COUNT = 6
MAX_MOBILE_DESC_COUNT = 20

WHITE_RGB_THRESHOLD = 245
WHITE_BORDER_RATIO = 0.985


# =========================================================
# dataclass
# =========================================================

@dataclass
class ImageCheckResult:
    file: str
    role: str
    ok: bool
    width: int
    height: int
    size_kb: float
    white_border: bool
    messages: List[str]


@dataclass
class ProductReport:
    batch_sup_no: str
    sale_product_name: str
    erp_sku: str
    sale_folder: Optional[str]
    sku_folder: Optional[str]
    copied_files: List[Dict[str, Any]]
    image_checks: List[Dict[str, Any]]
    mobile_text_file: Optional[str]
    warnings: List[str]


# =========================================================
# 基本工具
# =========================================================

def normalize_name(text: str) -> str:
    text = str(text or "").strip().lower()
    text = text.replace("（", "(").replace("）", ")")
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[\\/:\*\?\"<>\|]", "", text)
    return text


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def is_image(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in IMAGE_EXTS


def is_text(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in TEXT_EXTS


def list_images(folder: Path) -> List[Path]:
    if not folder.exists() or not folder.is_dir():
        return []
    return sorted([p for p in folder.iterdir() if is_image(p)], key=lambda p: p.name.lower())


def list_texts(folder: Path) -> List[Path]:
    if not folder.exists() or not folder.is_dir():
        return []
    return sorted([p for p in folder.iterdir() if is_text(p)], key=lambda p: p.name.lower())


def read_text_file(path: Path) -> str:
    for enc in ("utf-8", "utf-8-sig", "cp950", "big5"):
        try:
            return path.read_text(encoding=enc)
        except Exception:
            continue
    return path.read_text(errors="ignore")


def file_size_kb(path: Path) -> float:
    return path.stat().st_size / 1024.0


def copy_file(src: Path, dst: Path) -> None:
    ensure_dir(dst.parent)
    shutil.copy2(src, dst)


# =========================================================
# 圖片輸出
# =========================================================

def save_as_jpeg_fit_width(
    src: Path,
    dst: Path,
    target_width: int,
    max_height: Optional[int] = None,
    quality: int = 90,
) -> None:
    img = Image.open(src).convert("RGB")
    w, h = img.size
    if w <= 0 or h <= 0:
        raise ValueError(f"圖片尺寸異常: {src}")

    new_w = target_width
    new_h = round(h * (target_width / w))

    if max_height is not None and new_h > max_height:
        ratio = max_height / new_h
        new_w = round(new_w * ratio)
        new_h = max_height

    resized = img.resize((new_w, new_h), Image.LANCZOS)
    ensure_dir(dst.parent)
    resized.save(dst, format="JPEG", quality=quality, optimize=True)


def save_as_jpeg_keep_square(
    src: Path,
    dst: Path,
    target_size: tuple[int, int] = (1000, 1000),
    quality: int = 92,
) -> None:
    img = Image.open(src).convert("RGB")
    fitted = ImageOps.contain(img, target_size, Image.LANCZOS)

    canvas = Image.new("RGB", target_size, (255, 255, 255))
    x = (target_size[0] - fitted.width) // 2
    y = (target_size[1] - fitted.height) // 2
    canvas.paste(fitted, (x, y))

    ensure_dir(dst.parent)
    canvas.save(dst, format="JPEG", quality=quality, optimize=True)


# =========================================================
# ZIP：平鋪，不包最外層資料夾
# =========================================================

def zip_flat_folder(source_dir: Path, output_zip: Path) -> None:
    source_dir = source_dir.resolve()
    output_zip = output_zip.resolve()
    ensure_dir(output_zip.parent)

    files = [p for p in source_dir.rglob("*") if p.is_file()]
    if not files:
        raise ValueError(f"資料夾內沒有可壓縮的檔案: {source_dir}")

    with zipfile.ZipFile(output_zip, mode="w", compression=zipfile.ZIP_STORED) as zf:
        for file_path in files:
            arcname = file_path.relative_to(source_dir)
            zf.write(file_path, arcname=str(arcname))
            print(f"[ZIP] {file_path} -> {arcname}")


# =========================================================
# 白邊檢查
# =========================================================

def detect_white_border(path: Path) -> bool:
    try:
        img = Image.open(path).convert("RGB")
        w, h = img.size
        if w < 20 or h < 20:
            return False

        border = max(3, int(min(w, h) * 0.02))

        regions = [
            img.crop((0, 0, w, border)),
            img.crop((0, h - border, w, h)),
            img.crop((0, 0, border, h)),
            img.crop((w - border, 0, w, h)),
        ]

        def white_ratio(region: Image.Image) -> float:
            pixels = list(region.getdata())
            white_count = 0
            for r, g, b in pixels:
                if r >= WHITE_RGB_THRESHOLD and g >= WHITE_RGB_THRESHOLD and b >= WHITE_RGB_THRESHOLD:
                    white_count += 1
            return white_count / max(1, len(pixels))

        ratios = [white_ratio(r) for r in regions]
        white_edges = sum(1 for x in ratios if x >= WHITE_BORDER_RATIO)
        return white_edges >= 2
    except Exception:
        return False


# =========================================================
# 驗圖
# =========================================================

def check_image(path: Path, role: str) -> ImageCheckResult:
    messages: List[str] = []
    ok = True

    try:
        img = Image.open(path)
        width, height = img.size
    except Exception as e:
        return ImageCheckResult(
            file=str(path),
            role=role,
            ok=False,
            width=0,
            height=0,
            size_kb=0.0,
            white_border=False,
            messages=[f"無法讀取圖片: {e}"],
        )

    size_kb = round(file_size_kb(path), 2)
    white_border = detect_white_border(path)

    if white_border:
        ok = False
        messages.append("疑似白邊")

    if role.startswith("main"):
        if (width, height) != MAIN_SIZE:
            ok = False
            messages.append(f"主圖需 1000x1000，目前 {width}x{height}")
        if not (MAIN_MIN_KB <= size_kb <= MAIN_MAX_KB):
            ok = False
            messages.append(f"主圖大小需 {MAIN_MIN_KB}KB~{MAIN_MAX_KB}KB，目前 {size_kb}KB")

    elif role == "ad":
        if (width, height) != AD_SIZE:
            ok = False
            messages.append(f"廣告圖需 1000x1000，目前 {width}x{height}")
        if size_kb > AD_MAX_KB:
            ok = False
            messages.append(f"廣告圖大小需 <= {AD_MAX_KB}KB，目前 {size_kb}KB")

    elif role.startswith("mobile_desc"):
        if not (MOBILE_MIN_WIDTH <= width <= MOBILE_MAX_WIDTH):
            ok = False
            messages.append(
                f"手機版專推圖寬需介於 {MOBILE_MIN_WIDTH}~{MOBILE_MAX_WIDTH}px，目前 {width}px"
            )
        if height >= MOBILE_MAX_HEIGHT:
            ok = False
            messages.append(f"手機版專推圖高需 < {MOBILE_MAX_HEIGHT}px，目前 {height}px")
        if not (MOBILE_MIN_KB <= size_kb <= MOBILE_MAX_KB):
            ok = False
            messages.append(
                f"手機版專推圖大小需 {MOBILE_MIN_KB}KB~{MOBILE_MAX_KB}KB，目前 {size_kb}KB"
            )

    elif role.startswith("spec"):
        if (width, height) != SPEC_SIZE:
            ok = False
            messages.append(f"規格圖需 1000x1000，目前 {width}x{height}")
        if not (SPEC_MIN_KB <= size_kb <= SPEC_MAX_KB):
            ok = False
            messages.append(f"規格圖大小需 {SPEC_MIN_KB}KB~{SPEC_MAX_KB}KB，目前 {size_kb}KB")

    return ImageCheckResult(
        file=str(path),
        role=role,
        ok=ok,
        width=width,
        height=height,
        size_kb=size_kb,
        white_border=white_border,
        messages=messages,
    )


# =========================================================
# payload 工具
# =========================================================

def load_payload(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def save_payload(path: Path, payload: Dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def get_first_erp_sku(send_info: Dict[str, Any]) -> str:
    single_items = send_info.get("singleItemList") or []
    if isinstance(single_items, list) and single_items:
        first = single_items[0]
        if isinstance(first, dict):
            return str(first.get("entpGoodsNo") or "").strip()
    return ""


def get_sale_product_name(send_info: Dict[str, Any]) -> str:
    return str(send_info.get("supGoodsName_salePoint") or "").strip()


def set_mobile_content(send_info: Dict[str, Any], content: str) -> None:
    mdi = send_info.get("mobileDetailInfo")
    if not isinstance(mdi, dict):
        mdi = {"youtubeUrl": [], "content": ""}
        send_info["mobileDetailInfo"] = mdi
    mdi["content"] = content.strip()


# =========================================================
# 找資料夾
# =========================================================

def find_sale_folder(asset_root: Path, sale_product_name: str) -> Optional[Path]:
    target = normalize_name(sale_product_name)
    folders = [p for p in asset_root.iterdir() if p.is_dir()]

    exact = [p for p in folders if normalize_name(p.name) == target]
    if exact:
        return exact[0]

    fuzzy = [p for p in folders if target in normalize_name(p.name) or normalize_name(p.name) in target]
    if fuzzy:
        fuzzy.sort(key=lambda p: len(p.name))
        return fuzzy[0]

    return None


def find_sku_folder(sale_folder: Path, erp_sku: str) -> Optional[Path]:
    target = normalize_name(erp_sku)
    folders = [p for p in sale_folder.iterdir() if p.is_dir() and normalize_name(p.name) != "common"]

    exact = [p for p in folders if normalize_name(p.name) == target]
    if exact:
        return exact[0]

    fuzzy = [p for p in folders if target in normalize_name(p.name)]
    if fuzzy:
        fuzzy.sort(key=lambda p: len(p.name))
        return fuzzy[0]

    return None


def get_common_folder(sale_folder: Path) -> Optional[Path]:
    common = sale_folder / "common"
    if common.exists() and common.is_dir():
        return common
    return None


# =========================================================
# 圖片選取規則
# =========================================================

def merge_unique(paths1: List[Path], paths2: List[Path]) -> List[Path]:
    seen = set()
    result: List[Path] = []
    for p in paths1 + paths2:
        key = str(p.resolve()).lower()
        if key not in seen:
            seen.add(key)
            result.append(p)
    return result


def choose_main_images(sku_folder: Optional[Path], common_folder: Optional[Path]) -> List[Path]:
    sku_main = list_images(sku_folder / "main") if sku_folder else []
    common_main = list_images(common_folder / "main") if common_folder else []
    return merge_unique(sku_main, common_main)


def choose_desc_images(sku_folder: Optional[Path], common_folder: Optional[Path]) -> List[Path]:
    sku_desc = list_images(sku_folder / "desc") if sku_folder else []
    common_desc = list_images(common_folder / "desc") if common_folder else []
    return merge_unique(sku_desc, common_desc)


def choose_desc_text(sku_folder: Optional[Path], common_folder: Optional[Path]) -> Optional[Path]:
    sku_txt = list_texts(sku_folder / "desc") if sku_folder else []
    common_txt = list_texts(common_folder / "desc") if common_folder else []
    all_txt = sku_txt + common_txt
    return all_txt[0] if all_txt else None


def choose_spec_source_image(sku_folder: Optional[Path], common_folder: Optional[Path]) -> Optional[Path]:
    if sku_folder:
        sku_main = list_images(sku_folder / "main")
        if sku_main:
            return sku_main[0]

    if common_folder:
        common_main = list_images(common_folder / "main")
        if common_main:
            return common_main[0]

    return None


# =========================================================
# 規格圖命名回填
# =========================================================

def backfill_spec_img_names(send_info: Dict[str, Any]) -> None:
    batch = str(send_info.get("batchSupNo") or "").strip()
    if not batch:
        return

    single_items = send_info.get("singleItemList")
    if not isinstance(single_items, list):
        return

    for idx, item in enumerate(single_items, start=1):
        if not isinstance(item, dict):
            continue

        sup_goodsdt_code = str(item.get("supGoodsdtCode") or f"{idx:03d}").strip()
        if not sup_goodsdt_code:
            sup_goodsdt_code = f"{idx:03d}"
            item["supGoodsdtCode"] = sup_goodsdt_code

        if str(send_info.get("colSeq1") or "").strip():
            item["specImg1"] = f"{batch}_01_{sup_goodsdt_code}_B"
        if str(send_info.get("colSeq2") or "").strip():
            item.setdefault("specImg2", "")


# =========================================================
# 單商品處理
# =========================================================

def process_one_product(
    send_info: Dict[str, Any],
    asset_root: Path,
    stage_dir: Path,
) -> ProductReport:
    batch_sup_no = str(send_info.get("batchSupNo") or "").strip()
    sale_product_name = get_sale_product_name(send_info)
    erp_sku = get_first_erp_sku(send_info)

    warnings: List[str] = []
    copied_files: List[Dict[str, Any]] = []
    image_checks: List[Dict[str, Any]] = []

    if not batch_sup_no:
        raise ValueError("payload 缺少 batchSupNo")
    if not sale_product_name:
        raise ValueError("payload 缺少 supGoodsName_salePoint")
    if not erp_sku:
        warnings.append("singleItemList[0].entpGoodsNo 為空，無法精準找 SKU 資料夾")

    sale_folder = find_sale_folder(asset_root, sale_product_name)
    if not sale_folder:
        raise FileNotFoundError(f"找不到母資料夾: {sale_product_name}")

    sku_folder = find_sku_folder(sale_folder, erp_sku) if erp_sku else None
    common_folder = get_common_folder(sale_folder)
    # 主圖、專推圖、專推圖文字選用
    main_images = choose_main_images(sku_folder, common_folder)
    desc_images = choose_desc_images(sku_folder, common_folder)
    desc_text = choose_desc_text(sku_folder, common_folder)

    # 1) 主圖：只用 main，不再把 detail 當主圖
    used_main: List[Path] = []
    if main_images:
        used_main = main_images[:MAX_MAIN_COUNT]
    else:
        warnings.append("找不到 main 主圖")

    for idx, src in enumerate(used_main, start=1):
        dst = stage_dir / f"{batch_sup_no}_B{idx}.jpg"
        save_as_jpeg_keep_square(src, dst, target_size=(1000, 1000), quality=92)
        copied_files.append({"src": str(src), "dst": str(dst), "role": f"main_{idx}"})
        image_checks.append(asdict(check_image(dst, f"main_{idx}")))

    # 2) 廣告圖：先用主圖1 fallback
    if used_main:
        ad_src = used_main[2]
        ad_dst = stage_dir / f"{batch_sup_no}_O.jpg"
        save_as_jpeg_keep_square(ad_src, ad_dst, target_size=(1000, 1000), quality=92)
        copied_files.append({"src": str(ad_src), "dst": str(ad_dst), "role": "ad"})
        image_checks.append(asdict(check_image(ad_dst, "ad")))
    else:
        warnings.append("無法產生廣告圖，因為沒有主圖來源")

    # 3) 手機版專推圖：desc -> 轉成寬 <= 1000，且最終寬度必須 >= 750
    used_desc = desc_images[:MAX_MOBILE_DESC_COUNT]
    mobile_total_kb = 0.0
    mobile_idx = 1

    for src in used_desc:
        temp_dst = stage_dir / f"__tmp_mobile_{mobile_idx}.jpg"

        save_as_jpeg_fit_width(
            src,
            temp_dst,
            target_width=MOBILE_MAX_WIDTH,
            max_height=MOBILE_MAX_HEIGHT - 1,
            quality=90,
        )

        temp_check = check_image(temp_dst, f"mobile_desc_{mobile_idx}")

        # 小於 750px 的手機圖直接略過，不輸出到正式檔名
        if temp_check.width < MOBILE_MIN_WIDTH:
            temp_dst.unlink(missing_ok=True)
            warnings.append(
                f"略過手機圖 {src.name}，轉檔後寬度僅 {temp_check.width}px，小於 {MOBILE_MIN_WIDTH}px"
            )
            continue

        final_dst = stage_dir / f"{batch_sup_no}_m_1_{mobile_idx}.jpg"
        if final_dst.exists():
            final_dst.unlink()

        temp_dst.rename(final_dst)

        copied_files.append(
            {"src": str(src), "dst": str(final_dst), "role": f"mobile_desc_{mobile_idx}"}
        )

        final_check = check_image(final_dst, f"mobile_desc_{mobile_idx}")
        image_checks.append(asdict(final_check))
        mobile_total_kb += final_check.size_kb

        mobile_idx += 1

    if used_desc and mobile_total_kb >= MOBILE_TOTAL_MAX_KB:
        warnings.append(
            f"手機版專推圖總大小需 < 5MB，目前約 {mobile_total_kb / 1024:.2f}MB"
        )

    # 4) desc 文字回填 mobileDetailInfo.content
    if desc_text:
        content = read_text_file(desc_text).strip()
        set_mobile_content(send_info, content)
    else:
        fallback_content = str(send_info.get("content") or "").strip()
        if fallback_content:
            set_mobile_content(send_info, fallback_content)
            warnings.append("desc 裡找不到文字檔，已改用 payload.content 回填 mobileDetailInfo.content")
        else:
            warnings.append("desc 裡找不到文字檔，且 payload.content 也為空")

    # 5) specImg1 命名回填
    backfill_spec_img_names(send_info)

    # 6) 真的產生規格圖檔
    spec_src = choose_spec_source_image(sku_folder, common_folder)
    single_items = send_info.get("singleItemList") or []

    if spec_src and isinstance(single_items, list):
        for item in single_items:
            if not isinstance(item, dict):
                continue

            spec_img1 = str(item.get("specImg1") or "").strip()
            if spec_img1:
                spec_dst = stage_dir / f"{spec_img1}.jpg"
                save_as_jpeg_keep_square(spec_src, spec_dst, target_size=(1000, 1000), quality=92)
                copied_files.append({"src": str(spec_src), "dst": str(spec_dst), "role": "spec_1"})
                image_checks.append(asdict(check_image(spec_dst, "spec_1")))
    else:
        warnings.append("找不到規格圖來源，specImg1 對應圖檔不會產生")

    # 7) 手機版至少圖或 youtube 有一個
    mdi = send_info.get("mobileDetailInfo")
    youtube_urls = []
    if isinstance(mdi, dict):
        youtube_urls = mdi.get("youtubeUrl") or []

    if not used_desc and not youtube_urls:
        warnings.append("手機版專推圖與 youtubeUrl 都為空，可能不符 mobileDetailInfo 規則")

    return ProductReport(
        batch_sup_no=batch_sup_no,
        sale_product_name=sale_product_name,
        erp_sku=erp_sku,
        sale_folder=str(sale_folder) if sale_folder else None,
        sku_folder=str(sku_folder) if sku_folder else None,
        copied_files=copied_files,
        image_checks=image_checks,
        mobile_text_file=str(desc_text) if desc_text else None,
        warnings=warnings,
    )


# =========================================================
# 主程式
# =========================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="momo 自動抓圖 / 驗圖 / 平鋪打包")
    parser.add_argument("--payload", required=True, help="payload.json 路徑")
    parser.add_argument("--asset-root", required=True, help="素材根目錄")
    parser.add_argument("--output-dir", required=True, help="輸出目錄")
    parser.add_argument("--save-updated-payload", action="store_true", help="是否另存更新後的 payload")
    args = parser.parse_args()

    payload_path = Path(args.payload).resolve()
    asset_root = Path(args.asset_root).resolve()
    output_dir = Path(args.output_dir).resolve()

    stage_dir = output_dir / "stage"
    report_path = output_dir / "report.json"
    zip_path = output_dir / "images.zip"
    updated_payload_path = output_dir / "payload.updated.json"

    ensure_dir(output_dir)

    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    ensure_dir(stage_dir)

    payload = load_payload(payload_path)
    send_info_list = payload.get("sendInfoList")
    if not isinstance(send_info_list, list) or not send_info_list:
        raise ValueError("payload.json 沒有 sendInfoList")

    reports: List[Dict[str, Any]] = []

    for send_info in send_info_list:
        if not isinstance(send_info, dict):
            continue
        report = process_one_product(send_info, asset_root, stage_dir)
        reports.append(asdict(report))

    zip_flat_folder(stage_dir, zip_path)

    report_data = {
        "payload": str(payload_path),
        "asset_root": str(asset_root),
        "stage_dir": str(stage_dir),
        "zip_path": str(zip_path),
        "products": reports,
    }
    report_path.write_text(json.dumps(report_data, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.save_updated_payload:
        save_payload(updated_payload_path, payload)

    print(f"[OK] zip = {zip_path}")
    print(f"[OK] report = {report_path}")
    if args.save_updated_payload:
        print(f"[OK] updated payload = {updated_payload_path}")


if __name__ == "__main__":
    main()