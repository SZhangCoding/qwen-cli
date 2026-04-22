"""qwen-cli — chat.qwen.ai automation via kimi-webbridge."""

from __future__ import annotations

import argparse
import json
import sys

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def _out(data: dict, exit_code: int = 0) -> None:
    print(json.dumps({"ok": exit_code == 0, **data}, ensure_ascii=False, indent=2))
    sys.exit(exit_code)


def _bridge(args: argparse.Namespace):
    from qwen.bridge import Bridge
    return Bridge(session=args.session)


# ─── subcommands ─────────────────────────────────────────────────────────────

def cmd_check_login(args):
    from qwen.auth import check_login
    result = check_login(_bridge(args))
    _out({"data": result}, exit_code=0 if result.get("loggedIn") else 1)


def cmd_list_conversations(args):
    from qwen.conversations import list_conversations
    convs = list_conversations(_bridge(args), page=args.page)
    _out({"data": convs})


def cmd_get_conversation(args):
    from qwen.conversations import get_conversation
    detail = get_conversation(_bridge(args), args.chat_id)
    if detail.get("error"):
        _out({"error": {"code": "not_found", "message": detail["error"]}}, exit_code=1)
    _out({"data": detail})


def cmd_delete_conversation(args):
    from qwen.conversations import delete_conversation
    result = delete_conversation(_bridge(args), args.chat_id)
    _out({"data": result})


def cmd_chat(args):
    from qwen.chat import send_message
    content = open(args.content_file).read().strip() if args.content_file else args.content
    if not content:
        _out({"error": {"code": "missing_args", "message": "需要 --content 或 --content-file"}}, exit_code=2)
    result = send_message(
        _bridge(args),
        content=content,
        chat_id=args.chat_id,
        model=args.model,
        thinking=args.thinking,
        search=args.search,
        parent_id=args.parent_id,
        project_id=args.project_id,
    )
    _out({"data": result})


def cmd_new_chat(args):
    from qwen.chat import new_chat
    chat_id = new_chat(_bridge(args), model=args.model, project_id=args.project_id)
    _out({"data": {"chatId": chat_id}})


def cmd_list_projects(args):
    from qwen.projects import list_projects
    _out({"data": list_projects(_bridge(args))})


def cmd_get_project(args):
    from qwen.projects import get_project
    _out({"data": get_project(_bridge(args), args.project_id)})


def cmd_create_project(args):
    from qwen.projects import create_project
    instruction = open(args.instruction_file).read() if args.instruction_file else args.instruction
    _out({"data": create_project(
        _bridge(args),
        name=args.name,
        custom_instruction=instruction,
    )})


def cmd_update_project(args):
    from qwen.projects import update_project
    instruction = None
    if args.instruction_file:
        instruction = open(args.instruction_file).read()
    elif args.instruction is not None:
        instruction = args.instruction
    _out({"data": update_project(
        _bridge(args),
        args.project_id,
        name=args.name,
        custom_instruction=instruction,
    )})


def cmd_delete_project(args):
    from qwen.projects import delete_project
    _out({"data": delete_project(_bridge(args), args.project_id)})


def cmd_list_project_chats(args):
    from qwen.projects import list_project_chats
    _out({"data": list_project_chats(_bridge(args), args.project_id, page=args.page)})


def cmd_list_project_files(args):
    from qwen.projects import list_project_files
    _out({"data": list_project_files(_bridge(args), args.project_id)})


# ─── parser ──────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="qwen-cli",
        description="chat.qwen.ai (Qwen) CLI via kimi-webbridge",
    )
    p.add_argument("--session", default="qwen", help="webbridge session name")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("check-login", help="检查登录状态").set_defaults(func=cmd_check_login)

    sp = sub.add_parser("list-conversations", help="列出历史对话")
    sp.add_argument("--page", type=int, default=1)
    sp.set_defaults(func=cmd_list_conversations)

    sp = sub.add_parser("get-conversation", help="获取对话完整内容")
    sp.add_argument("--chat-id", required=True)
    sp.set_defaults(func=cmd_get_conversation)

    sp = sub.add_parser("delete-conversation", help="删除对话")
    sp.add_argument("--chat-id", required=True)
    sp.set_defaults(func=cmd_delete_conversation)

    sp = sub.add_parser("new-chat", help="新建对话，仅返回 chatId")
    sp.add_argument("--model", default="qwen3.6-plus")
    sp.add_argument("--project-id", help="挂到指定 project 下")
    sp.set_defaults(func=cmd_new_chat)

    sp = sub.add_parser("list-projects", help="列出所有项目")
    sp.set_defaults(func=cmd_list_projects)

    sp = sub.add_parser("get-project", help="获取项目详情")
    sp.add_argument("--project-id", required=True)
    sp.set_defaults(func=cmd_get_project)

    sp = sub.add_parser("create-project", help="创建新项目")
    sp.add_argument("--name", required=True)
    sp.add_argument("--instruction", help="自定义指令")
    sp.add_argument("--instruction-file", help="从文件读取指令内容")
    sp.set_defaults(func=cmd_create_project)

    sp = sub.add_parser("update-project", help="更新项目名称 / 指令")
    sp.add_argument("--project-id", required=True)
    sp.add_argument("--name")
    sp.add_argument("--instruction")
    sp.add_argument("--instruction-file")
    sp.set_defaults(func=cmd_update_project)

    sp = sub.add_parser("delete-project", help="删除项目")
    sp.add_argument("--project-id", required=True)
    sp.set_defaults(func=cmd_delete_project)

    sp = sub.add_parser("list-project-chats", help="列出项目内对话")
    sp.add_argument("--project-id", required=True)
    sp.add_argument("--page", type=int, default=1)
    sp.set_defaults(func=cmd_list_project_chats)

    sp = sub.add_parser("list-project-files", help="列出项目已上传文件")
    sp.add_argument("--project-id", required=True)
    sp.set_defaults(func=cmd_list_project_files)

    sp = sub.add_parser("chat", help="发送消息（新对话或追加到已有对话）")
    sp.add_argument("--content", help="消息内容")
    sp.add_argument("--content-file", help="从文件读取消息内容")
    sp.add_argument("--chat-id", help="已有对话 ID；省略则新建")
    sp.add_argument("--parent-id", help="父消息 ID（追加到已有对话时建议指定）")
    sp.add_argument("--model", default="qwen3.6-plus")
    sp.add_argument("--thinking", action="store_true", help="开启思考模式")
    sp.add_argument("--search", action="store_true", help="开启联网搜索")
    sp.add_argument("--project-id", help="新对话挂到指定 project 下（仅新建时生效）")
    sp.set_defaults(func=cmd_chat)

    return p


def main():
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except Exception as e:
        _out({"error": {"code": "runtime_error", "message": str(e)}}, exit_code=2)


if __name__ == "__main__":
    main()
