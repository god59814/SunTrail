import csv
import json
import subprocess
import sys
import webbrowser
from pathlib import Path

_SHARED_LIB = Path(__file__).resolve().parent.parent.parent / "shared"
if str(_SHARED_LIB) not in sys.path:
    sys.path.insert(0, str(_SHARED_LIB))
from work_paths import load_gsheet_pm_source, resolve_cred_path, shared_assets_dir, work_root_from
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

from start_image_hosting import start_http_server, start_cloudflared, IMAGE_HOSTING_PATH
from gsheet_reader import read_sheet_records
from shipping_fee import fee_from_dimension_sum, get_dimension_sum, SHIPPING_TIERS
from list_locations import (
    load_config as load_location_config,
    list_outbound_shipping_places,
    list_return_centers,
)


BASE_DIR = Path(__file__).resolve().parent
MAPPING_PATH = BASE_DIR.parent / "mapping.json"
DEV_MODE = "--dev" in sys.argv

_DEFAULT_ASSETS_DIR = str(shared_assets_dir(work_root_from(Path(__file__))))


def _merge_gsheet_config(gs_from_mapping: dict) -> dict:
    wr = work_root_from(Path(__file__))
    base = dict(load_gsheet_pm_source(wr))
    over = dict(gs_from_mapping or {})
    merged = {**base, **over}
    cred = str(merged.get("cred_path", "") or "").strip()
    if cred:
        merged["cred_path"] = resolve_cred_path(wr, cred)
    return merged


def load_mapping() -> dict:
    if not MAPPING_PATH.exists():
        return {}
    try:
        return json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def discover_asset_subdirs(assets_dir: Path) -> list[str]:
    if not assets_dir.exists() or not assets_dir.is_dir():
        return []
    subdirs: list[str] = []
    for p in sorted(assets_dir.iterdir()):
        if p.is_dir():
            subdirs.append(p.name)
    return subdirs


class CoupangToolUI(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Coupang商城 上架工具 UI")
        self.geometry("900x600")

        self.mapping = load_mapping()

        # ===== 狀態變數 =====
        self.dev_mode = DEV_MODE
        self.assets_dir_var = tk.StringVar(value=_DEFAULT_ASSETS_DIR)
        self.image_folder_var = tk.StringVar(value="")
        self.img_base_url_var = tk.StringVar(value="")
        self.fallback_main_image_var = tk.StringVar(value="")
        # 預設為實際上傳；dev 模式時你可以在 UI 勾選 dry-run
        self.dry_run_var = tk.BooleanVar(value=False)
        # 運費：從 gsheet 長+寬+高自動算出；退貨運費使用者可設，預設 100% 運費
        self.dimension_sum_display_var = tk.StringVar(value="—")
        self.auto_fee_display_var = tk.StringVar(value="—")
        self.return_charge_var = tk.StringVar(value="")
        # 運費模式與滿額免運門檻
        self.shipping_mode_var = tk.StringVar(value="NOT_FREE")
        self.free_over_var = tk.StringVar(value="999")
        # 額外資訊顯示：產品名稱 / 出貨地點 / 退貨地點 / 分類
        self.product_name_var = tk.StringVar(value="—")
        self.outbound_place_var = tk.StringVar(value="—")
        self.return_place_var = tk.StringVar(value="—")
        self.category_path_var = tk.StringVar(value="—")

        # 圖片伺服器相關 process 句柄（若 UI 幫你啟動，就在關閉時一併結束）
        self.http_proc: subprocess.Popen | None = None
        self.cf_proc: subprocess.Popen | None = None
        # 出貨/退貨地點快取（從 Coupang API 取得）
        self._outbound_cache: dict[str, dict] = {}
        self._return_cache: dict[str, dict] = {}
        self._location_loaded: bool = False

        # 從 mapping.json 裡拿一些預設值（若未來你想放進去）
        self._apply_defaults_from_mapping()

        # ===== UI 結構 =====
        self._build_widgets()

        # 初次載入 assets 子資料夾
        self.refresh_image_folders()
        # 運費預覽（從 gsheet 讀長+寬+高並算出運費）
        self.refresh_shipping_preview()

        # 確保關閉視窗時能順便結束背景 process
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # ------------------------------------------------------------------ UI
    def _build_widgets(self) -> None:
        # 上方控制區
        top_frame = ttk.LabelFrame(self, text="上架設定")
        top_frame.pack(side=tk.TOP, fill=tk.X, padx=10, pady=10)

        # 目前設定摘要：產品名稱 / 出貨地點 / 退貨地點 / 分類
        info_frame = ttk.LabelFrame(top_frame, text="目前設定摘要")
        info_frame.pack(fill=tk.X, pady=(0, 8))

        # 左側文字
        info_left = ttk.Frame(info_frame)
        info_left.pack(side=tk.LEFT, fill=tk.X, expand=True)

        info_row0 = ttk.Frame(info_left)
        info_row0.pack(fill=tk.X, pady=2)
        ttk.Label(info_row0, text="產品名稱：").pack(side=tk.LEFT)
        ttk.Label(info_row0, textvariable=self.product_name_var, foreground="#333").pack(side=tk.LEFT, padx=2)

        info_row1 = ttk.Frame(info_left)
        info_row1.pack(fill=tk.X, pady=2)
        ttk.Label(info_row1, text="出貨地點：").pack(side=tk.LEFT)
        ttk.Label(info_row1, textvariable=self.outbound_place_var, foreground="#333").pack(side=tk.LEFT, padx=2)

        info_row2 = ttk.Frame(info_left)
        info_row2.pack(fill=tk.X, pady=2)
        ttk.Label(info_row2, text="退貨地點：").pack(side=tk.LEFT)
        ttk.Label(info_row2, textvariable=self.return_place_var, foreground="#333").pack(side=tk.LEFT, padx=2)

        info_row3 = ttk.Frame(info_left)
        info_row3.pack(fill=tk.X, pady=2)
        ttk.Label(info_row3, text="現在分類：").pack(side=tk.LEFT)
        ttk.Label(
            info_row3,
            textvariable=self.category_path_var,
            foreground="#333",
            wraplength=650,
            justify=tk.LEFT,
        ).pack(side=tk.LEFT, padx=2)

        # 右側「重新載入設定」按鈕
        info_right = ttk.Frame(info_frame)
        info_right.pack(side=tk.RIGHT, padx=5, pady=2)
        ttk.Button(info_right, text="重新載入設定", command=self.reload_mapping_and_summary).pack(side=tk.TOP)

        # assets 資料夾
        row0 = ttk.Frame(top_frame)
        row0.pack(fill=tk.X, pady=5)
        ttk.Label(row0, text="assets 資料夾：").pack(side=tk.LEFT)
        assets_entry = ttk.Entry(row0, textvariable=self.assets_dir_var, width=40)
        assets_entry.pack(side=tk.LEFT, padx=5)
        ttk.Button(row0, text="瀏覽...", command=self.choose_assets_dir).pack(side=tk.LEFT)
        ttk.Button(row0, text="重新整理子資料夾", command=self.refresh_image_folders).pack(side=tk.LEFT, padx=5)

        # image-folder
        row1 = ttk.Frame(top_frame)
        row1.pack(fill=tk.X, pady=5)
        ttk.Label(row1, text="圖片子資料夾 (image-folder)：").pack(side=tk.LEFT)
        self.image_folder_combo = ttk.Combobox(row1, textvariable=self.image_folder_var, width=30)
        self.image_folder_combo.pack(side=tk.LEFT, padx=5)
        ttk.Label(row1, text="（可直接輸入或從下拉選擇）").pack(side=tk.LEFT)

        # 運費：長+寬+高（gsheet）、自動運費、退貨運費（可設，預設 100%）
        row1b = ttk.Frame(top_frame)
        row1b.pack(fill=tk.X, pady=5)
        ttk.Label(row1b, text="長+寬+高 =").pack(side=tk.LEFT)
        ttk.Label(row1b, textvariable=self.dimension_sum_display_var, width=8).pack(side=tk.LEFT, padx=2)
        ttk.Label(row1b, text="cm").pack(side=tk.LEFT)
        ttk.Label(row1b, text="  自動算出運費 =").pack(side=tk.LEFT, padx=(15, 0))
        ttk.Label(row1b, textvariable=self.auto_fee_display_var, width=6).pack(side=tk.LEFT, padx=2)
        ttk.Label(row1b, text="NT").pack(side=tk.LEFT)
        ttk.Button(row1b, text="顯示運費關係圖", command=self.show_shipping_tiers).pack(side=tk.LEFT, padx=(15, 0))
        row1c = ttk.Frame(top_frame)
        row1c.pack(fill=tk.X, pady=2)
        ttk.Label(row1c, text="退貨運費 (NT)：").pack(side=tk.LEFT)
        self.return_charge_entry = ttk.Entry(row1c, textvariable=self.return_charge_var, width=8)
        self.return_charge_entry.pack(side=tk.LEFT, padx=5)
        ttk.Label(row1c, text="（僅能輸入初始運費金額的 100% 至 150%。）").pack(side=tk.LEFT)

        # 運費模式：一般 / 滿額免運 / 全免運
        row1d = ttk.Frame(top_frame)
        row1d.pack(fill=tk.X, pady=2)
        ttk.Label(row1d, text="運費模式：").pack(side=tk.LEFT)
        for text, val in [("一般運費", "NOT_FREE"), ("滿額免運", "CONDITIONAL_FREE"), ("全免運", "FREE")]:
            ttk.Radiobutton(
                row1d,
                text=text,
                value=val,
                variable=self.shipping_mode_var,
                command=self.on_shipping_mode_change,
            ).pack(side=tk.LEFT, padx=4)

        row1e = ttk.Frame(top_frame)
        row1e.pack(fill=tk.X, pady=2)
        ttk.Label(row1e, text="滿額免運門檻 (NT)：").pack(side=tk.LEFT)
        self.free_over_entry = ttk.Entry(row1e, textvariable=self.free_over_var, width=10)
        self.free_over_entry.pack(side=tk.LEFT, padx=5)
        ttk.Label(row1e, text="（僅滿額免運時使用）").pack(side=tk.LEFT)

        # 下列進階選項只在 dev 模式顯示（啟動時加 --dev）
        if self.dev_mode:
            # img-base-url
            row2 = ttk.Frame(top_frame)
            row2.pack(fill=tk.X, pady=5)
            ttk.Label(row2, text="img-base-url：").pack(side=tk.LEFT)
            ttk.Entry(row2, textvariable=self.img_base_url_var, width=60).pack(side=tk.LEFT, padx=5)

            # fallback-main-image
            row3 = ttk.Frame(top_frame)
            row3.pack(fill=tk.X, pady=5)
            ttk.Label(row3, text="fallback-main-image：").pack(side=tk.LEFT)
            ttk.Entry(row3, textvariable=self.fallback_main_image_var, width=60).pack(side=tk.LEFT, padx=5)

            # dry-run
            row4 = ttk.Frame(top_frame)
            row4.pack(fill=tk.X, pady=5)
            ttk.Checkbutton(row4, text="只產 payload 不呼叫 API（--dry-run）", variable=self.dry_run_var).pack(side=tk.LEFT)

        # 動作按鈕
        btn_frame = ttk.Frame(top_frame)
        btn_frame.pack(fill=tk.X, pady=10)
        ttk.Button(btn_frame, text="上架", command=self.run_create_from_xlsx).pack(
            side=tk.LEFT, padx=5
        )
        ttk.Button(btn_frame, text="刷新圖片伺服器", command=self.refresh_image_hosting).pack(
            side=tk.LEFT, padx=5
        )
        ttk.Button(btn_frame, text="設定主圖", command=self.configure_main_image).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="設定詳圖", command=self.configure_detail_images).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="設定文案圖", command=self.configure_content_images).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="設定出貨/退貨地點", command=self.run_configure_mapping).pack(
            side=tk.LEFT, padx=5
        )
        ttk.Button(btn_frame, text="選擇分類", command=self.run_pick_category).pack(
            side=tk.LEFT, padx=5
        )
        ttk.Button(btn_frame, text="開啟 Coupang商城 商品管理", command=self.open_coupang_inventory).pack(
            side=tk.LEFT, padx=5
        )

        # 下方輸出區
        bottom_frame = ttk.LabelFrame(self, text="輸出 / 訊息")
        bottom_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=5)

        # 清除輸出按鈕列
        ctrl_row = ttk.Frame(bottom_frame)
        ctrl_row.pack(side=tk.TOP, fill=tk.X, pady=2)
        ttk.Button(ctrl_row, text="清除輸出", command=self.clear_output).pack(side=tk.RIGHT, padx=5)

        text_frame = ttk.Frame(bottom_frame)
        text_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self.output_text = tk.Text(text_frame, wrap=tk.NONE, state=tk.DISABLED)
        self.output_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        y_scroll = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=self.output_text.yview)
        y_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.output_text.configure(yscrollcommand=y_scroll.set)

    # ------------------------------------------------------------------ helpers
    def _apply_defaults_from_mapping(self) -> None:
        """
        從 mapping.json / image_hosting.json 讀取一些預設值，減少使用者輸入。
        """
        _ = self.mapping  # 目前你若未來想放 UI 預設值，可以從這裡擴充

        # 若有執行過 start_image_hosting.py，會在 image_hosting.json 寫入 img_base_url / assets_dir
        if IMAGE_HOSTING_PATH.exists():
            try:
                cfg = json.loads(IMAGE_HOSTING_PATH.read_text(encoding="utf-8"))
            except Exception:
                cfg = {}
            img_base = str(cfg.get("img_base_url", "") or "").strip()
            assets_path = str(cfg.get("assets_path", "") or "").strip()
            if img_base and not self.img_base_url_var.get().strip():
                self.img_base_url_var.set(img_base)
            cur_assets = self.assets_dir_var.get().strip()
            if assets_path and cur_assets in ("assets", _DEFAULT_ASSETS_DIR):
                self.assets_dir_var.set(assets_path)

        # 從 mapping.json 帶入運費模式與滿額免運預設值（若有）
        pd = self.mapping.get("payload_defaults") or {}
        t = str(pd.get("deliveryChargeType", "") or "").strip().upper()
        if t in ("NOT_FREE", "FREE", "CONDITIONAL_FREE"):
            self.shipping_mode_var.set(t)
        free_over = pd.get("freeShipOverAmount")
        if free_over is not None:
            try:
                self.free_over_var.set(str(int(free_over)))
            except (TypeError, ValueError):
                pass

        # 從 Coupang API 讀取出貨/退貨地點清單，供摘要顯示使用
        self._ensure_location_cache()

        # 從 mapping.json / gsheet 帶入摘要顯示
        # 現在分類：category.fixed.path（若有）
        cat = self.mapping.get("category") or {}
        fixed = cat.get("fixed") or {}
        path = str(fixed.get("path", "") or "").strip()
        if path:
            self.category_path_var.set(path)

        # 出貨地點：以 outboundShippingPlaceCode 或 multiShippingInfos[0].outboundShippingPlaceId 顯示
        outbound = pd.get("outboundShippingPlaceCode")
        if outbound is None:
            msi = pd.get("multiShippingInfos") or []
            if isinstance(msi, list) and msi:
                outbound = (msi[0] or {}).get("outboundShippingPlaceId")
        if outbound is not None:
            code_key = str(outbound)
            row = self._outbound_cache.get(code_key) or {}
            addr0 = (row.get("placeAddresses") or [{}])[0] if isinstance(row.get("placeAddresses"), list) else {}
            zip_code = str(addr0.get("returnZipCode") or "").strip()
            addr = str(addr0.get("returnAddress") or "").strip()
            addr_detail = str(addr0.get("returnAddressDetail") or "").strip()
            name = str(row.get("shippingPlaceName") or "").strip()
            parts = [p for p in [zip_code, addr, addr_detail] if p]
            text = f"代碼 {outbound}"
            if name:
                text += f"（{name}）"
            if parts:
                text += "： " + " ".join(parts)
            self.outbound_place_var.set(text)

        # 退貨地點：優先用 returnCenterCode 對應 API，否則 fallback 到 payload_defaults 內的地址欄位
        return_code = pd.get("returnCenterCode")
        text_return = ""
        if return_code is not None:
            rc_key = str(return_code)
            row_r = self._return_cache.get(rc_key) or {}
            addr0_r = (row_r.get("placeAddresses") or [{}])[0] if isinstance(row_r.get("placeAddresses"), list) else {}
            zip_r = str(addr0_r.get("returnZipCode") or "").strip()
            addr_r = str(addr0_r.get("returnAddress") or "").strip()
            detail_r = str(addr0_r.get("returnAddressDetail") or "").strip()
            name_r = str(row_r.get("shippingPlaceName") or "").strip()
            parts_r = [p for p in [zip_r, addr_r, detail_r] if p]
            text_return = f"代碼 {return_code}"
            if name_r:
                text_return += f"（{name_r}）"
            if parts_r:
                text_return += "： " + " ".join(parts_r)
        if not text_return:
            zip_code = str(pd.get("returnZipCode", "") or "").strip()
            addr = str(pd.get("returnAddress", "") or "").strip()
            addr_detail = str(pd.get("returnAddressDetail", "") or "").strip()
            ret_parts = [p for p in [zip_code, addr, addr_detail] if p]
            text_return = " ".join(ret_parts) if ret_parts else ""
        if text_return:
            self.return_place_var.set(text_return)

        # 產品名稱：從 gsheet 第一筆資料的 sale_product_name 或 erp_product_name
        inp = self.mapping.get("input") or {}
        fmt = str(inp.get("format", "")).strip().lower()
        if fmt == "gsheet":
            gs = _merge_gsheet_config(inp.get("gsheet") or {})
            try:
                rows, _ = read_sheet_records(
                    credentials_path=str(gs.get("cred_path", "")).strip(),
                    spreadsheet_id=str(gs.get("spreadsheet_id", "")).strip(),
                    worksheet_name=str(gs.get("worksheet_name", "")).strip(),
                    header_row=int(gs.get("header_row") or 2),
                    data_start_row=int(gs.get("data_start_row") or 4),
                )
            except Exception:
                rows = []
            if rows:
                first = rows[0]
                name = str(first.get("sale_product_name") or first.get("erp_product_name") or "").strip()
                if name:
                    self.product_name_var.set(name)

        # 同步刷新運費預覽（長+寬+高 / 自動運費 / 退貨運費預設）
        self.refresh_shipping_preview()

    def reload_mapping_and_summary(self) -> None:
        """
        重新讀取 mapping.json，並更新：
        - 運費相關預設
        - 產品名稱 / 出貨地點 / 退貨地點 / 現在分類
        - 運費預覽
        方便在執行 configure_mapping / 選擇分類之後立即看到最新設定。
        """
        try:
            self.mapping = load_mapping()
        except Exception as e:
            self.log(f"[設定] 重新載入 mapping.json 失敗：{e}")
            messagebox.showerror("重新載入設定失敗", f"無法重新載入 mapping.json：{e}")
            return

        self._apply_defaults_from_mapping()
        self.log("[設定] 已重新載入 mapping.json 並更新摘要。")

    def log(self, msg: str) -> None:
        self.output_text.configure(state=tk.NORMAL)
        self.output_text.insert(tk.END, msg + "\n")
        self.output_text.see(tk.END)
        self.output_text.configure(state=tk.DISABLED)

    def clear_output(self) -> None:
        self.output_text.configure(state=tk.NORMAL)
        self.output_text.delete("1.0", tk.END)
        self.output_text.configure(state=tk.DISABLED)

    def on_close(self) -> None:
        # 嘗試關閉我們啟動的 http.server / cloudflared
        for proc in (self.http_proc, self.cf_proc):
            if proc is not None:
                try:
                    proc.terminate()
                except Exception:
                    pass
        # UI 關閉時也順便移除 image_hosting.json，避免下次誤用舊的 URL
        try:
            if IMAGE_HOSTING_PATH.exists():
                IMAGE_HOSTING_PATH.unlink()
        except Exception:
            pass
        self.destroy()

    def choose_assets_dir(self) -> None:
        initial = (BASE_DIR / self.assets_dir_var.get()).resolve()
        if not initial.exists():
            initial = BASE_DIR
        d = filedialog.askdirectory(initialdir=initial, title="選擇 assets 資料夾")
        if d:
            self.assets_dir_var.set(str(Path(d).resolve()))
            self.refresh_image_folders()

    def refresh_image_folders(self) -> None:
        assets_dir = Path(self.assets_dir_var.get())
        if not assets_dir.is_absolute():
            assets_dir = (BASE_DIR / assets_dir).resolve()
        subdirs = discover_asset_subdirs(assets_dir)
        self.image_folder_combo["values"] = subdirs

    def _ensure_location_cache(self) -> None:
        """
        從 Coupang API 抓取一次出貨/退貨地點清單，快取在記憶體中。
        UI 啟動時與每次重新載入設定時呼叫。
        """
        if self._location_loaded:
            return
        try:
            cfg = load_location_config()
        except Exception as e:
            # 此時文字輸出區可能尚未建立，避免呼叫 self.log
            print(f"[地點] 載入 config/login_info.json 失敗：{e}")
            self._location_loaded = True
            return

        market = str(self.mapping.get("market", "TW")).strip().upper() or "TW"
        try:
            outbound_rows = list_outbound_shipping_places(cfg, market=market, page_size=50)
            return_rows = list_return_centers(cfg, market=market, page_size=50)
        except Exception as e:
            print(f"[地點] 從 Coupang API 取得地點失敗：{e}")
            self._location_loaded = True
            return

        self._outbound_cache = {str(r.get("outboundShippingPlaceCode")): r for r in outbound_rows if r.get("outboundShippingPlaceCode") is not None}
        self._return_cache = {str(r.get("returnCenterCode")): r for r in return_rows if r.get("returnCenterCode") is not None}
        self._location_loaded = True
        # widgets 可能還沒建好，優先用 print；若已經有 output_text，再額外寫入 UI
        msg = f"[地點] 已載入出貨地點 {len(self._outbound_cache)} 筆、退貨地點 {len(self._return_cache)} 筆。"
        print(msg)
        if hasattr(self, "output_text"):
            self.log(msg)

    def on_shipping_mode_change(self) -> None:
        """切換運費模式時，控制滿額免運輸入框啟用/停用。"""
        mode = self.shipping_mode_var.get().strip().upper()
        state = tk.NORMAL if mode == "CONDITIONAL_FREE" else tk.DISABLED
        self.free_over_entry.configure(state=state)

    def show_shipping_tiers(self) -> None:
        """
        顯示運費關係圖（長+寬+高 cm 對應運費 NT），
        紀錄日：2026/2/24。
        """
        lines: list[str] = []
        prev_max = 0
        for max_cm, fee in SHIPPING_TIERS:
            low = 0 if prev_max == 0 else prev_max + 1
            lines.append(f"長+寬+高 {low}-{max_cm} cm：NT$ {fee}")
            prev_max = max_cm
        msg = "運費關係圖（2026/2/24 紀錄）：\n\n" + "\n".join(lines)
        self.log("[運費關係圖] 顯示 2026/2/24 紀錄的運費級距。")
        messagebox.showinfo("運費關係圖（2026/2/24 紀錄）", msg)

    def refresh_shipping_preview(self) -> None:
        """從 gsheet 讀取第一筆的長+寬+高，計算運費並更新顯示；退貨運費預設為運費 100%。"""
        self.dimension_sum_display_var.set("—")
        self.auto_fee_display_var.set("—")
        inp = self.mapping.get("input") or {}
        fmt = str(inp.get("format", "")).strip().lower()
        if fmt != "gsheet":
            return
        gs = _merge_gsheet_config(inp.get("gsheet") or {})
        field_map = self.mapping.get("field_map") or {}
        group_key_col = str((self.mapping.get("grouping") or {}).get("group_key_column", "")).strip()
        fallback_key_col = str((self.mapping.get("grouping") or {}).get("fallback_group_key_column", "")).strip()
        try:
            data_rows, _ = read_sheet_records(
                credentials_path=str(gs.get("cred_path", "")).strip(),
                spreadsheet_id=str(gs.get("spreadsheet_id", "")).strip(),
                worksheet_name=str(gs.get("worksheet_name", "")).strip(),
                header_row=int(gs.get("header_row") or 2),
                data_start_row=int(gs.get("data_start_row") or 4),
            )
            limit = gs.get("data_row_count")
            if limit is not None:
                data_rows = data_rows[: int(limit)]
        except Exception as e:
            self.log(f"[運費預覽] 無法讀取 gsheet：{e}")
            return
        if not data_rows:
            return
        # 第一筆群組的第一列
        groups: dict[str, list] = {}
        for r in data_rows:
            key = str(r.get(group_key_col) or r.get(fallback_key_col) or "").strip() or "UNKNOWN"
            groups.setdefault(key, []).append(r)
        first_group = next(iter(groups.values()), [])
        first_row = first_group[0] if first_group else None
        if not first_row:
            return
        dim_sum = get_dimension_sum(first_row, field_map)
        if dim_sum is not None:
            fee = fee_from_dimension_sum(dim_sum)
            self.dimension_sum_display_var.set(str(dim_sum))
            self.auto_fee_display_var.set(str(fee))
            # 預設退貨運費 = 自動運費（100%），每次重新載入預覽都覆寫
            self.return_charge_var.set(str(fee))

    def _run_subprocess(self, args: list[str], title: str) -> subprocess.CompletedProcess[str] | None:
        """
        以同步方式呼叫子程式，並把 stdout/stderr 顯示在下方文字區域。
       （執行期間主視窗可能短暫無回應；若未來需要可改成背景執行＋執行緒。）
        """
        self.log(f"=== 執行：{title} ===")
        self.log(f"命令：{' '.join(args)}")
        try:
            proc = subprocess.run(
                args,
                cwd=str(BASE_DIR),
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
        except FileNotFoundError:
            self.log("錯誤：找不到 Python 或腳本。請確認已安裝 Python，並從專案目錄執行。")
            messagebox.showerror("錯誤", "找不到 Python 或腳本。")
            return None

        if proc.stdout:
            self.log("--- stdout ---")
            self.log(proc.stdout.rstrip("\n"))
        if proc.stderr:
            self.log("--- stderr ---")
            self.log(proc.stderr.rstrip("\n"))

        self.log(f"=== 結束，returncode={proc.returncode} ===")
        if proc.returncode != 0:
            messagebox.showwarning("執行結束", f"{title} 執行失敗，returncode={proc.returncode}")
        else:
            messagebox.showinfo("執行結束", f"{title} 已執行完成。")

        return proc

    # 啟動圖片伺服器（若尚未有 img-base-url）
    def ensure_image_hosting(self) -> bool:
        img_base = self.img_base_url_var.get().strip()
        if img_base:
            # 已經有網址就不重複啟動
            return True

        assets_dir = Path(self.assets_dir_var.get().strip() or "assets")
        if not assets_dir.is_absolute():
            assets_dir = (BASE_DIR / assets_dir).resolve()

        self.log(f"[圖片] 使用 assets 目錄：{assets_dir}")
        self.log("[圖片] 啟動本機 HTTP server 與 cloudflared（可能需要幾秒鐘）...")

        try:
            self.http_proc = start_http_server(assets_dir, port=8000)
            self.cf_proc, public_url = start_cloudflared(port=8000)
        except FileNotFoundError:
            self.log("錯誤：找不到 cloudflared，請先安裝或將其加入 PATH。")
            messagebox.showerror("錯誤", "找不到 cloudflared，請先安裝後再重試。")
            return False

        if not public_url:
            self.log("錯誤：無法從 cloudflared 取得 trycloudflare 公網網址。")
            messagebox.showerror("錯誤", "無法取得 trycloudflare 公網網址，請查看終端機輸出。")
            return False

        # 設定 UI 欄位與 image_hosting.json
        self.img_base_url_var.set(public_url)

        data = {
            "img_base_url": public_url,
            "assets_dir": assets_dir.name,
        }
        try:
            IMAGE_HOSTING_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            self.log(f"[圖片] 已寫入 {IMAGE_HOSTING_PATH.name}")
        except Exception as e:
            self.log(f"[圖片] 寫入 {IMAGE_HOSTING_PATH.name} 失敗：{e}")

        self.log(f"[圖片] 已取得 img-base-url: {public_url}")
        return True

    def refresh_image_hosting(self) -> None:
        """
        手動刷新圖片伺服器：先關閉現有 http / cloudflared，再重啟並取得新的網址。
        """
        # 先嘗試關閉舊的 process
        for proc in (self.http_proc, self.cf_proc):
            if proc is not None:
                try:
                    proc.terminate()
                except Exception:
                    pass
        self.http_proc = None
        self.cf_proc = None

        # 清掉舊的 URL 與設定檔，確保會重新啟動
        self.img_base_url_var.set("")
        try:
            if IMAGE_HOSTING_PATH.exists():
                IMAGE_HOSTING_PATH.unlink()
        except Exception:
            pass

        ok = self.ensure_image_hosting()
        if ok:
            messagebox.showinfo("圖片伺服器", "圖片伺服器已重新啟動並取得新的網址。")
        else:
            messagebox.showerror("圖片伺服器", "無法重新啟動圖片伺服器，請查看下方輸出訊息。")

    def summarize_upload_report(self) -> None:
        """
        讀取 out/upload_report.csv，整理上傳成功/失敗數量，顯示在 UI。
        """
        report_path = BASE_DIR / "out" / "upload_report.csv"
        if not report_path.exists():
            self.log("[上架結果] 找不到 out/upload_report.csv")
            return

        try:
            with report_path.open("r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                total = 0
                success = 0
                failed = 0
                rows_preview: list[dict] = []
                for row in reader:
                    total += 1
                    uploaded = str(row.get("uploaded", "")).strip().upper()
                    if uploaded == "YES":
                        success += 1
                    else:
                        failed += 1
                    if len(rows_preview) < 5:
                        rows_preview.append(row)
        except Exception as e:
            self.log(f"[上架結果] 讀取 upload_report.csv 失敗：{e}")
            return

        msg = f"總筆數: {total}，成功: {success}，未成功/略過: {failed}"
        self.log("[上架結果] " + msg)
        self.log("[上架結果] 前幾筆紀錄：")
        for r in rows_preview:
            self.log(f"  - group_name={r.get('group_name','')} uploaded={r.get('uploaded','')} http_status={r.get('http_status','')} message={r.get('message','')}")

        messagebox.showinfo("上架結果", msg)

    # ------------------------------------------------------------------ 圖片設定 actions
    def _load_image_cfg_for_folder(self, folder_name: str) -> tuple[Path, dict]:
        folder_name = self.image_folder_var.get().strip()
        if not folder_name:
            messagebox.showwarning("圖片設定", "請先在上方選擇『圖片子資料夾 (image-folder)』。")
            raise RuntimeError("no folder")

        assets_dir = Path(self.assets_dir_var.get().strip() or "assets")
        if not assets_dir.is_absolute():
            assets_dir = (BASE_DIR / assets_dir).resolve()
        folder_path = assets_dir / folder_name

        if not folder_path.exists() or not folder_path.is_dir():
            messagebox.showerror("圖片設定", f"找不到資料夾：{folder_path}")
            raise RuntimeError("folder not found")

        # 載入或建立 image_config.json
        cfg_path = BASE_DIR / "image_config.json"
        if cfg_path.exists():
            try:
                cfg_all = json.loads(cfg_path.read_text(encoding="utf-8"))
            except Exception:
                cfg_all = {}
        else:
            cfg_all = {}

        cfg = cfg_all.get(folder_name) or {}
        if not isinstance(cfg, dict):
            cfg = {}
        return folder_path, cfg_all

    def configure_main_image(self) -> None:
        try:
            folder_path, cfg_all = self._load_image_cfg_for_folder(self.image_folder_var.get().strip())
        except RuntimeError:
            return

        filetypes = [("Image files", "*.jpg *.jpeg *.png *.webp *.gif"), ("All files", "*.*")]
        main_file = filedialog.askopenfilename(
            parent=self,
            title="選擇主圖（單張）",
            initialdir=str(folder_path),
            filetypes=filetypes,
        )
        if not main_file:
            return
        main_name = Path(main_file).name

        folder_name = self.image_folder_var.get().strip()
        cfg = cfg_all.get(folder_name) or {}
        if not isinstance(cfg, dict):
            cfg = {}
        cfg["main"] = main_name
        cfg_all[folder_name] = cfg

        cfg_path = BASE_DIR / "image_config.json"
        cfg_path.write_text(json.dumps(cfg_all, ensure_ascii=False, indent=2), encoding="utf-8")

        self.log(f"[圖片設定] 已更新資料夾 '{folder_name}' 主圖: {main_name}")
        messagebox.showinfo("圖片設定", "主圖設定已儲存。")

    def configure_detail_images(self) -> None:
        try:
            folder_path, cfg_all = self._load_image_cfg_for_folder(self.image_folder_var.get().strip())
        except RuntimeError:
            return

        filetypes = [("Image files", "*.jpg *.jpeg *.png *.webp *.gif"), ("All files", "*.*")]
        detail_files = filedialog.askopenfilenames(
            parent=self,
            title="選擇詳細圖片（可多選，最多 9 張）",
            initialdir=str(folder_path),
            filetypes=filetypes,
        )
        detail_names = [Path(p).name for p in detail_files if p][:9]

        folder_name = self.image_folder_var.get().strip()
        cfg = cfg_all.get(folder_name) or {}
        if not isinstance(cfg, dict):
            cfg = {}
        cfg["details"] = detail_names
        cfg_all[folder_name] = cfg

        cfg_path = BASE_DIR / "image_config.json"
        cfg_path.write_text(json.dumps(cfg_all, ensure_ascii=False, indent=2), encoding="utf-8")

        self.log(f"[圖片設定] 已更新資料夾 '{folder_name}' 詳細圖片: {detail_names or '（未指定）'}")
        messagebox.showinfo("圖片設定", "詳細圖片設定已儲存。")

    def configure_content_images(self) -> None:
        try:
            folder_path, cfg_all = self._load_image_cfg_for_folder(self.image_folder_var.get().strip())
        except RuntimeError:
            return

        filetypes = [("Image files", "*.jpg *.jpeg *.png *.webp *.gif"), ("All files", "*.*")]
        content_files = filedialog.askopenfilenames(
            parent=self,
            title="選擇文案/內容圖片（可多選）",
            initialdir=str(folder_path),
            filetypes=filetypes,
        )
        content_names = [Path(p).name for p in content_files if p]

        folder_name = self.image_folder_var.get().strip()
        cfg = cfg_all.get(folder_name) or {}
        if not isinstance(cfg, dict):
            cfg = {}
        cfg["contents"] = content_names
        # 讓使用者輸入要搭配文案圖片顯示的文字（可留空）
        text = simpledialog.askstring(
            "內容文字",
            "輸入要與第一張文案圖片一起顯示的文字（可留空）。\n平台將使用 IMAGE_TEXT 格式上傳圖文。",
            parent=self,
        )
        if text is not None:
            cfg["contents_text"] = text
        cfg_all[folder_name] = cfg

        cfg_path = BASE_DIR / "image_config.json"
        cfg_path.write_text(json.dumps(cfg_all, ensure_ascii=False, indent=2), encoding="utf-8")

        self.log(
            f"[圖片設定] 已更新資料夾 '{folder_name}' 內容圖片: {content_names or '（未指定）'}"
            + (f"（含文字）" if (text or "").strip() else "")
        )
        messagebox.showinfo("圖片設定", "內容圖片與文字設定已儲存。")

    def run_create_from_xlsx(self) -> None:
        py = sys.executable or "python"
        args: list[str] = [py, "create_from_xlsx.py"]

        # 若尚未有 img-base-url，先自動啟動圖片伺服器 / cloudflared
        if not self.img_base_url_var.get().strip():
            ok = self.ensure_image_hosting()
            if not ok:
                return

        # dry-run
        if self.dry_run_var.get():
            args.append("--dry-run")

        # img-base-url
        img_base = self.img_base_url_var.get().strip()
        if img_base:
            args.extend(["--img-base-url", img_base])

        # assets-dir
        assets_dir = self.assets_dir_var.get().strip()
        if assets_dir:
            args.extend(["--assets-dir", assets_dir])

        # image-folder
        image_folder = self.image_folder_var.get().strip()
        if image_folder:
            args.extend(["--image-folder", image_folder])

        # fallback-main-image
        fallback = self.fallback_main_image_var.get().strip()
        if fallback:
            args.extend(["--fallback-main-image", fallback])

        # 退貨運費（運費由 create_from_xlsx 依 gsheet 長+寬+高自動計算）
        rc_str = self.return_charge_var.get().strip()
        if rc_str:
            try:
                rc_val = int(rc_str)
                args.extend(["--return-charge", str(rc_val)])
            except ValueError:
                self.log("[上架] 退貨運費請輸入整數，已略過 --return-charge")

        # 運費模式與滿額免運
        mode = self.shipping_mode_var.get().strip().upper() or "NOT_FREE"
        if mode not in ("NOT_FREE", "CONDITIONAL_FREE", "FREE"):
            mode = "NOT_FREE"
        args.extend(["--delivery-charge-type", mode])
        if mode == "CONDITIONAL_FREE":
            free_over_str = self.free_over_var.get().strip()
            if free_over_str:
                try:
                    free_over_val = int(free_over_str)
                    if free_over_val > 0:
                        args.extend(["--free-over", str(free_over_val)])
                    else:
                        self.log("[上架] 滿額免運門檻需為 > 0，已略過 --free-over")
                except ValueError:
                    self.log("[上架] 滿額免運門檻請輸入整數，已略過 --free-over")

        proc = self._run_subprocess(args, "create_from_xlsx.py")

        if proc is None:
            return

        # dry-run 模式只產 payload，不實際呼叫 API，就不去看 upload_report
        if self.dry_run_var.get():
            self.log("[上架結果] dry-run 模式：只產生 payload，未呼叫 Coupang API。")
            messagebox.showinfo("上架結果", "dry-run 模式已完成，只產生 payload，未呼叫 Coupang API。")
            return

        if proc.returncode == 0:
            # 解析 upload_report.csv 並顯示摘要
            self.summarize_upload_report()

    def run_configure_mapping(self) -> None:
        """
        configure_mapping.py 是互動式腳本，會用 input() 等方式等使用者操作。
        若用 subprocess.run 會把 UI 卡住，所以改成在新的終端視窗執行。
        """
        py = sys.executable or "python"
        market = str(self.mapping.get("market", "TW")).strip().upper() or "TW"
        args = [
            py,
            "configure_mapping.py",
            "--market",
            market,
            "--only-usable",
            "--sync-return-address",
            "--sync-return-deliver-code",
        ]

        self.log(f"=== 在新終端視窗執行 configure_mapping.py ===")
        try:
            creationflags = 0
            if sys.platform.startswith("win") and hasattr(subprocess, "CREATE_NEW_CONSOLE"):
                creationflags = subprocess.CREATE_NEW_CONSOLE  # type: ignore[attr-defined]

            subprocess.Popen(
                args,
                cwd=str(BASE_DIR),
                creationflags=creationflags,
            )
        except FileNotFoundError:
            self.log("錯誤：找不到 configure_mapping.py 或 Python。")
            messagebox.showerror("錯誤", "找不到 configure_mapping.py 或 Python。")
            return

        messagebox.showinfo(
            "已啟動",
            "已在新的終端視窗開啟 configure_mapping.py，\n請在該視窗中完成出貨/退貨地點設定。",
        )

    def run_pick_category(self) -> None:
        """
        pick_category_interactive.py 同樣是互動式腳本，改成在新的終端視窗執行。
        """
        py = sys.executable or "python"
        args = [py, "pick_category_interactive.py", "--mapping", "mapping.json", "--limit", "30", "--match", "AND"]

        self.log(f"=== 在新終端視窗執行 pick_category_interactive.py ===")
        try:
            creationflags = 0
            if sys.platform.startswith("win") and hasattr(subprocess, "CREATE_NEW_CONSOLE"):
                creationflags = subprocess.CREATE_NEW_CONSOLE  # type: ignore[attr-defined]

            subprocess.Popen(
                args,
                cwd=str(BASE_DIR),
                creationflags=creationflags,
            )
        except FileNotFoundError:
            self.log("錯誤：找不到 pick_category_interactive.py 或 Python。")
            messagebox.showerror("錯誤", "找不到 pick_category_interactive.py 或 Python。")
            return

        messagebox.showinfo(
            "已啟動",
            "已在新的終端視窗開啟 pick_category_interactive.py，\n請在該視窗中完成分類設定。",
        )

    def open_coupang_inventory(self) -> None:
        """用預設瀏覽器開啟 Coupang 賣家後台商品管理頁面。"""
        url = (
            "https://wing.coupang.com/vendor-inventory/list"
            "?searchKeywordType=ALL&searchKeywords=&salesMethod=ALL&productStatus=ALL"
            "&stockSearchType=ALL&shippingFeeSearchType=ALL&displayCategoryCodes="
            "&listingStartTime=null&listingEndTime=null&saleEndDateSearchType=ALL"
            "&bundledShippingSearchType=ALL&upBundling=ALL&displayDeletedProduct=false"
            "&shippingMethod=ALL&exposureStatus=ALL&locale=zh_TW"
            "&sortMethod=SORT_BY_REGISTRATION_DATE&countPerPage=50&page=1"
        )
        webbrowser.open(url)


def main() -> None:
    app = CoupangToolUI()
    app.mainloop()


if __name__ == "__main__":
    main()

