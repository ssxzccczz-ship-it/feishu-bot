@echo off
title 飞书 Claude 机器人
cd /d F:\ai\feishu_bot
echo ========================================
echo  飞书 Claude 机器人
echo  端口: 7897
echo ========================================
echo.
python feishu_server.py
pause
