#!/usr/bin/env python3
"""
Discord Badge Maximizer - Discord Application Bot
Allows controlling stream farming and game playtime spoofing entirely via
Slash Commands (/slash), Modals (Popup forms), and an Interactive Control Panel.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import threading
import time
import urllib.request
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands, ui
from discord.ext import commands

# Import core engines from existing scripts (keeping 100% original logic intact)
from games import ApplicationController, AccountSessionState
from stream import (
    Console,
    GatewayStreamEngine,
    Session,
    State,
    ensure_token,
    extract_channel_id,
    find_random_voice_channel_from_token,
    format_timer,
    validate_stream_target,
    STREAM_TIERS,
    USERS_URL,
    ME_URL,
    USER_AGENT,
)

ROOT = Path(__file__).resolve().parent

# Load environment variables from .env if present
def _load_env_file(filepath: Path) -> None:
    if filepath.is_file():
        try:
            for line in filepath.read_text(encoding="utf-8").splitlines():
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
        label="ID Phòng Voice (Tùy chọn - Bỏ trống = Random)",
        placeholder="Bỏ trống để bot tự chọn phòng voice ngẫu nhiên...",
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

    async def on_submit(self, interaction: discord.Interaction):
        state = State.load()
        state.token = self.main_token.value.strip()

        if self.alt_token.value.strip():
            state.viewer_token = self.alt_token.value.strip()
        if self.cookie.value.strip():
            state.cookie = self.cookie.value.strip()
        if self.channel_id.value.strip():
            state.last_channel_id = extract_channel_id(self.channel_id.value.strip())

        state.save()

        # Sync with games.py session state
        game_state = AccountSessionState.restore()
        game_state.token = state.token
        if state.cookie:
            game_state.cookie = state.cookie
        game_state.persist()

        embed = discord.Embed(
            title="✅ Đã cập nhật cấu hình thành công qua Popup!",
            description="Thông tin tài khoản chính và phụ đã được lưu an toàn.",
            color=discord.Color.green(),
        )
        embed.add_field(name="Tài khoản chính (Cày badge)", value=mask_token(state.token), inline=True)
        embed.add_field(name="Tài khoản phụ (Alt Viewer)", value=mask_token(state.viewer_token), inline=True)
        embed.add_field(name="Clearance Cookie", value="`Đã cấu hình`" if state.has_cookie else "*(Trống)*", inline=True)
        embed.add_field(
            name="Phòng Voice",
            value=f"`{state.last_channel_id}`" if state.last_channel_id else "🎲 *Tự động chọn ngẫu nhiên*",
            inline=True
        )
        embed.set_footer(text="Bây giờ bạn có thể dùng /stream start hoặc bấm nút trên Bảng điều khiển!")
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
        state = State.load()
        state.token = self.main_token.value.strip()
        if self.cookie.value.strip():
            state.cookie = self.cookie.value.strip()
        state.save()

        game_state = AccountSessionState.restore()
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
        state = State.load()
        state.viewer_token = self.alt_token.value.strip()
        state.save()

        embed = discord.Embed(
            title="✅ Đã lưu Tài khoản Phụ (Alt Viewer) thành công!",
            description="Tài khoản này sẽ tự động tham gia phòng voice và xem stream khi bạn phát.",
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
        state = State.load()
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
                title="❌ Dữ liệu không hợp lệ",
                description="Số lượng game và số giờ phải là số hợp lệ.",
                color=discord.Color.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=False)

        controller = ApplicationController()
        result = await asyncio.to_thread(controller.start_headless, games_count=target_count, hours=target_hours)

        if not result.get("success"):
            embed = discord.Embed(
                title="❌ Cày game thất bại",
                description=f"Lỗi: `{result.get('error')}`",
                color=discord.Color.red(),
            )
            await interaction.followup.send(embed=embed)
            return

        fresh_game_state = AccountSessionState.restore()
        accepted = result.get("accepted", 0)
        total_req = result.get("total_requested", 0)
        hours_added = result.get("total_hours", 0.0)

        embed = discord.Embed(
            title="🎮 Đã cày giờ game thành công!",
            color=discord.Color.green(),
        )
        embed.add_field(name="Số game đã gửi", value=f"`{accepted}/{total_req}` games", inline=True)
        embed.add_field(name="Thời gian mỗi game", value=f"`{target_hours:g}h`", inline=True)
        embed.add_field(name="Tổng số giờ vừa cộng", value=f"`+{hours_added:,.0f}h`", inline=True)
        embed.add_field(
            name="Tổng tích lũy hiện tại",
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

    @ui.button(label="⚙️ Cài đặt Tài khoản (Popup)", style=discord.ButtonStyle.primary, row=0)
    async def btn_setup(self, interaction: discord.Interaction, button: ui.Button):
        state = State.load()
        await interaction.response.send_modal(AllInOneSetupModal(current_state=state))

    @ui.button(label="👤 Cài Acc Phụ (Alt Viewer)", style=discord.ButtonStyle.secondary, row=0)
    async def btn_alt(self, interaction: discord.Interaction, button: ui.Button):
        state = State.load()
        await interaction.response.send_modal(AltAccountModal(current_state=state))

    @ui.button(label="🔴 Bắt đầu Stream (Auto Room)", style=discord.ButtonStyle.success, row=1)
    async def btn_stream_start(self, interaction: discord.Interaction, button: ui.Button):
        await handle_start_stream(interaction, channel_id=None, guild_id=None)

    @ui.button(label="⏹️ Dừng Stream", style=discord.ButtonStyle.danger, row=1)
    async def btn_stream_stop(self, interaction: discord.Interaction, button: ui.Button):
        await handle_stop_stream(interaction)

    @ui.button(label="📊 Xem Trạng thái", style=discord.ButtonStyle.secondary, row=1)
    async def btn_stream_status(self, interaction: discord.Interaction, button: ui.Button):
        await handle_stream_status(interaction)

    @ui.button(label="🎮 Cày Giờ Game (Popup)", style=discord.ButtonStyle.primary, row=2)
    async def btn_farm_games(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(FarmGamesModal())

    @ui.button(label="🏆 Xem Profile & Badges", style=discord.ButtonStyle.secondary, row=2)
    async def btn_profile(self, interaction: discord.Interaction, button: ui.Button):
        await handle_view_profile(interaction)


# ==========================================
# 3. HELPER BUSINESS LOGIC FUNCTIONS
# ==========================================

async def handle_start_stream(
    interaction: discord.Interaction,
    channel_id: Optional[str] = None,
    guild_id: Optional[str] = None,
):
    state = State.load()
    if not state.has_token:
        embed = discord.Embed(
            title="❌ Chưa có Token tài khoản chính",
            description="Vui lòng dùng nút **⚙️ Cài đặt Tài khoản** hoặc lệnh `/setup` để điền token trước.",
            color=discord.Color.red(),
        )
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    with bot.stream_lock:
        if bot.active_engine and bot.active_engine.running:
            embed = discord.Embed(
                title="⚠️ Đang có phiên Stream chạy rồi!",
                description="Bot đang phát một luồng stream. Dùng nút **📊 Xem Trạng thái** hoặc **⏹️ Dừng Stream**.",
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
            description=f"Không thể đồng bộ token với Discord: `{exc}`\nHãy kiểm tra lại token bằng `/setup`.",
            color=discord.Color.red(),
        )
        await interaction.followup.send(embed=embed)
        return

    target_cid = extract_channel_id(channel_id.strip()) if channel_id else ""
    target_gid = guild_id.strip() if guild_id else ""
    selected_name = "Voice Channel"
    is_random = False

    # Auto-pick random voice room if no channel_id provided
    if not target_cid:
        # Option A: Check voice channels in current Discord server
        if interaction.guild and interaction.guild.voice_channels:
            available_vcs = list(interaction.guild.voice_channels)
            if available_vcs:
                chosen = random.choice(available_vcs)
                target_cid = str(chosen.id)
                target_gid = str(interaction.guild.id)
                selected_name = f"#{chosen.name} ({interaction.guild.name})"
                is_random = True

        # Option B: Discover random voice channel across all servers user is in
        if not target_cid:
            rcid, rgid, rname = find_random_voice_channel_from_token(state.token, session.super_props)
            if rcid:
                target_cid = rcid
                target_gid = rgid
                selected_name = f"#{rname}"
                is_random = True

        # Option C: Fall back to last_channel_id if present
        if not target_cid and state.last_channel_id:
            target_cid = state.last_channel_id
            target_gid = state.last_guild_id
            selected_name = f"Kênh lưu trước: {target_cid}"

    if not target_cid:
        embed = discord.Embed(
            title="❌ Không tìm thấy phòng Voice nào",
            description="Tài khoản chưa tham gia server nào có phòng voice khả dụng, hoặc thiếu quyền kết nối.",
            color=discord.Color.red(),
        )
        await interaction.followup.send(embed=embed)
        return

    engine = GatewayStreamEngine(state, session, Console())

    def _run_stream():
        engine.start_stream(guild_id=target_gid, channel_id=target_cid)
        with bot.stream_lock:
            if bot.active_engine == engine:
                bot.active_engine = None
                bot.stream_thread = None

    with bot.stream_lock:
        bot.active_engine = engine
        bot.session_started_at = time.time()
        bot.stream_thread = threading.Thread(target=_run_stream, daemon=True)
        bot.stream_thread.start()

    embed = discord.Embed(
        title="🎲 Đã tự động vào phòng Voice ngẫu nhiên!" if is_random else "🔴 Đã kích hoạt phát Live Stream Go Live!",
        description=f"Tài khoản đang kết nối Gateway và phát trực tiếp vào phòng voice.",
        color=discord.Color.brand_green(),
    )
    embed.add_field(name="Phòng Voice", value=f"`{selected_name}` (`{target_cid}`)", inline=False)
    embed.add_field(
        name="Alt Viewer Tự động",
        value="`Bật (Opcode 20)`" if state.has_viewer_token else "`Tắt (Cần người ngồi xem)`",
        inline=True
    )
    embed.add_field(name="Trạng thái", value="🟢 **Đang phát sóng**", inline=True)
    embed.set_footer(text="Dùng /stream status hoặc nút Xem Trạng thái để theo dõi.")
    await interaction.followup.send(embed=embed)


async def handle_stop_stream(interaction: discord.Interaction):
    with bot.stream_lock:
        engine = bot.active_engine

    if not engine or not engine.running:
        embed = discord.Embed(
            title="ℹ️ Không có phiên Stream nào đang chạy",
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
        bot.active_engine = None
        bot.stream_thread = None

    fresh_state = State.load()
    embed = discord.Embed(
        title="⏹️ Đã dừng Live Stream thành công",
        color=discord.Color.purple(),
    )
    embed.add_field(name="Thời gian tính trong phiên", value=f"`{format_timer(engine.counted_stream_seconds)}`", inline=True)
    embed.add_field(name="Tổng số giờ đã cày", value=f"**{fresh_state.total_stream_hours_claimed:,.1f}h**", inline=True)
    embed.set_footer(text="Lưu ý: Huy hiệu Discord Streaming thường cập nhật sau 24-48 giờ.")
    await interaction.followup.send(embed=embed)


async def handle_stream_status(interaction: discord.Interaction):
    with bot.stream_lock:
        engine = bot.active_engine
        started_at = bot.session_started_at

    state = State.load()

    if not engine or not engine.running:
        embed = discord.Embed(
            title="⚪ Trạng thái: Đang nghỉ (Idle)",
            color=discord.Color.light_grey(),
        )
        embed.add_field(
            name="Tổng giờ đã cày",
            value=f"**{state.total_stream_hours_claimed:,.1f}h** ({state.total_stream_sessions} phiên)",
            inline=False
        )
        embed.set_footer(text="Dùng /stream start hoặc bấm nút Bắt đầu Stream để phát!")
        await interaction.response.send_message(embed=embed)
        return

    viewers = engine.get_viewer_count()
    duration_str = format_timer(time.time() - started_at) if started_at > 0 else "0s"
    counted_str = format_timer(engine.counted_stream_seconds)
    counted_hours = engine.counted_stream_seconds / 3600.0

    embed = discord.Embed(
        title="🔴 Đang phát Live Stream",
        color=discord.Color.red() if viewers > 0 else discord.Color.gold(),
    )
    embed.add_field(name="Kênh Voice", value=f"`#{engine.channel_name}` (`{engine.channel_id}`)", inline=True)
    embed.add_field(name="Số người xem", value=f"👀 **{viewers}** viewer(s)", inline=True)
    embed.add_field(name="Thời gian chạy", value=f"`{duration_str}`", inline=True)
    embed.add_field(name="Giờ hợp lệ tích lũy phiên này", value=f"`{counted_str}` ({counted_hours:.2f}h)", inline=False)
    embed.add_field(name="Tổng số giờ trọn đời", value=f"`{state.total_stream_hours_claimed + counted_hours:,.1f}h`", inline=False)

    if viewers == 0:
        embed.set_footer(text="⚠️ Cảnh báo: Có 0 người xem. Cần ít nhất 1 người xem (hoặc Alt Viewer) để giờ stream được tính!")
    else:
        embed.set_footer(text="✅ Đang tích lũy giờ stream đều đặn!")

    await interaction.response.send_message(embed=embed)


async def handle_view_profile(interaction: discord.Interaction):
    state = State.load()
    if not state.has_token:
        embed = discord.Embed(
            title="❌ Chưa có Token tài khoản",
            description="Vui lòng dùng nút Cài đặt hoặc `/setup` để điền token trước.",
            color=discord.Color.red(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    await interaction.response.defer()

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
        await interaction.followup.send(embed=embed)
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
        await interaction.followup.send(embed=embed)
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

    await interaction.followup.send(embed=embed)


# ==========================================
# 4. BOT CLIENT & SLASH COMMANDS REGISTRATION
# ==========================================

class StreamBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)
        self.active_engine: Optional[GatewayStreamEngine] = None
        self.stream_thread: Optional[threading.Thread] = None
        self.stream_lock = threading.Lock()
        self.session_started_at: float = 0.0

    async def setup_hook(self) -> None:
        print("[Bot] Đang đồng bộ Slash Commands với Discord...")
        synced = await self.tree.sync()
        print(f"[Bot] Đã đồng bộ thành công {len(synced)} lệnh Slash.")


bot = StreamBot()
tree = bot.tree


@bot.event
async def on_ready():
    print("=" * 60)
    print(f"  Bot đã online: {bot.user} (ID: {bot.user.id})")
    print(f"  Mời Bot vào Server và gõ /panel để mở Bảng điều khiển!")
    print("=" * 60)


# --- POPUP MODAL SETUP COMMANDS ---

@tree.command(name="setup", description="Mở Popup cài đặt Tài khoản (Chính & Phụ) trực tiếp trên Discord")
async def setup_cmd(interaction: discord.Interaction):
    state = State.load()
    await interaction.response.send_modal(AllInOneSetupModal(current_state=state))


@tree.command(name="set_token", description="Mở Popup cấu hình Token tài khoản chính & phụ")
async def set_token_cmd(interaction: discord.Interaction):
    state = State.load()
    await interaction.response.send_modal(AllInOneSetupModal(current_state=state))


@tree.command(name="setup_main", description="Mở Popup cài đặt riêng cho Tài khoản Chính (Acc cày)")
async def setup_main_cmd(interaction: discord.Interaction):
    state = State.load()
    await interaction.response.send_modal(MainAccountModal(current_state=state))


@tree.command(name="setup_alt", description="Mở Popup cài đặt riêng cho Tài khoản Phụ (Alt Viewer)")
async def setup_alt_cmd(interaction: discord.Interaction):
    state = State.load()
    await interaction.response.send_modal(AltAccountModal(current_state=state))


# --- INTERACTIVE DASHBOARD / PANEL ---

@tree.command(name="panel", description="Mở Bảng Điều Khiển tương tác với các nút bấm trực tiếp")
async def panel_cmd(interaction: discord.Interaction):
    state = State.load()
    game_state = AccountSessionState.restore()

    with bot.stream_lock:
        is_live = bot.active_engine is not None and bot.active_engine.running

    embed = discord.Embed(
        title="🎛️ Bảng Điều Khiển Discord Badge Maximizer",
        description="Quản lý việc cày huy hiệu tài khoản chính và phụ trực tiếp qua các nút bấm bên dưới:",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Tài khoản chính", value=mask_token(state.token), inline=True)
    embed.add_field(name="Tài khoản phụ (Alt)", value=mask_token(state.viewer_token), inline=True)
    embed.add_field(name="Clearance Cookie", value="`Đã cấu hình`" if state.has_cookie else "*(Trống)*", inline=True)
    embed.add_field(
        name="Trạng thái Stream",
        value="🔴 **Đang phát Live**" if is_live else "⚪ **Đang nghỉ (Idle)**",
        inline=True
    )
    embed.add_field(
        name="Tổng giờ Stream đã cày",
        value=f"**{state.total_stream_hours_claimed:,.1f}h** ({state.total_stream_sessions} phiên)",
        inline=True
    )
    embed.add_field(
        name="Tổng giờ Game đã cày",
        value=f"**{game_state.total_hours_claimed:,.0f}h** ({game_state.total_games_claimed} games)",
        inline=True
    )
    embed.set_footer(text="Bấm 'Cài đặt Tài khoản' để mở popup điền token • Bấm 'Bắt đầu Stream' để auto vào room!")

    view = ControlPanelView(bot_instance=bot)
    await interaction.response.send_message(embed=embed, view=view)


# --- STREAM COMMANDS GROUP ---

stream_group = app_commands.Group(name="stream", description="Quản lý tính năng Live Stream cày Streaming Badge")


@stream_group.command(name="start", description="Bắt đầu phát Live Stream (Tự động vào phòng random nếu không điền ID)")
@app_commands.describe(
    channel_id="ID kênh voice tùy chọn (Bỏ trống = Bot tự chọn phòng random)",
    guild_id="ID Server tùy chọn"
)
async def stream_start_cmd(interaction: discord.Interaction, channel_id: Optional[str] = None, guild_id: Optional[str] = None):
    await handle_start_stream(interaction, channel_id=channel_id, guild_id=guild_id)


@stream_group.command(name="stop", description="Dừng phiên phát Live Stream hiện tại và lưu lại số giờ")
async def stream_stop_cmd(interaction: discord.Interaction):
    await handle_stop_stream(interaction)


@stream_group.command(name="status", description="Xem trạng thái phiên phát Stream hiện tại")
async def stream_status_cmd(interaction: discord.Interaction):
    await handle_stream_status(interaction)


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

    state = State.load()
    if not state.has_token:
        embed = discord.Embed(
            title="❌ Chưa có Token tài khoản",
            description="Vui lòng dùng `/setup` để điền token trước.",
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

    controller = ApplicationController()
    result = await asyncio.to_thread(controller.start_headless, games_count=target_count, hours=target_hours)

    if not result.get("success"):
        embed = discord.Embed(
            title="❌ Cày game thất bại",
            description=f"Lỗi: `{result.get('error')}`",
            color=discord.Color.red(),
        )
        await interaction.followup.send(embed=embed)
        return

    fresh_game_state = AccountSessionState.restore()
    accepted = result.get("accepted", 0)
    total_req = result.get("total_requested", 0)
    hours_added = result.get("total_hours", 0.0)

    embed = discord.Embed(
        title="🎮 Đã cày giờ game thành công!",
        color=discord.Color.green(),
    )
    embed.add_field(name="Số game đã gửi", value=f"`{accepted}/{total_req}` games", inline=True)
    embed.add_field(name="Thời gian mỗi game", value=f"`{target_hours:g}h`", inline=True)
    embed.add_field(name="Tổng số giờ vừa cộng", value=f"`+{hours_added:,.0f}h`", inline=True)
    embed.add_field(
        name="Tổng tích lũy hiện tại",
        value=f"**{fresh_game_state.total_hours_claimed:,.0f}h** trên **{fresh_game_state.total_games_claimed}** games",
        inline=False
    )
    embed.set_footer(text="Lưu ý: Hệ thống Discord Analytics đồng bộ tiến độ badge sau 24-48 giờ.")
    await interaction.followup.send(embed=embed)


# --- UTILITY COMMANDS ---

@tree.command(name="profile", description="Kiểm tra thông tin tài khoản và danh sách huy hiệu Discord")
async def profile_cmd(interaction: discord.Interaction):
    await handle_view_profile(interaction)


@tree.command(name="config", description="Xem cấu hình hiện tại (Token đã được che giấu)")
async def config_cmd(interaction: discord.Interaction):
    state = State.load()
    game_state = AccountSessionState.restore()

    embed = discord.Embed(
        title="⚙️ Cấu hình hiện tại của Tool",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Tài khoản chính (Cày)", value=mask_token(state.token), inline=True)
    embed.add_field(name="Tài khoản phụ (Viewer)", value=mask_token(state.viewer_token), inline=True)
    embed.add_field(name="Clearance Cookie", value="`Đã cấu hình`" if state.has_cookie else "*(Chưa có)*", inline=True)
    embed.add_field(
        name="Phòng Voice Mặc định",
        value=f"`{state.last_channel_id}`" if state.last_channel_id else "🎲 *Auto Random*",
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
    embed.set_footer(text="Gợi ý: Dùng /panel để mở Bảng Điều Khiển với các nút bấm tiện lợi!")

    await interaction.response.send_message(embed=embed, ephemeral=True)


@tree.command(name="clear_config", description="Xóa toàn bộ token và cookie đã lưu")
async def clear_config_cmd(interaction: discord.Interaction):
    state = State.load()
    state.token = ""
    state.cookie = ""
    state.viewer_token = ""
    state.analytics_token = ""
    state.save()

    game_state = AccountSessionState.restore()
    game_state.token = ""
    game_state.cookie = ""
    game_state.analytics_token = ""
    game_state.persist()

    embed = discord.Embed(
        title="🗑️ Đã xóa sạch cấu hình",
        description="Toàn bộ token và cookie đã được reset.",
        color=discord.Color.orange(),
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@tree.command(name="help", description="Xem hướng dẫn toàn bộ lệnh và Bảng điều khiển")
async def help_cmd(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📖 Hướng dẫn sử dụng Bot Badge Maximizer",
        description="Bạn có thể dùng lệnh Slash (/) hoặc mở Bảng Điều Khiển bằng nút bấm:",
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
            "`/config` - Xem cấu hình hiện tại và tổng số giờ đã cày\n"
            "`/clear_config` - Xóa thông tin token khỏi bot"
        ),
        inline=False
    )
    embed.add_field(
        name="🔴 Cày Streaming Badge",
        value=(
            "`/stream start` - Tự động tìm phòng voice ngẫu nhiên và phát Live Stream\n"
            "`/stream stop` - Dừng phát stream và lưu lại số giờ\n"
            "`/stream status` - Xem thời gian đã stream và số lượng viewer"
        ),
        inline=False
    )
    embed.add_field(
        name="🎮 Cày Game Playtime & Profile",
        value=(
            "`/farm_games` - Mở Popup hoặc chạy cày playtime cho nhiều game tự động\n"
            "`/profile` - Kiểm tra huy hiệu hiện có trên tài khoản"
        ),
        inline=False
    )
    embed.set_footer(text="Discord Badge Maximizer · All-in-One Bot Control with Modals")
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
