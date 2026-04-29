import json

m = json.load(open("shopee_category_mapping.json", "r", encoding="utf-8"))["path_to_id"]

def search_category_id(query: str, topk: int = 20):
    q = query.strip().lower()
    hits = []
    for path, cid in m.items():
        score = 0
        p = path.lower()
        # 很簡單的計分：完全包含 + 長度接近
        if q in p:
            score += 100
        # 多關鍵字
        for tok in q.split():
            if tok and tok in p:
                score += 10
        score -= abs(len(p) - len(q)) * 0.05
        if score > 0:
            hits.append((score, cid, path))
    hits.sort(reverse=True)
    return hits[:topk]

for s, cid, path in search_category_id("吹風機 家電"):
    print(f"{cid}\t{path}\t(score={s:.1f})")
