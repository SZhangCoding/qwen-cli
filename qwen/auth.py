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
    """确保本 session 有一个停在 chat.qwen.ai、且云盾签名链路已就绪的 tab。

    全新 session 还没有 tab 时 evaluate 会 502，此时应当去开 tab 而不是抛错
    （close_session 之后再调用就是这个场景）。

    开完 tab 必须等 antibot.wait_ready()：页面冷加载后约 1.3s 内 bx-ua 签名是退化的，
    这个窗口里打签名接口会直接把账号推去滑块验证。详见 antibot 模块 docstring。
    """
    from .bridge import BridgeError
    from .antibot import wait_ready

    try:
        url = bridge.evaluate("location.href") or ""
    except BridgeError:
        url = ""
    if "chat.qwen.ai" not in url:
        bridge.navigate_or_reuse("https://chat.qwen.ai/")
        bridge.wait_for_document_ready(timeout=15.0)
    # tab 已经热着时 wait_ready 首轮即返回（一次 evaluate，~10ms），不构成额外开销
    wait_ready(bridge, timeout=20.0)


def check_login(bridge: Bridge) -> dict:
    _ensure_origin(bridge)
    raw = bridge.evaluate(_JS)
    return json.loads(raw) if isinstance(raw, str) else {}
