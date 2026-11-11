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

ROOT = Path(__file__).resolve().parent
USER_DATA_DIR = ROOT / "data" / "users"
USER_DATA_DIR.mkdir(parents=True, exist_ok=True)

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
# MULTI-USER STORAGE & HELPERS
# ==========================================

def get_user_state_file(user_id: int) -> Path:
    return USER_DATA_DIR / f"{user_id}.json"


def get_user_state(user_id: int) -> State:
    """Loads state for a specific Discord user ID."""
    p = get_user_state_file(user_id)
    if not p.exists():
        # Check if legacy session.json exists and migrate for first user
        legacy_file = ROOT / "session.json"
        if legacy_file.exists():
            try:
                legacy_state = State.load(legacy_file)
                if legacy_state.token:
                    legacy_state._filepath = p
                    legacy_state.save()
                    return legacy_state
            except Exception:
                pass
    return State.load(p)


def save_user_state(user_id: int, state: State) -> None:
    """Saves state for a specific Discord user ID."""
    p = get_user_state_file(user_id)
    state.save(p)


def mask_token(token: str) -> str:
    if not token:
        return "*(Chưa thiết lập)*"
    if len(token) <= 12:
        return "********"
    return f"`{token[:6]}...{token[-4:]}`"


# ==========================================
# 1. DISCORD MODALS (POPUP FORMS)
# ==========================================

class AllInOneSetupModal(ui.Modal, title="Cấu hình Tài khoản (Chính & Phụ)"):
    main_token = ui.TextInput(
        label="Token Tài khoản Chính (Acc cần cày badge)",
        placeholder="Dán Discord user token tài khoản chính vào đây...",
        style=discord.TextStyle.short,
        required=True,
        min_length=20,
    )
    alt_token = ui.TextInput(
        label="Token Tài khoản Phụ (Alt Viewer xem stream)",
        placeholder="Dán token acc phụ để tự động ngồi xem stream (tùy chọn)...",
        style=discord.TextStyle.short,
        required=False,
    )
    cookie = ui.TextInput(
        label="Cookie cf_clearance (Dùng để cày giờ game)",
        placeholder="Dán giá trị cf_clearance cookie header (tùy chọn)...",
        style=discord.TextStyle.paragraph,
        required=False,
    )
    channel_id = ui.TextInput(
        label="ID Kênh Voice (Tùy chọn - Bỏ trống = Random)",
        placeholder="Dán ID phòng voice để lọc theo ID (Bỏ trống = Auto random)...",
        style=discord.TextStyle.short,
        required=False,
    )
    stream_url = ui.TextInput(
        label="Link YouTube / Stream URL (Bỏ trống = Mẫu)",
        placeholder="https://www.youtube.com/watch?v=... hoặc để trống để lặp video mẫu",
        style=discord.TextStyle.short,
        required=False,
    )

    def __init__(self, current_state: State) -> None:
        super().__init__()
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
        state.token = self.main_token.value.strip()

        if self.alt_token.value.strip():
            state.viewer_token = self.alt_token.value.strip()
        if self.cookie.value.strip():
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
        game_state.persist()

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
        label="ID Phòng Voice (Để trống = Tự động Random)",
        placeholder="Dán ID phòng voice để lọc theo ID (Bỏ trống = Auto random)...",
        style=discord.TextStyle.short,
        required=False,
    )
    stream_url = ui.TextInput(
        label="Link YouTube / Stream URL (Để trống = Video mẫu)",
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
        placeholder="Dán token tài khoản chính vào đây...",
        style=discord.TextStyle.short,
        required=True,
        min_length=20,
    )
    cookie = ui.TextInput(
        label="Cookie cf_clearance (Tùy chọn - Cày game)",
        placeholder="Dán cf_clearance cookie header...",
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
        state.token = self.main_token.value.strip()
        if self.cookie.value.strip():
            state.cookie = self.cookie.value.strip()
        save_user_state(user_id, state)

        user_file = get_user_state_file(user_id)
        game_state = AccountSessionState.restore(user_file)
        game_state.token = state.token
        if state.cookie:
            game_state.cookie = state.cookie
        game_state.persist()

        embed = discord.Embed(
            title="✅ Đã lưu Tài khoản Chính thành công!",
            color=discord.Color.green(),
        )
        embed.add_field(name="Token Chính", value=mask_token(state.token), inline=True)
        embed.add_field(name="Cookie", value="`Đã lưu`" if state.has_cookie else "*(Trống)*", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class AltAccountModal(ui.Modal, title="Cài đặt Tài khoản Phụ (Alt Viewer)"):
    alt_token = ui.TextInput(
        label="Token Tài khoản Phụ (Alt Viewer)",
        placeholder="Dán token tài khoản phụ để auto-view stream (Opcode 20)...",
        style=discord.TextStyle.short,
        required=True,
        min_length=20,
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
            description="Tài khoản phụ này sẽ tự động tham gia phòng voice và xem stream khi bạn phát.",
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

        await interaction.response.defer(ephemeral=False)

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
            await interaction.followup.send(embed=embed)
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
        await interaction.followup.send(embed=embed)


# ==========================================
# 2. INTERACTIVE CONTROL PANEL VIEW (BUTTONS)
# ==========================================

class ControlPanelView(ui.View):
    def __init__(self, bot_instance: StreamBot) -> None:
        super().__init__(timeout=None)
        self.bot_instance = bot_instance

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
        await interaction.response.defer(ephemeral=False)

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
            await interaction.followup.send(embed=embed)
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
            await interaction.followup.send(embed=embed)
            return

    # Save last channel for this user
    state.last_channel_id = target_cid
    state.last_guild_id = target_gid
    save_user_state(user_id, state)

    # Initialize engine with user's state
    engine = GatewayStreamEngine(state, session, Console())

    def _run_stream(uid: int, eng: GatewayStreamEngine, gid: str, cid: str):
        try:
            eng.start_stream(guild_id=gid, channel_id=cid, wait_for_stdin=False)
        finally:
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
    await interaction.followup.send(embed=embed)
    embed.add_field(name="Trạng thái", value="🟢 **Đang phát sóng**", inline=True)
    embed.set_footer(text="Dùng /stream status hoặc nút Xem Trạng thái để theo dõi.")
    await interaction.followup.send(embed=embed)


async def handle_stop_stream(interaction: discord.Interaction):
    user_id = interaction.user.id
    with bot.stream_lock:
        engine = bot.active_engines.get(user_id)

    if not engine or not engine.running:
        embed = discord.Embed(
            title="ℹ️ Bạn không có phiên Stream nào đang chạy",
            description="Dùng nút **🔴 Bắt đầu Stream** để phát sóng.",
            color=discord.Color.light_grey(),
        )
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=False)

    # Request graceful stop
    engine.stop()

    # Wait up to 5s for cleanup to finish
    for _ in range(10):
        if not engine.running:
            break
        await asyncio.sleep(0.5)

    with bot.stream_lock:
        bot.active_engines.pop(user_id, None)
        bot.stream_threads.pop(user_id, None)
        bot.session_started_at.pop(user_id, None)

    fresh_state = get_user_state(user_id)
    embed = discord.Embed(
        title="⏹️ Đã dừng Live Stream thành công",
        description="Phiên phát sóng của bạn đã kết thúc an toàn.",
        color=discord.Color.purple(),
    )
    embed.add_field(name="Người dùng", value=interaction.user.mention, inline=True)
    embed.add_field(name="Thời gian tính trong phiên", value=f"`{format_timer(engine.counted_stream_seconds)}`", inline=True)
    embed.add_field(name="Tổng số giờ đã cày", value=f"**{fresh_state.total_stream_hours_claimed:,.1f}h**", inline=True)
    embed.set_footer(text="Lưu ý: Huy hiệu Discord Streaming thường cập nhật sau 24-48 giờ.")
    await interaction.followup.send(embed=embed)


async def handle_stream_status(interaction: discord.Interaction):
    user_id = interaction.user.id
    with bot.stream_lock:
        engine = bot.active_engines.get(user_id)
        started_at = bot.session_started_at.get(user_id, 0.0)

    state = get_user_state(user_id)

    if not engine or not engine.running:
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
        print("[Bot] Đang đồng bộ Slash Commands với Discord...")
        synced = await self.tree.sync()
        print(f"[Bot] Đã đồng bộ thành công {len(synced)} lệnh Slash.")


bot = StreamBot()
tree = bot.tree


@bot.event
async def on_ready():
    print("=" * 60)
    print(f"  Bot Multi-User đã online: {bot.user} (ID: {bot.user.id})")
    print(f"  Gõ /panel để mở Bảng điều khiển tương tác!")
    print("=" * 60)


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


@stream_group.command(name="stop", description="Dừng phiên phát Live Stream của bạn và lưu lại số giờ")
async def stream_stop_cmd(interaction: discord.Interaction):
    await handle_stop_stream(interaction)


@stream_group.command(name="status", description="Xem trạng thái phiên phát Stream của bạn")
async def stream_status_cmd(interaction: discord.Interaction):
    await handle_stream_status(interaction)


@stream_group.command(name="list", description="Xem danh sách tất cả các luồng Stream đang chạy trên Bot (Admin)")
async def stream_list_cmd(interaction: discord.Interaction):
    with bot.stream_lock:
        running_users = list(bot.active_engines.keys())

    if not running_users:
        embed = discord.Embed(
            title="⚪ Không có luồng Stream nào đang chạy",
            description="Hiện không có thành viên nào đang phát sóng.",
            color=discord.Color.light_grey(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    lines = []
    for uid in running_users:
        eng = bot.active_engines.get(uid)
        if eng and eng.running:
            v_cnt = eng.get_viewer_count()
            secs = eng.counted_stream_seconds
            lines.append(f"• <@{uid}>: `#{eng.channel_name}` (`{eng.channel_id}`) - {format_timer(secs)} (👀 {v_cnt} viewers)")

    embed = discord.Embed(
        title=f"🔴 Danh sách Luồng Stream Đang Chạy ({len(lines)})",
        description="\n".join(lines) if lines else "Không có luồng nào.",
        color=discord.Color.brand_green(),
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


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

    await interaction.response.defer(ephemeral=False)

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
        await interaction.followup.send(embed=embed)
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
    await interaction.followup.send(embed=embed)


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

    tree.add_command(stream_group)
    bot.run(bot_token)


if __name__ == "__main__":
    run_bot()
