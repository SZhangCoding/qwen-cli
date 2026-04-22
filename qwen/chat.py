"""Send a chat message via the completions streaming endpoint."""

from __future__ import annotations

import json

from .bridge import Bridge
from .auth import _ensure_origin

_JS_NEW_CHAT = """
(async () => {
    const body = {title: "New Chat", chat_type: "t2t", models: [%s]};
    const projectId = %s;
    if (projectId) body.project_id = projectId;
    const r = await fetch("/api/v2/chats/new", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(body)
    });
    const j = await r.json();
    return JSON.stringify(j);
})()
"""

# Consume SSE stream in-browser and return {content, reasoning, usage}
_JS_SEND = r"""
(async () => {
    const chatId = %s;
    const model = %s;
    const userContent = %s;
    const thinking = %s;
    const search = %s;
    const parentId = %s;
    const msgId = crypto.randomUUID();
    const body = {
        stream: true,
        version: "2.1",
        incremental_output: true,
        chat_id: chatId,
        chat_mode: "normal",
        model: model,
        parent_id: parentId,
        messages: [{
            fid: msgId,
            parentId: parentId,
            childrenIds: [],
            role: "user",
            content: userContent,
            user_action: "chat",
            files: [],
            timestamp: Math.floor(Date.now()/1000),
            models: [model],
            chat_type: "t2t",
            feature_config: {
                thinking_enabled: thinking,
                output_schema: "phase",
                research_mode: "normal",
                auto_thinking: false,
                thinking_mode: "Thinking",
                thinking_format: "summary",
                auto_search: search
            },
            extra: {meta: {subChatType: "t2t"}},
            sub_chat_type: "t2t",
            parent_id: parentId
        }],
        timestamp: Math.floor(Date.now()/1000)
    };
    const r = await fetch("/api/v2/chat/completions?chat_id=" + chatId, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(body)
    });
    if (!r.ok) return JSON.stringify({error: "http_" + r.status, body: await r.text().catch(()=>null)});
    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    let answer = "";
    let reasoning = "";
    let usage = null;
    let responseId = null;
    while (true) {
        const {done, value} = await reader.read();
        if (done) break;
        buf += decoder.decode(value, {stream: true});
        const lines = buf.split("\n");
        buf = lines.pop();
        for (const line of lines) {
            const trimmed = line.trim();
            if (!trimmed || !trimmed.startsWith("data:")) continue;
            const data = trimmed.substring(5).trim();
            if (!data || data === "[DONE]") continue;
            try {
                const evt = JSON.parse(data);
                if (evt["response.created"]) {
                    responseId = evt["response.created"].response_id;
                    continue;
                }
                const delta = evt.choices && evt.choices[0] && evt.choices[0].delta;
                if (delta) {
                    if (delta.phase === "answer") answer += delta.content || "";
                    else if (delta.phase === "thinking_summary") reasoning += delta.content || "";
                }
                if (evt.usage) usage = evt.usage;
            } catch (e) {}
        }
    }
    return JSON.stringify({
        chatId,
        responseId,
        content: answer,
        reasoning: reasoning || null,
        usage
    });
})()
"""


def new_chat(bridge: Bridge, model: str = "qwen3.6-plus", project_id: str | None = None) -> str:
    """Create a new chat, return chat ID. Optionally attach to a project."""
    _ensure_origin(bridge)
    raw = bridge.evaluate(_JS_NEW_CHAT % (json.dumps(model), json.dumps(project_id)))
    data = json.loads(raw) if isinstance(raw, str) else raw
    if not data.get("success"):
        raise RuntimeError(f"create chat failed: {data}")
    return data["data"]["id"]


def send_message(
    bridge: Bridge,
    content: str,
    chat_id: str | None = None,
    model: str = "qwen3.6-plus",
    thinking: bool = False,
    search: bool = False,
    parent_id: str | None = None,
    project_id: str | None = None,
    timeout: float = 300.0,
) -> dict:
    """Send a message; create new chat if chat_id omitted. Returns full assistant response."""
    _ensure_origin(bridge)
    if not chat_id:
        chat_id = new_chat(bridge, model=model, project_id=project_id)
    js = _JS_SEND % (
        json.dumps(chat_id),
        json.dumps(model),
        json.dumps(content),
        "true" if thinking else "false",
        "true" if search else "false",
        json.dumps(parent_id),
    )
    raw = bridge.evaluate(js, timeout=timeout)
    result = json.loads(raw) if isinstance(raw, str) else raw
    if result.get("error"):
        raise RuntimeError(f"chat failed: {result}")
    return result
