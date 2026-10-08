"""图形界面入口。"""

from __future__ import annotations

import sys


def _detect_system_theme() -> str:
    """探测 Windows 系统深浅色主题；失败时返回 light。"""
    try:
        import winreg  # noqa: S107

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return "dark" if value == 0 else "light"
    except Exception:  # noqa: BLE001
        return "light"


def main(argv: list[str] | None = None) -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from gui import theme
    from gui.main_window import MainWindow

    args = argv if argv is not None else sys.argv

    app = QApplication(args)
    app.setApplicationName("图片转换器")
    app.setOrganizationName("imgspec")

    # 跟随系统主题，除非命令行显式指定
    if "--dark" in args:
        theme.set_theme("dark")
    elif "--light" in args:
        theme.set_theme("light")
    else:
        theme.set_theme(_detect_system_theme())

    # Fusion 打底再叠自定义样式表：Windows 原生样式对 QSS 的支持不完整
    app.setStyle("Fusion")

    # 字体与样式表必须在窗口创建之前设定
    app.setFont(theme.ui_font(theme.FONT_MD))
    app.setStyleSheet(theme.build_stylesheet(theme.current_theme().name))
    app.setWindowIcon(theme.app_icon(theme.current_theme().name))

    window = MainWindow()
    window.show()

    # --smoke：启动后短暂时间自动退出，用于验证程序能正常启动
    if "--smoke" in args:
        QTimer.singleShot(800, window.close)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
