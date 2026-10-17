@echo off
title Simplifi - Browser Sign In
echo Sign in only on Quicken's website in the browser window that opens.
echo The helper saves encrypted tokens only after direct API reads succeed.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" browser-login
echo.
pause
