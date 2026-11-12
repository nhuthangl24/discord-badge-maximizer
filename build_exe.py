#!/usr/bin/env python3
"""
Build Script for Discord Badge Maximizer (Windows .exe)
Packages gui.py into a single standalone DiscordBadgeMaximizer.exe with PyInstaller.
"""

import os
import sys
import shutil
import subprocess
from pathlib import Path

# Fix Windows console encoding issues for Unicode characters
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent

def build():
    print("=" * 60)
    print("  BUILDING DISCORD BADGE MAXIMIZER (STANDALONE EXE)")
    print("=" * 60)

    # Clean previous build artifacts
    for folder in ["build", "dist"]:
        p = ROOT / folder
        if p.exists():
            print(f"Cleaning {folder}/...")
            try:
                shutil.rmtree(p)
            except Exception as e:
                print(f"Warning cleaning {folder}: {e}")

    # Separator for PyInstaller --add-data
    sep = ";" if sys.platform == "win32" else ":"

    # Data assets
    video_asset = ROOT / "assets" / "video.mp4"
    data_args = []
    if video_asset.exists():
        data_args.extend(["--add-data", f"{video_asset}{sep}assets"])

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name=DiscordBadgeMaximizer",
        "--onefile",
        "--noconsole",
        *data_args,
        "--collect-all", "customtkinter",
        "--collect-all", "imageio_ffmpeg",
        "--hidden-import", "cryptography",
        "--hidden-import", "dave",
        "--hidden-import", "yt_dlp",
        "--hidden-import", "tkinter",
        "--hidden-import", "darkdetect",
        "--hidden-import", "crypto_vault",
        "--hidden-import", "stream",
        "--hidden-import", "games",
        "--hidden-import", "bot",
        "--hidden-import", "discord",
        "--hidden-import", "discord.ui",
        "--hidden-import", "discord.app_commands",
        str(ROOT / "gui.py"),
    ]

    print("Running command:")
    print(" ".join(cmd))
    print()

    res = subprocess.run(cmd, cwd=str(ROOT))
    if res.returncode == 0:
        print()
        print("=" * 60)
        dist_dir = ROOT / "dist"
        exe_name = "DiscordBadgeMaximizer.exe" if sys.platform == "win32" else "DiscordBadgeMaximizer"
        target_path = dist_dir / exe_name
        if target_path.exists():
            print(f"  BUILD SUCCESS!")
            print(f"  Output binary: {target_path}")
            print(f"  File size: {target_path.stat().st_size / (1024*1024):.2f} MB")
        else:
            print(f"  Build completed in directory: {dist_dir}")
        print("=" * 60)
    else:
        print()
        print(f"  BUILD FAILED (Code {res.returncode})")
        sys.exit(res.returncode)

if __name__ == "__main__":
    build()
