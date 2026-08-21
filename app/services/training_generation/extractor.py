"""Strict extraction of one canonical JSON object from provider text.

Fail closed. A single optional outer markdown fence is accepted because the
current text-generation stack still emits one. Prose, multiple values, and
best-effort `{`…`}` slicing are rejected.
"""
from __future__ import annotations

import json
import re

from app.services.training_generation.output_errors import (
    ParseFailedError,
    TruncatedError,
)

_FENCE_RE = re.compile(
    r"^```(?:json)?\s*\r?\n(.*)\r?\n```\s*$",
    re.DOTALL | re.IGNORECASE,
)


def json_structure_incomplete(text: str) -> bool:
    """True when braces/brackets/strings are unfinished. Not a truncation claim."""
    depth_obj = 0
    depth_arr = 0
    in_string = False
    escape = False
    for char in text:
        if in_string:
            if escape:
                escape = False
                continue
            if char == "\\":
                escape = True
                continue
            if char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth_obj += 1
        elif char == "}":
            depth_obj -= 1
            if depth_obj < 0:
                return False
        elif char == "[":
            depth_arr += 1
        elif char == "]":
            depth_arr -= 1
            if depth_arr < 0:
                return False
    return in_string or escape or depth_obj > 0 or depth_arr > 0


def _strip_one_fence(raw: str) -> str:
    text = (raw or "").strip()
    match = _FENCE_RE.match(text)
    if match:
        return match.group(1).strip()
    if text.startswith("```"):
        raise ParseFailedError("markdown fence is incomplete or not the entire payload")
    return text


def extract_plan_object(raw, *, truncated: bool = False) -> dict:
    """Return the single top-level JSON object or raise a typed parse error."""
    if raw is None:
        text = ""
    elif isinstance(raw, (bytes, bytearray)):
        raise ParseFailedError("provider output must be text")
    else:
        text = str(raw)

    stripped = _strip_one_fence(text)
    if not stripped:
        if truncated:
            raise TruncatedError("provider output was empty after truncation")
        raise ParseFailedError("provider output was empty")

    incomplete = json_structure_incomplete(stripped)
    try:
        decoder = json.JSONDecoder()
        obj, index = decoder.raw_decode(stripped)
    except json.JSONDecodeError as exc:
        if truncated:
            raise TruncatedError("provider output was truncated mid-JSON") from exc
        raise ParseFailedError("provider output is not valid JSON") from exc

    leftover = stripped[index:].strip()
    if leftover:
        raise ParseFailedError("multiple JSON values are not a canonical plan")
    if not isinstance(obj, dict) or isinstance(obj, bool):
        raise ParseFailedError("top-level JSON value must be an object")
    if truncated and incomplete:
        raise TruncatedError("provider marked truncation on incomplete JSON")
    return obj
