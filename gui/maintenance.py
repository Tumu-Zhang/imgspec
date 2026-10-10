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
from urllib.parse import quote

from PySide6.QtCore import QThread, Signal

from imgspec import __version__

REPO = "Tumu-Zhang/imgspec"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
LATEST_RELEASE_API = f"https://api.github.com/repos/{REPO}/releases/latest"
ISSUES_PAGE = f"https://github.com/{REPO}/issues/new/choose"
TIMEOUT_SECONDS = 5.0

# 邮件反馈的接收邮箱（项目专属邮箱，公开在应用里；不要放私人主邮箱）。
FEEDBACK_EMAIL = "imgspec@163.com"

# mailto 链接有长度上限（Windows 实用上限约 2K 字符），按「URL 编码后」的
# 长度做预算：中文经百分号编码会膨胀 9 倍，所以这里管的是编码后总长。
MAILTO_URL_BUDGET = 1500
_ERROR_LINE_MAX = 160  # 单条问题记录截断长度（路径类消息可能很长）


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


def build_log_text(
    records: list[tuple[str, str, str]], *, generated_at: str, note: str = ""
) -> str:
    """把界面日志渲染成可导出的纯文本。

    records 为 (时间戳, 级别, 消息) 三元组列表；头部固定带版本与系统信息 ——
    用户把这份文件附到 Issue 里，维护者不必再回头问「你用的哪个版本」。
    note 是尾部的补充说明（例如「日志可能含本机路径，分享前请确认」）。
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
    if note:
        lines.append(note)
    return "\n".join(lines)


def build_feedback_body(records: list[tuple[str, str, str]]) -> str:
    """邮件反馈正文：环境信息 + 最近的问题记录（⚠/✗ 级别）。

    正文走 mailto 预填，预算按 URL 编码后长度计（见 MAILTO_URL_BUDGET，
    中文百分号编码会膨胀 9 倍）：优先保留最新的问题，放不下的从最旧的
    开始省略；连最新一条都放不下时硬截断它 —— 最新问题必须在场。
    完整日志不进正文（太长），引导用户用「导出日志」导出后作附件。
    """
    header = ["图片转换器 错误反馈", *environment_lines(), "", "最近的问题："]
    footer = [
        "",
        "请补充：做了什么操作？预期与实际结果是什么？",
        "（完整日志：应用内「导出日志」导出后，作为附件添加到本邮件）",
    ]
    errors = [
        f"{stamp} [{level.upper()}] {_shorten(message)}"
        for stamp, level, message in records
        if level in ("warn", "error")
    ][-8:]
    if not errors:
        errors = ["（本次会话没有错误记录）"]

    for keep_n in range(len(errors), 0, -1):
        dropped = len(errors) - keep_n
        marker = [f"（较早的 {dropped} 条已省略）"] if dropped else []
        body = "\n".join(header + marker + errors[-keep_n:] + footer)
        if _encoded_len(body) <= MAILTO_URL_BUDGET:
            return body

    # 连最新一条都放不下：截到预算内（保语义：最新问题不能缺席）
    dropped = len(errors) - 1
    marker = [f"（较早的 {dropped} 条已省略）"] if dropped else []
    room = MAILTO_URL_BUDGET - _encoded_len("\n".join(header + marker + footer)) - 12
    return "\n".join(header + marker + [_fit(errors[-1], max(room, 30))] + footer)


def _shorten(text: str, limit: int = _ERROR_LINE_MAX) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _encoded_len(text: str) -> int:
    return len(quote(text, safe=""))


def _fit(text: str, budget: int) -> str:
    """把文本截到「URL 编码后长度」不超过 budget 的最长前缀（二分）。"""
    if _encoded_len(text) <= budget:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _encoded_len(text[:mid]) <= budget:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + "…"


def build_feedback_mailto(records: list[tuple[str, str, str]]) -> str:
    """组装 mailto 链接：主题带版本便于一眼分诊，正文预填错误摘要。"""
    subject = f"[图片转换器 Bug 反馈] v{app_version()}"
    return (
        f"mailto:{FEEDBACK_EMAIL}"
        f"?subject={quote(subject, safe='')}"
        f"&body={quote(build_feedback_body(records), safe='')}"
    )


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
    "FEEDBACK_EMAIL",
    "ISSUES_PAGE",
    "LATEST_RELEASE_API",
    "MAILTO_URL_BUDGET",
    "RELEASES_PAGE",
    "TIMEOUT_SECONDS",
    "UpdateChecker",
    "app_version",
    "build_feedback_body",
    "build_feedback_mailto",
    "build_log_text",
    "environment_lines",
    "fetch_latest_release",
    "is_newer",
    "parse_version",
]
