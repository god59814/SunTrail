from __future__ import annotations

import json
import re
from typing import Any


def _last_regex_match(pattern: str, text: str) -> re.Match[str] | None:
    matches = list(re.finditer(pattern, text))
    return matches[-1] if matches else None


def parse_llm_result_regex(raw_output: str) -> dict[str, Any]:
    text = raw_output.strip()
    text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.DOTALL).strip()

    code_match = _last_regex_match(r"category_code\s*:\s*(.+)", text)
    path_match = _last_regex_match(r"category_path\s*:\s*(.+)", text)
    reason_match = _last_regex_match(r"reason\s*:\s*(.+)", text)
    conf_match = _last_regex_match(r"confidence\s*:\s*([0-9.]+)", text)

    return {
        "category_code": code_match.group(1).strip() if code_match else "",
        "category_path": path_match.group(1).strip() if path_match else "",
        "reason": reason_match.group(1).strip() if reason_match else "",
        "confidence": float(conf_match.group(1)) if conf_match else 0.0,
    }


def _normalize_llm_dict(d: dict[str, Any]) -> dict[str, Any]:
    code = str(d.get("category_code", d.get("code", "")) or "").strip().strip("\"'")
    path = str(d.get("category_path", d.get("path", "")) or "").strip().strip("\"'")
    reason = str(d.get("reason", "") or "").strip()
    conf = d.get("confidence", d.get("score", 0.0))
    try:
        confidence = float(conf)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    return {
        "category_code": code,
        "category_path": path,
        "reason": reason,
        "confidence": confidence,
    }


def _is_placeholder_text(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return True
    bad_tokens = {
        "<code>",
        "string",
        "...",
        "…",
        "最多20字",
        "最多 20 字",
        "簡短原因",
        "short reason",
        "reason",
        "category_code",
        "必須為候選之一",
        "示意文字",
    }
    if t in bad_tokens:
        return True
    return bool(re.fullmatch(r"[<>\[\]\{\}_\-\s./:;]+", t))


def sanitize_reason(raw_reason: str, fallback_note: str = "") -> str:
    reason = (raw_reason or "").strip()
    if _is_placeholder_text(reason):
        reason = ""
    if len(reason) > 24:
        reason = reason[:24].rstrip()
    if fallback_note:
        reason = f"{reason} [{fallback_note}]".strip()
    return reason


def parse_llm_result_json_first(raw_output: str) -> tuple[dict[str, Any], str]:
    """優先 JSON；失敗則 regex。回傳 (normalized_dict, parse_mode)。"""
    text = raw_output.strip()
    text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.DOTALL).strip()

    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    chunks_to_try: list[str] = []
    if m:
        chunks_to_try.append(m.group(1).strip())
    chunks_to_try.append(text)

    for chunk in chunks_to_try:
        if not chunk:
            continue
        start = chunk.find("{")
        if start < 0:
            continue
        depth = 0
        for i in range(start, len(chunk)):
            if chunk[i] == "{":
                depth += 1
            elif chunk[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(chunk[start : i + 1])
                        if isinstance(obj, dict):
                            return _normalize_llm_dict(obj), "json"
                    except json.JSONDecodeError:
                        break
                    break

    parsed = parse_llm_result_regex(text)
    return _normalize_llm_dict(parsed), "regex"

