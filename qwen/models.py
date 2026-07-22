"""Available model list."""

from __future__ import annotations

import json

from .bridge import Bridge
from .auth import _ensure_origin

_JS_LIST = """
(async () => {
    const r = await fetch("/api/models");
    const j = await r.json();
    const items = j.data || j || [];
    return JSON.stringify(items.map(m => {
        const meta = (m.info && m.info.meta) || {};
        return {
            id: m.id,
            name: (m.info && m.info.name) || m.name || null,
            short_description: meta.short_description || null,
            max_context_length: meta.max_context_length || null,
            capabilities: meta.capabilities || null
        };
    }));
})()
"""


def list_models(bridge: Bridge) -> list[dict]:
    _ensure_origin(bridge)
    raw = bridge.evaluate(_JS_LIST)
    return json.loads(raw) if isinstance(raw, str) else []


_JS_META = """
(async () => {
    const r = await fetch("/api/models");
    const j = await r.json();
    const m = (j.data || j || []).find(x => x.id === %s);
    return JSON.stringify(m ? ((m.info && m.info.meta) || {}) : null);
})()
"""


def get_model_meta(bridge: Bridge, model_id: str) -> dict | None:
    """取单个模型的 meta。/api/models 不在云盾签名列表里，调用成本低且不增加风控风险。"""
    raw = bridge.evaluate(_JS_META % json.dumps(model_id))
    return json.loads(raw) if isinstance(raw, str) else None


def thinking_is_mandatory(meta: dict | None) -> bool:
    """该模型是否禁止关闭思考模式。

    站点用 meta.think_skip 表达"能不能跳过思考"（2026-07-23 实测全模型对比）：
      {"enable": false} → 不允许关，传 thinking_enabled:false 会被服务端拒为
                          invalid_input（目前只有 qwen3.8-max-preview 是这个值）
      {"enable": true}  → 允许关
      null              → 老式模型，不限制（如 qwen3.7-max，实测 --no-thinking 正常）
    """
    if not meta:
        return False
    skip = meta.get("think_skip")
    return isinstance(skip, dict) and skip.get("enable") is False
