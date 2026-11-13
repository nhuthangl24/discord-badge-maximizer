#!/usr/bin/env python3
"""
Discord Badge Maximizer - Multi-User Discord Application Bot
Control Playtime & Live Streaming Badges directly via Discord UI Modals & Slash Commands.

Supports:
- Multi-User Isolation: Each Discord user configures and runs their own stream & playtime sessions.
- Discord Modals (Popup forms) for entering Main/Alt tokens and cf_clearance cookies.
- Interactive Control Panel (/panel) with buttons for one-click streaming and setup.
- Smart Auto-Voice Channel Selection: Guaranteed to pick accessible voice rooms for the user's account.
- Ephemeral credentials: Token submissions and configs are only visible to the user who ran them.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import re
import sys
import threading
import time
import urllib.request
import urllib.error
from dataclasses import asdict
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands, ui
from discord.ext import commands

# Import core business engines from local codebase
from stream import (
    State,
    Session,
    Console,
    GatewayStreamEngine,
    ensure_token,
    validate_stream_target,
    find_random_voice_channel_from_token,
    extract_channel_id,
    format_timer,
    _discord_api_get,
    GATEWAY_URL,
    ME_URL,
    USERS_URL,
    USER_AGENT,
)
from games import (
    AccountSessionState,
    ApplicationController,
    load_game_catalog,
)
import crypto_vault
import quest_manager

if getattr(sys, "frozen", False):
    ROOT = Path(sys.executable).resolve().parent
else:
    ROOT = Path(__file__).resolve().parent

DATA_DIR = crypto_vault.get_data_dir()
USER_DATA_DIR = crypto_vault.get_user_data_dir()

# Auto-sync user backup files if user backed up to data/user or data/*.json
try:
    crypto_vault.sync_user_backup_files()
except Exception as _e:
    print(f"⚠️ [Data Sync Init] {_e}")

# Auto-expand system PATH to ensure ffmpeg in Homebrew/local paths is found
_common_bin_dirs = [
    "/opt/homebrew/bin",
    "/opt/homebrew/sbin",
    "/usr/local/bin",
    "/usr/bin",
    "/bin",
    str(Path.home() / ".local" / "bin"),
    str(Path.home() / "bin"),
]
_sep = ";" if sys.platform == "win32" else ":"
_cur_path = os.environ.get("PATH", "")
_to_add = [d for d in _common_bin_dirs if os.path.isdir(d) and d not in _cur_path]
if _to_add:
    os.environ["PATH"] = _sep.join(_to_add) + _sep + _cur_path

# Load environment variables from .env
def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip("'\"")
            if k and k not in os.environ:
                os.environ[k] = v
    except Exception:
        pass

_load_env_file(ROOT / ".env")


# ==========================================
# ADMIN PRIVILEGES
# ==========================================

PRIMARY_ADMIN_ID: int = 707934327467671643
ADMIN_IDS: set[int] = {PRIMARY_ADMIN_ID}
_env_admins = os.environ.get("ADMIN_IDS", "").strip()
if _env_admins:
    for _a in _env_admins.split(","):
        _a = _a.strip()
        if _a.isdigit():
            ADMIN_IDS.add(int(_a))


def is_admin(user_id: int) -> bool:
    """Returns True if the Discord user ID is an authorized bot admin."""
    return user_id in ADMIN_IDS


# ==========================================
# MULTI-USER STORAGE & SECURITY CLEANSE
# ==========================================

def _cleanse_contaminated_data() -> None:
    """
    Purges any user state files that were accidentally infected by legacy session.json,
    and moves session.json away so it can never be read or leak tokens again.
    """
    legacy_file = ROOT / "session.json"
    leaked_tokens = set()
    if legacy_file.exists():
        try:
            raw = json.loads(legacy_file.read_text(encoding="utf-8"))
            for k in ("token", "viewer_token"):
                v = raw.get(k, "").strip()
                if v:
                    leaked_tokens.add(v)
        except Exception:
            pass
        try:
            legacy_file.rename(ROOT / "session.json.contaminated.bak")
            print("🔒 [SECURITY] Đã di chuyển legacy session.json sang session.json.contaminated.bak để tránh lộ token.")
        except Exception:
            pass

    # 1. Purge legacy test files only if matching specific leaked tokens
    if USER_DATA_DIR.exists():
        for f in USER_DATA_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                tok = data.get("token", "").strip()
                vtok = data.get("viewer_token", "").strip()
                # Do not delete files if tokens are encrypted (they are user vaults)
                if not crypto_vault.is_encrypted(tok) and ((tok and tok in leaked_tokens) or (vtok and vtok in leaked_tokens)):
                    f.unlink(missing_ok=True)
                    print(f"🔒 [SECURITY] Đã xóa file cấu hình nhiễm token cũ: {f.name}")
            except Exception:
                pass

        # 2. Re-sync backup files before loading
        crypto_vault.sync_user_backup_files()

_cleanse_contaminated_data()


def get_user_state_file(user_id: int) -> Path:
    return USER_DATA_DIR / f"{user_id}.json"


def get_user_state(user_id: int) -> State:
    """
    Loads state for a specific Discord user ID.
    GUARANTEE: 100% isolated per Discord user ID.
    Never falls back to session.json, .env, or another user's credentials.
    """
    p = get_user_state_file(user_id)
    if not p.exists():
        state = State()
        state._filepath = p
        return state
    state = State.load(p)
    state._filepath = p
    return state


def save_user_state(user_id: int, state: State) -> None:
    """Saves state strictly for a specific Discord user ID."""
    p = get_user_state_file(user_id)
    state._filepath = p
    state.save(p)


def mask_token(token: str) -> str:
    if not token:
        return "*(Chưa thiết lập)*"
    if len(token) <= 12:
        return "********"
    return f"`{token[:6]}...{token[-4:]}` 🔒 *(Mã hóa DES+AES)*"


# ==========================================
# 1. DISCORD MODALS (POPUP FORMS)
# ==========================================

class AllInOneSetupModal(ui.Modal, title="Cấu hình Tài khoản (Chính & Phụ)"):
    main_token = ui.TextInput(
        label="Token Chính (Acc cần cày badge)",
        placeholder="Dán Discord user token tài khoản chính vào đây...",
        style=discord.TextStyle.short,
        required=True,
        min_length=20,
    )
    alt_token = ui.TextInput(
        label="Token Phụ (Alt Viewer xem stream)",
        placeholder="Dán token acc phụ để tự động ngồi xem stream (tùy chọn)...",
        style=discord.TextStyle.short,
        required=False,
    )
    cookie = ui.TextInput(
        label="Cookie cf_clearance (Cày giờ game)",
        placeholder="Dán giá trị cf_clearance cookie header (tùy chọn)...",
        style=discord.TextStyle.paragraph,
        required=False,
    )
    channel_id = ui.TextInput(
        label="ID Kênh Voice (Bỏ trống = Tự động)",
        placeholder="Dán ID phòng voice để lọc theo ID (Bỏ trống = Auto random)...",
        style=discord.TextStyle.short,
        required=False,
    )
    stream_url = ui.TextInput(
        label="Link YouTube / URL Video (Bỏ trống = Mẫu)",
        placeholder="https://www.youtube.com/watch?v=... hoặc để trống để lặp video mẫu",
        style=discord.TextStyle.short,
        required=False,
    )

    def __init__(self, current_state: State) -> None:
        super().__init__()
        # Pre-fill existing credentials so the user never has to re-type them!
        if current_state.token:
            self.main_token.default = current_state.token
        if current_state.viewer_token:
            self.alt_token.default = current_state.viewer_token
        if current_state.cookie:
            self.cookie.default = current_state.cookie
        if current_state.last_channel_id:
            self.channel_id.default = current_state.last_channel_id
        if current_state.stream_url:
            self.stream_url.default = current_state.stream_url

    async def on_submit(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        state = get_user_state(user_id)
        if self.main_token.value.strip():
            state.token = self.main_token.value.strip()

        state.viewer_token = self.alt_token.value.strip()
        state.cookie = self.cookie.value.strip()
        if self.channel_id.value.strip():
            state.last_channel_id = extract_channel_id(self.channel_id.value.strip())
        else:
            state.last_channel_id = ""

        state.stream_url = self.stream_url.value.strip()

        save_user_state(user_id, state)

        # Sync with games.py session state for this user
        user_file = get_user_state_file(user_id)
        game_state = AccountSessionState.restore(user_file)
        game_state.token = state.token
        if state.cookie:
            game_state.cookie = state.cookie
        game_state.persist(user_file)

        embed = discord.Embed(
            title="✅ Đã cập nhật cấu hình thành công qua Popup!",
            description="Thông tin tài khoản chính và phụ của bạn đã được lưu an toàn (chỉ bạn nhìn thấy).",
            color=discord.Color.green(),
        )
        embed.add_field(name="Tài khoản chính (Cày badge)", value=mask_token(state.token), inline=True)
        embed.add_field(name="Tài khoản phụ (Alt Viewer)", value=mask_token(state.viewer_token), inline=True)
        embed.add_field(name="Clearance Cookie", value="`Đã cấu hình`" if state.has_cookie else "*(Trống)*", inline=True)
        if state.last_channel_id:
            embed.add_field(name="Phòng Voice (Khóa theo ID)", value=f"`{state.last_channel_id}`", inline=True)
        else:
            embed.add_field(name="Phòng Voice", value="🎲 *Tự động chọn ngẫu nhiên*", inline=True)
        if state.stream_url:
            embed.add_field(name="Nguồn phát Video", value=f"📺 `{state.stream_url}`", inline=False)
        else:
            embed.add_field(name="Nguồn phát Video", value="🎞️ *assets/video.mp4 (Lặp vô tận - Loop)*", inline=False)

        embed.set_footer(text="Bây giờ bạn có thể bấm nút '🔴 Bắt đầu Stream' hoặc gõ /stream start!")
        await interaction.response.send_message(embed=embed, ephemeral=True)


class SetStreamSourceModal(ui.Modal, title="Cài đặt Video Stream & Phòng Voice"):
    channel_id = ui.TextInput(
        label="ID Phòng Voice (Bỏ trống = Tự động)",
        placeholder="Dán ID phòng voice để lọc theo ID (Bỏ trống = Auto random)...",
        style=discord.TextStyle.short,
        required=False,
    )
    stream_url = ui.TextInput(
        label="Link YouTube / URL Video (Bỏ trống = Mẫu)",
        placeholder="https://www.youtube.com/watch?v=... hoặc để trống...",
        style=discord.TextStyle.short,
        required=False,
    )

    def __init__(self, current_state: State) -> None:
        super().__init__()
        if current_state.last_channel_id:
            self.channel_id.default = current_state.last_channel_id
        if current_state.stream_url:
            self.stream_url.default = current_state.stream_url

    async def on_submit(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        state = get_user_state(user_id)
        if self.channel_id.value.strip():
            state.last_channel_id = extract_channel_id(self.channel_id.value.strip())
        else:
            state.last_channel_id = ""

        state.stream_url = self.stream_url.value.strip()
        save_user_state(user_id, state)

        embed = discord.Embed(
            title="✅ Đã cập nhật Nguồn phát & Phòng Voice!",
            color=discord.Color.green(),
        )
        embed.add_field(
            name="Phòng Voice",
            value=f"`{state.last_channel_id}` (Khóa theo ID)" if state.last_channel_id else "🎲 *Tự động chọn ngẫu nhiên*",
            inline=True
        )
        embed.add_field(
            name="Nguồn phát Video",
            value=f"📺 `{state.stream_url}`" if state.stream_url else "🎞️ *assets/video.mp4 (Lặp vô tận - Loop)*",
            inline=False
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


class MainAccountModal(ui.Modal, title="Cài đặt Tài khoản Chính (Acc cày)"):
    main_token = ui.TextInput(
        label="Token Tài khoản Chính",
        placeholder="Dán token tài khoản chính (Tự động mã hóa DES+AES)...",
        style=discord.TextStyle.short,
        required=True,
        min_length=20,
    )
    cookie = ui.TextInput(
        label="Cookie cf_clearance (Tùy chọn - Cày game)",
        placeholder="Dán cf_clearance cookie header (tùy chọn)...",
        style=discord.TextStyle.paragraph,
        required=False,
    )

    def __init__(self, current_state: State) -> None:
        super().__init__()
        if current_state.token:
            self.main_token.default = current_state.token
        if current_state.cookie:
            self.cookie.default = current_state.cookie

    async def on_submit(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        state = get_user_state(user_id)
        if self.main_token.value.strip():
            state.token = self.main_token.value.strip()
        state.cookie = self.cookie.value.strip()
        save_user_state(user_id, state)

        user_file = get_user_state_file(user_id)
        game_state = AccountSessionState.restore(user_file)
        game_state.token = state.token
        if state.cookie:
            game_state.cookie = state.cookie
        game_state.persist(user_file)

        embed = discord.Embed(
            title="✅ Đã lưu Tài khoản Chính thành công!",
            description="Token và Cookie đã được bảo mật mã hóa lồng nhau bằng **TripleDES + AES-256**.",
            color=discord.Color.green(),
        )
        embed.add_field(name="Token Chính", value=mask_token(state.token), inline=True)
        embed.add_field(name="Cookie", value="`Đã lưu & mã hóa`" if state.has_cookie else "*(Trống)*", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class AltAccountModal(ui.Modal, title="Cài đặt Tài khoản Phụ (Alt Viewer)"):
    alt_token = ui.TextInput(
        label="Token Tài khoản Phụ (Alt Viewer)",
        placeholder="Dán token tài khoản phụ để auto-view stream (Opcode 20)...",
        style=discord.TextStyle.short,
        required=False,
    )

    def __init__(self, current_state: State) -> None:
        super().__init__()
        if current_state.viewer_token:
            self.alt_token.default = current_state.viewer_token

    async def on_submit(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        state = get_user_state(user_id)
        state.viewer_token = self.alt_token.value.strip()
        save_user_state(user_id, state)

        embed = discord.Embed(
            title="✅ Đã lưu Tài khoản Phụ (Alt Viewer) thành công!",
            description="Tài khoản phụ đã được mã hóa lồng nhau **DES + AES** và sẽ tự động tham gia phòng voice xem stream.",
            color=discord.Color.green(),
        )
        embed.add_field(name="Token Phụ", value=mask_token(state.viewer_token), inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class FarmGamesModal(ui.Modal, title="Cày Giờ Chơi Game"):
    count = ui.TextInput(
        label="Số lượng Game cần cày",
        placeholder="Ví dụ: 50 hoặc 100",
        default="50",
        style=discord.TextStyle.short,
        required=True,
    )
    hours = ui.TextInput(
        label="Số giờ cho mỗi Game",
        placeholder="Ví dụ: 1.0 hoặc 50.0",
        default="1.0",
        style=discord.TextStyle.short,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        state = get_user_state(user_id)
        if not state.has_token:
            embed = discord.Embed(
                title="❌ Chưa có Token tài khoản",
                description="Vui lòng dùng nút Cài đặt hoặc `/setup` để điền token trước.",
                color=discord.Color.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        if not state.has_cookie:
            embed = discord.Embed(
                title="❌ Thiếu Cookie cf_clearance",
                description="Cày giờ game bắt buộc cần cookie `cf_clearance`. Vui lòng cập nhật qua nút Cài đặt hoặc `/setup`.",
                color=discord.Color.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        try:
            target_count = int(self.count.value.strip())
            target_hours = float(self.hours.value.strip())
        except ValueError:
            embed = discord.Embed(
                title="❌ Giá trị không hợp lệ",
                description="Số game phải là số nguyên (ví dụ: 50) và số giờ là số thực (ví dụ: 1.0 hoặc 50.0).",
                color=discord.Color.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        with bot.stream_lock:
            if user_id in bot.active_farm_threads and bot.active_farm_threads[user_id].is_alive():
                embed = discord.Embed(
                    title="⚠️ Bạn đang có một phiên cày game đang chạy!",
                    description="Vui lòng đợi phiên trước hoàn thành rồi hãy bắt đầu phiên mới.",
                    color=discord.Color.yellow(),
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

        await interaction.response.defer(ephemeral=True)

        user_file = get_user_state_file(user_id)
        controller = ApplicationController(session_path=user_file)
        controller.state.token = state.token
        controller.state.cookie = state.cookie

        result = await asyncio.to_thread(controller.start_headless, games_count=target_count, hours=target_hours)

        if not result.get("success"):
            embed = discord.Embed(
                title="❌ Cày game thất bại",
                description=f"Lỗi: `{result.get('error')}`",
                color=discord.Color.red(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        fresh_game_state = AccountSessionState.restore(user_file)
        fresh_user_state = get_user_state(user_id)
        fresh_user_state.total_games_claimed = fresh_game_state.total_games_claimed
        fresh_user_state.total_hours_claimed = fresh_game_state.total_hours_claimed
        save_user_state(user_id, fresh_user_state)

        accepted = result.get("accepted", 0)
        total_req = result.get("total_requested", 0)
        hours_added = result.get("total_hours", 0.0)

        embed = discord.Embed(
            title="🎮 Đã cày giờ game thành công!",
            color=discord.Color.green(),
        )
        embed.add_field(name="Người dùng", value=interaction.user.mention, inline=True)
        embed.add_field(name="Số game đã gửi", value=f"`{accepted}/{total_req}` games", inline=True)
        embed.add_field(name="Thời gian mỗi game", value=f"`{target_hours:g}h`", inline=True)
        embed.add_field(name="Tổng số giờ vừa cộng", value=f"`+{hours_added:,.0f}h`", inline=True)
        embed.add_field(
            name="Tổng tích lũy hiện tại của bạn",
            value=f"**{fresh_game_state.total_hours_claimed:,.0f}h** trên **{fresh_game_state.total_games_claimed}** games",
            inline=False
        )
        embed.set_footer(text="Lưu ý: Hệ thống Discord Analytics đồng bộ tiến độ badge sau 24-48 giờ.")
        await interaction.followup.send(embed=embed, ephemeral=True)


# ==========================================
# 2. INTERACTIVE CONTROL PANEL VIEW (BUTTONS)
# ==========================================

class ControlPanelView(ui.View):
    def __init__(self, bot_instance: StreamBot) -> None:
        super().__init__(timeout=None)
        self.bot_instance = bot_instance

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: ui.Item[Any]) -> None:
        if isinstance(error, discord.NotFound) and getattr(error, "code", None) == 10062:
            return
        print(f"[ControlPanelView Error in {item}] {error}")

    @ui.button(label="⚙️ Cài đặt Tài khoản (Popup)", style=discord.ButtonStyle.primary, row=0, custom_id="panel_btn_setup")
    async def btn_setup(self, interaction: discord.Interaction, button: ui.Button):
        state = get_user_state(interaction.user.id)
        await interaction.response.send_modal(AllInOneSetupModal(current_state=state))

    @ui.button(label="👤 Cài Acc Phụ (Alt)", style=discord.ButtonStyle.secondary, row=0, custom_id="panel_btn_alt")
    async def btn_alt(self, interaction: discord.Interaction, button: ui.Button):
        state = get_user_state(interaction.user.id)
        await interaction.response.send_modal(AltAccountModal(current_state=state))

    @ui.button(label="📺 Nguồn Video & Kênh (Popup)", style=discord.ButtonStyle.secondary, row=0, custom_id="panel_btn_source")
    async def btn_source(self, interaction: discord.Interaction, button: ui.Button):
        state = get_user_state(interaction.user.id)
        await interaction.response.send_modal(SetStreamSourceModal(current_state=state))

    @ui.button(label="🔴 Bắt đầu Stream", style=discord.ButtonStyle.success, row=1, custom_id="panel_btn_stream_start")
    async def btn_stream_start(self, interaction: discord.Interaction, button: ui.Button):
        await handle_start_stream(interaction, channel_id=None, guild_id=None)

    @ui.button(label="⏹️ Dừng Stream", style=discord.ButtonStyle.danger, row=1, custom_id="panel_btn_stream_stop")
    async def btn_stream_stop(self, interaction: discord.Interaction, button: ui.Button):
        await handle_stop_stream(interaction)

    @ui.button(label="📊 Xem Trạng thái", style=discord.ButtonStyle.secondary, row=1, custom_id="panel_btn_stream_status")
    async def btn_stream_status(self, interaction: discord.Interaction, button: ui.Button):
        await handle_stream_status(interaction)

    @ui.button(label="🎮 Cày Giờ Game (Popup)", style=discord.ButtonStyle.primary, row=2, custom_id="panel_btn_farm_games")
    async def btn_farm_games(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(FarmGamesModal())

    @ui.button(label="🏆 Xem Profile & Badges", style=discord.ButtonStyle.secondary, row=2, custom_id="panel_btn_profile")
    async def btn_profile(self, interaction: discord.Interaction, button: ui.Button):
        await handle_view_profile(interaction)

    @ui.button(label="👑 Quản trị Bot (Admin)", style=discord.ButtonStyle.secondary, row=2, custom_id="panel_btn_admin")
    async def btn_admin(self, interaction: discord.Interaction, button: ui.Button):
        try:
            if not is_admin(interaction.user.id):
                embed = discord.Embed(
                    title="⛔ Truy cập bị từ chối",
                    description=f"Bạn không có quyền Admin ({PRIMARY_ADMIN_ID}) để truy cập bảng điều khiển này!",
                    color=discord.Color.red(),
                )
                if not interaction.response.is_done():
                    await interaction.response.send_message(embed=embed, ephemeral=True)
                else:
                    await interaction.followup.send(embed=embed, ephemeral=True)
                return

            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)

            embed = discord.Embed(
                title="👑 Bảng Quản Trị Hệ Thống (Dành Cho Admin)",
                description=(
                    f"Xin chào <@{interaction.user.id}>! Bạn đang đăng nhập với quyền **Admin Tối Cao**.\n\n"
                    "• **📋 Xem Luồng Stream**: Giám sát mọi phiên stream đang chạy của tất cả user.\n"
                    "• **📊 Thống Kê Toàn Bộ**: Xem tổng user, tổng số giờ cày stream/game cả hệ thống.\n"
                    "• **🔄 Stream Lại Tất Cả**: Khởi động lại stream cho toàn bộ người dùng đã cài đặt token.\n"
                    "• **🛑 Dừng Toàn Bộ (Emergency)**: Cắt toàn bộ luồng phát sóng ngay lập tức.\n"
                    "• **🎯 Quản Lý Quests**: Kiểm tra & tự động accept/complete Quest cho toàn hệ thống.\n\n"
                    "💡 *Bạn cũng có thể gõ lệnh `/admin restart_all` hoặc `/admin stop <user>`.*"
                ),
                color=discord.Color.gold(),
            )
            view = AdminPanelView()
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        except discord.NotFound:
            pass

    @ui.button(label="🎯 Quản Lý Quests", style=discord.ButtonStyle.primary, row=3, custom_id="panel_btn_quest_panel")
    async def btn_quest_panel(self, interaction: discord.Interaction, button: ui.Button):
        await handle_quest_panel(interaction)

    @ui.button(label="⚡ Auto Nhận Tất Cả", style=discord.ButtonStyle.success, row=3, custom_id="panel_btn_quest_enroll_all")
    async def btn_quest_enroll_all(self, interaction: discord.Interaction, button: ui.Button):
        await handle_quest_enroll_all(interaction)

    @ui.button(label="🎬 Làm Video Quests", style=discord.ButtonStyle.secondary, row=3, custom_id="panel_btn_quest_video")
    async def btn_quest_video(self, interaction: discord.Interaction, button: ui.Button):
        await handle_quest_video(interaction)

    @ui.button(label="🎁 Claim Quà Quests", style=discord.ButtonStyle.secondary, row=3, custom_id="panel_btn_quest_claim")
    async def btn_quest_claim(self, interaction: discord.Interaction, button: ui.Button):
        await handle_quest_claim(interaction)


class QuestControlView(ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: ui.Item[Any]) -> None:
        if isinstance(error, discord.NotFound) and getattr(error, "code", None) == 10062:
            return
        print(f"[QuestControlView Error in {item}] {error}")

    @ui.button(label="🔄 Quét & Làm Mới", style=discord.ButtonStyle.primary, row=0, custom_id="quest_btn_refresh")
    async def btn_refresh(self, interaction: discord.Interaction, button: ui.Button):
        await handle_quest_panel(interaction)

    @ui.button(label="⚡ Auto Nhận Tất Cả", style=discord.ButtonStyle.success, row=0, custom_id="quest_btn_enroll_all")
    async def btn_enroll_all(self, interaction: discord.Interaction, button: ui.Button):
        await handle_quest_enroll_all(interaction)

    @ui.button(label="🎬 Làm Video Quests", style=discord.ButtonStyle.secondary, row=0, custom_id="quest_btn_video")
    async def btn_video(self, interaction: discord.Interaction, button: ui.Button):
        await handle_quest_video(interaction)

    @ui.button(label="🎮 Chạy Game Process", style=discord.ButtonStyle.primary, row=1, custom_id="quest_btn_desktop")
    async def btn_desktop(self, interaction: discord.Interaction, button: ui.Button):
        await handle_quest_desktop(interaction)

    @ui.button(label="🎁 Claim Tất Cả Quà", style=discord.ButtonStyle.secondary, row=1, custom_id="quest_btn_claim_all")
    async def btn_claim_all(self, interaction: discord.Interaction, button: ui.Button):
        await handle_quest_claim(interaction)

    @ui.button(label="🚀 Chạy Auto Trọn Gói", style=discord.ButtonStyle.success, row=2, custom_id="quest_btn_full_auto")
    async def btn_full_auto(self, interaction: discord.Interaction, button: ui.Button):
        await handle_quest_full_auto(interaction)

    @ui.button(label="🔙 Bảng Điều Khiển Chính", style=discord.ButtonStyle.secondary, row=2, custom_id="quest_btn_back")
    async def btn_back(self, interaction: discord.Interaction, button: ui.Button):
        try:
            with bot.stream_lock:
                active_count = len([e for e in bot.active_engines.values() if e.running])
            embed = discord.Embed(
                title="🎛️ Bảng Điều Khiển Discord Badge Maximizer (Multi-User)",
                description=(
                    "Hệ thống hỗ trợ **nhiều người dùng cùng lúc** (Multi-User Isolation).\n"
                    "Mỗi người bấm nút bên dưới sẽ tự quản lý tài khoản riêng biệt của mình qua Popup Form ẩn danh:\n\n"
                    "• **⚙️ Cài đặt Tài khoản**: Mở popup điền token chính, cookie và alt token.\n"
                    "• **🔴 Bắt đầu Stream**: Tự động tìm phòng voice khả dụng và phát sóng.\n"
                    "• **⏹️ Dừng Stream**: Dừng luồng phát sóng của bạn và lưu lại số giờ.\n"
                    "• **📊 Xem Trạng thái**: Kiểm tra thời gian stream tích lũy của bạn.\n"
                    "• **🎮 Cày Giờ Game**: Mở popup chọn số game và số giờ để cày tự động.\n"
                    "• **🎯 Nhiệm Vụ Quests**: Tự động quét, nhận và hoàn thành Discord Quests."
                ),
                color=discord.Color.blurple(),
            )
            embed.add_field(name="⚡ Máy chủ Bot", value=f"Đang phát sóng: **{active_count}** luồng đồng thời", inline=False)
            view = ControlPanelView(bot_instance=bot)
            if not interaction.response.is_done():
                await interaction.response.edit_message(embed=embed, view=view)
            else:
                await interaction.message.edit(embed=embed, view=view)
        except Exception as e:
            print(f"[Quest Back Error] {e}")


class AdminPanelView(ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: ui.Item[Any]) -> None:
        if isinstance(error, discord.NotFound) and getattr(error, "code", None) == 10062:
            return
        print(f"[AdminPanelView Error in {item}] {error}")

    @ui.button(label="📋 Xem Tất Cả Luồng Stream", style=discord.ButtonStyle.primary, row=0, custom_id="admin_btn_list")
    async def btn_list(self, interaction: discord.Interaction, button: ui.Button):
        try:
            if not is_admin(interaction.user.id):
                if not interaction.response.is_done():
                    return await interaction.response.send_message("⛔ Chỉ Admin mới có quyền truy cập!", ephemeral=True)
                return await interaction.followup.send("⛔ Chỉ Admin mới có quyền truy cập!", ephemeral=True)
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)
            embed = build_admin_stream_list_embed()
            await interaction.followup.send(embed=embed, ephemeral=True)
        except discord.NotFound:
            pass

    @ui.button(label="📊 Thống Kê Toàn Bộ Bot", style=discord.ButtonStyle.secondary, row=0, custom_id="admin_btn_stats")
    async def btn_stats(self, interaction: discord.Interaction, button: ui.Button):
        try:
            if not is_admin(interaction.user.id):
                if not interaction.response.is_done():
                    return await interaction.response.send_message("⛔ Chỉ Admin mới có quyền truy cập!", ephemeral=True)
                return await interaction.followup.send("⛔ Chỉ Admin mới có quyền truy cập!", ephemeral=True)
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)
            embed = build_admin_stats_embed()
            await interaction.followup.send(embed=embed, ephemeral=True)
        except discord.NotFound:
            pass

    @ui.button(label="🧹 Dọn Luồng Treo (Prune)", style=discord.ButtonStyle.secondary, row=1, custom_id="admin_btn_prune")
    async def btn_prune(self, interaction: discord.Interaction, button: ui.Button):
        try:
            if not is_admin(interaction.user.id):
                if not interaction.response.is_done():
                    return await interaction.response.send_message("⛔ Chỉ Admin mới có quyền truy cập!", ephemeral=True)
                return await interaction.followup.send("⛔ Chỉ Admin mới có quyền truy cập!", ephemeral=True)
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)
            pruned = prune_disconnected_streams()
            embed = discord.Embed(
                title="🧹 [Admin] Đã dọn dẹp các luồng Stream bị treo / Reconnecting!",
                description=(
                    f"Đã dừng và dọn sạch **{pruned}** luồng bị mất kết nối.\n\n"
                    f"🟢 **Tất cả các luồng đang phát (LIVE) của các user khác vẫn tiếp tục chạy 100% bình thường!**"
                ),
                color=discord.Color.green(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        except discord.NotFound:
            pass

    @ui.button(label="🔄 Stream Lại Tất Cả Users", style=discord.ButtonStyle.success, row=1, custom_id="admin_btn_restart_all")
    async def btn_restart_all(self, interaction: discord.Interaction, button: ui.Button):
        try:
            if not is_admin(interaction.user.id):
                if not interaction.response.is_done():
                    return await interaction.response.send_message("⛔ Chỉ Admin mới có quyền truy cập!", ephemeral=True)
                return await interaction.followup.send("⛔ Chỉ Admin mới có quyền truy cập!", ephemeral=True)
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)
            success, failed = await asyncio.to_thread(restart_all_users_streams)
            embed = discord.Embed(
                title=f"🔄 [Admin] Kết Quả Stream Lại Cho Tất Cả Users ({len(success)} thành công)",
                color=discord.Color.green() if success else discord.Color.red(),
            )
            if success:
                embed.add_field(name=f"✅ Đã kết nối & phát sóng ({len(success)} users)", value="\n".join(success[:20]), inline=False)
            if failed:
                embed.add_field(name=f"⚠️ Không thể phát ({len(failed)} users)", value="\n".join(failed[:15]), inline=False)
            embed.set_footer(text=f"Admin: {PRIMARY_ADMIN_ID} · Dùng /admin list để kiểm tra chi tiết.")
            await interaction.followup.send(embed=embed, ephemeral=True)
        except discord.NotFound:
            pass

    @ui.button(label="🛑 Dừng Tất Cả (Stop All)", style=discord.ButtonStyle.danger, row=1, custom_id="admin_btn_stop_all")
    async def btn_stop_all(self, interaction: discord.Interaction, button: ui.Button):
        try:
            if not is_admin(interaction.user.id):
                if not interaction.response.is_done():
                    return await interaction.response.send_message("⛔ Chỉ Admin mới có quyền truy cập!", ephemeral=True)
                return await interaction.followup.send("⛔ Chỉ Admin mới có quyền truy cập!", ephemeral=True)
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)
            stopped = stop_all_streams()
            embed = discord.Embed(
                title="🛑 [Admin] Đã dừng toàn bộ luồng Stream!",
                description=f"Đã dừng an toàn **{stopped}** luồng phát sóng trên bot.",
                color=discord.Color.red(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        except discord.NotFound:
            pass

    @ui.button(label="🎯 Báo Cáo Quest Toàn Bot", style=discord.ButtonStyle.primary, row=2, custom_id="admin_btn_quest_panel")
    async def btn_admin_quest_panel(self, interaction: discord.Interaction, button: ui.Button):
        try:
            if not is_admin(interaction.user.id):
                if not interaction.response.is_done():
                    return await interaction.response.send_message("⛔ Chỉ Admin mới có quyền truy cập!", ephemeral=True)
                return await interaction.followup.send("⛔ Chỉ Admin mới có quyền truy cập!", ephemeral=True)
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)
            embed = await asyncio.to_thread(build_admin_quests_summary_embed)
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            print(f"[Admin Quest Panel Error] {e}")

    @ui.button(label="⚡ Auto Accept Quests Cho Tất Cả", style=discord.ButtonStyle.success, row=2, custom_id="admin_btn_quest_enroll_all")
    async def btn_admin_quest_enroll_all(self, interaction: discord.Interaction, button: ui.Button):
        try:
            if not is_admin(interaction.user.id):
                if not interaction.response.is_done():
                    return await interaction.response.send_message("⛔ Chỉ Admin mới có quyền truy cập!", ephemeral=True)
                return await interaction.followup.send("⛔ Chỉ Admin mới có quyền truy cập!", ephemeral=True)
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)
            embed = await asyncio.to_thread(admin_enroll_all_users_quests)
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            print(f"[Admin Quest Enroll All Error] {e}")

    @ui.button(label="🚀 Chạy Auto Quests Toàn Bộ Users", style=discord.ButtonStyle.danger, row=2, custom_id="admin_btn_quest_auto_all")
    async def btn_admin_quest_auto_all(self, interaction: discord.Interaction, button: ui.Button):
        try:
            if not is_admin(interaction.user.id):
                if not interaction.response.is_done():
                    return await interaction.response.send_message("⛔ Chỉ Admin mới có quyền truy cập!", ephemeral=True)
                return await interaction.followup.send("⛔ Chỉ Admin mới có quyền truy cập!", ephemeral=True)
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)
            embed = await asyncio.to_thread(admin_auto_run_all_users_quests)
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            print(f"[Admin Quest Auto All Error] {e}")


# ==========================================
# 3. HELPER BUSINESS LOGIC FUNCTIONS
# ==========================================

async def handle_start_stream(
    interaction: discord.Interaction,
    channel_id: Optional[str] = None,
    guild_id: Optional[str] = None,
    stream_url: Optional[str] = None,
):
    user_id = interaction.user.id
    state = get_user_state(user_id)

    if stream_url and stream_url.strip():
        state.stream_url = stream_url.strip()
        save_user_state(user_id, state)

    if not state.has_token:
        embed = discord.Embed(
            title="❌ Chưa có Token tài khoản",
            description=(
                "Bạn chưa cài đặt token tài khoản cần cày badge!\n"
                "👉 Hãy bấm nút **⚙️ Cài đặt Tài khoản** trên bảng điều khiển hoặc gõ `/setup` để điền token."
            ),
            color=discord.Color.red(),
        )
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    with bot.stream_lock:
        eng = bot.active_engines.get(user_id)
        if eng and eng.running:
            embed = discord.Embed(
                title="⚠️ Bạn đang có một phiên Stream đang chạy rồi!",
                description="Dùng nút **📊 Xem Trạng thái** hoặc **⏹️ Dừng Stream** để quản lý luồng của bạn.",
                color=discord.Color.yellow(),
            )
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
            return

    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)

    session = Session.new()
    try:
        ensure_token(state, session.super_props)
    except Exception as exc:
        embed = discord.Embed(
            title="❌ Lỗi xác thực tài khoản",
            description=f"Không thể kết nối token tài khoản của bạn: `{exc}`\nHãy kiểm tra lại token bằng `/setup`.",
            color=discord.Color.red(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    # Channel resolution:
    # Priority 1: channel_id passed explicitly into command (/stream start channel_id:...)
    # Priority 2: state.last_channel_id saved from /setup or modal (LỌC THEO ID)
    # Priority 3: Auto-discovery (random voice room across accessible servers)
    target_cid = ""
    target_gid = guild_id.strip() if guild_id else ""
    selected_name = "Voice Channel"
    is_random = False

    if channel_id and channel_id.strip():
        target_cid = extract_channel_id(channel_id.strip())
    elif state.last_channel_id and state.last_channel_id.strip():
        target_cid = extract_channel_id(state.last_channel_id.strip())
        target_gid = state.last_guild_id or target_gid

    # Case 1: LỌC THEO ID (Filter / Lock to specific Channel ID)
    if target_cid:
        valid, gid, name = validate_stream_target(
            token=state.token,
            super_props=session.super_props,
            channel_id=target_cid,
            console=Console(),
            guild_id=target_gid,
        )
        if not valid:
            embed = discord.Embed(
                title="❌ Kênh Voice theo ID không hợp lệ hoặc thiếu quyền truy cập",
                description=(
                    f"Phòng Voice ID `{target_cid}` không khả dụng cho tài khoản user của bạn:\n"
                    f"• Lí do: `{name}`\n\n"
                    f"**👉 Khắc phục:** Đảm bảo tài khoản user của bạn ({mask_token(state.token)}) "
                    f"đã tham gia server chứa phòng voice này và có quyền **Kết nối (Connect)** + **Phát trực tiếp (Stream)**.\n\n"
                    f"💡 *Mẹo: Nếu muốn đổi sang tự bốc phòng ngẫu nhiên, hãy bấm nút **📺 Nguồn Video & Kênh** và để trống ô ID.*"
                ),
                color=discord.Color.red(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        target_gid = gid
        selected_name = f"#{name}"

    # Case 2: Auto-pick random voice channel (Smart Discovery for user's account)
    else:
        is_random = True
        found_in_current_guild = False

        # Step 2A: Check if user account is in current guild and can connect to a voice channel
        if interaction.guild and interaction.guild.voice_channels:
            # Check if user account belongs to this server
            ug_status, ug_list = _discord_api_get("https://discord.com/api/v9/users/@me/guilds", state.token, session.super_props)
            user_in_guild = False
            if ug_status == 200 and isinstance(ug_list, list):
                user_in_guild = any(str(g.get("id")) == str(interaction.guild.id) for g in ug_list)

            if user_in_guild:
                vcs = list(interaction.guild.voice_channels)
                random.shuffle(vcs)
                for vc in vcs:
                    cid_cand = str(vc.id)
                    valid, gid, name = validate_stream_target(
                        token=state.token,
                        super_props=session.super_props,
                        channel_id=cid_cand,
                        console=Console(),
                        guild_id=str(interaction.guild.id),
                    )
                    if valid:
                        target_cid = cid_cand
                        target_gid = gid or str(interaction.guild.id)
                        selected_name = f"#{name} ({interaction.guild.name})"
                        found_in_current_guild = True
                        break

        # Step 2B: Fallback to scanning all servers the user account belongs to
        if not found_in_current_guild:
            rcid, rgid, rname = find_random_voice_channel_from_token(state.token, session.super_props)
            if rcid:
                target_cid = rcid
                target_gid = rgid
                selected_name = f"#{rname}"

        # Step 2C: If still no channel found, provide actionable help
        if not target_cid:
            embed = discord.Embed(
                title="❌ Không tìm thấy phòng Voice khả dụng cho tài khoản của bạn!",
                description=(
                    f"Tài khoản user của bạn ({mask_token(state.token)}) chưa tham gia server nào có phòng voice, "
                    f"hoặc chưa được mời vào server này ({interaction.guild.name if interaction.guild else 'Discord'}).\n\n"
                    f"**👉 Cách khắc phục đơn giản:**\n"
                    f"1. **Mời tài khoản user của bạn** vào server này và đảm bảo có quyền **Kết nối (Connect)** + **Phát trực tiếp (Stream)** vào phòng voice.\n"
                    f"2. Hoặc **tự tạo 1 Server Discord riêng** bằng tài khoản đó, tạo 1 kênh voice bất kỳ, rồi bấm lại nút **Bắt đầu Stream**!"
                ),
                color=discord.Color.red(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

    # Save last channel for this user
    state.last_channel_id = target_cid
    state.last_guild_id = target_gid
    save_user_state(user_id, state)

    # Initialize engine with user's state
    engine = GatewayStreamEngine(state, session, Console())

    def _run_stream(uid: int, eng: GatewayStreamEngine, gid: str, cid: str):
        C = eng.console
        eng.stopped_by_user = False
        restarts = 0
        while not eng.stopped_by_user:
            try:
                eng.start_stream(guild_id=gid, channel_id=cid, wait_for_stdin=False)
            except Exception as exc:
                C.warn("stream", f"stream cycle error for user {uid}: {exc}")

            if eng.stopped_by_user:
                break

            restarts += 1
            delay = min(8.0, 2.0 + (restarts % 4) * 1.5)
            C.info("stream", f"🔄 [Đa luồng Auto-Res] Luồng của user {uid} tự động khôi phục phát lại sau {delay:.1f}s (Chế độ phát liên tục không tắt)...")
            time.sleep(delay)

        with bot.stream_lock:
            if bot.active_engines.get(uid) == eng:
                bot.active_engines.pop(uid, None)
                bot.session_started_at.pop(uid, None)
                bot.stream_threads.pop(uid, None)

    with bot.stream_lock:
        bot.active_engines[user_id] = engine
        bot.session_started_at[user_id] = time.time()
        t = threading.Thread(target=_run_stream, args=(user_id, engine, target_gid, target_cid), daemon=True)
        bot.stream_threads[user_id] = t
        t.start()

    embed = discord.Embed(
        title="🎲 Đã tự động vào phòng Voice ngẫu nhiên!" if is_random else "🎯 Đã kết nối phòng Voice theo ID!",
        description=f"Tài khoản của bạn đang kết nối Gateway và phát trực tiếp vào phòng voice.",
        color=discord.Color.brand_green(),
    )
    embed.add_field(name="Người dùng", value=interaction.user.mention, inline=True)
    embed.add_field(name="Phòng Voice", value=f"`{selected_name}` (`{target_cid}`)", inline=False)
    if state.stream_url:
        embed.add_field(name="Nguồn phát Video", value=f"📺 `{state.stream_url}`", inline=False)
    else:
        embed.add_field(name="Nguồn phát Video", value="🎞️ `assets/video.mp4 (Lặp vô tận - Loop)`", inline=False)
    embed.add_field(
        name="Alt Viewer Tự động",
        value="`Bật (Opcode 20)`" if state.has_viewer_token else "`Tắt (Cần người ngồi xem)`",
        inline=True
    )
    embed.add_field(name="Trạng thái", value="🟢 **Đang phát sóng**", inline=True)
    embed.set_footer(text="Dùng /stream status hoặc nút Xem Trạng thái để theo dõi.")
    await interaction.followup.send(embed=embed, ephemeral=True)


async def handle_stop_stream(interaction: discord.Interaction, target_user_id: Optional[int] = None):
    caller_id = interaction.user.id
    target_id = target_user_id or caller_id

    # If attempting to stop another user's stream, require admin
    if target_id != caller_id and not is_admin(caller_id):
        embed = discord.Embed(
            title="⛔ Truy cập bị từ chối",
            description=f"Bạn không có quyền Admin ({PRIMARY_ADMIN_ID}) để dừng luồng stream của người khác!",
            color=discord.Color.red(),
        )
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    with bot.stream_lock:
        engine = bot.active_engines.get(target_id)

    if not engine or engine.stopped_by_user:
        msg = "Bạn không có phiên Stream nào đang chạy" if target_id == caller_id else f"Người dùng <@{target_id}> không có phiên Stream nào đang chạy"
        embed = discord.Embed(
            title="ℹ️ " + msg,
            description="Dùng nút **🔴 Bắt đầu Stream** để phát sóng.",
            color=discord.Color.light_grey(),
        )
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)

    # Request graceful stop
    engine.stop()

    # Wait up to 5s for cleanup to finish
    for _ in range(10):
        if not engine.running:
            break
        await asyncio.sleep(0.5)

    with bot.stream_lock:
        bot.active_engines.pop(target_id, None)
        bot.stream_threads.pop(target_id, None)
        bot.session_started_at.pop(target_id, None)

    fresh_state = get_user_state(target_id)
    title_text = "⏹️ Đã dừng Live Stream thành công" if target_id == caller_id else f"👑 [Admin] Đã dừng Live Stream của <@{target_id}>"
    embed = discord.Embed(
        title=title_text,
        description="Phiên phát sóng đã kết thúc an toàn.",
        color=discord.Color.purple(),
    )
    embed.add_field(name="Người dùng", value=f"<@{target_id}>", inline=True)
    embed.add_field(name="Thời gian tính trong phiên", value=f"`{format_timer(engine.counted_stream_seconds)}`", inline=True)
    embed.add_field(name="Tổng số giờ đã cày", value=f"**{fresh_state.total_stream_hours_claimed:,.1f}h**", inline=True)
    embed.set_footer(text="Lưu ý: Huy hiệu Discord Streaming thường cập nhật sau 24-48 giờ.")
    await interaction.followup.send(embed=embed, ephemeral=True)


async def handle_stream_status(interaction: discord.Interaction):
    user_id = interaction.user.id
    with bot.stream_lock:
        engine = bot.active_engines.get(user_id)
        started_at = bot.session_started_at.get(user_id, 0.0)

    state = get_user_state(user_id)

    if not engine or engine.stopped_by_user:
        embed = discord.Embed(
            title="⚪ Trạng thái: Đang nghỉ (Idle)",
            description="Tài khoản của bạn hiện không phát sóng.",
            color=discord.Color.light_grey(),
        )
        embed.add_field(
            name="Tổng giờ đã cày của bạn",
            value=f"**{state.total_stream_hours_claimed:,.1f}h** ({state.total_stream_sessions} phiên)",
            inline=False
        )
        embed.set_footer(text="Dùng /stream start hoặc bấm nút Bắt đầu Stream để phát!")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    viewers = engine.get_viewer_count()
    duration_str = format_timer(time.time() - started_at) if started_at > 0 else "0s"
    counted_str = format_timer(engine.counted_stream_seconds)
    counted_hours = engine.counted_stream_seconds / 3600.0

    embed = discord.Embed(
        title="🔴 Đang phát Live Stream",
        color=discord.Color.red() if viewers > 0 else discord.Color.gold(),
    )
    embed.add_field(name="Người dùng", value=interaction.user.mention, inline=True)
    embed.add_field(name="Kênh Voice", value=f"`#{engine.channel_name}` (`{engine.channel_id}`)", inline=True)
    embed.add_field(name="Số người xem", value=f"👀 **{viewers}** viewer(s)", inline=True)
    embed.add_field(name="Thời gian chạy", value=f"`{duration_str}`", inline=True)
    embed.add_field(name="Giờ hợp lệ tích lũy phiên này", value=f"`{counted_str}` ({counted_hours:.2f}h)", inline=False)
    embed.add_field(name="Tổng số giờ trọn đời của bạn", value=f"`{state.total_stream_hours_claimed + counted_hours:,.1f}h`", inline=False)

    if viewers == 0:
        embed.set_footer(text="⚠️ Cảnh báo: Có 0 người xem. Cần ít nhất 1 người xem (hoặc Alt Viewer) để giờ stream được tính!")
    else:
        embed.set_footer(text="✅ Đang tích lũy giờ stream đều đặn!")

    await interaction.response.send_message(embed=embed, ephemeral=True)


async def handle_view_profile(interaction: discord.Interaction):
    user_id = interaction.user.id
    state = get_user_state(user_id)
    if not state.has_token:
        embed = discord.Embed(
            title="❌ Chưa có Token tài khoản",
            description="Vui lòng dùng nút Cài đặt hoặc `/setup` để điền token trước.",
            color=discord.Color.red(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    session = Session.new()
    try:
        req_me = urllib.request.Request(
            ME_URL,
            headers={
                "authorization": state.token,
                "user-agent": USER_AGENT,
                "x-super-properties": session.super_props,
            },
        )
        with urllib.request.urlopen(req_me, timeout=10) as resp:
            user_data = json.loads(resp.read().decode("utf-8"))
            uid = user_data.get("id", "")
    except Exception as exc:
        embed = discord.Embed(
            title="❌ Không thể lấy User ID",
            description=f"Lỗi: `{exc}`",
            color=discord.Color.red(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    try:
        url = f"{USERS_URL}/{uid}/profile?with_mutual_guilds=false&with_mutual_friends_count=false"
        req_profile = urllib.request.Request(
            url,
            headers={
                "authorization": state.token,
                "user-agent": USER_AGENT,
                "x-super-properties": session.super_props,
            },
        )
        with urllib.request.urlopen(req_profile, timeout=15) as resp:
            profile_data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        embed = discord.Embed(
            title="❌ Lỗi tải Profile",
            description=f"Lỗi: `{exc}`",
            color=discord.Color.red(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    user = profile_data.get("user", {})
    badges = profile_data.get("badges", [])
    username = user.get("username", "Unknown")
    global_name = user.get("global_name")
    display_name = f"{global_name} (@{username})" if global_name else f"@{username}"
    avatar_id = user.get("avatar")
    avatar_url = f"https://cdn.discordapp.com/avatars/{uid}/{avatar_id}.png" if avatar_id else None

    embed = discord.Embed(
        title=f"👤 Profile: {display_name}",
        color=discord.Color.blurple(),
    )
    if avatar_url:
        embed.set_thumbnail(url=avatar_url)

    embed.add_field(name="User ID", value=f"`{uid}`", inline=True)
    embed.add_field(name="Stream Hours đã claim", value=f"`{state.total_stream_hours_claimed:,.1f}h`", inline=True)

    if badges:
        badge_lines = []
        for b in badges:
            bid = b.get("id", "badge")
            desc = b.get("description", "")
            badge_lines.append(f"• **{bid}**: {desc}")
        embed.add_field(name=f"Huy hiệu ({len(badges)})", value="\n".join(badge_lines), inline=False)
    else:
        embed.add_field(name="Huy hiệu", value="*(Chưa trang bị huy hiệu công khai)*", inline=False)

    await interaction.followup.send(embed=embed, ephemeral=True)


# ==========================================
# 3.5. DISCORD QUESTS AUTOMATION & UI
# ==========================================

def build_user_quests_embed(user_id: int, quests: list[quest_manager.QuestItem]) -> discord.Embed:
    embed = discord.Embed(
        title="🎯 Bảng Nhiệm Vụ Discord Quests Của Bạn",
        color=discord.Color.blurple(),
    )
    if not quests:
        embed.description = (
            f"Chào <@{user_id}>! Hiện tại không tìm thấy nhiệm vụ Discord Quests nào khả dụng trên tài khoản của bạn.\n\n"
            "💡 *Mẹo: Discord thường phát hành Quests định kỳ theo từng tuần hoặc sự kiện game mới.*"
        )
        return embed

    active_quests = [q for q in quests if not q.is_expired]
    expired_quests = [q for q in quests if q.is_expired]
    enrolled_count = len([q for q in active_quests if q.is_enrolled])
    completed_count = len([q for q in active_quests if q.is_completed])
    claimed_count = len([q for q in active_quests if q.is_claimed])

    desc = (
        f"Người dùng: <@{user_id}>\n"
        f"📊 **{len(active_quests)}** nhiệm vụ khả dụng"
    )
    if expired_quests:
        desc += f" *(Đã tự động lọc bỏ **{len(expired_quests)}** quest cũ hết hạn)*"
    desc += (
        f"\n⏳ Đang làm: **{enrolled_count}** | ✅ Chờ claim quà: **{completed_count}** | 🎁 Đã nhận quà: **{claimed_count}**\n\n"
        "⚡ *Bấm các nút bên dưới để tự động nhận tất cả, xem video, chạy tiến trình game PC hoặc nhận phần thưởng.*"
    )
    embed.description = desc

    # Sort quests so active actionable quests appear first
    sorted_quests = (
        [q for q in active_quests if not q.is_completed]
        + [q for q in active_quests if q.is_completed and not q.is_claimed]
        + [q for q in active_quests if q.is_claimed]
        + expired_quests
    )

    for q in sorted_quests[:12]:
        task_lines = []
        for t in q.tasks:
            t_name_vn = {
                "WATCH_VIDEO": "🎬 Xem Video",
                "WATCH_VIDEO_ON_MOBILE": "📱 Xem Video (Mobile)",
                "PLAY_ON_DESKTOP": "🎮 Chơi Game PC (Desktop Process)",
                "STREAM_ON_DESKTOP": "🔴 Stream Màn Hình",
                "PLAY_ACTIVITY": "🎲 Chơi Activity",
            }.get(t.task_name, t.task_name)
            bar = quest_manager.make_progress_bar(t.progress, t.target, length=10)
            prog_m = t.progress / 60
            target_m = t.target / 60
            task_lines.append(f"• **{t_name_vn}**:\n  {bar} `{t.progress:.0f}s / {t.target:.0f}s` (`{prog_m:.1f}m / {target_m:.1f}m`)")

        tasks_str = "\n".join(task_lines) if task_lines else "• Không có thông tin task"
        val = (
            f"**Trạng thái:** {q.status_label}\n"
            f"**Game:** `{q.game_title}`\n"
            f"**Tiến độ:**\n{tasks_str}\n"
            f"**Hạn:** `{q.expires_at[:10] if q.expires_at else 'N/A'}`"
        )
        if q.reward_name:
            val += f" | **Quà:** `{q.reward_name}`"
        val += f"\n**Quest ID:** `{q.id}`"

        embed.add_field(
            name=f"{q.status_emoji} {q.name}",
            value=val,
            inline=False,
        )

    embed.set_footer(text="Discord Badge Maximizer · Auto Quest Completer")
    return embed


async def send_quest_notification(
    interaction: discord.Interaction,
    title: str,
    description: str,
    color: discord.Color = discord.Color.green(),
    fields: Optional[list[tuple[str, str, bool]]] = None,
) -> None:
    """
    Sends a real Discord notification (mentioning the user) to ensure the user gets
    push notifications and alerts when Auto Quest actions start and complete.
    """
    user = interaction.user
    embed = discord.Embed(title=title, description=description, color=color)
    if fields:
        for fname, fval, inline in fields:
            embed.add_field(name=fname, value=fval, inline=inline)
    embed.set_footer(text="Discord Badge Maximizer · Auto Quest Notifications")

    content = f"🔔 {user.mention}"
    sent = False

    # 1. Try sending to the channel where interaction happened
    if interaction.channel and hasattr(interaction.channel, "send"):
        try:
            await interaction.channel.send(content=content, embed=embed)
            sent = True
        except Exception:
            pass

    # 2. Try sending DM if channel failed
    if not sent:
        try:
            await user.send(content=content, embed=embed)
            sent = True
        except Exception:
            pass

    # 3. Fallback to ephemeral followup
    if not sent:
        try:
            await interaction.followup.send(content=content, embed=embed, ephemeral=True)
        except Exception:
            pass


class PublicQuestProgressTracker:
    """
    Manages live PUBLIC process tracking for Discord Auto-Quests in a text channel.
    Updates the public message every 5 seconds (5s / lần) with real-time countdown,
    progress bars, task icons, and Orbs virtual currency balance.
    """
    def __init__(self, message: discord.Message, user: discord.User, channel: Any, title: str = "DISCORD AUTO QUESTS"):
        self.message = message
        self.user = user
        self.channel = channel
        self.title = title
        self.orbs: Optional[int] = None
        self.orbs_gained: int = 0
        self.live_items: list[dict[str, Any]] = []
        self.is_done = False
        self.ticker_task: Optional[asyncio.Task] = None
        self.results: list[dict[str, Any]] = []
        self.start_time = time.time()
        self.loop = asyncio.get_running_loop()

    def start(self):
        self.ticker_task = asyncio.create_task(self._ticker_loop())

    async def _ticker_loop(self):
        while not self.is_done:
            await asyncio.sleep(5.0)  # Exactly 5-second interval
            if self.is_done:
                break
            # Decrement countdown
            for item in self.live_items:
                if item["state"] == "working" and item["countdown"] > 0:
                    item["countdown"] = max(0, item["countdown"] - 5)
                    if item["countdown"] == 0:
                        item["state"] = "done"

            await self.update_display()

    def on_progress_tick(self, data: dict[str, Any]):
        qid = data.get("quest_id")
        for item in self.live_items:
            if item["id"] == qid:
                item["current"] = data.get("current", item["current"])
                item["target"] = data.get("target", item["target"])
                item["percent"] = data.get("percent", item["percent"])
                item["bar"] = data.get("bar", item["bar"])
                item["countdown"] = data.get("countdown", item["countdown"])
                item["state"] = data.get("state", item["state"])
                if "error" in data:
                    item["error"] = data["error"]
                break

    def build_live_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title=f"🎯 {self.title} · TIẾN TRÌNH CÔNG KHAI",
            color=discord.Color.blurple(),
        )
        orbs_str = f"`{self.orbs:,}` Orbs" if self.orbs is not None else "Đang kiểm tra..."
        working_count = len([x for x in self.live_items if x["state"] == "working"])
        done_count = len([x for x in self.live_items if x["state"] in ("done", "claimed")])
        failed_count = len([x for x in self.live_items if x["state"] == "failed"])

        status_line = f"🟢 Đang thực hiện (`{working_count}` đang chạy, `{done_count}` đã xong)"
        if failed_count > 0:
            status_line += f", `{failed_count}` tạm hoãn/thất bại"

        embed.description = (
            f"👤 **Người dùng:** {self.user.mention} | 🔮 **Số dư Orbs:** {orbs_str}\n"
            f"⚡ **Trạng thái:** {status_line}\n"
            f"⏱️ **Tiến trình tự động cập nhật mỗi 5 giây...**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )

        for item in self.live_items[:8]:
            icon = item.get("task_icon", "🎯")
            name = item.get("name", "Nhiệm vụ")
            ttype_vn = quest_manager.task_label_vn(item.get("task_type"))
            cur = int(item.get("current", 0))
            tgt = int(item.get("target", 0))
            pct = item.get("percent", 0)
            bar = item.get("bar") or quest_manager.make_progress_bar(cur, tgt)
            c_sec = item.get("countdown", 0)
            cd_str = quest_manager.format_countdown(c_sec)
            reward = item.get("reward", "Phần thưởng")

            state_badge = "⏳ Đang làm (working)"
            if item["state"] == "done":
                state_badge = "✔ Hoàn thành (done)"
            elif item["state"] == "claimed":
                state_badge = "★ Đã nhận quà (claimed)"
            elif item["state"] == "failed":
                err_text = item.get("error")
                state_badge = f"❌ Thất bại ({err_text})" if err_text else "❌ Thất bại (failed)"

            field_val = (
                f"• **Loại:** {ttype_vn}\n"
                f"• **Tiến độ:** {bar} `{cur}s / {tgt}s` (`{pct}%`)\n"
                f"• **Trạng thái:** {state_badge} · ⏱️ Còn lại: `{cd_str}`\n"
                f"• **Phần thưởng:** 🎁 `{reward}`"
            )
            embed.add_field(name=f"{icon} {name}", value=field_val, inline=False)

        embed.set_footer(text="HieuTool Discord-Auto-Quests Engine · Cập nhật công khai 5s/lần")
        return embed

    async def update_display(self):
        try:
            embed = self.build_live_embed()
            await self.message.edit(embed=embed)
        except Exception as exc:
            print(f"[PublicQuestProgressTracker Update Error] {exc}")

    async def finish(self, results: list[dict[str, Any]], final_orbs: Optional[int] = None):
        self.is_done = True
        if self.ticker_task and not self.ticker_task.done():
            self.ticker_task.cancel()

        if final_orbs is not None and self.orbs is not None:
            self.orbs_gained = max(0, final_orbs - self.orbs)
            self.orbs = final_orbs
        elif final_orbs is not None:
            self.orbs = final_orbs

        embed = discord.Embed(
            title="★ BÁO CÁO TỔNG KẾT THỰC HIỆN AUTO QUESTS",
            color=discord.Color.green(),
        )
        orbs_info = f"🔮 **Số dư Orbs cuối:** `{self.orbs:,} Orbs`" if self.orbs is not None else ""
        if self.orbs_gained > 0:
            orbs_info += f" (**+{self.orbs_gained} Orbs** nhận được!)"

        embed.description = (
            f"👤 **Người dùng:** {self.user.mention}\n"
            f"{orbs_info}\n"
            "✨ **Đã hoàn tất toàn bộ nhiệm vụ (All tasks finished)!**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )

        for res in results[:12]:
            icon = res.get("icon", "🎯")
            name = res.get("name", "Quest")
            status_text = "SUCCESS" if res.get("success") else "FAILED"
            status_emoji = "✅" if res.get("success") else "❌"
            reward = res.get("reward", "Reward")
            note = res.get("note")
            res_val = f"• Kết quả: {status_emoji} **{status_text}**\n• Phần thưởng: 🎁 `{reward}`"
            if note:
                res_val += f"\n• Ghi chú: `{note}`"
            embed.add_field(
                name=f"{icon} {name[:30]}",
                value=res_val,
                inline=True,
            )

        embed.set_footer(text="HieuTool Discord-Auto-Quests Engine · Hoàn tất nhiệm vụ")
        try:
            await self.message.edit(embed=embed)
            await self.channel.send(content=f"🔔 {self.user.mention} **Tiến trình Auto Quests công khai đã hoàn tất!**")
        except Exception as exc:
            print(f"[PublicQuestProgressTracker Finish Error] {exc}")


async def run_public_auto_quests(
    interaction: discord.Interaction,
    mode: str = "all",  # "all" | "video" | "desktop"
    target_quest_id: Optional[str] = None,
) -> None:
    """
    Main runner: posts a PUBLIC message in the text channel and updates every 5 seconds.
    """
    user_id = interaction.user.id
    state = get_user_state(user_id)
    if not state.has_token:
        embed = discord.Embed(
            title="⚠️ Chưa cài đặt Token!",
            description="Bạn chưa lưu Token Discord. Vui lòng bấm **⚙️ Cài đặt Tài khoản (Popup)** hoặc gõ `/setup` trước!",
            color=discord.Color.gold(),
        )
        if not interaction.response.is_done():
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        return await interaction.followup.send(embed=embed, ephemeral=True)

    mode_title = {
        "all": "AUTO QUESTS TRỌN GÓI",
        "video": "HOÀN THÀNH VIDEO QUESTS",
        "desktop": "GIẢ LẬP GAME PROCESS PC",
    }.get(mode, "DISCORD AUTO QUESTS")

    init_embed = discord.Embed(
        title=f"🎯 {mode_title} · KHỞI CHẠY TIẾN TRÌNH",
        description=(
            f"👤 **Người dùng:** {interaction.user.mention}\n"
            "🔍 **Đang kết nối Discord API & kiểm tra danh sách nhiệm vụ...**\n"
            "⏱️ *Tiến trình sẽ được cập nhật công khai tại tin nhắn này mỗi 5 giây.*"
        ),
        color=discord.Color.blurple(),
    )
    init_embed.set_footer(text="HieuTool Discord-Auto-Quests Engine")

    public_msg: Optional[discord.Message] = None

    # Handle interaction response publicly
    if not interaction.response.is_done():
        try:
            await interaction.response.send_message(embed=init_embed, ephemeral=False)
            public_msg = await interaction.original_response()
        except Exception:
            await interaction.response.send_message("🚀 Đang khởi chạy tiến trình Auto Quests công khai...", ephemeral=True)
            if interaction.channel and hasattr(interaction.channel, "send"):
                public_msg = await interaction.channel.send(embed=init_embed)
    else:
        if interaction.channel and hasattr(interaction.channel, "send"):
            public_msg = await interaction.channel.send(embed=init_embed)

    if not public_msg:
        return

    tracker = PublicQuestProgressTracker(
        message=public_msg,
        user=interaction.user,
        channel=interaction.channel,
        title=mode_title,
    )

    # 1. Fetch balance
    tracker.orbs = await asyncio.to_thread(quest_manager.get_orbs_balance, state.token)

    # 2. Load quests
    store = quest_manager.QuestStore(state.token)
    await asyncio.to_thread(store.load_quests)
    pending = store.pending()

    # Filter according to mode
    if mode == "video":
        pending = [q for q in pending if q.has_video_task]
    elif mode == "desktop":
        pending = [q for q in pending if q.has_desktop_task]
    if target_quest_id:
        pending = [q for q in pending if q.id == target_quest_id.strip()]

    # Process already-enrolled quests first to bypass rate-limits
    pending.sort(key=lambda q: 0 if q.is_enrolled() else 1)

    # 3. If no pending quests or all quests blocked by rate-limit
    is_blocked, wait_sec, block_msg = store.is_enrollment_blocked()
    unaccepted = [q for q in pending if not q.is_enrolled()]

    if not pending:
        claimable = store.claimable()
        claimed = 0
        if claimable:
            claimed = await asyncio.to_thread(store.grab_all_rewards)

        desc = (
            f"👤 **Người dùng:** {interaction.user.mention}\n"
            f"🔮 **Số dư Orbs:** `{tracker.orbs:,} Orbs`\n\n"
            "✨ **Không có nhiệm vụ nào đang chờ xử lý!**\n"
            "• Tất cả nhiệm vụ hiện tại đều đã hoàn thành hoặc đã hết hạn."
        )
        if claimed > 0:
            desc += f"\n🎁 *Đã tự động nhận thưởng thành công cho **{claimed}** nhiệm vụ!*"

        done_embed = discord.Embed(
            title=f"🎯 {mode_title} · HOÀN TẤT",
            description=desc,
            color=discord.Color.green(),
        )
        done_embed.set_footer(text="HieuTool Discord-Auto-Quests Engine")
        await public_msg.edit(embed=done_embed)
        return

    if is_blocked and len(unaccepted) == len(pending):
        blocked_embed = discord.Embed(
            title=f"⏳ {mode_title} · TẠM DỪNG DO RATE LIMIT DISCORD",
            description=(
                f"👤 **Người dùng:** {interaction.user.mention}\n"
                f"🔮 **Số dư Orbs:** `{tracker.orbs:,} Orbs`\n\n"
                f"🚫 **Discord đang tạm giới hạn tần suất nhận Quests trên tài khoản (HTTP 429)!**\n"
                f"⏱️ **Thời gian cần chờ:** `{quest_manager.format_countdown(wait_sec)}`\n\n"
                f"💡 Hiện tại có **{len(unaccepted)}** nhiệm vụ mới chưa thể nhận do giới hạn từ Discord.\n"
                f"Vui lòng đợi `{quest_manager.format_countdown(wait_sec)}` rồi bấm thực hiện lại!"
            ),
            color=discord.Color.gold(),
        )
        blocked_embed.set_footer(text="HieuTool Discord-Auto-Quests Engine · Tự động bảo vệ tài khoản")
        await public_msg.edit(embed=blocked_embed)
        return

    # 4. Populate live items
    tracker.live_items = [
        {
            "id": q.id,
            "name": q.name,
            "task_type": q.detect_task_type(),
            "task_icon": quest_manager.task_icon(q.detect_task_type()),
            "reward": q.get_reward_label(),
            "target": q.get_target(),
            "current": q.get_progress(),
            "percent": int((q.get_progress() / q.get_target()) * 100) if q.get_target() > 0 else 0,
            "bar": quest_manager.make_progress_bar(q.get_progress(), q.get_target()),
            "countdown": q.get_remaining(),
            "state": "working",
        }
        for q in pending
    ]

    # Start 5s ticker
    tracker.start()
    await tracker.update_display()

    # 5. Execute quests
    for q in pending:
        def on_tick(data):
            tracker.on_progress_tick(data)

        success = await asyncio.to_thread(store.execute_quest, q, on_tick)
        item = next((x for x in tracker.live_items if x["id"] == q.id), None)
        note = "Thành công" if success else "Thất bại"
        if item:
            item["state"] = "claimed" if success else "failed"
            item["countdown"] = 0
            if success:
                item["current"] = item["target"]
                item["percent"] = 100
            elif "error" in item:
                note = item["error"]

        tracker.results.append({
            "icon": quest_manager.task_icon(q.detect_task_type()),
            "name": q.name,
            "success": success,
            "reward": q.get_reward_label(),
            "note": note,
        })

    # 6. Finalize with Orbs balance update
    final_orbs = await asyncio.to_thread(quest_manager.get_orbs_balance, state.token)
    await tracker.finish(tracker.results, final_orbs=final_orbs)


async def handle_quest_panel(interaction: discord.Interaction) -> None:
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        user_id = interaction.user.id
        state = get_user_state(user_id)
        if not state.has_token:
            embed = discord.Embed(
                title="⚠️ Chưa cài đặt Token!",
                description=(
                    "Bạn chưa lưu Token Discord tài khoản cá nhân.\n"
                    "Vui lòng bấm **⚙️ Cài đặt Tài khoản (Popup)** hoặc gõ `/setup` để điền Token trước khi quét Quests!"
                ),
                color=discord.Color.gold(),
            )
            return await interaction.followup.send(embed=embed, ephemeral=True)

        ok, quests, msg = await asyncio.to_thread(quest_manager.fetch_all_quests, state.token)
        if not ok:
            embed = discord.Embed(
                title="❌ Lỗi Quét Discord Quests",
                description=f"Không thể kết nối đến Discord API Quests:\n`{msg}`",
                color=discord.Color.red(),
            )
            return await interaction.followup.send(embed=embed, ephemeral=True)

        embed = build_user_quests_embed(user_id, quests)
        view = QuestControlView()
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    except Exception as exc:
        print(f"[handle_quest_panel Error] {exc}")


async def handle_quest_enroll_all(interaction: discord.Interaction) -> None:
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        user_id = interaction.user.id
        state = get_user_state(user_id)
        if not state.has_token:
            embed = discord.Embed(
                title="⚠️ Chưa cài đặt Token!",
                description="Vui lòng bấm **⚙️ Cài đặt Tài khoản (Popup)** để lưu Token trước!",
                color=discord.Color.gold(),
            )
            return await interaction.followup.send(embed=embed, ephemeral=True)

        enrolled, total, logs = await asyncio.to_thread(quest_manager.auto_enroll_all_quests, state.token)
        is_warning = any("429" in log or "giới hạn" in log or "⚠️" in log or "🚫" in log for log in logs)
        embed_color = discord.Color.green() if enrolled > 0 else (discord.Color.gold() if is_warning else discord.Color.blurple())
        embed = discord.Embed(
            title=f"⚡ Kết Quả Tự Động Nhận Quests ({enrolled}/{total})",
            color=embed_color,
        )
        embed.description = "\n".join(logs[:15]) if logs else "Không có nhiệm vụ nào cần nhận."
        embed.set_footer(text="Bấm '🎯 Quản Lý Quests' để kiểm tra lại toàn bộ danh sách.")
        view = QuestControlView()
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    except Exception as exc:
        print(f"[handle_quest_enroll_all Error] {exc}")


async def handle_quest_video(interaction: discord.Interaction) -> None:
    await run_public_auto_quests(interaction, mode="video")


async def handle_quest_desktop(interaction: discord.Interaction, quest_id: Optional[str] = None) -> None:
    await run_public_auto_quests(interaction, mode="desktop", target_quest_id=quest_id)


async def handle_quest_claim(interaction: discord.Interaction) -> None:
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        user_id = interaction.user.id
        state = get_user_state(user_id)
        if not state.has_token:
            return await interaction.followup.send("⚠️ Bạn chưa cài đặt Token!", ephemeral=True)

        claimed, logs = await asyncio.to_thread(quest_manager.claim_all_completed_quests, state.token)
        embed = discord.Embed(
            title=f"🎁 Nhận Quà Nhiệm Vụ ({claimed} đã nhận)",
            description="\n".join(logs[:15]) if logs else "Không có nhiệm vụ nào đang chờ nhận quà.",
            color=discord.Color.green() if claimed > 0 else discord.Color.blue(),
        )
        view = QuestControlView()
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    except Exception as exc:
        print(f"[handle_quest_claim Error] {exc}")


async def handle_quest_full_auto(interaction: discord.Interaction) -> None:
    await run_public_auto_quests(interaction, mode="all")


# ==========================================
# 4. BOT CLIENT & SLASH COMMANDS REGISTRATION
# ==========================================

class StreamBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)
        self.stream_lock = threading.Lock()
        # Multi-user isolation dictionaries: user_id (int) -> object
        self.active_engines: dict[int, GatewayStreamEngine] = {}
        self.stream_threads: dict[int, threading.Thread] = {}
        self.session_started_at: dict[int, float] = {}
        self.active_farm_threads: dict[int, threading.Thread] = {}

    async def setup_hook(self) -> None:
        self.add_view(ControlPanelView(self))
        self.add_view(AdminPanelView())
        self.add_view(QuestControlView())
        print("[Bot] Đang đồng bộ Slash Commands với Discord...")
        synced = await self.tree.sync()
        print(f"[Bot] Đã đồng bộ thành công {len(synced)} lệnh Slash.")


bot = StreamBot()
tree = bot.tree


# ==========================================
# ADMIN SYSTEM OPERATIONS & EMBEDS
# ==========================================

def build_admin_stream_list_embed() -> discord.Embed:
    with bot.stream_lock:
        running_users = list(bot.active_engines.keys())

    if not running_users:
        return discord.Embed(
            title="⚪ Không có luồng Stream nào đang chạy",
            description="Hiện không có thành viên nào đang phát sóng.",
            color=discord.Color.light_grey(),
        )

    lines = []
    for uid in running_users:
        eng = bot.active_engines.get(uid)
        if eng and eng.running:
            v_cnt = eng.get_viewer_count()
            secs = eng.counted_stream_seconds
            media_info = "YouTube URL" if getattr(eng.state, "stream_url", None) else "Video Loop"
            if eng.stream_connected:
                status_badge = "🟢 **LIVE**"
            elif eng.voice_connected:
                status_badge = "⏳ **Bắt đầu stream**"
            else:
                status_badge = "⚠️ **Mất kết nối (Reconnecting)**"
            lines.append(
                f"• <@{uid}> (`{uid}`):\n"
                f"  └ Kênh: `#{eng.channel_name}` (`{eng.channel_id}`) | {status_badge} | 👀 {v_cnt} viewers | ⏱️ {format_timer(secs)} | 📺 {media_info}"
            )

    embed = discord.Embed(
        title=f"👑 [Admin] Danh sách Luồng Stream Đang Chạy ({len(lines)})",
        description="\n\n".join(lines) if lines else "Không có luồng nào đang chạy.",
        color=discord.Color.gold(),
    )
    embed.set_footer(text=f"Admin ID: {PRIMARY_ADMIN_ID} · Dùng /admin stop <user> hoặc /admin prune.")
    return embed


def build_admin_stats_embed() -> discord.Embed:
    with bot.stream_lock:
        active_count = len([e for e in bot.active_engines.values() if e.running])
        farming_count = len([t for t in bot.active_farm_threads.values() if t.is_alive()])

    user_files = list(USER_DATA_DIR.glob("*.json")) if USER_DATA_DIR.exists() else []
    total_users = len(user_files)

    total_stream_hours = 0.0
    total_stream_sessions = 0
    total_game_hours = 0.0
    total_games_count = 0

    for f in user_files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            total_stream_hours += float(d.get("total_stream_hours_claimed", 0.0))
            total_stream_sessions += int(d.get("total_stream_sessions", 0))
            total_game_hours += float(d.get("total_hours_claimed", 0.0))
            total_games_count += int(d.get("total_games_claimed", 0))
        except Exception:
            pass

    embed = discord.Embed(
        title="👑 [Admin] Báo Cáo & Thống Kê Toàn Bộ Hệ Thống",
        color=discord.Color.gold(),
    )
    embed.add_field(name="👥 Tổng người dùng đã lưu", value=f"`{total_users}` users", inline=True)
    embed.add_field(name="🔴 Stream đang chạy", value=f"`{active_count}` luồng", inline=True)
    embed.add_field(name="🎮 Cày Game đang chạy", value=f"`{farming_count}` luồng", inline=True)
    embed.add_field(
        name="⏱️ Tổng giờ Stream đã cày (Toàn bot)",
        value=f"**{total_stream_hours:,.1f}h** ({total_stream_sessions:,} phiên)",
        inline=False
    )
    embed.add_field(
        name="🕹️ Tổng giờ Game đã cày (Toàn bot)",
        value=f"**{total_game_hours:,.0f}h** trên **{total_games_count:,}** games",
        inline=False
    )
    embed.set_footer(text=f"Admin: {PRIMARY_ADMIN_ID} · Discord Badge Maximizer")
    return embed


def stop_all_streams() -> int:
    with bot.stream_lock:
        uids = list(bot.active_engines.keys())

    count = 0
    for uid in uids:
        with bot.stream_lock:
            engine = bot.active_engines.get(uid)
        if engine and engine.running:
            engine.stop()
            count += 1
            with bot.stream_lock:
                bot.active_engines.pop(uid, None)
                bot.stream_threads.pop(uid, None)
                bot.session_started_at.pop(uid, None)
    return count


def prune_disconnected_streams() -> int:
    """Stops and clears any streams that have lost connection or are stuck reconnecting.
    Active LIVE streams (stream_connected=True) are strictly protected and untouched.
    """
    with bot.stream_lock:
        uids = list(bot.active_engines.keys())

    pruned = 0
    for uid in uids:
        with bot.stream_lock:
            engine = bot.active_engines.get(uid)
        if engine:
            is_dead = (not engine.running) or (not engine.stream_connected)
            if is_dead:
                engine.stop()
                pruned += 1
                with bot.stream_lock:
                    bot.active_engines.pop(uid, None)
                    bot.stream_threads.pop(uid, None)
                    bot.session_started_at.pop(uid, None)
    return pruned


def restart_all_users_streams() -> tuple[list[str], list[str]]:
    """
    Restarts/starts streaming for all configured users in USER_DATA_DIR.
    Returns: (success_list, failed_list)
    """
    if not USER_DATA_DIR.exists():
        return [], ["Thư mục data/users chưa tồn tại."]

    user_files = list(USER_DATA_DIR.glob("*.json"))
    if not user_files:
        return [], ["Không tìm thấy người dùng nào đã lưu cấu hình."]

    success = []
    failed = []

    for f in user_files:
        try:
            uid_str = f.stem
            if not uid_str.isdigit():
                continue
            uid = int(uid_str)
            state = get_user_state(uid)
            if not state.has_token:
                continue

            # 1. Cleanly stop old stream session if running
            with bot.stream_lock:
                old_eng = bot.active_engines.get(uid)
            if old_eng and old_eng.running:
                old_eng.stop()
                with bot.stream_lock:
                    bot.active_engines.pop(uid, None)
                    bot.stream_threads.pop(uid, None)
                    bot.session_started_at.pop(uid, None)

            # 2. Check token validity
            session = Session.new()
            try:
                ensure_token(state, session.super_props)
            except Exception as exc:
                failed.append(f"<@{uid}> (`{uid}`): Token không hợp lệ ({exc})")
                continue

            # 3. Resolve target voice channel
            target_cid = ""
            target_gid = state.last_guild_id or ""
            ch_name = "Voice Channel"

            if state.last_channel_id and state.last_channel_id.strip():
                cid = extract_channel_id(state.last_channel_id.strip())
                valid, gid, name = validate_stream_target(
                    token=state.token,
                    super_props=session.super_props,
                    channel_id=cid,
                    console=Console(),
                    guild_id=target_gid,
                )
                if valid:
                    target_cid = cid
                    target_gid = gid
                    ch_name = f"#{name}"

            if not target_cid:
                # Auto-discover random channel
                rcid, rgid, rname = find_random_voice_channel_from_token(state.token, session.super_props)
                if rcid:
                    target_cid = rcid
                    target_gid = rgid
                    ch_name = f"#{rname}"
                    state.last_channel_id = rcid
                    state.last_guild_id = rgid
                    save_user_state(uid, state)

            if not target_cid:
                failed.append(f"<@{uid}> (`{uid}`): Không tìm thấy phòng voice khả dụng")
                continue

            # 4. Spawn new GatewayStreamEngine
            engine = GatewayStreamEngine(state, session, Console())

            def _run(u_id: int, eng: GatewayStreamEngine, g_id: str, c_id: str):
                C = eng.console
                eng.stopped_by_user = False
                restarts = 0
                while not eng.stopped_by_user:
                    try:
                        eng.start_stream(guild_id=g_id, channel_id=c_id, wait_for_stdin=False)
                    except Exception as exc:
                        C.warn("stream", f"stream cycle error for user {u_id}: {exc}")

                    if eng.stopped_by_user:
                        break

                    restarts += 1
                    delay = min(8.0, 2.0 + (restarts % 4) * 1.5)
                    C.info("stream", f"🔄 [Đa luồng Auto-Res] Luồng của user {u_id} tự động khôi phục phát lại sau {delay:.1f}s (Phát liên tục không tắt)...")
                    time.sleep(delay)

                with bot.stream_lock:
                    if bot.active_engines.get(u_id) == eng:
                        bot.active_engines.pop(u_id, None)
                        bot.session_started_at.pop(u_id, None)
                        bot.stream_threads.pop(u_id, None)

            with bot.stream_lock:
                bot.active_engines[uid] = engine
                bot.session_started_at[uid] = time.time()
                t = threading.Thread(target=_run, args=(uid, engine, target_gid, target_cid), daemon=True)
                bot.stream_threads[uid] = t
                t.start()

            success.append(f"<@{uid}> (`{uid}`) ➔ `{ch_name}`")
        except Exception as exc:
            failed.append(f"<@{uid}>: Lỗi ({exc})")

    return success, failed


async def autonomous_stream_supervisor():
    """Background supervisor that monitors multi-user streaming threads every 15 seconds.
    - NEVER ends any user's stream unless the user explicitly stops it!
    - If a worker thread ever dies unexpectedly, automatically revives/resumes it in its dedicated thread!
    - Protects 24/7 continuous stream accumulation across all users.
    """
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            await asyncio.sleep(15)
            with bot.stream_lock:
                uids = list(bot.active_engines.keys())

            for uid in uids:
                with bot.stream_lock:
                    eng = bot.active_engines.get(uid)
                    t = bot.stream_threads.get(uid)

                if not eng:
                    continue

                if eng.stopped_by_user:
                    with bot.stream_lock:
                        bot.active_engines.pop(uid, None)
                        bot.stream_threads.pop(uid, None)
                        bot.session_started_at.pop(uid, None)
                    continue

                # Thread died unexpectedly while user still wants it running -> Auto-Revive
                if t is not None and not t.is_alive() and not eng.stopped_by_user:
                    print(f"[AutoSupervisor] Luồng của user {uid} bị gián đoạn, đang tự động hồi sinh (auto-res) lại ngay...")
                    state = get_user_state(uid)
                    target_gid = eng.guild_id or state.last_guild_id
                    target_cid = eng.channel_id or state.last_channel_id

                    def _revive_run(u_id: int, e: GatewayStreamEngine, g_id: str, c_id: str):
                        C = e.console
                        restarts = 0
                        while not e.stopped_by_user:
                            try:
                                e.start_stream(guild_id=g_id, channel_id=c_id, wait_for_stdin=False)
                            except Exception as exc:
                                C.warn("stream", f"stream cycle error for user {u_id}: {exc}")

                            if e.stopped_by_user:
                                break

                            restarts += 1
                            delay = min(8.0, 2.0 + (restarts % 4) * 1.5)
                            C.info("stream", f"🔄 [Đa luồng Auto-Res] Luồng của user {u_id} tự động khôi phục phát lại sau {delay:.1f}s...")
                            time.sleep(delay)

                        with bot.stream_lock:
                            if bot.active_engines.get(u_id) == e:
                                bot.active_engines.pop(u_id, None)
                                bot.session_started_at.pop(u_id, None)
                                bot.stream_threads.pop(u_id, None)

                    new_t = threading.Thread(target=_revive_run, args=(uid, eng, target_gid, target_cid), daemon=True)
                    with bot.stream_lock:
                        bot.stream_threads[uid] = new_t
                    new_t.start()
        except Exception:
            pass


def build_admin_quests_summary_embed() -> discord.Embed:
    if not USER_DATA_DIR.exists():
        return discord.Embed(title="📊 [Admin] Quests Thống Kê", description="Chưa có dữ liệu users.", color=discord.Color.red())

    user_files = list(USER_DATA_DIR.glob("*.json"))
    embed = discord.Embed(
        title="🎯 [Admin] Báo Cáo Quests Toàn Bộ Hệ Thống",
        color=discord.Color.gold(),
    )
    user_rows = []
    total_quests_found = 0
    total_enrolled_found = 0
    total_completed_found = 0

    for f in user_files:
        uid_str = f.stem
        if not uid_str.isdigit():
            continue
        uid = int(uid_str)
        state = get_user_state(uid)
        if not state.has_token:
            continue
        ok, quests, _ = quest_manager.fetch_all_quests(state.token)
        if ok and quests:
            active_q = [q for q in quests if not q.is_expired]
            total_quests_found += len(active_q)
            enrolled = len([q for q in active_q if q.is_enrolled])
            completed = len([q for q in active_q if q.is_completed])
            total_enrolled_found += enrolled
            total_completed_found += completed
            user_rows.append(f"• <@{uid}>: `{len(active_q)}` quests khả dụng (`{enrolled}` đã nhận, `{completed}` đã xong)")
        elif ok and not quests:
            user_rows.append(f"• <@{uid}>: 0 quests khả dụng")
        else:
            user_rows.append(f"• <@{uid}>: ⚠️ Lỗi đọc quest")

    embed.add_field(name="👥 Tổng Users có Token", value=f"**{len(user_rows)}** users", inline=True)
    embed.add_field(name="🎯 Quests Khả Dụng (Còn hạn)", value=f"**{total_quests_found}** quests", inline=True)
    embed.add_field(name="✅ Đã nhận / Đã xong", value=f"**{total_enrolled_found}** đã nhận / **{total_completed_found}** xong", inline=True)

    if user_rows:
        embed.add_field(name="Chi tiết từng User", value="\n".join(user_rows[:20]), inline=False)
    else:
        embed.description = "Chưa có user nào có token hợp lệ để quét."

    embed.set_footer(text=f"Admin: {PRIMARY_ADMIN_ID} · Dùng các nút bấm để Auto Nhận cho tất cả.")
    return embed


def admin_enroll_all_users_quests() -> discord.Embed:
    if not USER_DATA_DIR.exists():
        return discord.Embed(title="⚡ [Admin] Auto Nhận Quests", description="Chưa có user nào.", color=discord.Color.red())

    user_files = list(USER_DATA_DIR.glob("*.json"))
    logs = []
    total_users_enrolled = 0
    total_new_enrolled = 0

    for f in user_files:
        uid_str = f.stem
        if not uid_str.isdigit():
            continue
        uid = int(uid_str)
        state = get_user_state(uid)
        if not state.has_token:
            continue
        enrolled, total, _ = quest_manager.auto_enroll_all_quests(state.token)
        if enrolled > 0:
            total_users_enrolled += 1
            total_new_enrolled += enrolled
            logs.append(f"• <@{uid}>: Đã nhận thành công **+{enrolled}** quests mới")
        else:
            logs.append(f"• <@{uid}>: Đã nhận từ trước hoặc không có quest mới")

    embed = discord.Embed(
        title="⚡ [Admin] Kết Quả Auto Nhận Quests Cho Toàn Bộ Users",
        description=(
            f"Đã duyệt qua **{len(user_files)}** users trong hệ thống.\n"
            f"Tổng số quest vừa nhận mới: **+{total_new_enrolled}** nhiệm vụ cho **{total_users_enrolled}** users.\n\n"
            + "\n".join(logs[:20])
        ),
        color=discord.Color.green() if total_new_enrolled > 0 else discord.Color.blurple(),
    )
    return embed


def admin_auto_run_all_users_quests() -> discord.Embed:
    if not USER_DATA_DIR.exists():
        return discord.Embed(title="🚀 [Admin] Auto Quests Trọn Gói", description="Chưa có user nào.", color=discord.Color.red())

    user_files = list(USER_DATA_DIR.glob("*.json"))
    logs = []
    total_enrolled = 0
    total_video = 0
    total_claimed = 0

    for f in user_files:
        uid_str = f.stem
        if not uid_str.isdigit():
            continue
        uid = int(uid_str)
        state = get_user_state(uid)
        if not state.has_token:
            continue
        summary = quest_manager.auto_run_quest_flow(state.token)
        total_enrolled += summary["enrolled_new"]
        total_video += summary["video_completed"]
        total_claimed += summary["claimed"]
        logs.append(
            f"• <@{uid}>: Nhận **+{summary['enrolled_new']}**, Video **+{summary['video_completed']}**, Claim **+{summary['claimed']}**"
        )

    embed = discord.Embed(
        title="🚀 [Admin] Báo Cáo Chạy Auto Quests Trọn Gói Cho Toàn Bộ Users",
        description=(
            f"Tổng kết hệ thống:\n"
            f"• 🎯 Quests mới đã nhận: **+{total_enrolled}**\n"
            f"• 🎬 Video quests đã xong: **+{total_video}**\n"
            f"• 🎁 Quà đã tự động claim: **+{total_claimed}**\n\n"
            + "\n".join(logs[:20])
        ),
        color=discord.Color.green(),
    )
    return embed


@bot.event
async def on_ready():
    migrated = crypto_vault.migrate_all_user_states(USER_DATA_DIR)
    print("=" * 60)
    print(f"  Bot Multi-User đã online: {bot.user} (ID: {bot.user.id})")
    print(f"  Admin ID: {PRIMARY_ADMIN_ID} (Quyền Quản Trị Tối Cao)")
    print(f"  Bảo mật Token: Lồng nhau TripleDES + AES-256 ({migrated} files đã mã hóa)")
    print(f"  Gõ /panel để mở Bảng điều khiển tương tác!")
    print(f"  Chế độ Tự Động Giám Sát & Dọn Luồng (Auto-Supervisor): BẬT (100% Tự Động)")
    print("=" * 60)
    bot.loop.create_task(autonomous_stream_supervisor())


# --- POPUP MODAL SETUP COMMANDS ---

@tree.command(name="setup", description="Mở Popup cài đặt Tài khoản (Chính & Phụ) trực tiếp trên Discord")
async def setup_cmd(interaction: discord.Interaction):
    state = get_user_state(interaction.user.id)
    await interaction.response.send_modal(AllInOneSetupModal(current_state=state))


@tree.command(name="set_token", description="Mở Popup cấu hình Token tài khoản chính & phụ")
async def set_token_cmd(interaction: discord.Interaction):
    state = get_user_state(interaction.user.id)
    await interaction.response.send_modal(AllInOneSetupModal(current_state=state))


@tree.command(name="setup_main", description="Mở Popup cài đặt riêng cho Tài khoản Chính (Acc cày)")
async def setup_main_cmd(interaction: discord.Interaction):
    state = get_user_state(interaction.user.id)
    await interaction.response.send_modal(MainAccountModal(current_state=state))


@tree.command(name="setup_alt", description="Mở Popup cài đặt riêng cho Tài khoản Phụ (Alt Viewer)")
async def setup_alt_cmd(interaction: discord.Interaction):
    state = get_user_state(interaction.user.id)
    await interaction.response.send_modal(AltAccountModal(current_state=state))


# --- INTERACTIVE DASHBOARD / PANEL ---

@tree.command(name="panel", description="Mở Bảng Điều Khiển tương tác với các nút bấm trực tiếp")
async def panel_cmd(interaction: discord.Interaction):
    with bot.stream_lock:
        active_count = len([e for e in bot.active_engines.values() if e.running])

    embed = discord.Embed(
        title="🎛️ Bảng Điều Khiển Discord Badge Maximizer (Multi-User)",
        description=(
            "Hệ thống hỗ trợ **nhiều người dùng cùng lúc** (Multi-User Isolation).\n"
            "Mỗi người bấm nút bên dưới sẽ tự quản lý tài khoản riêng biệt của mình qua Popup Form ẩn danh:\n\n"
            "• **⚙️ Cài đặt Tài khoản**: Mở popup điền token chính, cookie và alt token.\n"
            "• **🔴 Bắt đầu Stream**: Tự động tìm phòng voice khả dụng và phát sóng.\n"
            "• **⏹️ Dừng Stream**: Dừng luồng phát sóng của bạn và lưu lại số giờ.\n"
            "• **📊 Xem Trạng thái**: Kiểm tra thời gian stream tích lũy của bạn.\n"
            "• **🎮 Cày Giờ Game**: Mở popup chọn số game và số giờ để cày tự động."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="⚡ Máy chủ Bot",
        value=f"Đang phát sóng: **{active_count}** luồng đồng thời",
        inline=False
    )
    embed.set_footer(text="Mọi thông tin Token bạn điền trong Popup đều được bảo mật và chỉ bạn nhìn thấy!")

    view = ControlPanelView(bot_instance=bot)
    await interaction.response.send_message(embed=embed, view=view)


# --- QUEST COMMANDS GROUP ---

quest_group = app_commands.Group(name="quest", description="Quản lý và tự động hoàn thành Discord Quests")


@quest_group.command(name="panel", description="Mở Bảng Điều Khiển Discord Quests với đầy đủ nút bấm")
async def quest_panel_cmd(interaction: discord.Interaction):
    await handle_quest_panel(interaction)


@quest_group.command(name="list", description="Xem danh sách tất cả các nhiệm vụ Discord Quests hiện có của bạn")
async def quest_list_cmd(interaction: discord.Interaction):
    await handle_quest_panel(interaction)


@quest_group.command(name="auto", description="Tự động quét, nhận và làm nhiệm vụ Quests (Hiển thị công khai, cập nhật 5s/lần)")
async def quest_auto_cmd(interaction: discord.Interaction):
    await handle_quest_full_auto(interaction)


@quest_group.command(name="enroll", description="Tự động nhận một hoặc toàn bộ nhiệm vụ Discord Quests")
@app_commands.describe(quest_id="(Tùy chọn) ID của Quest muốn nhận (Bỏ trống để tự động nhận tất cả)")
async def quest_enroll_cmd(interaction: discord.Interaction, quest_id: Optional[str] = None):
    state = get_user_state(interaction.user.id)
    if not state.has_token:
        return await interaction.response.send_message("⚠️ Bạn chưa cài đặt Token! Vui lòng dùng `/setup` trước.", ephemeral=True)
    if not quest_id:
        return await handle_quest_enroll_all(interaction)
    await interaction.response.defer(ephemeral=True)
    ok, msg = await asyncio.to_thread(quest_manager.enroll_quest, state.token, quest_id.strip())
    color = discord.Color.green() if ok else discord.Color.red()
    embed = discord.Embed(title=f"Kết quả nhận Quest `{quest_id}`", description=msg, color=color)
    await interaction.followup.send(embed=embed, ephemeral=True)


@quest_group.command(name="video", description="Tự động xem video hoàn thành Video Quests (Hiển thị công khai, cập nhật 5s/lần)")
async def quest_video_cmd(interaction: discord.Interaction):
    await handle_quest_video(interaction)


@quest_group.command(name="play", description="Giả lập chơi Game PC hoàn thành Quest (Hiển thị công khai, cập nhật 5s/lần)")
@app_commands.describe(quest_id="(Tùy chọn) ID của Quest muốn chơi (Bỏ trống để tự động làm quest đầu tiên)")
async def quest_play_cmd(interaction: discord.Interaction, quest_id: Optional[str] = None):
    await handle_quest_desktop(interaction, quest_id=quest_id)


@quest_group.command(name="claim", description="Nhận phần thưởng cho các nhiệm vụ đã hoàn thành")
@app_commands.describe(quest_id="(Tùy chọn) ID Quest cần claim (Bỏ trống để claim tất cả)")
async def quest_claim_cmd(interaction: discord.Interaction, quest_id: Optional[str] = None):
    state = get_user_state(interaction.user.id)
    if not state.has_token:
        return await interaction.response.send_message("⚠️ Bạn chưa cài đặt Token!", ephemeral=True)
    if not quest_id:
        return await handle_quest_claim(interaction)
    await interaction.response.defer(ephemeral=True)
    ok, msg = await asyncio.to_thread(quest_manager.claim_quest_reward, state.token, quest_id.strip())
    color = discord.Color.green() if ok else discord.Color.red()
    embed = discord.Embed(title=f"Kết quả Claim Quest `{quest_id}`", description=msg, color=color)
    await interaction.followup.send(embed=embed, ephemeral=True)


# --- STREAM COMMANDS GROUP ---

stream_group = app_commands.Group(name="stream", description="Quản lý tính năng Live Stream cày Streaming Badge")


@stream_group.command(name="start", description="Bắt đầu phát Live Stream (Lọc theo ID phòng đã lưu hoặc bốc random)")
@app_commands.describe(
    channel_id="ID kênh voice cụ thể (Bỏ trống = Dùng ID đã lưu trong cấu hình hoặc Auto)",
    stream_url="(Tùy chọn) Link YouTube hoặc URL video để phát trực tiếp",
    guild_id="(Tùy chọn) ID Server chứa phòng voice"
)
async def stream_start_cmd(
    interaction: discord.Interaction,
    channel_id: Optional[str] = None,
    stream_url: Optional[str] = None,
    guild_id: Optional[str] = None,
):
    await handle_start_stream(interaction, channel_id=channel_id, guild_id=guild_id, stream_url=stream_url)


@stream_group.command(name="play", description="Phát trực tiếp link YouTube hoặc video URL vào phòng Voice")
@app_commands.describe(
    url="Link YouTube (https://www.youtube.com/watch?v=...) hoặc video URL",
    channel_id="(Tùy chọn) ID kênh voice cụ thể muốn phát vào"
)
async def stream_play_cmd(
    interaction: discord.Interaction,
    url: str,
    channel_id: Optional[str] = None,
):
    await handle_start_stream(interaction, channel_id=channel_id, stream_url=url)


@stream_group.command(name="set_channel", description="Khóa phòng Voice mặc định theo ID (hoặc đặt lại random)")
@app_commands.describe(
    channel_id="ID phòng voice (để trống hoặc gõ 'random' để tự động chọn ngẫu nhiên)"
)
async def stream_set_channel_cmd(interaction: discord.Interaction, channel_id: Optional[str] = None):
    user_id = interaction.user.id
    state = get_user_state(user_id)
    if not channel_id or channel_id.strip().lower() in ("random", "none", "clear", ""):
        state.last_channel_id = ""
        save_user_state(user_id, state)
        await interaction.response.send_message("🎲 Đã chuyển sang chế độ **Tự động chọn phòng Voice ngẫu nhiên**!", ephemeral=True)
    else:
        cid = extract_channel_id(channel_id.strip())
        state.last_channel_id = cid
        save_user_state(user_id, state)
        await interaction.response.send_message(f"🎯 Đã khóa phòng Voice mặc định theo ID: `{cid}`!", ephemeral=True)


@stream_group.command(name="stop", description="Dừng phiên phát Live Stream (Admin có thể chọn dừng người khác)")
@app_commands.describe(user="(Tùy chọn - Dành cho Admin) Chọn người dùng cần dừng stream")
async def stream_stop_cmd(interaction: discord.Interaction, user: Optional[discord.User] = None):
    target_id = user.id if user else None
    await handle_stop_stream(interaction, target_user_id=target_id)


@stream_group.command(name="status", description="Xem trạng thái phiên phát Stream của bạn")
async def stream_status_cmd(interaction: discord.Interaction):
    await handle_stream_status(interaction)


@stream_group.command(name="list", description="Xem danh sách tất cả các luồng Stream đang chạy trên Bot (Admin)")
async def stream_list_cmd(interaction: discord.Interaction):
    if not is_admin(interaction.user.id):
        embed = discord.Embed(
            title="⛔ Truy cập bị từ chối",
            description=f"Lệnh này chỉ dành riêng cho Admin của Bot (`{PRIMARY_ADMIN_ID}`)!",
            color=discord.Color.red(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    embed = build_admin_stream_list_embed()
    await interaction.response.send_message(embed=embed, ephemeral=True)


# --- ADMIN COMMANDS GROUP ---

admin_group = app_commands.Group(name="admin", description="Các lệnh quản trị Bot dành riêng cho Admin (707934327467671643)")


@admin_group.command(name="list", description="Xem danh sách tất cả các luồng Stream đang chạy của các user")
async def admin_list_cmd(interaction: discord.Interaction):
    if not is_admin(interaction.user.id):
        return await interaction.response.send_message(f"⛔ Lệnh này chỉ dành cho Admin (`{PRIMARY_ADMIN_ID}`)!", ephemeral=True)
    embed = build_admin_stream_list_embed()
    await interaction.response.send_message(embed=embed, ephemeral=True)


@admin_group.command(name="stop", description="Dừng luồng Stream của một người dùng cụ thể")
@app_commands.describe(user="Người dùng cần dừng stream")
async def admin_stop_cmd(interaction: discord.Interaction, user: discord.User):
    if not is_admin(interaction.user.id):
        return await interaction.response.send_message(f"⛔ Lệnh này chỉ dành cho Admin (`{PRIMARY_ADMIN_ID}`)!", ephemeral=True)
    await handle_stop_stream(interaction, target_user_id=user.id)


@admin_group.command(name="stop_all", description="Dừng khẩn cấp toàn bộ các luồng Stream đang chạy trên Bot")
async def admin_stop_all_cmd(interaction: discord.Interaction):
    if not is_admin(interaction.user.id):
        return await interaction.response.send_message(f"⛔ Lệnh này chỉ dành cho Admin (`{PRIMARY_ADMIN_ID}`)!", ephemeral=True)
    stopped = stop_all_streams()
    embed = discord.Embed(
        title="🛑 [Admin] Đã dừng toàn bộ luồng Stream!",
        description=f"Đã dừng an toàn **{stopped}** luồng phát sóng trên bot.",
        color=discord.Color.red(),
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@admin_group.command(name="restart_all", description="Phát sóng lại / Khởi động lại luồng Stream cho tất cả người dùng")
async def admin_restart_all_cmd(interaction: discord.Interaction):
    if not is_admin(interaction.user.id):
        return await interaction.response.send_message(f"⛔ Lệnh này chỉ dành cho Admin (`{PRIMARY_ADMIN_ID}`)!", ephemeral=True)
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
    success, failed = await asyncio.to_thread(restart_all_users_streams)
    embed = discord.Embed(
        title=f"🔄 [Admin] Kết quả Phát Lại Stream Toàn Bộ ({len(success)} thành công)",
        color=discord.Color.green() if success else discord.Color.red(),
    )
    if success:
        embed.add_field(name=f"✅ Đã kết nối & phát sóng ({len(success)} users)", value="\n".join(success[:20]), inline=False)
    if failed:
        embed.add_field(name=f"⚠️ Không thể phát ({len(failed)} users)", value="\n".join(failed[:15]), inline=False)
    embed.set_footer(text=f"Admin: {PRIMARY_ADMIN_ID} · Dùng /admin list để kiểm tra chi tiết.")
    await interaction.followup.send(embed=embed, ephemeral=True)


@admin_group.command(name="prune", description="Dọn dẹp các luồng stream bị rớt mạng/treo reconnecting mà giữ nguyên các luồng LIVE")
async def admin_prune_cmd(interaction: discord.Interaction):
    if not is_admin(interaction.user.id):
        return await interaction.response.send_message(f"⛔ Lệnh này chỉ dành cho Admin (`{PRIMARY_ADMIN_ID}`)!", ephemeral=True)
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
    pruned = prune_disconnected_streams()
    embed = discord.Embed(
        title="🧹 [Admin] Đã dọn dẹp các luồng Stream bị treo / Reconnecting!",
        description=(
            f"Đã dừng và dọn sạch **{pruned}** luồng bị mất kết nối.\n\n"
            f"🟢 **Tất cả các luồng đang phát (LIVE) của các user khác vẫn tiếp tục chạy 100% bình thường, không bị gián đoạn!**"
        ),
        color=discord.Color.green(),
    )
    await interaction.followup.send(embed=embed, ephemeral=True)


@admin_group.command(name="stats", description="Xem thống kê toàn bộ hệ thống (Tổng user, tổng giờ, luồng chạy)")
async def admin_stats_cmd(interaction: discord.Interaction):
    if not is_admin(interaction.user.id):
        return await interaction.response.send_message(f"⛔ Lệnh này chỉ dành cho Admin (`{PRIMARY_ADMIN_ID}`)!", ephemeral=True)
    embed = build_admin_stats_embed()
    await interaction.response.send_message(embed=embed, ephemeral=True)


@admin_group.command(name="reset_user", description="Xóa sạch dữ liệu cấu hình của một người dùng (Admin Reset)")
@app_commands.describe(user="Người dùng cần reset cấu hình")
async def admin_reset_user_cmd(interaction: discord.Interaction, user: discord.User):
    if not is_admin(interaction.user.id):
        return await interaction.response.send_message(f"⛔ Lệnh này chỉ dành cho Admin (`{PRIMARY_ADMIN_ID}`)!", ephemeral=True)

    with bot.stream_lock:
        eng = bot.active_engines.get(user.id)
    if eng and eng.running:
        eng.stop()
        with bot.stream_lock:
            bot.active_engines.pop(user.id, None)
            bot.stream_threads.pop(user.id, None)
            bot.session_started_at.pop(user.id, None)

    p = get_user_state_file(user.id)
    existed = p.exists()
    p.unlink(missing_ok=True)

    embed = discord.Embed(
        title="🗑️ [Admin] Đã reset cấu hình người dùng",
        description=f"Đã xóa file cấu hình và dừng mọi tiến trình của <@{user.id}> ({'Đã xóa file' if existed else 'Chưa có file'}).",
        color=discord.Color.orange(),
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@admin_group.command(name="panel", description="Mở Bảng Điều Khiển Admin với các nút bấm")
async def admin_panel_cmd(interaction: discord.Interaction):
    if not is_admin(interaction.user.id):
        return await interaction.response.send_message(f"⛔ Lệnh này chỉ dành cho Admin (`{PRIMARY_ADMIN_ID}`)!", ephemeral=True)
    embed = discord.Embed(
        title="👑 Bảng Quản Trị Hệ Thống (Dành Cho Admin)",
        description=(
            f"Xin chào <@{interaction.user.id}>! Bạn đang đăng nhập với quyền **Admin Tối Cao**.\n"
            "Bạn có thể giám sát tất cả các luồng đang phát hoặc dừng khẩn cấp bất kỳ luồng nào."
        ),
        color=discord.Color.gold(),
    )
    view = AdminPanelView()
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


@admin_group.command(name="quests_summary", description="[Admin] Xem báo cáo thống kê Quests của toàn bộ user trong hệ thống")
async def admin_quests_summary_cmd(interaction: discord.Interaction):
    if not is_admin(interaction.user.id):
        return await interaction.response.send_message(f"⛔ Lệnh này chỉ dành cho Admin (`{PRIMARY_ADMIN_ID}`)!", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    embed = await asyncio.to_thread(build_admin_quests_summary_embed)
    await interaction.followup.send(embed=embed, ephemeral=True)


@admin_group.command(name="quests_enroll_all", description="[Admin] Tự động quét và nhận toàn bộ Quests cho TẤT CẢ users")
async def admin_quests_enroll_all_cmd(interaction: discord.Interaction):
    if not is_admin(interaction.user.id):
        return await interaction.response.send_message(f"⛔ Lệnh này chỉ dành cho Admin (`{PRIMARY_ADMIN_ID}`)!", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    embed = await asyncio.to_thread(admin_enroll_all_users_quests)
    await interaction.followup.send(embed=embed, ephemeral=True)


@admin_group.command(name="quests_auto_all", description="[Admin] Chạy auto flow Quests trọn gói (Nhận + Video + Claim) cho TẤT CẢ users")
async def admin_quests_auto_all_cmd(interaction: discord.Interaction):
    if not is_admin(interaction.user.id):
        return await interaction.response.send_message(f"⛔ Lệnh này chỉ dành cho Admin (`{PRIMARY_ADMIN_ID}`)!", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    embed = await asyncio.to_thread(admin_auto_run_all_users_quests)
    await interaction.followup.send(embed=embed, ephemeral=True)


# --- GAME PLAYTIME COMMAND ---

@tree.command(name="farm_games", description="Cày thời gian chơi game cho tài khoản (Mở Popup nếu không điền số)")
@app_commands.describe(
    count="(Tùy chọn) Số lượng game cần cày (Ví dụ: 50)",
    hours="(Tùy chọn) Số giờ cho mỗi game (Ví dụ: 1.0)"
)
async def farm_games_cmd(interaction: discord.Interaction, count: Optional[int] = None, hours: Optional[float] = None):
    if count is None and hours is None:
        await interaction.response.send_modal(FarmGamesModal())
        return

    target_count = count if count is not None else 50
    target_hours = hours if hours is not None else 1.0

    user_id = interaction.user.id
    state = get_user_state(user_id)
    if not state.has_token:
        embed = discord.Embed(
            title="❌ Chưa có Token tài khoản",
            description="Vui lòng dùng `/setup` hoặc nút Cài đặt để điền token trước.",
            color=discord.Color.red(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    if not state.has_cookie:
        embed = discord.Embed(
            title="❌ Thiếu Cookie cf_clearance",
            description="Cày giờ game bắt buộc cần cookie `cf_clearance`. Vui lòng cập nhật qua `/setup`.",
            color=discord.Color.red(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    user_file = get_user_state_file(user_id)
    controller = ApplicationController(session_path=user_file)
    controller.state.token = state.token
    controller.state.cookie = state.cookie

    result = await asyncio.to_thread(controller.start_headless, games_count=target_count, hours=target_hours)

    if not result.get("success"):
        embed = discord.Embed(
            title="❌ Cày game thất bại",
            description=f"Lỗi: `{result.get('error')}`",
            color=discord.Color.red(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    fresh_game_state = AccountSessionState.restore(user_file)
    fresh_user_state = get_user_state(user_id)
    fresh_user_state.total_games_claimed = fresh_game_state.total_games_claimed
    fresh_user_state.total_hours_claimed = fresh_game_state.total_hours_claimed
    save_user_state(user_id, fresh_user_state)

    accepted = result.get("accepted", 0)
    total_req = result.get("total_requested", 0)
    hours_added = result.get("total_hours", 0.0)

    embed = discord.Embed(
        title="🎮 Đã cày giờ game thành công!",
        color=discord.Color.green(),
    )
    embed.add_field(name="Người dùng", value=interaction.user.mention, inline=True)
    embed.add_field(name="Số game đã gửi", value=f"`{accepted}/{total_req}` games", inline=True)
    embed.add_field(name="Thời gian mỗi game", value=f"`{target_hours:g}h`", inline=True)
    embed.add_field(name="Tổng số giờ vừa cộng", value=f"`+{hours_added:,.0f}h`", inline=True)
    embed.add_field(
        name="Tổng tích lũy hiện tại của bạn",
        value=f"**{fresh_game_state.total_hours_claimed:,.0f}h** trên **{fresh_game_state.total_games_claimed}** games",
        inline=False
    )
    embed.set_footer(text="Lưu ý: Hệ thống Discord Analytics đồng bộ tiến độ badge sau 24-48 giờ.")
    await interaction.followup.send(embed=embed, ephemeral=True)


# --- UTILITY COMMANDS ---

@tree.command(name="profile", description="Kiểm tra thông tin tài khoản và danh sách huy hiệu Discord của bạn")
async def profile_cmd(interaction: discord.Interaction):
    await handle_view_profile(interaction)


@tree.command(name="config", description="Xem cấu hình hiện tại của bạn (Token đã được che giấu)")
async def config_cmd(interaction: discord.Interaction):
    user_id = interaction.user.id
    state = get_user_state(user_id)
    user_file = get_user_state_file(user_id)
    game_state = AccountSessionState.restore(user_file)

    embed = discord.Embed(
        title="⚙️ Cấu hình Tài khoản của bạn",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Tài khoản chính (Cày)", value=mask_token(state.token), inline=True)
    embed.add_field(name="Tài khoản phụ (Viewer)", value=mask_token(state.viewer_token), inline=True)
    embed.add_field(name="Clearance Cookie", value="`Đã cấu hình`" if state.has_cookie else "*(Chưa có)*", inline=True)
    embed.add_field(
        name="Phòng Voice Đã lưu (Lọc theo ID)",
        value=f"`{state.last_channel_id}`" if state.last_channel_id else "🎲 *Auto Random*",
        inline=True
    )
    embed.add_field(
        name="Nguồn phát Video",
        value=f"📺 `{state.stream_url}`" if state.stream_url else "🎞️ *Video mẫu (Lặp vô tận)*",
        inline=True
    )
    embed.add_field(
        name="Tổng giờ Stream đã cày",
        value=f"**{state.total_stream_hours_claimed:,.1f}h** ({state.total_stream_sessions} phiên)",
        inline=False
    )
    embed.add_field(
        name="Tổng giờ Game đã cày",
        value=f"**{game_state.total_hours_claimed:,.0f}h** ({game_state.total_games_claimed} games)",
        inline=False
    )
    embed.set_footer(text="Dùng /panel để mở Bảng Điều Khiển nút bấm hoặc /setup để sửa token!")

    await interaction.response.send_message(embed=embed, ephemeral=True)


@tree.command(name="clear_config", description="Xóa toàn bộ token và cookie đã lưu của bạn")
async def clear_config_cmd(interaction: discord.Interaction):
    user_id = interaction.user.id
    state = get_user_state(user_id)
    state.token = ""
    state.cookie = ""
    state.viewer_token = ""
    state.analytics_token = ""
    save_user_state(user_id, state)

    user_file = get_user_state_file(user_id)
    game_state = AccountSessionState.restore(user_file)
    game_state.token = ""
    game_state.cookie = ""
    game_state.analytics_token = ""
    game_state.persist(user_file)

    embed = discord.Embed(
        title="🗑️ Đã xóa sạch cấu hình của bạn",
        description="Toàn bộ token và cookie của bạn đã được reset khỏi bot.",
        color=discord.Color.orange(),
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@tree.command(name="help", description="Xem hướng dẫn toàn bộ lệnh và Bảng điều khiển")
async def help_cmd(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📖 Hướng dẫn sử dụng Bot Badge Maximizer (Multi-User)",
        description=(
            "Bot hỗ trợ **nhiều thành viên dùng chung** mà không sợ lộ token hay bị xung đột phiên stream!\n"
            "Mỗi người dùng đều có một hồ sơ độc lập."
        ),
        color=discord.Color.blue(),
    )
    embed.add_field(
        name="🎛️ Bảng Điều Khiển Trực Quan",
        value="`/panel` - Mở Bảng điều khiển có sẵn các nút bấm (Cài đặt, Bắt đầu stream, Dừng stream...)",
        inline=False
    )
    embed.add_field(
        name="🔑 Cài đặt Tài khoản (Popup Modal)",
        value=(
            "`/setup` hoặc `/set_token` - Mở Popup Form điền token tài khoản chính & phụ\n"
            "`/setup_main` - Mở Popup cài riêng cho tài khoản chính\n"
            "`/setup_alt` - Mở Popup cài riêng cho tài khoản phụ (Alt Viewer)\n"
            "`/config` - Xem cấu hình và số giờ đã cày của bạn\n"
            "`/clear_config` - Xóa thông tin token của bạn khỏi bot"
        ),
        inline=False
    )
    embed.add_field(
        name="🔴 Cày Streaming Badge",
        value=(
            "`/stream start` - Tự động phát Live Stream (lọc theo ID đã lưu hoặc bốc ngẫu nhiên)\n"
            "`/stream play <url>` - Phát trực tiếp link YouTube hoặc Video URL vào phòng Voice\n"
            "`/stream set_channel <id>` - Khóa phòng Voice mặc định theo ID (hoặc đặt lại random)\n"
            "`/stream stop` - Dừng phát stream của bạn và lưu lại số giờ\n"
            "`/stream status` - Xem thời gian đã stream và số lượng viewer của bạn\n"
            "`/stream list` - Xem tất cả các luồng đang phát trên server"
        ),
        inline=False
    )
    embed.add_field(
        name="🎮 Cày Game Playtime & Profile",
        value=(
            "`/farm_games` - Mở Popup hoặc chạy cày playtime cho nhiều game tự động\n"
            "`/profile` - Kiểm tra huy hiệu hiện có trên tài khoản của bạn"
        ),
        inline=False
    )
    embed.add_field(
        name="🎯 Tự Động Hoàn Thành Discord Quests",
        value=(
            "`/quest panel` - Mở Bảng Điều Khiển Discord Quests chuyên dụng\n"
            "`/quest list` - Xem danh sách nhiệm vụ Discord Quests hiện có\n"
            "`/quest auto` - Tự động quét, nhận tất cả quest và làm video quest (1-Click)\n"
            "`/quest enroll [id]` - Nhận một quest cụ thể hoặc toàn bộ quest\n"
            "`/quest video` - Tự động chạy xem video hoàn thành Video Quests\n"
            "`/quest claim [id]` - Nhận phần thưởng cho nhiệm vụ đã hoàn thành"
        ),
        inline=False
    )
    if is_admin(interaction.user.id):
        embed.add_field(
            name="👑 Lệnh Quản Trị Hệ Thống (Dành Riêng Cho Admin)",
            value=(
                "`/admin panel` - Mở Bảng Điều Khiển Admin với các nút bấm trực quan\n"
                "`/admin list` - Xem tất cả luồng stream đang phát của mọi user\n"
                "`/admin restart_all` - Phát sóng lại / Stream lại cho toàn bộ người dùng\n"
                "`/admin stop <user>` - Dừng luồng stream của bất kỳ user nào\n"
                "`/admin stop_all` - Dừng khẩn cấp toàn bộ các luồng trên bot\n"
                "`/admin stats` - Xem báo cáo thống kê toàn bộ hệ thống bot\n"
                "`/admin reset_user <user>` - Xóa sạch dữ liệu cấu hình của user\n"
                "`/admin quests_summary` - Báo cáo thống kê Quests của toàn bộ user\n"
                "`/admin quests_enroll_all` - Tự động nhận Quests cho TẤT CẢ users\n"
                "`/admin quests_auto_all` - Chạy auto Quests trọn gói cho TẤT CẢ users"
            ),
            inline=False
        )
    embed.set_footer(text="Discord Badge Maximizer · Multi-User Bot Control with Modals")
    await interaction.response.send_message(embed=embed)


# ==========================================
# 5. MAIN RUNNER
# ==========================================

def run_bot() -> None:
    bot_token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    if not bot_token:
        print("\n" + "!" * 60)
        print("  LỖI: Chưa có DISCORD_BOT_TOKEN trong file .env!")
        print("  1. Tạo bot tại: https://discord.com/developers/applications")
        print("  2. Lấy Bot Token (trong mục 'Bot')")
        print("  3. Thêm vào file .env: DISCORD_BOT_TOKEN=your_bot_token_here")
        print("!" * 60 + "\n")
        token_input = input("Hoặc dán DISCORD_BOT_TOKEN vào đây: ").strip()
        if token_input:
            bot_token = token_input
            os.environ["DISCORD_BOT_TOKEN"] = bot_token
        else:
            print("Đã hủy khởi động.")
            return

    tree.add_command(quest_group)
    tree.add_command(stream_group)
    tree.add_command(admin_group)
    try:
        bot.run(bot_token)
    except discord.errors.LoginFailure:
        print("\n" + "!" * 65)
        print("  ❌ LỖI ĐĂNG NHẬP BOT THẤT BẠI: 401 Unauthorized (Improper token)")
        print("!" * 65)
        print("  DISCORD_BOT_TOKEN hiện tại không hợp lệ hoặc đã bị vô hiệu hóa!")
        print("\n  👉 CÁC NGUYÊN NHÂN PHỔ BIẾN:")
        print("  1. Dán nhầm User Token (Token tài khoản cá nhân):")
        print("     - bot.py yêu cầu TOKEN CỦA BOT DISCORD (tạo trên Developer Portal),")
        print("       KHÔNG PHẢI User Token lấy từ F12/DevTools.")
        print("     - Nếu muốn stream/cày badge trực tiếp bằng User Token mà không cần")
        print("       lập Bot Discord, hãy chạy: python3 stream.py hoặc python3 gui.py")
        print("  2. Nhầm Client Secret hoặc Application ID thay vì Bot Token.")
        print("  3. Token đã bị Discord tự động thu hồi (do đổi mật khẩu, bật 2FA,")
        print("     hoặc token bị lộ lên GitHub).")
        print("\n  👉 CÁCH KHẮC PHỤC:")
        print("  1. Truy cập: https://discord.com/developers/applications")
        print("  2. Chọn App của bạn -> Vào menu 'Bot' ở cột bên trái.")
        print("  3. Bấm 'Reset Token' và copy token mới.")
        print("  4. Dán vào file .env: DISCORD_BOT_TOKEN=<token_vừa_copy>")
        print("!" * 65 + "\n")
        sys.exit(1)


if __name__ == "__main__":
    run_bot()
