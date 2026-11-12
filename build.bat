@echo off
title Build Discord Badge Maximizer EXE
cd /d "%~dp0"

echo ======================================================
echo   DANG CAI DAT THU VIEN VA BUILD DISCORD BADGE MAXIMIZER EXE
echo ======================================================
echo.

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

echo.
echo ======================================================
echo   DANG TIEN HANH DONG GOI PYINSTALLER THANH EXE...
echo ======================================================
echo.

python build_exe.py

echo.
if exist "dist\DiscordBadgeMaximizer.exe" (
    echo ======================================================
    echo   BUILD THANH CONG!
    echo   File EXE nam tai: dist\DiscordBadgeMaximizer.exe
    echo ======================================================
) else (
    echo [!] Co loi xay ra trong qua trinh build.
)
pause
