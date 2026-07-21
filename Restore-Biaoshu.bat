@echo off
REM 模块：标书离线恢复批处理入口
REM 用途：将显式备份目录转发至 PowerShell 包装，不默认选择最近备份
REM 对接：tools\v1-ops\Restore-Biaoshu.ps1；tools\v1-ops\biaoshu_restore.py
REM 二次开发：不得增加 skip/force；可见错误须中文；不得自动停机
setlocal EnableExtensions
cd /d "%~dp0"

if "%~1"=="" (
  echo 必须显式指定备份目录
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\v1-ops\Restore-Biaoshu.ps1" -BackupDir "%~1"
exit /b %ERRORLEVEL%
