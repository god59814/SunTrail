import argparse
import json
import re
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright


def _is_api_like(resource_type: str, url: str) -> bool:
    # Playwright resource_type: "xhr", "fetch", "document", "script", ...
    if resource_type in ("xhr", "fetch"):
        return True
    # fallback heuristics
    return bool(re.search(r"/api/|/v\d+/|graphql", url, re.IGNORECASE))


def _same_origin(url: str, base_origin: str) -> bool:
    try:
        u = urlparse(url)
        return f"{u.scheme}://{u.netloc}" == base_origin
    except Exception:
        return False


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", required=True, help="Target page URL")
    p.add_argument("--out", default="out_capture", help="Output directory")
    p.add_argument("--duration", type=int, default=30, help="Capture seconds after load")
    p.add_argument("--headless", action="store_true", help="Run headless")
    p.add_argument("--only-same-origin", action="store_true", help="Only record same-origin requests as the page")
    p.add_argument("--user-data-dir", default=None, help="Use existing Chrome profile directory for logged-in sessions")
    p.add_argument("--slowmo", type=int, default=0, help="Slow down actions (ms), useful for debugging")
    args = p.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Writes:
    # - all_requests.jsonl: everything (optional filter)
    # - api_calls.jsonl: fetch/xhr (API-like)
    # - api_calls_summary.json: grouped by (method,url) with count/statuses
    all_path = out_dir / "all_requests.jsonl"
    api_path = out_dir / "api_calls.jsonl"
    summary_path = out_dir / "api_calls_summary.json"

    all_f = all_path.open("w", encoding="utf-8")
    api_f = api_path.open("w", encoding="utf-8")

    # In-memory summary
    summary = {}

    def add_summary(method: str, url: str, status: int | None):
        key = f"{method} {url}"
        ent = summary.get(key) or {"count": 0, "statuses": {}, "first_ts": None, "last_ts": None}
        ent["count"] += 1
        if status is not None:
            ent["statuses"][str(status)] = ent["statuses"].get(str(status), 0) + 1
        now = time.time()
        if ent["first_ts"] is None:
            ent["first_ts"] = now
        ent["last_ts"] = now
        summary[key] = ent

    with sync_playwright() as pw:
        chromium = pw.chromium

        if args.user_data_dir:
            # Persistent context uses a real Chrome profile directory.
            # Great for "already logged-in" capture.
            context = chromium.launch_persistent_context(
                user_data_dir=args.user_data_dir,
                headless=args.headless,
                slow_mo=args.slowmo,
            )
        else:
            browser = chromium.launch(headless=args.headless, slow_mo=args.slowmo)
            context = browser.new_context()

        page = context.new_page()

        base_origin = ""
        try:
            parsed = urlparse(args.url)
            base_origin = f"{parsed.scheme}://{parsed.netloc}"
        except Exception:
            base_origin = ""

        # Map request -> response info
        req_info = {}

        def on_request(request):
            url = request.url
            if args.only_same_origin and base_origin and not _same_origin(url, base_origin):
                return

            resource_type = request.resource_type
            method = request.method
            headers = request.headers

            post_data = None
            try:
                post_data = request.post_data
            except Exception:
                post_data = None

            record = {
                "ts": time.time(),
                "type": "request",
                "resource_type": resource_type,
                "method": method,
                "url": url,
                "headers": headers,
                "post_data": post_data,
            }

            # Keep minimal for matching response later
            req_info[request] = {
                "ts": record["ts"],
                "resource_type": resource_type,
                "method": method,
                "url": url,
            }

            all_f.write(json.dumps(record, ensure_ascii=False) + "\n")

            if _is_api_like(resource_type, url):
                api_f.write(json.dumps(record, ensure_ascii=False) + "\n")
                add_summary(method, url, None)

        def on_response(response):
            try:
                request = response.request
            except Exception:
                return

            url = response.url
            if args.only_same_origin and base_origin and not _same_origin(url, base_origin):
                return

            status = response.status
            headers = response.headers

            body_text = None
            content_type = headers.get("content-type", "")

            # Be careful: response bodies can be huge/binary.
            # Only store body when it's JSON/text and not too big.
            try:
                if "application/json" in content_type or content_type.startswith("text/"):
                    body_text = response.text()
                    if body_text and len(body_text) > 200000:
                        body_text = body_text[:200000] + "\n...[truncated]"
            except Exception:
                body_text = None

            base = req_info.get(request) or {}
            record = {
                "ts": time.time(),
                "type": "response",
                "resource_type": base.get("resource_type"),
                "method": base.get("method"),
                "url": url,
                "status": status,
                "headers": headers,
                "body": body_text,
            }

            all_f.write(json.dumps(record, ensure_ascii=False) + "\n")

            if _is_api_like(base.get("resource_type") or "", url):
                api_f.write(json.dumps(record, ensure_ascii=False) + "\n")
                add_summary(base.get("method") or "GET", url, status)

        page.on("request", on_request)
        page.on("response", on_response)

        page.goto(args.url, wait_until="networkidle")

        # Keep the page open for capturing more API calls (manual操作或頁面自動輪詢)
        time.sleep(args.duration)

        # Dump summary
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

        api_f.close()
        all_f.close()

        context.close()

    print(f"Saved: {all_path}")
    print(f"Saved: {api_path}")
    print(f"Saved: {summary_path}")


if __name__ == "__main__":
    main()
