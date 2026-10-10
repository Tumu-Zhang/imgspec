"""会话日志落盘与崩溃归档的纯逻辑测试（不起界面）。

覆盖：正常退出留标记、异常退出下次启动归档成 crash-*、归档数量上限、
以及未 start 时的空操作安全。
"""

from __future__ import annotations

import time

from gui.session_log import KEEP_ARCHIVES, OK_FOOTER, SESSION_NAME, SessionLog


class TestSessionLog:
    def test_writes_records_and_footer(self, tmp_path):
        log = SessionLog(tmp_path)
        log.start("会话头")
        log.append_record("[10:00]", "warn", "源文件无 DPI 元数据")
        log.finish()

        text = (tmp_path / SESSION_NAME).read_text(encoding="utf-8")
        assert "会话头" in text
        assert "[WARN] 源文件无 DPI 元数据" in text
        assert text.rstrip().endswith(OK_FOOTER)

    def test_crash_detected_and_archived_on_next_start(self, tmp_path):
        first = SessionLog(tmp_path)
        first.start("第一次会话")
        first.append_record("[10:01]", "error", "转换时炸了")
        # 模拟被强杀/崩溃：不写结束标记直接释放句柄
        # （真实崩溃时进程消失、句柄由系统释放；同进程测试里必须手动关，
        #  否则 Windows 不允许改名一个仍被打开的文件）
        first.abandon()

        second = SessionLog(tmp_path)
        second.start("第二次会话")

        assert second.previous_crash is not None
        assert second.previous_crash.name.startswith("crash-")
        assert "转换时炸了" in (second.crash_text() or "")
        # 新会话是干净的新文件，不含上次内容
        assert second.path is not None
        assert "转换时炸了" not in second.path.read_text(encoding="utf-8")
        second.finish()

    def test_clean_exit_not_flagged_as_crash(self, tmp_path):
        first = SessionLog(tmp_path)
        first.start("第一次会话")
        first.finish()

        second = SessionLog(tmp_path)
        second.start("第二次会话")

        assert second.previous_crash is None
        assert second.previous_session is not None
        assert second.previous_session.name.startswith("session-")
        second.finish()

    def test_first_run_has_no_previous(self, tmp_path):
        log = SessionLog(tmp_path / "logs")  # 目录还不存在
        log.start("第一次")
        assert log.previous_crash is None
        assert log.previous_session is None
        log.finish()
        assert (tmp_path / "logs" / SESSION_NAME).exists()

    def test_archives_pruned_to_limit(self, tmp_path):
        for index in range(KEEP_ARCHIVES + 4):
            path = tmp_path / f"session-2026010{index % 10}-1200{index:02d}.log"
            path.write_text("旧会话\n" + OK_FOOTER, encoding="utf-8")
            time.sleep(0.01)  # 让 mtime 可区分（清理按 mtime 取最旧）

        log = SessionLog(tmp_path)
        log.start("新会话")

        archives = list(tmp_path.glob("session-*.log")) + list(tmp_path.glob("crash-*.log"))
        assert len(archives) <= KEEP_ARCHIVES
        log.finish()

    def test_write_before_start_is_safe(self, tmp_path):
        log = SessionLog(tmp_path)
        log.write("还没开始")  # 不应抛异常
        log.append_record("[00:00]", "info", "x")
        log.finish()
        assert not (tmp_path / SESSION_NAME).exists()
        assert log.crash_text() is None
