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

def run_pyinstaller(mode="onefile"):
    sep = ";" if sys.platform == "win32" else ":"
    video_asset = ROOT / "assets" / "video.mp4"
    data_args = []
    if video_asset.exists():
        data_args.extend(["--add-data", f"{video_asset}{sep}assets"])

    mode_arg = "--onefile" if mode == "onefile" else "--onedir"
    name = "DiscordBadgeMaximizer" if mode == "onefile" else "DiscordBadgeMaximizer-App"

    cmd = [
        sys.executable, "-m", "PyInstaller",
        f"--name={name}",
        mode_arg,
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

    print(f"Running PyInstaller ({mode})...")
    res = subprocess.run(cmd, cwd=str(ROOT))
    if res.returncode != 0:
        print(f"Error during {mode} build!")
        sys.exit(res.returncode)

def build():
    print("=" * 60)
    print("  BUILDING DISCORD BADGE MAXIMIZER (STANDALONE EXE + FOLDER)")
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

    # 1. Build Onedir (Less likely to be flagged by antivirus)
    run_pyinstaller(mode="onedir")
    app_folder = ROOT / "dist" / "DiscordBadgeMaximizer-App"
    if app_folder.exists():
        print("Compressing onedir app to zip archive...")
        shutil.make_archive(
            str(ROOT / "dist" / "DiscordBadgeMaximizer-Folder"),
            "zip",
            root_dir=str(ROOT / "dist"),
            base_dir="DiscordBadgeMaximizer-App"
        )
        print("Created dist/DiscordBadgeMaximizer-Folder.zip")

    # 2. Build Onefile
    run_pyinstaller(mode="onefile")

    print()
    print("=" * 60)
    print("  ALL BUILDS COMPLETED SUCCESSFULLY!")
    dist_dir = ROOT / "dist"
    for item in dist_dir.iterdir():
        if item.is_file():
            print(f"  - {item.name} ({item.stat().st_size / (1024*1024):.2f} MB)")
    print("=" * 60)

if __name__ == "__main__":
    build()
