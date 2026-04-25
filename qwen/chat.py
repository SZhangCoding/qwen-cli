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
    const fileRefs = %s;
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
            files: fileRefs,
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


def _build_file_ref(upload: dict, user_id: str) -> dict:
    """Turn an upload_file() result into the shape required by messages[].files[]."""
    now_ms = int(__import__("time").time() * 1000)
    name = upload["filename"]
    size = upload["filesize"]
    ctype = upload.get("content_type") or "application/octet-stream"
    fid = upload["file_id"]
    return {
        "type": "file",
        "file": {
            "created_at": now_ms,
            "data": {},
            "filename": name,
            "hash": None,
            "id": fid,
            "user_id": user_id,
            "meta": {
                "name": name,
                "size": size,
                "content_type": ctype,
                "parse_meta": {"parse_status": "success"},
            },
            "update_at": now_ms,
        },
        "id": fid,
        "url": upload.get("file_url") or "",
        "name": name,
        "collection_name": "",
        "progress": 0,
        "status": "uploaded",
        "size": size,
        "error": "",
        "file_type": ctype,
        "showType": "file",
        "file_class": "document",
    }


def _smart_default_timeout(thinking: bool, n_files: int) -> float:
    """根据 thinking + 附件数估算合理的 bridge.evaluate 超时.

    经验值：
    - 基础 300s 够大多数无 thinking、无附件场景
    - 每个附件读+解析约 +30s（OSS 上传 + 后端 parse）
    - thinking 模式额外 +600s（reasoning tokens 大幅拉长 SSE）
    上限 1800s，避免无限阻塞。
    """
    t = 300.0 + 30.0 * n_files
    if thinking:
        t += 600.0
    return min(t, 1800.0)


def send_message(
    bridge: Bridge,
    content: str,
    chat_id: str | None = None,
    model: str = "qwen3.6-plus",
    thinking: bool = False,
    search: bool = False,
    parent_id: str | None = None,
    project_id: str | None = None,
    files: list[str] | None = None,
    timeout: float | None = None,
) -> dict:
    """Send a message; create new chat if chat_id omitted. Returns full assistant response.

    `files` is a list of local paths to upload and attach to the message.
    `timeout` 为 None 时根据 thinking + 文件数自动估算（参 _smart_default_timeout）。
    """
    from .files import upload_file
    from .auth import check_login

    _ensure_origin(bridge)
    if not chat_id:
        chat_id = new_chat(bridge, model=model, project_id=project_id)

    file_refs: list[dict] = []
    uploads: list[dict] = []
    if files:
        user_id = (check_login(bridge).get("userInfo") or {}).get("id")
        if not user_id:
            raise RuntimeError("无法获取 user_id（检查登录状态）")
        for path in files:
            up = upload_file(bridge, path)
            uploads.append(up)
            file_refs.append(_build_file_ref(up, user_id))

    if timeout is None:
        timeout = _smart_default_timeout(thinking, len(file_refs))

    js = _JS_SEND % (
        json.dumps(chat_id),
        json.dumps(model),
        json.dumps(content),
        "true" if thinking else "false",
        "true" if search else "false",
        json.dumps(parent_id),
        json.dumps(file_refs, ensure_ascii=False),
    )
    raw = bridge.evaluate(js, timeout=timeout)
    result = json.loads(raw) if isinstance(raw, str) else raw
    if result.get("error"):
        raise RuntimeError(f"chat failed: {result}")
    if uploads:
        result["uploadedFiles"] = [{"file_id": u["file_id"], "filename": u["filename"]} for u in uploads]
    return result
