import gspread
from google.oauth2.service_account import Credentials
from typing import List, Dict, Any, Tuple


# 只讀
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
# 若你之後要回寫（寫入上架狀態/商品ID），改用：
# SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def _normalize_header(h: Any) -> str:
    s = "" if h is None else str(h)
    return s.strip().replace("\n", " ")


def _dedupe_headers(raw_headers: List[Any]) -> Tuple[List[str], List[Tuple[int, str, str]]]:
    """
    回傳：
    - headers: 已補名 + 去重後的 header 清單
    - mapping: (col_index_1based, original, final) 方便你 debug 對照
    """
    used: Dict[str, int] = {}
    headers: List[str] = []
    mapping: List[Tuple[int, str, str]] = []

    for i, h in enumerate(raw_headers, start=1):
        orig = _normalize_header(h)
        base = orig if orig else f"__col{i}"

        if base not in used:
            used[base] = 1
            final = base
        else:
            used[base] += 1
            final = f"{base}__{used[base]}"

        headers.append(final)
        mapping.append((i, orig, final))

    return headers, mapping


def read_sheet_records(
    credentials_path: str,
    spreadsheet_id: str,
    worksheet_name: str,
    header_row: int = 1,
    data_start_row: int | None = None,
    keep_empty_rows: bool = False,
) -> Tuple[List[Dict[str, Any]], List[Tuple[int, str, str]]]:
    """
    讀取 Google Sheet，允許 header 空白/重複，並回傳：
    - records: List[dict] 每列一筆
    - header_mapping: 欄位對照表 (col, original, final)

    header_row: header 在第幾列（1-based）
    data_start_row: 資料從第幾列開始（1-based）
      - 若為 None，預設為 header_row + 1
    """
    if data_start_row is None:
        data_start_row = header_row + 1
    if data_start_row <= header_row:
        raise ValueError("data_start_row must be > header_row")

    creds = Credentials.from_service_account_file(credentials_path, scopes=SCOPES)
    client = gspread.authorize(creds)
    ws = client.open_by_key(spreadsheet_id).worksheet(worksheet_name)

    all_values = ws.get_all_values()
    if len(all_values) < header_row:
        return [], []

    raw_headers = all_values[header_row - 1]
    headers, header_mapping = _dedupe_headers(raw_headers)

    records: List[Dict[str, Any]] = []

    # data rows slice: data_start_row 是 1-based
    for r in range(data_start_row - 1, len(all_values)):
        row = all_values[r]

        # 補齊/截斷到與 headers 同長
        if len(row) < len(headers):
            row = row + [""] * (len(headers) - len(row))
        else:
            row = row[: len(headers)]

        # 空列處理
        if not keep_empty_rows and not any(str(x).strip() for x in row):
            continue

        records.append(dict(zip(headers, row)))

    return records, header_mapping