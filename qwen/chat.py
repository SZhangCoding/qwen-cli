"""Send a chat message via the completions streaming endpoint."""

from __future__ import annotations

import json

from .bridge import Bridge
from .auth import _ensure_origin
from .antibot import (
    CaptchaRequired,
    assert_no_captcha,
    looks_like_punish,
    reclassify_timeout,
)
from .bridge import BridgeTimeout

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
    const text = await r.text();
    try {
        return JSON.stringify(JSON.parse(text));
    } catch (e) {
        // 被 WAF 拦下时这里是 punish HTML，交给 Python 侧分类
        return JSON.stringify({success: false, status: r.status, body: text.slice(0, 2000)});
    }
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
    if (!r.ok) return JSON.stringify({
        error: "http_" + r.status,
        status: r.status,
        contentType: r.headers.get("content-type"),
        body: await r.text().catch(()=>null)
    });
    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    let answer = "";
    let reasoning = "";
    let usage = null;
    let responseId = null;
    let streamError = null;
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
                // 服务端会在流里下发 error 事件（HTTP 仍是 200）。漏掉它就会变成
                // "content 为空但 ok:true"，把服务端拒绝伪装成模型没话说。
                if (evt.error) { streamError = evt.error; continue; }
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
        usage,
        streamError
    });
})()
"""


def new_chat(bridge: Bridge, model: str = "qwen3.8-max-preview", project_id: str | None = None) -> str:
    """Create a new chat, return chat ID. Optionally attach to a project."""
    _ensure_origin(bridge)
    try:
        raw = bridge.evaluate(_JS_NEW_CHAT % (json.dumps(model), json.dumps(project_id)))
    except BridgeTimeout as e:
        reclassify_timeout(bridge, e)
        raise
    data = json.loads(raw) if isinstance(raw, str) else raw
    if not data.get("success"):
        if looks_like_punish(data.get("status"), data.get("body")):
            raise CaptchaRequired(
                "新建对话被云盾拦截（WAF punish）。请在浏览器窗口完成滑块后重试。"
            )
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
    model: str = "qwen3.8-max-preview",
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

    if not thinking:
        # 有的模型禁止关思考，服务端只回一句 invalid_input，不预检的话极难定位
        from .models import get_model_meta, thinking_is_mandatory
        if thinking_is_mandatory(get_model_meta(bridge, model)):
            raise ValueError(
                f"模型 {model} 不支持关闭思考模式（think_skip.enable=false），"
                "带 --no-thinking 会被服务端拒绝。请去掉 --no-thinking，"
                "或换用支持关闭的模型（如 qwen3.7-max）。"
            )

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
    try:
        raw = bridge.evaluate(js, timeout=timeout)
    except BridgeTimeout as e:
        reclassify_timeout(bridge, e)
        raise
    result = json.loads(raw) if isinstance(raw, str) else raw
    if result.get("error"):
        # 被云盾拦下时返回的是 HTML punish 页而不是 SSE，报"chat failed"会误导排查方向
        if looks_like_punish(result.get("status"), result.get("body")):
            raise CaptchaRequired(
                "chat.qwen.ai 对本次请求下发了人机验证（WAF punish）。"
                "请在浏览器窗口完成滑块后重试；若频繁出现，降低调用频率。"
            )
        assert_no_captcha(bridge)
        raise RuntimeError(f"chat failed: {result}")
    if result.get("streamError"):
        err = result["streamError"]
        raise RuntimeError(
            f"服务端拒绝了请求: {err.get('code')} — {err.get('details') or err}"
        )
    if uploads:
        result["uploadedFiles"] = [{"file_id": u["file_id"], "filename": u["filename"]} for u in uploads]
    return result
