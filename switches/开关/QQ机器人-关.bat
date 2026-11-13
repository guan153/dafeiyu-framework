@echo off
chcp 936 >nul
echo 正在停止 QQ 机器人（保留你的 QQ 客户端）...
powershell -NoProfile -ExecutionPolicy Bypass -File "G:\温柔大姐姐\_v1交付\框架\服务.ps1" -Action stop
echo.
pause