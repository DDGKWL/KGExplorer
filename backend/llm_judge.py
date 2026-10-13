"""LLM-based semantic consistency judgment for cross-source attribute slots.

First used by Case 3 (News): the module itself is case-agnostic. Callers pass
an entity name, an attribute label, and one entry per source describing what
that source claims; the LLM returns a structured verdict:

    {"verdict": "consistent" | "conflict" | "uncertain", "reason": "..."}

Design rules:
- Verdicts are ADDITIVE metadata. They never overwrite the deterministic
  rule-based status computed by the existing pipelines; rule colors remain the
  fallback whenever no verdict (or an uncertain one) exists.
- All calls run with temperature=0 and JSON-only responses so repeated runs
  are stable. Failures degrade to "uncertain" instead of raising, so a batch
  never loses already-collected verdicts.
- Configuration comes from environment variables (see backend/.env.example):
    DEEPSEEK_API_KEY   required for live calls
    LLM_MODEL          default "deepseek-chat"
    LLM_BASE_URL       default "https://api.deepseek.com"
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from openai import OpenAI
except ImportError:  # surfaced as a clear runtime error on first live use
    OpenAI = None

DEFAULT_MODEL = "deepseek-chat"
DEFAULT_BASE_URL = "https://api.deepseek.com"
VERDICTS = ("consistent", "conflict", "uncertain")
MAX_ENTRIES = 8
MAX_VALUES_PER_ENTRY = 4
MAX_EVIDENCE_CHARS = 400

SYSTEM_PROMPT = (
    "你是跨来源知识图谱的新闻语义一致性判定助手。多家媒体报道同一事件时，"
    "对同一属性给出了各自表述。请判断这些表述在语义上是否指向同一事实："
    "同义改写、详略差异、单位换算、别名指称均视为一致(consistent)；"
    "只有指称不同对象/时间/地点/数量/归因等实质矛盾才视为冲突(conflict)；"
    "信息不足以判断时用(uncertain)。只输出JSON对象："
    '{"verdict": "consistent|conflict|uncertain", "reason": "不超过80字的中文理由"}'
)

# In-memory cache shared by the online /api/llm/judge endpoint. The offline
# batch script keeps its own on-disk sidecar cache instead.
_MEMORY_CACHE: Dict[str, Dict[str, Any]] = {}
_CLIENT: Optional["OpenAI"] = None


class LLMJudgeError(RuntimeError):
    """Raised when a live judgment is impossible (missing key or SDK)."""


def _load_dotenv() -> None:
    """Populate os.environ from backend/.env (never overrides real env vars).

    backend/.env.example tells users to "copy to backend/.env" - this makes
    that instruction actually work no matter which working directory a
    script runs from. Malformed lines are skipped; empty values (e.g. a
    still-blank DEEPSEEK_API_KEY=) are harmless because callers strip() and
    treat empty as "not configured". Real environment variables always win.
    """
    path = Path(__file__).resolve().parent / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def config() -> Dict[str, str]:
    _load_dotenv()
    return {
        "api_key": os.environ.get("DEEPSEEK_API_KEY", "").strip(),
        "model": os.environ.get("LLM_MODEL", "").strip() or DEFAULT_MODEL,
        "base_url": os.environ.get("LLM_BASE_URL", "").strip() or DEFAULT_BASE_URL,
    }


def is_configured() -> bool:
    return bool(config()["api_key"]) and OpenAI is not None


def verdict_key(event_id: str, attribute: str, reports_map: Any) -> str:
    """Stable key for one comparison.

    frontend/src/data/store.js derives the same key from the served
    comparisons payload, so offline sidecar verdicts and online judgments
    address the same slots. Keys are plain strings (no hashing) so they stay
    debuggable and auto-invalidate when upstream values change.
    """
    parts: List[str] = []
    for report_id, items in (reports_map or {}).items():
        for item in items or []:
            canonical = str((item or {}).get("canonical") or "").strip().lower()
            if canonical:
                parts.append(f"{report_id}:{canonical}")
    parts.sort()
    return f"{event_id}::{attribute}::" + ";".join(parts)


def build_payload(
    entity_name: str,
    attribute_label: str,
    entries: List[Dict[str, Any]],
    missing_sources: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Normalize caller input into the JSON block embedded in the user prompt."""
    claims = []
    for entry in (entries or [])[:MAX_ENTRIES]:
        values = [str(value).strip() for value in (entry.get("values") or []) if str(value).strip()]
        if not values:
            continue
        evidence = str(entry.get("evidence") or "").strip()
        claims.append({
            "source": str(entry.get("source") or "unknown"),
            "value": "；".join(values[:MAX_VALUES_PER_ENTRY]),
            "evidence": evidence[:MAX_EVIDENCE_CHARS] or None,
        })
    return {
        "entity": str(entity_name or ""),
        "attribute": str(attribute_label or ""),
        "claims": claims,
        "sources_without_value": [str(name) for name in (missing_sources or [])][:MAX_ENTRIES],
    }


def judge_consistency(
    entity_name: str,
    attribute_label: str,
    entries: List[Dict[str, Any]],
    missing_sources: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Judge one comparison. Returns {verdict, reason, model, cached}."""
    payload = build_payload(entity_name, attribute_label, entries, missing_sources)
    cache_key = "sha1:" + hashlib.sha1(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    cached = _MEMORY_CACHE.get(cache_key)
    if cached:
        return {**cached, "cached": True}
    model = config()["model"]
    result = _call_llm(payload, model)
    _MEMORY_CACHE[cache_key] = result
    return {**result, "cached": False}


def _get_client():
    global _CLIENT
    if OpenAI is None:
        raise LLMJudgeError("openai SDK 未安装，请先: pip install -r backend/requirements.txt")
    settings = config()
    if not settings["api_key"]:
        raise LLMJudgeError("缺少 DEEPSEEK_API_KEY 环境变量（参见 backend/.env.example）")
    if _CLIENT is None:
        _CLIENT = OpenAI(api_key=settings["api_key"], base_url=settings["base_url"])
    return _CLIENT


def _user_prompt(payload: Dict[str, Any]) -> str:
    return (
        f"事件: {payload['entity']}\n"
        f"属性: {payload['attribute']}\n"
        "各来源表述(JSON):\n"
        f"{json.dumps({'claims': payload['claims'], 'sources_without_value': payload['sources_without_value']}, ensure_ascii=False, indent=2)}\n"
        "请按系统指令输出JSON判定。"
    )


def _call_llm(payload: Dict[str, Any], model: str) -> Dict[str, Any]:
    try:
        client = _get_client()
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _user_prompt(payload)},
            ],
            temperature=0,
            max_tokens=300,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content or ""
        return _parse_verdict(content, model)
    except Exception as exc:  # degrade to uncertain; batch runs keep collected verdicts
        return {
            "verdict": "uncertain",
            "reason": f"LLM调用失败: {type(exc).__name__}: {exc}"[:300],
            "model": model,
        }


def _parse_verdict(content: str, model: str) -> Dict[str, Any]:
    try:
        data = json.loads(content)
    except (TypeError, ValueError):
        data = None
    if not isinstance(data, dict) or data.get("verdict") not in VERDICTS:
        snippet = " ".join(str(content).split())[:200]
        return {
            "verdict": "uncertain",
            "reason": f"LLM输出无法解析为有效判定: {snippet}",
            "model": model,
        }
    reason = " ".join(str(data.get("reason") or "").split())
    return {"verdict": data["verdict"], "reason": reason, "model": model}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
