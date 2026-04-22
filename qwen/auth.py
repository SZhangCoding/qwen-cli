"""Login status via /api/v1/auths/."""

from __future__ import annotations

import json

from .bridge import Bridge

_JS = """
(async () => {
    try {
        const r = await fetch("/api/v1/auths/");
        if (r.status === 401 || r.status === 403) return JSON.stringify({loggedIn: false});
        const j = await r.json();
        return JSON.stringify({
            loggedIn: true,
            userInfo: {id: j.id, email: j.email, name: j.name, role: j.role}
        });
    } catch (e) {
        return JSON.stringify({loggedIn: false, error: String(e)});
    }
})()
"""


def _ensure_origin(bridge: Bridge) -> None:
    url = bridge.evaluate("location.href") or ""
    if "chat.qwen.ai" not in url:
        bridge.navigate("https://chat.qwen.ai/")


def check_login(bridge: Bridge) -> dict:
    _ensure_origin(bridge)
    raw = bridge.evaluate(_JS)
    return json.loads(raw) if isinstance(raw, str) else {}
