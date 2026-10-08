"""office 模块的单元测试。

这里只测不依赖真实 Office 的部分 —— 进程识别、收尾策略、错误提示。
真实的 PPT -> PDF 导出在验证脚本里单独跑（见 README 的测试说明）。
"""

from __future__ import annotations

import pytest

from imgspec import office


class FakeCompleted:
    def __init__(self, stdout: bytes) -> None:
        self.stdout = stdout
        self.stderr = b""
        self.returncode = 0


class TestListPowerPointPids:
    def test_parses_csv_rows(self, monkeypatch):
        payload = (
            b'"POWERPNT.EXE","1234","Console","1","100,000 K"\r\n'
            b'"POWERPNT.EXE","5678","Console","1","200,000 K"\r\n'
        )
        monkeypatch.setattr(office.subprocess, "run", lambda *a, **k: FakeCompleted(payload))
        assert office._list_powerpoint_pids() == {1234, 5678}

    def test_gbk_hint_line_yields_nothing(self, monkeypatch):
        """中文 Windows 在没有匹配进程时会输出 GBK 提示行，不能因此报错。"""
        payload = "信息: 没有运行的任务匹配指定标准。".encode("gbk")
        monkeypatch.setattr(office.subprocess, "run", lambda *a, **k: FakeCompleted(payload))
        assert office._list_powerpoint_pids() == set()

    def test_empty_output(self, monkeypatch):
        monkeypatch.setattr(office.subprocess, "run", lambda *a, **k: FakeCompleted(b""))
        assert office._list_powerpoint_pids() == set()

    def test_subprocess_failure_is_swallowed(self, monkeypatch):
        """tasklist 不可用时应退化成空集，而不是让整个转换失败。"""

        def boom(*_args, **_kwargs):
            raise OSError("tasklist not found")

        monkeypatch.setattr(office.subprocess, "run", boom)
        assert office._list_powerpoint_pids() == set()

    def test_ignores_other_processes(self, monkeypatch):
        payload = (
            b'"WINWORD.EXE","111","Console","1","10 K"\r\n'
            b'"POWERPNT.EXE","222","Console","1","20 K"\r\n'
        )
        monkeypatch.setattr(office.subprocess, "run", lambda *a, **k: FakeCompleted(payload))
        assert office._list_powerpoint_pids() == {222}


class TestSessionTeardown:
    """收尾必须走「终止自己的进程」，不能依赖那套 76 秒的优雅退出。"""

    def test_kills_tracked_processes(self, monkeypatch, tmp_path):
        killed: list[int] = []
        monkeypatch.setattr(office, "_kill_processes", lambda pids: killed.extend(pids))

        session = office.PowerPointSession(cache_dir=tmp_path / "cache")
        session._own_pids = {4242}
        session._app = object()
        session.__exit__(None, None, None)

        assert killed == [4242]
        assert session._own_pids == set()

    def test_falls_back_to_quit_without_pid(self, monkeypatch, tmp_path):
        quit_calls: list[bool] = []
        monkeypatch.setattr(office, "_kill_processes", lambda pids: None)

        class FakeApp:
            def Quit(self) -> None:
                quit_calls.append(True)

        session = office.PowerPointSession(cache_dir=tmp_path / "cache")
        session._own_pids = set()
        session._app = FakeApp()
        session.__exit__(None, None, None)

        assert quit_calls == [True]

    def test_teardown_is_idempotent(self, monkeypatch, tmp_path):
        killed: list[int] = []
        monkeypatch.setattr(office, "_kill_processes", lambda pids: killed.extend(pids))

        session = office.PowerPointSession(cache_dir=tmp_path / "cache")
        session._own_pids = {99}
        session.__exit__(None, None, None)
        session.__exit__(None, None, None)
        assert killed == [99]  # 第二次不会再杀一遍

    def test_owned_cache_removed_on_exit(self, tmp_path):
        session = office.PowerPointSession()
        cache = session.cache_dir
        assert cache.exists()
        session.__exit__(None, None, None)
        assert not cache.exists()

    def test_provided_cache_is_kept(self, tmp_path):
        shared = tmp_path / "shared_cache"
        session = office.PowerPointSession(cache_dir=shared)
        session.__exit__(None, None, None)
        assert shared.exists()


class TestCachePaths:
    def test_cache_key_changes_with_file(self, tmp_path):
        session = office.PowerPointSession(cache_dir=tmp_path / "cache")
        try:
            first = tmp_path / "a.pptx"
            second = tmp_path / "b.pptx"
            first.write_bytes(b"aaa")
            second.write_bytes(b"bbb")
            assert session._cache_for(first) != session._cache_for(second)
            assert session._cache_for(first).suffix == ".pdf"
        finally:
            session.__exit__(None, None, None)

    def test_cache_key_changes_with_mtime(self, tmp_path):
        import os
        import time

        session = office.PowerPointSession(cache_dir=tmp_path / "cache")
        try:
            deck = tmp_path / "deck.pptx"
            deck.write_bytes(b"content")
            before = session._cache_for(deck)
            time.sleep(0.01)
            os.utime(deck, (time.time() + 5, time.time() + 5))
            assert session._cache_for(deck) != before
        finally:
            session.__exit__(None, None, None)


class TestAvailability:
    def test_non_windows_reports_unavailable(self, monkeypatch):
        monkeypatch.setattr(office.os, "name", "posix")
        available, note = office.is_available()
        assert not available
        assert "非 Windows" in note

    def test_missing_pywin32_reports_hint(self, monkeypatch):
        monkeypatch.setattr(office.os, "name", "nt")

        def boom():
            raise office.OfficeUnavailableError("未安装 pywin32，无法驱动 PowerPoint。")

        monkeypatch.setattr(office, "_import_win32", boom)
        available, note = office.is_available()
        assert not available
        assert "pywin32" in note


class TestExportGuards:
    def test_export_before_start_raises(self, tmp_path):
        session = office.PowerPointSession(cache_dir=tmp_path / "cache")
        try:
            with pytest.raises(office.OfficeUnavailableError, match="尚未启动"):
                session.export_pdf(tmp_path / "x.pptx")
        finally:
            session.__exit__(None, None, None)

    def test_missing_source_raises(self, tmp_path):
        session = office.PowerPointSession(cache_dir=tmp_path / "cache")
        session._app = object()  # 骗过「未启动」检查
        try:
            with pytest.raises(FileNotFoundError):
                session.export_pdf(tmp_path / "nope.pptx")
        finally:
            session._app = None
            session.__exit__(None, None, None)

    def test_cached_pdf_is_reused(self, tmp_path):
        """同一源文件第二次导出应直接命中缓存，不再调用 PowerPoint。"""
        session = office.PowerPointSession(cache_dir=tmp_path / "cache")
        session._app = object()
        try:
            deck = tmp_path / "deck.pptx"
            deck.write_bytes(b"content")
            cached = session._cache_for(deck)
            cached.write_bytes(b"%PDF-1.4 fake")
            assert session.export_pdf(deck) == cached
        finally:
            session._app = None
            session.__exit__(None, None, None)
