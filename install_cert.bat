@echo off
setlocal
title Cai Dat Chu Ky So - Discord Badge Maximizer

echo ===================================================================
echo     CAI DAT CHU KY SO XAC THUC CHO DISCORD BADGE MAXIMIZER
echo ===================================================================
echo.
echo Buoc nay giup Windows SmartScreen va Windows Defender tin tuong 
echo 100%% ung dung DiscordBadgeMaximizer va khong con canh bao nguy hiem.
echo.

:: Check Admin rights
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [THONG BAO] Dang yeu cau quyen Administrator de them chung chi vao he thong...
    powershell -Command "Start-Process cmd -ArgumentList '/c \"%~dpnx0\"' -Verb RunAs"
    exit /b
)

set CER_FILE=%~dp0DiscordBadgeMaximizer.cer

if not exist "%CER_FILE%" (
    echo [LOI] Khong tim thay file chung chi: %CER_FILE%
    echo Vui long de file DiscordBadgeMaximizer.cer cung thu muc voi file .bat nay!
    pause
    exit /b 1
)

echo Dang cai dat chung chi so vao Trusted Root Store cua Windows...
certutil -addstore -f "Root" "%CER_FILE%"

if %errorlevel% equ 0 (
    echo.
    echo ===================================================================
    echo  [THANH CONG] Da them Chu ky so vao he thong Windows thanh cong!
    echo  Tu bay gio ban co the mo DiscordBadgeMaximizer ma khong bi chan.
    echo ===================================================================
) else (
    echo.
    echo [THAT BAI] Khong the cai dat chung chi. Vui long kiem tra lai.
)

echo.
pause
