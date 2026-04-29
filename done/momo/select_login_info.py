import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Select login_info.json by entpCode")
    parser.add_argument("--entpCode", required=True, help="Key in config/all_login_info.json")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent
    all_path = repo_root / "config" / "all_login_info.json"
    out_path = repo_root / "config" / "login_info.json"

    entp_code = str(args.entpCode).strip()
    if not entp_code:
        raise SystemExit("--entpCode must not be empty")

    all_data = json.loads(all_path.read_text(encoding="utf-8"))
    if not isinstance(all_data, dict):
        raise ValueError("config/all_login_info.json must be a JSON object")

    if entp_code not in all_data:
        available = ", ".join(map(str, all_data.keys()))
        raise KeyError(f"entpCode={entp_code} not found in all_login_info.json. available: {available}")

    selected = all_data[entp_code]
    if not isinstance(selected, dict):
        raise ValueError("Selected entp entry must be an object")

    # 必要 key（與 momotest/xlsx_to_payload.py 的 LOGININFO_KEYS 保持一致）
    required = ["scm_domain", "entpCode", "entpID", "entpPwd", "otpBackNo"]
    missing = [k for k in required if k not in selected or str(selected[k]).strip() == ""]
    if missing:
        raise ValueError(f"Selected login info missing keys: {missing}")

    out_path.write_text(
        json.dumps(selected, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[LOGIN] wrote {out_path} for entpCode={entp_code}")


if __name__ == "__main__":
    main()

