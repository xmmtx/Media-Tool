"""字幕组（release group）识别回退：LLM 结构化输出。

当本地字幕组字典未命中时，把原始文件名交给配置的 LLM（OpenAI 兼容或
Anthropic），要求其按 JSON Schema 返回 ``{"subgroup": "...", "aliases": [...]}``。

- 配置项 ``llm.enabled`` / ``llm.provider``（openai|anthropic）/
  ``llm.base_url`` / ``llm.api_key`` / ``llm.model``，来自 ``config.json``。
- 未启用或未配置 Key 时 ``available`` 为 ``False``，调用方应把文件推入
  手动干预队列。
- 解析失败（网络/超时/非法 JSON/空结果）统一返回 ``None``，绝不抛异常。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Dict, Optional

# 系统提示词：要求输出字幕组名及其已知别名
_SYSTEM_PROMPT = (
    "You are a media torrent subtitle-group (字幕组) parser. "
    "Given a raw media filename, extract the release/subtitle group name. "
    'Reply ONLY with JSON matching: {"subgroup": "<group name or empty string>", '
    '"aliases": ["<known name variants, may be empty>"]}. '
    'If no group is identifiable, set "subgroup" to an empty string.'
)

# Anthropic tool_use 用的 JSON Schema
_JSON_SCHEMA: Dict = {
    "type": "object",
    "properties": {
        "subgroup": {"type": "string"},
        "aliases": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["subgroup", "aliases"],
}

_DEFAULT_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
}


class LlmSubgroupProvider:
    """字幕组识别回退提供者（非 BaseProvider，专用于组名解析）。"""

    name = "llm"

    def __init__(self, config=None) -> None:
        self.config = config
        self._load_config()

    def _load_config(self) -> None:
        g = self._cfg
        self.enabled = bool(g("llm.enabled", False))
        self.provider = str(g("llm.provider", "openai") or "openai").lower()
        self.base_url = str(g("llm.base_url", "") or "")
        self.api_key = str(g("llm.api_key", "") or "")
        self.model = str(g("llm.model", "") or "")

    def _cfg(self, key: str, default=None):
        if self.config is None:
            return default
        getter = getattr(self.config, "get", None)
        if getter is None:
            return self.config.get(key, default) if isinstance(self.config, dict) else default
        return getter(key, default)

    @property
    def available(self) -> bool:
        return bool(self.enabled and self.api_key)

    # ── 对外接口 ──────────────────────────────────────────────────────────

    def parse_subgroup(self, raw: str, timeout: float = 15.0) -> Optional[Dict]:
        """解析原始文件名中的字幕组名。

        返回 ``{"subgroup": str, "aliases": [str]}``；无法识别或出错时返回 ``None``。
        """
        if not self.available or not raw:
            return None
        try:
            if self.provider == "anthropic":
                content = self._call_anthropic(raw, timeout)
            else:
                content = self._call_openai(raw, timeout)
        except (urllib.error.URLError, OSError, ValueError, KeyError,
                TimeoutError, json.JSONDecodeError):
            return None
        if not content:
            return None
        data = self._extract_json(content)
        if not data:
            return None
        subgroup = str(data.get("subgroup") or "").strip()
        if not subgroup:
            return None
        aliases = data.get("aliases") or []
        if isinstance(aliases, str):
            aliases = [aliases]
        aliases = [str(a).strip() for a in aliases if str(a).strip() and str(a).strip() != subgroup]
        return {"subgroup": subgroup, "aliases": aliases}

    # ── OpenAI 兼容实现 ───────────────────────────────────────────────────

    def _call_openai(self, raw: str, timeout: float) -> str:
        base = (self.base_url or _DEFAULT_BASE_URLS["openai"]).rstrip("/")
        payload = {
            "model": self.model or "gpt-4o-mini",
            "temperature": 0,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": raw},
            ],
            "response_format": {"type": "json_object"},
        }
        req = urllib.request.Request(
            f"{base}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"]

    # ── Anthropic 实现 ────────────────────────────────────────────────────

    def _call_anthropic(self, raw: str, timeout: float) -> str:
        base = (self.base_url or _DEFAULT_BASE_URLS["anthropic"]).rstrip("/")
        payload = {
            "model": self.model or "claude-3-5-haiku-latest",
            "max_tokens": 256,
            "system": _SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": raw}],
            "tools": [
                {
                    "name": "parse_subgroup",
                    "description": "Extract subtitle group from a filename.",
                    "input_schema": _JSON_SCHEMA,
                }
            ],
            "tool_choice": {"type": "tool", "name": "parse_subgroup"},
        }
        req = urllib.request.Request(
            f"{base}/messages",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        for block in data.get("content", []) or []:
            if block.get("type") == "tool_use":
                return json.dumps(block.get("input", {}))
        return ""

    @staticmethod
    def _extract_json(content: str) -> Optional[Dict]:
        """从模型输出中提取 JSON（容忍代码块/前后杂讯）。"""
        text = content.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
