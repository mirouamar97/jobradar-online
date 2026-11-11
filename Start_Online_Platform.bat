@echo off
chcp 65001 >nul
cd /d "%~dp0"
title JobRadar Online - Live Platform
echo ========================================================
echo   JobRadar DZ - منصة التوظيف المنشورة على الإنترنت
echo   الرابط العالمي المباشر: https://mirouamar97.github.io/jobradar-online/
echo ========================================================
start "" "https://mirouamar97.github.io/jobradar-online/"
python -m http.server 8000
pause
