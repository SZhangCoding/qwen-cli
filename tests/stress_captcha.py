"""滑块（阿里云盾）压力测试 —— 带熔断的渐进加压。

目的：验证 antibot 就绪门控在负载下能挡住滑块触发。**任一步检测到滑块立即中止**，
不盲目加压（加压本身就有把滑块打出来的风险，与目标相反）。

三个阶段，逐级贴近真实：
  1. 冷启动    close_session → 重开 → 立即打签名接口（滑块主诱因：退化签名窗口）
  2. 热连打    单 tab 按 gap 连续打签名接口（频率风控）
  3. 真实负载  用 chat/completions 实际发消息，含多轮追加（--phase c，会消耗 token）

探针默认用 new-chat：签名接口但不消耗 token、不产内容，是验证防滑块路径最干净的信号，
每次建完即删。阶段 3 才用真实 chat。

用法：
    python3 tests/stress_captcha.py                    # 阶段 1+2（不烧 token）
    python3 tests/stress_captcha.py --phase c          # 阶段 3（真实 chat）
    python3 tests/stress_captcha.py --cold 12 --hot 15 --hot-gap 1.0

注意：需要 kimi-webbridge 在跑、且已登录 chat.qwen.ai。这是对真实站点的联网测试，
不是离线单测——会占用账号会话，请勿高频反复运行。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qwen.bridge import Bridge, BridgeTimeout  # noqa: E402
from qwen.auth import _ensure_origin  # noqa: E402
from qwen.antibot import get_state, CaptchaRequired  # noqa: E402
from qwen.chat import new_chat, send_message  # noqa: E402
from qwen.conversations import delete_conversation  # noqa: E402

PROBE_MODEL = "qwen3.7-max"          # 探针与模型无关，选个轻的
CHAT_MODEL = "qwen3.8-max-preview"   # 阶段 3 用真实默认旗舰（thinking 必开）


class Tripped(Exception):
    """熔断：检测到滑块。"""


def check(b: Bridge, where: str) -> dict:
    st = get_state(b)
    if st.get("captcha"):
        raise Tripped(f"[{where}] kind={st.get('captchaKind')} trace={st.get('traceId')}")
    return st


def _safe_close(sess: str) -> None:
    try:
        Bridge(session=sess)._call("close_session", None)
    except Exception:
        pass


def _safe_delete(b: Bridge, cid: str) -> None:
    try:
        delete_conversation(b, cid)
        time.sleep(0.5)
    except Exception:
        pass


def phase_cold(sess: str, n: int, gap: float, log: dict) -> None:
    print(f"=== 阶段1 冷启动 x{n}（gap {gap}s）===", flush=True)
    for i in range(n):
        _safe_close(sess)
        time.sleep(0.5)
        b = Bridge(session=sess)
        t0 = time.time()
        _ensure_origin(b)                       # 内含 wait_ready 门控
        ready_dt = time.time() - t0
        st = check(b, f"cold#{i} post-ready")
        cid = new_chat(b, model=PROBE_MODEL)    # 签名写
        check(b, f"cold#{i} post-newchat")
        _safe_delete(b, cid)
        log["cold"].append({"i": i, "ready_s": round(ready_dt, 2), "hasUid": st.get("hasUidToken")})
        print(f"  cold#{i}: ready {ready_dt:.2f}s hasUid={st.get('hasUidToken')}", flush=True)
        if i < n - 1:
            time.sleep(gap)


def phase_hot(sess: str, n: int, gap: float, log: dict) -> None:
    print(f"=== 阶段2 热连打 x{n}（gap {gap}s）===", flush=True)
    b = Bridge(session=sess)
    _ensure_origin(b)
    for i in range(n):
        t0 = time.time()
        cid = new_chat(b, model=PROBE_MODEL)
        dt = time.time() - t0
        st = check(b, f"hot#{i} post-newchat")
        _safe_delete(b, cid)
        log["hot"].append({"i": i, "newchat_s": round(dt, 2)})
        print(f"  hot#{i}: {dt:.2f}s ready={st.get('ready')}", flush=True)
        if i < n - 1:
            time.sleep(gap)


_SINGLE = ["用一个字回答：1+1=？", "用一个字回答：天空的颜色？", "用一个字回答：水的化学式首字母？"]
_MULTI = ["记住数字 7。只回复：好", "把它加 3。只回复结果数字", "再乘 2。只回复结果数字"]


def phase_chat(sess: str, gap: float, log: dict) -> list[str]:
    """真实 chat 负载：单轮 x3 + 多轮 x3。返回创建的 chatId 供清理。"""
    print(f"=== 阶段C 真实负载（{CHAT_MODEL}, gap {gap}s）===", flush=True)
    b = Bridge(session=sess)
    _ensure_origin(b)
    created: list[str] = []

    for i, q in enumerate(_SINGLE):
        check(b, f"single#{i} pre")
        t0 = time.time()
        r = send_message(b, content=q, model=CHAT_MODEL, thinking=True)
        dt = time.time() - t0
        created.append(r["chatId"])
        check(b, f"single#{i} post")
        u = r.get("usage") or {}
        log["chat"].append({"kind": "single", "i": i, "s": round(dt, 1), "out_tok": u.get("output_tokens")})
        print(f"  single#{i}: {dt:.1f}s tok={u.get('output_tokens')} -> {(r.get('content') or '')[:20]!r}", flush=True)
        time.sleep(gap)

    chat_id = parent = None
    for i, q in enumerate(_MULTI):
        check(b, f"multi#{i} pre")
        t0 = time.time()
        r = send_message(b, content=q, model=CHAT_MODEL, thinking=True, chat_id=chat_id, parent_id=parent)
        dt = time.time() - t0
        chat_id, parent = r["chatId"], r["responseId"]
        if chat_id not in created:
            created.append(chat_id)
        check(b, f"multi#{i} post")
        u = r.get("usage") or {}
        log["chat"].append({"kind": "multi", "i": i, "s": round(dt, 1), "out_tok": u.get("output_tokens")})
        print(f"  multi#{i}: {dt:.1f}s tok={u.get('output_tokens')} -> {(r.get('content') or '')[:20]!r}", flush=True)
        time.sleep(gap)

    return created


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=["probe", "c", "all"], default="probe",
                    help="probe=阶段1+2（不烧token）; c=阶段3真实chat; all=全部")
    ap.add_argument("--cold", type=int, default=5)
    ap.add_argument("--hot", type=int, default=8)
    ap.add_argument("--cold-gap", type=float, default=3.0)
    ap.add_argument("--hot-gap", type=float, default=2.5)
    ap.add_argument("--chat-gap", type=float, default=3.0)
    a = ap.parse_args()

    sess = "qwen-stress"
    log: dict = {"cold": [], "hot": [], "chat": [], "tripped": None}
    created: list[str] = []
    try:
        if a.phase in ("probe", "all"):
            phase_cold(sess, a.cold, a.cold_gap, log)
            phase_hot(sess, a.hot, a.hot_gap, log)
        if a.phase in ("c", "all"):
            created = phase_chat(sess, a.chat_gap, log)
        print("=== 全程无滑块 ✅ ===", flush=True)
    except Tripped as e:
        log["tripped"] = str(e)
        print(f"!!! 熔断: {e}", flush=True)
    except (CaptchaRequired, BridgeTimeout) as e:
        log["tripped"] = f"{type(e).__name__}: {e}"
        print(f"!!! {type(e).__name__}: {e}", flush=True)
    finally:
        if created:
            b = Bridge(session=sess)
            for cid in created:
                _safe_delete(b, cid)
            print(f"cleaned {len(created)} chats", flush=True)
        _safe_close(sess)

    if log["cold"]:
        rs = [r["ready_s"] for r in log["cold"]]
        print(f"\n冷启动就绪耗时: min {min(rs)} / max {max(rs)} / avg {round(sum(rs)/len(rs), 2)}s")
        print(f"就绪时 hasUid 全为真: {all(r['hasUid'] for r in log['cold'])}")
    if log["chat"]:
        print(f"阶段C 总输出 token ≈ {sum((r['out_tok'] or 0) for r in log['chat'])}")

    n_sig = len(log["cold"]) + len(log["hot"]) + len(log["chat"])
    print(f"\n签名请求总数 {n_sig}；tripped = {log['tripped']}")
    sys.exit(1 if log["tripped"] else 0)


if __name__ == "__main__":
    main()
