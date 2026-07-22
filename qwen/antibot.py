"""阿里云盾（baxia / AWSC）就绪门控与滑块验证检测。

背景（2026-07-23 实测，chat.qwen.ai fe 0.2.76 + baxiaCommon 2.5.36）：

chat.qwen.ai 用阿里 baxia SDK 劫持了 `window.fetch` / `XMLHttpRequest`，对命中
`checkApiPath` 的接口注入 `bx-ua` / `bx-umidtoken` 签名头。命中列表（`Vd`）包含我们
用到的全部写接口：

    /api/v2/chat/completions  /api/v2/chats  /api/v1/chats
    /api/v2/chats/new         /api/v{1,2}/files/getstsToken

（`/api/v1/auths/`、`/api/models`、`/api/v2/projects` 不在列表内，不签名。）

签名依赖 AWSC/fireye 初始化完成。baxia 的 `preRequest` 在 AWSC 未就绪时最多等
`awscTimeout + 200ms`（站点配置 awscTimeout=3000），**超时后仍会照常发出请求**，
此时带的是 `defaultToken1`/`defaultToken3` 占位 token。WAF 收到弱签名请求 →
下发 punish → 页面弹滑块。

即：**页面冷加载后约 1.3s 内发出的签名接口调用，会以退化签名发出，这是滑块的主要
诱因。** 实测冷加载时序：

    0.4s  readyState=loading   baxiaCommon 未加载
    0.9s  readyState=complete  baxiaInitialized=true  但 uid token 仍为空 ← 危险区
    1.3s  uid token 就绪，可以安全发请求

注意 `document.readyState === "complete"` 和 `window.baxiaInitialized === true`
都**不足以**判断就绪，必须拿到 uid token。

下面的 `_JS_READY` 直接复刻站点自己的就绪判定（main.js 里的 `Yd()`）：

    Yd = () => window.baxiaCommon
             && window.__baxia__?.baxiaPromptInit
             && window.baxiaInitialized
             && !!window.__baxia__.getFYModule.getUidToken()

历史坑：旧代码用 `wait_for_initial_state()` 等 `window.__INITIAL_STATE__`，而该站
根本不存在这个全局——实测 12s 轮询始终为 false，等于每次白等满 timeout 且没有任何
就绪保证。已由本模块取代。
"""

from __future__ import annotations

import json
import time

from .bridge import Bridge, BridgeError


class CaptchaRequired(Exception):
    """chat.qwen.ai 正在要求滑块验证，需要人工在浏览器里完成。"""


# 站点自身的就绪判定 + 滑块 DOM 探测，一次 evaluate 返回全部状态
_JS_STATE = r"""
(() => {
    const b = window.__baxia__ || {};
    let uid = null;
    try {
        uid = b.getFYModule && b.getFYModule.getUidToken && b.getFYModule.getUidToken();
    } catch (e) {}

    // 两套验证码 UI，实测选择器（2026-07-23 抓真实 punish 页确认）：
    //   阿里云 WAF 全屏滑块  #waf_nc_block / #WAF_NC_WRAPPER / #aliyunCaptcha-window-embed
    //   baxia 内联滑块        .nc_wrapper / ._nc（baxiaCommon isRendered 用的就是这两个）
    // 注意不能用 offsetParent 判可见性：WAF 容器是 position:fixed，fixed 元素的
    // offsetParent 恒为 null，用它会把正在显示的滑块判成"没有"。改用 rect 尺寸。
    const SELECTORS = [
        ["waf_punish", "#waf_nc_block, #WAF_NC_WRAPPER, .waf-nc-wrapper, #aliyunCaptcha-window-embed.aliyunCaptcha-show, #nocaptcha"],
        ["baxia_slider", ".nc_wrapper, ._nc, #baxia-dialog, .baxia-dialog"]
    ];

    const isVisible = (n) => {
        const st = getComputedStyle(n);
        if (st.display === "none" || st.visibility === "hidden" || st.opacity === "0") return false;
        const r = n.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
    };

    let kind = null;
    for (const [name, sel] of SELECTORS) {
        for (const n of document.querySelectorAll(sel)) {
            if (isVisible(n)) { kind = name; break; }
        }
        if (kind) break;
    }

    const traceEl = document.querySelector("#waf-nc-traceid");

    return JSON.stringify({
        url: location.href,
        ready: !!(window.baxiaCommon && b.baxiaPromptInit && window.baxiaInitialized && uid),
        baxiaLoaded: !!window.baxiaCommon,
        baxiaInitialized: !!window.baxiaInitialized,
        needDelay: !!window.baxiaNeedDelay,
        hasUidToken: !!uid,
        captcha: !!kind,
        captchaKind: kind,
        // 滑块解开后 TraceID 节点仍留在 DOM 里，只在真的挂着验证时才报，避免误导
        traceId: (kind && traceEl) ? traceEl.textContent.trim() : null
    });
})()
"""

_KIND_LABEL = {
    "waf_punish": "阿里云 WAF 全屏滑块",
    "baxia_slider": "baxia 内联滑块",
}


def _captcha_msg(state: dict) -> str:
    kind = _KIND_LABEL.get(state.get("captchaKind"), "人机验证")
    trace = state.get("traceId")
    return (
        f"chat.qwen.ai 弹出了{kind}"
        + (f"（{trace}）" if trace else "")
        + "，请在浏览器窗口里手动完成滑块后重试。这一步必须人工完成，重试无用。"
    )


# WAF punish 响应特征：非 JSON 的 HTML 正文 / 405 / 含云盾脚本引用
_PUNISH_MARKERS = (
    "nc.js",
    "baxia",
    "ALIYUN_WAF",
    "x5referer",
    "captcha",
    "punish",
    "//g.alicdn.com/sd/",
)


def get_state(bridge: Bridge) -> dict:
    """读取当前 tab 的云盾就绪 / 滑块状态。"""
    raw = bridge.evaluate(_JS_STATE)
    return json.loads(raw) if isinstance(raw, str) else {}


def looks_like_punish(status: int | None, body: str | None) -> bool:
    """判断一个 API 响应是否是 WAF punish（滑块拦截）而非正常业务响应。"""
    if status in (403, 405):
        return True
    if not body:
        return False
    head = body[:2000]
    if head.lstrip().startswith("<"):  # API 端点返回 HTML 基本就是被拦了
        return any(m in head for m in _PUNISH_MARKERS) or "<html" in head.lower()
    return False


def wait_ready(bridge: Bridge, timeout: float = 20.0, poll: float = 0.3) -> dict:
    """阻塞直到云盾签名链路就绪；就绪后返回状态字典。

    检测到滑块直接抛 CaptchaRequired —— 此时再怎么等也不会就绪，快速失败比空转好。
    超时不抛错（页面可能是不加载 baxia 的子页），由调用方决定是否继续。
    """
    deadline = time.monotonic() + timeout
    state: dict = {}
    while time.monotonic() < deadline:
        try:
            state = get_state(bridge)
        except BridgeError:
            state = {}
            time.sleep(poll)
            continue
        if state.get("captcha"):
            raise CaptchaRequired(_captcha_msg(state))
        if state.get("ready"):
            return state
        time.sleep(poll)
    return state


def reclassify_timeout(bridge: Bridge, exc: Exception) -> None:
    """evaluate 超时后调用：如果页面上挂着滑块，说明请求是被 WAF 扣住的，改抛 CaptchaRequired。

    实测被 punish 的 fetch 不会返回 4xx/5xx，而是一直 pending 等人过验证，
    表现成"超时"。直接报超时会把排查方向带偏到"模型太慢/timeout 调小了"。
    检测本身失败时保持原异常，不要用二次故障掩盖一次故障。
    """
    try:
        state = get_state(bridge)
    except BridgeError:
        return
    if state.get("captcha"):
        raise CaptchaRequired("请求被挂起：" + _captcha_msg(state)) from exc


def assert_no_captcha(bridge: Bridge) -> None:
    """轻量检查：仅确认当前没有挂着滑块，不等待就绪。"""
    try:
        state = get_state(bridge)
    except BridgeError:
        return
    if state.get("captcha"):
        raise CaptchaRequired(_captcha_msg(state))
