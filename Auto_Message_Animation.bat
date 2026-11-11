@echo off
chcp 65001 >nul
cd /d "%~dp0"
title JobRadar DZ - Animation Auto-Messenger
echo ========================================================
echo   JobRadar DZ - المراسلة التلقائية لفرص الأنيماتور والمعارض
echo ========================================================
python auto_message_animation.py
pause
