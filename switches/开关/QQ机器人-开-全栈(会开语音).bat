@echo off
chcp 936 >nul
echo ============================================================
echo  警告：这是全栈启动，会拉起 TTS/ASR 语音栈！
echo  只想要聊天，请用「QQ机器人-开.bat」或「QQ机器人-开-聊天only.bat」。
echo ============================================================
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "G:\温柔大姐姐\_v1交付\框架\服务.ps1" -Action start
echo.
pause