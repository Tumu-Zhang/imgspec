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
