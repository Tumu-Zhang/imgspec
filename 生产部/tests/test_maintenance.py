"""维护功能的纯逻辑测试：日志导出文本、版本比较、Release 查询的失败静默。

不启动界面、不触网：网络分支全部用假的 urlopen 覆盖。
"""

from __future__ import annotations

import io
import json

import pytest

from gui import maintenance


class TestLogText:
    def test_contains_environment_and_records(self):
        text = maintenance.build_log_text(
            [("[10:00]", "warn", "源文件无 DPI 元数据")],
            generated_at="2026-10-10 15:20:00",
        )
        assert "图片转换器 · 转换日志" in text
        assert "版本：" in text
        assert "系统：" in text
        assert "导出时间：2026-10-10 15:20:00" in text
        assert "[WARN] 源文件无 DPI 元数据" in text

    def test_empty_session_still_has_header(self):
        text = maintenance.build_log_text([], generated_at="2026-10-10 15:20:00")
        assert "没有日志记录" in text
        assert "版本：" in text

    def test_level_rendered_uppercase(self):
        text = maintenance.build_log_text([("[09:00]", "error", "boom")], generated_at="x")
        assert "[ERROR] boom" in text

    def test_note_appended_at_tail(self):
        text = maintenance.build_log_text(
            [], generated_at="x", note="【提示】日志可能包含本机文件路径"
        )
        assert "日志可能包含本机文件路径" in text

    def test_no_note_leaves_no_trailing_text(self):
        text = maintenance.build_log_text([], generated_at="x")
        assert "【提示】" not in text


class TestVersionComparison:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("v1.2.3", (1, 2, 3)),
            ("1.2", (1, 2)),
            ("V2.0.0", (2, 0, 0)),
            ("1.0.0-beta", (1, 0, 0)),
            ("", None),
            ("v", None),
            ("abc", None),
            (".", None),
        ],
    )
    def test_parse_version(self, text, expected):
        assert maintenance.parse_version(text) == expected

    @pytest.mark.parametrize(
        "candidate,current,expected",
        [
            ("v1.1.0", "1.0.0", True),
            ("v1.0.1", "1.0.0", True),
            ("v2", "1.9.9", True),
            ("v1.0.0", "1.0.0", False),
            ("1.0", "1.0.0", False),
            ("v0.9.9", "1.0.0", False),
            # 任一方解析不了时宁可不提示
            ("garbage", "1.0.0", False),
            ("v1.0.0", "garbage", False),
        ],
    )
    def test_is_newer(self, candidate, current, expected):
        assert maintenance.is_newer(candidate, current) is expected


class TestFetchLatestRelease:
    def test_offline_returns_none(self, monkeypatch):
        def boom(*_args, **_kwargs):
            raise OSError("network unreachable")

        monkeypatch.setattr(maintenance.urllib.request, "urlopen", boom)
        assert maintenance.fetch_latest_release(timeout=0.01) is None

    def test_timeout_returns_none(self, monkeypatch):
        def slow(*_args, **_kwargs):
            raise TimeoutError("timed out")

        monkeypatch.setattr(maintenance.urllib.request, "urlopen", slow)
        assert maintenance.fetch_latest_release(timeout=0.01) is None

    def test_parses_payload(self, monkeypatch):
        payload = json.dumps(
            {
                "tag_name": "v1.1.0",
                "html_url": "https://example.com/releases/v1.1.0",
                "name": "1.1.0",
            }
        ).encode("utf-8")
        monkeypatch.setattr(
            maintenance.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(payload)
        )
        assert maintenance.fetch_latest_release() == {
            "tag": "v1.1.0",
            "url": "https://example.com/releases/v1.1.0",
            "name": "1.1.0",
        }

    def test_missing_tag_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            maintenance.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b"{}")
        )
        assert maintenance.fetch_latest_release() is None

    def test_broken_json_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            maintenance.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b"not json")
        )
        assert maintenance.fetch_latest_release() is None


class TestFeedbackEmail:
    """邮件反馈：mailto 组装、错误摘要、URL 长度预算。"""

    def test_mailto_targets_project_inbox_with_subject(self):
        from urllib.parse import parse_qs, unquote, urlparse

        url = maintenance.build_feedback_mailto([("[09:00]", "error", "boom")])
        assert url.startswith(f"mailto:{maintenance.FEEDBACK_EMAIL}?")
        query = parse_qs(urlparse(url).query)
        assert query["subject"][0] == f"[图片转换器 Bug 反馈] v{maintenance.app_version()}"
        # 中文经百分号编码后必须可无损还原
        assert unquote(query["body"][0]).startswith("图片转换器 错误反馈")

    def test_body_contains_environment_and_errors(self):
        from urllib.parse import parse_qs, urlparse

        records = [
            ("[09:00]", "info", "普通信息不应进正文"),
            ("[09:01]", "warn", "源文件无 DPI 元数据"),
            ("[09:02]", "error", "转换失败"),
        ]
        query = parse_qs(urlparse(maintenance.build_feedback_mailto(records)).query)
        body = query["body"][0]
        assert "版本：" in body and "系统：" in body
        assert "[WARN] 源文件无 DPI 元数据" in body
        assert "[ERROR] 转换失败" in body
        assert "普通信息" not in body  # info 级别过滤掉
        assert "导出日志" in body  # 引导附完整日志

    def test_body_without_errors_has_placeholder(self):
        from urllib.parse import parse_qs, urlparse

        query = parse_qs(urlparse(maintenance.build_feedback_mailto([])).query)
        assert "没有错误记录" in query["body"][0]

    def test_long_message_truncated(self):
        from urllib.parse import parse_qs, urlparse

        url = maintenance.build_feedback_mailto([("[09:00]", "error", "长" * 500)])
        body = parse_qs(urlparse(url).query)["body"][0]
        assert "长" * 160 not in body  # 超过 160 字被截断
        assert body.count("长") <= 160

    def test_over_budget_drops_oldest_keeps_newest(self):
        from urllib.parse import parse_qs, quote, urlparse

        records = [
            (f"[09:{i:02d}]", "error", f"错误{i}：" + "路径" * 70) for i in range(8)
        ]
        url = maintenance.build_feedback_mailto(records)
        query = parse_qs(urlparse(url).query)
        body = query["body"][0]
        assert len(quote(body, safe="")) <= maintenance.MAILTO_URL_BUDGET
        assert "[ERROR] 错误7" in body  # 最新一条永远保留
        assert "较早的" in body  # 丢弃提示
        assert len(url) < 2083  # Windows mailto 实用上限

    def test_all_in_budget_keeps_all_without_marker(self):
        from urllib.parse import parse_qs, urlparse

        records = [("[09:00]", "error", "boom")]
        query = parse_qs(urlparse(maintenance.build_feedback_mailto(records)).query)
        assert "较早的" not in query["body"][0]
        assert "[ERROR] boom" in query["body"][0]
