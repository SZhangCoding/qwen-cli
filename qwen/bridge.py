"""Kimi WebBridge client — thin wrapper around the daemon REST API."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any


class BridgeError(Exception):
    pass


class BridgeTimeout(BridgeError):
    """evaluate 超时。

    重要：被 WAF punish 的请求不会返回错误码，而是**一直挂着**等人过滑块，
    所以超时往往意味着"弹了验证码"而不是"模型太慢"。调用方应据此改判，
    见 antibot.reclassify_timeout()。
    """


class Bridge:
    def __init__(self, host: str = "127.0.0.1", port: int = 10086, session: str = "qwen"):
        self.base = f"http://{host}:{port}"
        self.session = session

    def _call(self, action: str, args: dict | None = None, timeout: float = 30) -> Any:
        payload = {"action": action, "session": self.session}
        if args is not None:
            payload["args"] = args
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{self.base}/command",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                result = json.loads(resp.read())
        except TimeoutError as e:
            # socket 读超时不是 URLError 子类，必须单独接，否则会漏成裸异常
            raise BridgeTimeout(f"{action} timed out after {timeout}s") from e
        except urllib.error.URLError as e:
            if isinstance(e.reason, TimeoutError):
                raise BridgeTimeout(f"{action} timed out after {timeout}s") from e
            raise BridgeError(f"webbridge unreachable: {e}") from e

        if not result.get("ok"):
            err = result.get("error", {})
            raise BridgeError(err.get("message", str(result)))
        return result.get("data")

    def find_tab(self, url: str) -> str | None:
        """Return tabId if a tab matching the URL domain is already open, else None."""
        try:
            data = self._call("find_tab", {"url": url})
            return data.get("tabId") if isinstance(data, dict) else None
        except BridgeError:
            return None

    def navigate(self, url: str, new_tab: bool = False) -> None:
        self._call("navigate", {"url": url, "newTab": new_tab})

    def navigate_or_reuse(self, url: str) -> None:
        """Reuse existing tab if one for this URL is already open; otherwise open new tab."""
        if self.find_tab(url) is None:
            self.navigate(url, new_tab=True)

    def evaluate(self, code: str, timeout: float = 30) -> Any:
        data = self._call("evaluate", {"code": code}, timeout=timeout)
        return data.get("value") if isinstance(data, dict) else data

    def wait_for_document_ready(self, timeout: float = 15.0) -> bool:
        """等到文档加载完成。

        注意这只是"页面加载完"，**不代表**可以安全打接口——chat.qwen.ai 的云盾签名
        要晚约 0.4s 才就绪，见 antibot.wait_ready()。
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if self.evaluate("document.readyState === 'complete'"):
                    return True
            except BridgeError:
                pass
            time.sleep(0.3)
        return False
