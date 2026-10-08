@echo off
chcp 65001 >nul
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo.
    echo 没有找到 python。请先安装 Python 3.10 或更高版本，并勾选 "Add python.exe to PATH"。
    echo.
    pause
    exit /b 1
)

python -c "import PySide6, PIL, fitz" >nul 2>nul
if errorlevel 1 (
    echo.
    echo 正在安装依赖（首次运行需要联网）...
    echo.
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo 依赖安装失败，请手动执行： python -m pip install -r requirements.txt
        echo.
        pause
        exit /b 1
    )
)

python -m gui
if errorlevel 1 pause
