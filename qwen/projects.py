"""Projects: create / list / detail / update / delete, plus list chats within a project."""

from __future__ import annotations

import json

from .bridge import Bridge
from .auth import _ensure_origin


_DEFAULT_ICON = "icon=icon-line-rename-01&style=character-primary-text"


def _js_fetch(path: str, method: str = "GET", body: dict | None = None) -> str:
    opts = {"method": method, "headers": {"Content-Type": "application/json"}}
    if body is not None:
        opts["body"] = json.dumps(body, ensure_ascii=False)
    return (
        "(async () => {"
        f"const r = await fetch({json.dumps(path)}, {json.dumps(opts, ensure_ascii=False)});"
        "const t = await r.text();"
        "return JSON.stringify({status: r.status, body: t});"
        "})()"
    )


def _call(bridge: Bridge, path: str, method: str = "GET", body: dict | None = None) -> dict:
    _ensure_origin(bridge)
    raw = bridge.evaluate(_js_fetch(path, method, body))
    wrap = json.loads(raw) if isinstance(raw, str) else raw
    try:
        payload = json.loads(wrap["body"])
    except Exception:
        raise RuntimeError(f"non-JSON response ({wrap.get('status')}): {wrap.get('body','')[:200]}")
    if not payload.get("success"):
        raise RuntimeError(f"{method} {path} failed: {payload}")
    return payload.get("data")


def list_projects(bridge: Bridge) -> list[dict]:
    data = _call(bridge, "/api/v2/projects/")
    return data or []


def get_project(bridge: Bridge, project_id: str) -> dict:
    return _call(bridge, f"/api/v2/projects/{project_id}")


def create_project(
    bridge: Bridge,
    name: str,
    custom_instruction: str | None = None,
    icon: str = _DEFAULT_ICON,
    memory_span: str = "default",
) -> dict:
    body = {
        "name": name,
        "icon": icon,
        "memory_span": memory_span,
        "custom_instruction": custom_instruction or "",
    }
    created = _call(bridge, "/api/v2/projects/", method="POST", body=body)
    return get_project(bridge, created["id"])


def update_project(
    bridge: Bridge,
    project_id: str,
    name: str | None = None,
    custom_instruction: str | None = None,
    icon: str | None = None,
    memory_span: str | None = None,
) -> dict:
    body: dict = {}
    if name is not None:
        body["name"] = name
    if custom_instruction is not None:
        body["custom_instruction"] = custom_instruction
    if icon is not None:
        body["icon"] = icon
    if memory_span is not None:
        body["memory_span"] = memory_span
    if not body:
        raise ValueError("至少需要一个可更新字段 (--name / --instruction / --icon / --memory-span)")
    _call(bridge, f"/api/v2/projects/{project_id}", method="PUT", body=body)
    return get_project(bridge, project_id)


def delete_project(bridge: Bridge, project_id: str) -> dict:
    return _call(bridge, f"/api/v2/projects/{project_id}", method="DELETE")


def list_project_chats(bridge: Bridge, project_id: str, page: int = 1) -> list[dict]:
    data = _call(bridge, f"/api/v2/chats/?project_id={project_id}&page={page}")
    return data or []


def list_project_files(bridge: Bridge, project_id: str) -> dict:
    return _call(bridge, f"/api/v2/projects/{project_id}/files")
