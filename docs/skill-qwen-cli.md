---
name: qwen-cli
description: Use when the user wants to chat with Qwen (通义千问), query chat.qwen.ai, list or read Qwen conversation history, or automate interactions with Qwen models (Qwen3.6-Plus, Qwen3-Max, Qwen3-Coder etc.) programmatically. Invoke when user mentions "qwen", "通义千问", "千问", or asks to send messages / get responses from Qwen.
---

# qwen-cli

Automates chat.qwen.ai (Alibaba Qwen / 通义千问) via the kimi-webbridge daemon using the user's real logged-in browser session.

## Prerequisites

1. kimi-webbridge daemon running:
   ```bash
   ~/.kimi-webbridge/bin/kimi-webbridge status
   ```
   If not running: invoke the `kimi-webbridge` skill.

2. CLI available:
   ```bash
   python3 ~/Documents/GitHub/qwen-cli/cli.py --help
   ```

3. Logged in to chat.qwen.ai in Chrome:
   ```bash
   python3 ~/Documents/GitHub/qwen-cli/cli.py check-login
   ```

## Commands

| Command | Args / Flags | Returns |
|---------|-------------|---------|
| `captcha-status` | — | `{ready, hasUidToken, captcha, captchaKind}`；挂着滑块时 exit 3 |
| `check-login` | — | `{loggedIn, userInfo: {id, email, name, role}}` |
| `list-models` | — | `[{id, name, short_description, max_context_length, capabilities}]` |
| `list-conversations` | `[--page N]` | `[{id, title, updated_at, created_at}]` |
| `get-conversation` | `--chat-id ID` | `{id, title, messages: [{role, content, model, timestamp}]}` |
| `delete-conversation` | `--chat-id ID` | `{status}` |
| `new-chat` | `[--model MODEL] [--project-id ID]` | `{chatId}` |
| `chat` | `--content "..." [--chat-id ID] [--model MODEL] [--no-thinking] [--search] [--project-id ID] [--file PATH ...]` | `{chatId, responseId, content, reasoning, usage, uploadedFiles?}` |
| `list-projects` | — | `[{id, name, custom_instruction, ...}]` |
| `get-project` | `--project-id ID` | `{id, name, custom_instruction, memory_span, ...}` |
| `create-project` | `--name NAME [--instruction TXT \| --instruction-file PATH]` | `{id, name, custom_instruction, ...}` |
| `update-project` | `--project-id ID [--name] [--instruction \| --instruction-file]` | 更新后的 project 对象 |
| `delete-project` | `--project-id ID` | `{status}` |
| `list-project-chats` | `--project-id ID [--page N]` | `[{id, title, project_id, ...}]` |
| `list-project-files` | `--project-id ID` | `{project_id, files: [...]}` |
| `upload-file` | `--file PATH [--project-id ID] [--filetype EXT] [--content-type auto\|MIME] [--no-wait]` | `{file_id, filename, parse, ...}` |
| `delete-project-file` | `--project-id ID --file-id ID` | `{status}` |

Run `python3 ~/Documents/GitHub/qwen-cli/cli.py <command> --help` for full flags.

## Output Format

All commands return JSON on stdout:
```json
{"ok": true, "data": ...}
```

退出码：`0` 成功 / `1` 业务性失败（如未登录、对话不存在）/ `2` 运行时错误 /
**`3` 需要人工过滑块验证**（`{"error":{"code":"captcha_required"}}`）。

## 滑块验证（人机验证）

chat.qwen.ai 用阿里云盾（baxia + AWSC）给下列接口注入 `bx-ua` / `bx-umidtoken`
签名头：`/api/v2/chat/completions`、`/api/v2/chats*`、`/api/v1/chats`、
`/api/v{1,2}/files/getstsToken`。签名不合格 → WAF 下发 punish → 弹滑块。

**什么时候会触发（2026-07-23 实测结论）：**

1. **页面冷加载后约 1.3s 内打签名接口** —— 这是主因。云盾 `preRequest` 在 AWSC
   未就绪时最多等 3.2s 就**照发不误**，带的是 `defaultToken1/3` 占位 token。
   实测冷加载时序：0.9s 时 `document.readyState==="complete"` 且
   `baxiaInitialized===true`，但 uid token 仍为空——**这两个信号都不足以判断就绪**。
2. 高频连打签名接口（尤其新建对话 / 上传文件），触发 WAF 频率风控。
3. 浏览器会话本身已被判定风险（换 IP、长期挂机等）。

**CLI 已内置的规避措施**（`qwen/antibot.py`）：每条命令在打任何接口前，都会轮询站点
自己的就绪判定（`baxiaCommon && __baxia__.baxiaPromptInit && baxiaInitialized &&
getFYModule.getUidToken()`）直到为真，再发请求；同时探测滑块 DOM 与 WAF punish 响应，
命中就以 `captcha_required` / exit 3 快速失败。

**被拦时的表现（实测）：** punish 的请求**不会返回 4xx/5xx，而是一直挂着**等人过验证。
所以"chat 卡住直到 timeout"最可能的原因是弹了滑块，而不是模型慢或 timeout 设小了。
CLI 已在超时后自动改判为 `captcha_required`，别再去调大 `--timeout`。

**因此调用方要做的：**

- **不要重试 exit 3**。滑块必须人工过，重试只会加重风控。看到 exit 3 就停下来，
  提示用户去浏览器窗口手动完成滑块，然后再继续。
- **不要绕过 CLI 直接 `evaluate` 打这些接口**。裸 `fetch` 不经过就绪门控，正是踩坑路径。
- **批量任务要限速**：连续多轮时每次调用之间留 2–3s 间隔，避免命中频率风控。
- 复用同一个 session 的热 tab 比反复 `close_session` 更安全 —— 每次关了重开都会重新
  经历一次冷加载窗口。批量任务开一次 tab 用到底，结束再关。
- 排查时先跑 `captcha-status` 确认是"没就绪"还是"已经挂了滑块"。

## Common Workflows

**单轮问答（新建对话并发送）：**
```bash
python3 ~/Documents/GitHub/qwen-cli/cli.py chat \
  --content "用一句话解释什么是熵"
# 返回内容中的 chatId 可用于后续追加
```

**追加消息到已有对话：**
```bash
python3 ~/Documents/GitHub/qwen-cli/cli.py chat \
  --chat-id <chatId> \
  --content "继续深入讲讲"
```

**开启思考模式 + 联网搜索：**
```bash
python3 ~/Documents/GitHub/qwen-cli/cli.py chat \
  --content "明天慕尼黑天气" --search   # thinking 默认开；用 --no-thinking 关
```

**读取历史对话：**
```bash
# 列表
python3 ~/Documents/GitHub/qwen-cli/cli.py list-conversations
# 详情
python3 ~/Documents/GitHub/qwen-cli/cli.py get-conversation --chat-id <ID>
```

**从文件读取长 prompt：**
```bash
python3 ~/Documents/GitHub/qwen-cli/cli.py chat \
  --content-file /abs/path/prompt.txt
```

**带文件提问（对话内附件）：**
```bash
python3 ~/Documents/GitHub/qwen-cli/cli.py chat \
  --file /abs/path/report.pdf \
  --content "总结这份报告的核心观点"
# 可多次 --file 附多个文件
```
CLI 会自动完成：上传 OSS → parse → 在本条消息里作为附件引用。

**项目（Projects）工作流：**
```bash
# 创建带自定义指令的项目
python3 ~/Documents/GitHub/qwen-cli/cli.py create-project \
  --name "番茄小说写作" --instruction-file /abs/path/system_prompt.md

# 在项目下起一轮对话（自动应用项目指令）
python3 ~/Documents/GitHub/qwen-cli/cli.py chat \
  --project-id <PROJECT_ID> --content "写一个开头"

# 列出项目内所有对话
python3 ~/Documents/GitHub/qwen-cli/cli.py list-project-chats --project-id <PROJECT_ID>

# 修改项目指令
python3 ~/Documents/GitHub/qwen-cli/cli.py update-project \
  --project-id <PROJECT_ID> --instruction-file /abs/path/new_prompt.md
```

## Supported Models

用 `list-models` 查当前账号实际可用的模型（不要凭记忆猜 id）：
```bash
python3 ~/Documents/GitHub/qwen-cli/cli.py list-models
```
返回 `{id, name, short_description, max_context_length, capabilities}`。

常见值（2026-07-22 实测）：
- `qwen3.8-max-preview`（**默认**，旗舰预览，1M 上下文）
- `qwen3.7-max` / `qwen3.7-plus`
- `qwen3.6-plus` / `qwen3.6-max-preview`
- `qwen3.5-397b-a17b` / `qwen3.5-flash`
- `qwen3-coder-plus` / `qwen3-vl-plus`

## Known Limitations

- 依赖浏览器 session 认证，账号会话失效时需手动重登
- **部分模型禁止关思考**：模型 meta 里 `think_skip.enable === false` 表示不能跳过思考
  （目前只有默认模型 `qwen3.8-max-preview`），传 `--no-thinking` 会被服务端拒为
  `invalid_input`。CLI 已加预检并直接报错说明。要关思考请换 `qwen3.7-max`
  （`think_skip` 为 null，不限制）
- 单次 chat 请求 timeout 默认按 thinking + 文件数自动估算（基础 300s + 每文件 30s + thinking 600s，上限 1800s）；可用 `--timeout N` 覆盖
- 多轮追加需要传 `--chat-id` 和 `--parent-id`，否则分支可能不正确
- 上传文件命令 `upload-file` 可单独使用；`chat --file` 会自动上传后引用

## 运行时建议

1. **每个 skill 必须用自己独占的 session 名，任务全部跑完再关**——避免与其他并发调
   qwen-cli 的 skill 抢同一 tab 而产生串行排队。实证：同 session 长轮串行（B 等 A 21s），
   多 session 减半排队但不消除。约定命名 `qwen-<skillname>`（如 `qwen-elternbrief`）。

   ⚠️ 关 session 的粒度是**整个任务**，不是每次调用。每次 `close_session` 后再调用都要
   重新走一遍页面冷加载 + 云盾就绪等待（约 4.5s），且多一次落进滑块窗口的机会。
   模板：
   ```bash
   SESS=qwen-<yourskill>

   # 开本 skill 独占 tab
   curl -s -X POST http://127.0.0.1:10086/command -H "Content-Type: application/json" \
     -d "{\"action\":\"find_tab\",\"args\":{\"url\":\"https://chat.qwen.ai/\",\"newTab\":true},\"session\":\"$SESS\"}"

   # 调用：--session 是全局 flag，必须放 chat 之前
   python3 ~/Documents/GitHub/qwen-cli/cli.py --session "$SESS" chat --content-file /tmp/prompt.txt

   # 用完关掉
   curl -s -X POST http://127.0.0.1:10086/command -H "Content-Type: application/json" \
     -d "{\"action\":\"close_session\",\"session\":\"$SESS\"}"
   ```
2. **元信息标题污染评审**：内容送 LLM 评审 PK 时，prompt 里去掉"生成精修版/对齐 XXX"等元说明（实测损失 5+ 分）

### 模型选项

⚠️ 默认已切到 `qwen3.8-max-preview`（旗舰预览）。该模型**必须开着 thinking**，
见 Known Limitations。历史教训：`max` 系 + thinking + 多文件的组合在番茄章节级任务上观察到过响应漂移 —— 长任务如遇输出不稳，先降到 `qwen3.7-plus` 或关 thinking 再排查，不要一上来怀疑 prompt。
