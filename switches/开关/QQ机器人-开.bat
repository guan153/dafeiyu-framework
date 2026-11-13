@echo off
chcp 65001 >nul
title QQ Bot - START (chat only)
echo.
echo  ==========================================================
echo   QQ Bot  -  START  (CHAT ONLY)
echo  ----------------------------------------------------------
echo   will start : QQ + SnowLuma + BEAM-gateway(8920) + AstrBot
echo   will  NOT  : TTS(8912) ASR(8913) QwenEmo(8914)
echo                ComfyUI(8188) capability-manager(8921)
echo   no process is killed, no config file is modified
echo  ==========================================================
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\tools\start-chat-only.ps1" -Action start
set RC=%errorlevel%
echo.
if "%RC%"=="0" (echo  [OK] chat stack is up, GPU services are DOWN.) else (echo  [WARN] exit code = %RC% , check the FAIL lines above.)
echo.
pause
