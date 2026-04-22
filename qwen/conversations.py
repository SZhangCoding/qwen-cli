"""Conversation list and detail."""

from __future__ import annotations

import json

from .bridge import Bridge
from .auth import _ensure_origin

_JS_LIST = """
(async () => {
    const r = await fetch("/api/v2/chats/?page=%d&exclude_project=%s");
    const j = await r.json();
    return JSON.stringify(j.data || []);
})()
"""

_JS_DETAIL = """
(async () => {
    const r = await fetch("/api/v2/chats/%s");
    const j = await r.json();
    const d = j.data;
    if (!d) return JSON.stringify({error: "not found"});
    const messages = ((d.chat && d.chat.history && d.chat.history.messages) || {});
    const ordered = Object.values(messages).sort((a, b) => (a.timestamp || 0) - (b.timestamp || 0));
    return JSON.stringify({
        id: d.id,
        title: d.title,
        messages: ordered.map(m => {
            let content = m.content || "";
            if (!content && m.content_list) {
                content = m.content_list
                    .filter(c => c.phase === "answer")
                    .map(c => c.content).join("");
            }
            return {
                id: m.id,
                role: m.role,
                content,
                model: m.model || null,
                timestamp: m.timestamp || null
            };
        })
    });
})()
"""


def list_conversations(bridge: Bridge, page: int = 1, exclude_project: bool = True) -> list[dict]:
    _ensure_origin(bridge)
    raw = bridge.evaluate(_JS_LIST % (page, "true" if exclude_project else "false"))
    return json.loads(raw) if isinstance(raw, str) else []


def get_conversation(bridge: Bridge, chat_id: str) -> dict:
    _ensure_origin(bridge)
    raw = bridge.evaluate(_JS_DETAIL % chat_id)
    return json.loads(raw) if isinstance(raw, str) else {}


def delete_conversation(bridge: Bridge, chat_id: str) -> dict:
    _ensure_origin(bridge)
    raw = bridge.evaluate(
        f'(async () => {{ const r = await fetch("/api/v2/chats/{chat_id}", '
        '{method: "DELETE"}); return JSON.stringify({status: r.status}); })()'
    )
    return json.loads(raw) if isinstance(raw, str) else {}
