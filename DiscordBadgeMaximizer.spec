# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all
from pathlib import Path

ROOT = Path.cwd()

datas = [('web', 'web')]
if (ROOT / 'assets').exists():
    datas.append(('assets', 'assets'))

binaries = []
hiddenimports = [
    'cryptography',
    'crypto_vault',
    'user_manager',
    'games',
    'quest_manager',
    'toinaylogi',
    'web_dashboard',
    'aiohttp',
    'tkinter',
    'darkdetect',
    'discord',
    'discord.ui',
    'discord.app_commands',
    'bot',
]
tmp_ret = collect_all('customtkinter')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('aiohttp')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

a = Analysis(
    ['gui.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='DiscordBadgeMaximizer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
app = BUNDLE(
    exe,
    name='DiscordBadgeMaximizer.app',
    icon=None,
    bundle_identifier=None,
)
