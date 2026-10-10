"""维护功能：日志导出文本、版本比较、更新检查。

设计约束（重要）
----------------
更新检查对网络「零假设」：离线、超时、被墙、接口变更、证书异常
一律静默失败（返回 None），绝不抛异常、绝不阻塞界面、也不发送任何用户数据。
仓库访问在国内网络下不稳定是已知事实，所以自动检查只在能连上时有存在感，
连不上就当无事发生 —— 界面不因此出现任何等待或报错。
"""

from __future__ import annotations

import json
import platform
import sys
import urllib.request

from PySide6.QtCore import QThread, Signal

from imgspec import __version__

REPO = "Tumu-Zhang/imgspec"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
LATEST_RELEASE_API = f"https://api.github.com/repos/{REPO}/releases/latest"
ISSUES_PAGE = f"https://github.com/{REPO}/issues/new/choose"
TIMEOUT_SECONDS = 5.0


def app_version() -> str:
    """当前版本号。唯一事实源：imgspec.__version__。"""
    return __version__


def environment_lines() -> list[str]:
    """版本与系统信息：跟随导出的日志一起给维护者，省去来回询问。"""
    return [
        f"版本：{app_version()}",
        f"系统：{platform.platform()}",
        f"Python：{sys.version.split()[0]}",
    ]


def build_log_text(records: list[tuple[str, str, str]], *, generated_at: str) -> str:
    """把界面日志渲染成可导出的纯文本。

    records 为 (时间戳, 级别, 消息) 三元组列表；头部固定带版本与系统信息 ——
    用户把这份文件附到 Issue 里，维护者不必再回头问「你用的哪个版本」。
    """
    lines = ["图片转换器 · 转换日志", "=" * 44]
    lines.extend(environment_lines())
    lines.append(f"导出时间：{generated_at}")
    lines.append("=" * 44)
    if not records:
        lines.append("（本次会话没有日志记录）")
    else:
        for stamp, level, message in records:
            lines.append(f"{stamp} [{level.upper()}] {message}")
    lines.append("")
    return "\n".join(lines)


def parse_version(text: str) -> tuple[int, ...] | None:
    """把 'v1.2.3' / '1.2' / '1.0.0-beta' 这类标签解析成整数元组。

    无法解析（空串、纯字母等）返回 None。预发布后缀（-beta）直接截断，
    对「是否比当前版本新」的判断足够。
    """
    cleaned = text.strip().lstrip("vV").strip()
    if not cleaned:
        return None
    parts: list[int] = []
    for chunk in cleaned.split("."):
        digits = ""
        for char in chunk:
            if char.isdigit():
                digits += char
            else:
                break
        if not digits:
            return None
        parts.append(int(digits))
    return tuple(parts)


def is_newer(candidate: str, current: str) -> bool:
    """candidate 是否比 current 新。任一方无法解析时返回 False（宁可不提示）。"""
    a, b = parse_version(candidate), parse_version(current)
    if a is None or b is None:
        return False
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


def fetch_latest_release(timeout: float = TIMEOUT_SECONDS) -> dict | None:
    """查询 GitHub 最新 Release。任何失败返回 None（调用方据此静默）。"""
    request = urllib.request.Request(
        LATEST_RELEASE_API,
        headers={
            "User-Agent": f"imgspec/{app_version()}",
            "Accept": "application/vnd.github+json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - 离线/超时/证书/格式异常统统静默
        return None
    if not isinstance(payload, dict):
        return None
    tag = str(payload.get("tag_name") or "").strip()
    if not tag:
        return None
    return {
        "tag": tag,
        "url": str(payload.get("html_url") or RELEASES_PAGE),
        "name": str(payload.get("name") or tag),
    }


class UpdateChecker(QThread):
    """后台查询最新版本，结果经 checked 信号回界面线程（None = 失败）。"""

    checked = Signal(object)

    def __init__(self, parent=None, timeout: float = TIMEOUT_SECONDS) -> None:
        super().__init__(parent)
        self._timeout = timeout

    def run(self) -> None:
        self.checked.emit(fetch_latest_release(self._timeout))


__all__ = [
    "ISSUES_PAGE",
    "LATEST_RELEASE_API",
    "RELEASES_PAGE",
    "TIMEOUT_SECONDS",
    "UpdateChecker",
    "app_version",
    "build_log_text",
    "environment_lines",
    "fetch_latest_release",
    "is_newer",
    "parse_version",
]
