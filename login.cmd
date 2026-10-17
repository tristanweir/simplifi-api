@echo off
title Simplifi - Local Sign In
echo Client auth revision: session-state-v2
echo Sign in locally. Password and MFA input are hidden.
echo Credentials are sent only to services.quicken.com.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" login
echo.
pause
