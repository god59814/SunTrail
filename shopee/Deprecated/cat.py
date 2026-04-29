import json
import pandas as pd

XLS_PATH = "Shopee_category_list.xls"
OUT_PATH = "shopee_category_mapping.json"

df = pd.read_excel(XLS_PATH)

def norm(s):
    s = "" if pd.isna(s) else str(s).strip()
    return s

path_to_id = {}
id_to_path = {}

for _, r in df.iterrows():
    cid = norm(r.get("Category ID"))
    lv1 = norm(r.get("Level 1 Category"))
    lv2 = norm(r.get("Level 2 Category"))
    lv3 = norm(r.get("Level 3 Category"))
    lv4 = norm(r.get("Level 4 Category"))

    path_parts = [p for p in [lv1, lv2, lv3, lv4] if p]
    if not cid or not path_parts:
        continue

    path = " > ".join(path_parts)
    path_to_id[path] = cid
    id_to_path[cid] = path

with open(OUT_PATH, "w", encoding="utf-8") as f:
    json.dump(
        {"path_to_id": path_to_id, "id_to_path": id_to_path},
        f,
        ensure_ascii=False,
        indent=2,
    )

print("ok:", OUT_PATH, "paths=", len(path_to_id))
