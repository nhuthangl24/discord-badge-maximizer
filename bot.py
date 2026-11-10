#!/usr/bin/env python3
"""
Discord Badge Maximizer - Discord Application Bot
Allows controlling stream farming and game playtime spoofing entirely via Slash Commands (/slash).
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import urllib.request
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
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


class StreamBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)
        self.active_engine: Optional[GatewayStreamEngine] = None
        self.stream_thread: Optional[threading.Thread] = None
        self.stream_lock = threading.Lock()
        self.session_started_at: float = 0.0

    async def setup_hook(self) -> None:
        # Sync slash commands globally
        print("[Bot] Đang đồng bộ Slash Commands với Discord...")
        synced = await self.tree.sync()
        print(f"[Bot] Đã đồng bộ thành công {len(synced)} lệnh Slash.")


bot = StreamBot()
tree = bot.tree


@bot.event
async def on_ready():
    print("=" * 55)
    print(f"  Bot đã online: {bot.user} (ID: {bot.user.id})")
    print(f"  Mời Bot vào Server của bạn để dùng lệnh /slash")
    print("=" * 55)


# ==========================================
# 1. AUTHENTICATION & CONFIG SLASH COMMANDS
# ==========================================

@tree.command(name="set_token", description="Cấu hình Token tài khoản phụ (Ẩn danh - Không cần ID phòng)")
@app_commands.describe(
    token="Discord User Token của tài khoản phụ cần cày badge",
    cookie="(Tùy chọn) Cookie cf_clearance (Chỉ cần nếu muốn cày giờ game)",
    viewer_token="(Tùy chọn) Token tài khoản alt thứ 2 để tự động xem stream",
    channel_id="(Tùy chọn) Bỏ trống để bot tự động chọn phòng voice ngẫu nhiên",
    guild_id="(Tùy chọn) Bỏ trống nếu không cần chỉ định server"
)
async def set_token_cmd(
    interaction: discord.Interaction,
    token: str,
    cookie: Optional[str] = None,
    viewer_token: Optional[str] = None,
    channel_id: Optional[str] = None,
    guild_id: Optional[str] = None,
):
    # Always ephemeral so credentials are never leaked in public server
    await interaction.response.defer(ephemeral=True)

    state = State.load()
    state.token = token.strip()
    if cookie:
        state.cookie = cookie.strip()
    if viewer_token:
        state.viewer_token = viewer_token.strip()
    if channel_id:
        state.last_channel_id = extract_channel_id(channel_id.strip())
    if guild_id:
        state.last_guild_id = guild_id.strip()

    # Save to session.json
    state.save()

    # Also sync with games.py session state
    game_state = AccountSessionState.restore()
    game_state.token = state.token
    if cookie:
        game_state.cookie = state.cookie
    game_state.persist()

    embed = discord.Embed(
        title="✅ Đã cập nhật Token & Cấu hình thành công",
        description="Thông tin tài khoản phụ đã được lưu an toàn vào `session.json` cục bộ.",
        color=discord.Color.green(),
    )
    embed.add_field(name="User Token", value=mask_token(state.token), inline=True)
    embed.add_field(name="Clearance Cookie", value="`Đã lưu`" if state.has_cookie else "*(Trống)*", inline=True)
    embed.add_field(name="Alt Viewer Token", value=mask_token(state.viewer_token), inline=True)
    if state.last_channel_id:
        embed.add_field(name="Voice Channel ID", value=f"`{state.last_channel_id}`", inline=True)
    if state.last_guild_id:
        embed.add_field(name="Guild ID", value=f"`{state.last_guild_id}`", inline=True)

    embed.set_footer(text="Gợi ý: Dùng /stream start để bắt đầu phát stream hoặc /farm_games để cày giờ game!")
    await interaction.followup.send(embed=embed, ephemeral=True)


@tree.command(name="config", description="Xem cấu hình tài khoản phụ hiện tại (Đã che giấu token)")
async def config_cmd(interaction: discord.Interaction):
    state = State.load()
    game_state = AccountSessionState.restore()

    embed = discord.Embed(
        title="⚙️ Cấu hình hiện tại của Tool",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Main User Token", value=mask_token(state.token), inline=True)
    embed.add_field(name="Clearance Cookie", value="`Đã cấu hình`" if state.has_cookie else "*(Chưa có)*", inline=True)
    embed.add_field(name="Alt Viewer Token", value=mask_token(state.viewer_token), inline=True)
    embed.add_field(name="Voice Channel Mặc định", value=f"`{state.last_channel_id}`" if state.last_channel_id else "*(Chưa đặt)*", inline=True)
    embed.add_field(name="Guild ID Mặc định", value=f"`{state.last_guild_id}`" if state.last_guild_id else "*(Chưa đặt)*", inline=True)
    embed.add_field(name="Tổng giờ Stream đã cày", value=f"**{state.total_stream_hours_claimed:,.1f}h** ({state.total_stream_sessions} phiên)", inline=False)
    embed.add_field(name="Tổng giờ Game đã cày", value=f"**{game_state.total_hours_claimed:,.0f}h** ({game_state.total_games_claimed} games)", inline=False)

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


# ==========================================
# 2. STREAMING SLASH COMMANDS
# ==========================================

stream_group = app_commands.Group(name="stream", description="Quản lý tính năng Live Stream cày Streaming Badge")


@stream_group.command(name="start", description="Bắt đầu phát Live Stream Go Live (Tự động vào phòng random nếu không điền ID)")
@app_commands.describe(
    channel_id="ID kênh voice tùy chọn (Bỏ trống = Bot tự chọn phòng random)",
    guild_id="ID Server tùy chọn"
)
async def stream_start(interaction: discord.Interaction, channel_id: Optional[str] = None, guild_id: Optional[str] = None):
    state = State.load()
    if not state.has_token:
        embed = discord.Embed(
            title="❌ Chưa có Token tài khoản phụ",
            description="Vui lòng dùng lệnh `/set_token` trước để điền token tài khoản cần cày badge.",
            color=discord.Color.red(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    with bot.stream_lock:
        if bot.active_engine and bot.active_engine.running:
            embed = discord.Embed(
                title="⚠️ Đang có phiên Stream chạy rồi!",
                description="Bot đang phát một luồng stream. Hãy dùng `/stream status` để kiểm tra hoặc `/stream stop` để dừng.",
                color=discord.Color.yellow(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

    await interaction.response.defer()

    session = Session.new()
    try:
        ensure_token(state, session.super_props)
    except Exception as exc:
        embed = discord.Embed(
            title="❌ Lỗi xác thực tài khoản phụ",
            description=f"Không thể đồng bộ analytics token từ Discord: `{exc}`\nHãy kiểm tra lại token bằng `/set_token`.",
            color=discord.Color.red(),
        )
        await interaction.followup.send(embed=embed)
        return

    target_cid = extract_channel_id(channel_id.strip()) if channel_id else ""
    target_gid = guild_id.strip() if guild_id else ""
    selected_name = "Voice Channel"
    is_random = False

    # Auto-pick random voice room if no channel_id specified
    if not target_cid:
        # Option A: Check voice channels in current Discord server
        if interaction.guild and interaction.guild.voice_channels:
            import random
            available_vcs = list(interaction.guild.voice_channels)
            if available_vcs:
                chosen = random.choice(available_vcs)
                target_cid = str(chosen.id)
                target_gid = str(interaction.guild.id)
                selected_name = f"#{chosen.name} ({interaction.guild.name})"
                is_random = True

        # Option B: Discover random voice channel from any server user is in
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
            description="Tài khoản phụ chưa tham gia server nào có phòng voice khả dụng, hoặc tài khoản thiếu quyền kết nối.",
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
        description=f"Tài khoản phụ đang kết nối và phát trực tiếp vào phòng voice.",
        color=discord.Color.brand_green(),
    )
    embed.add_field(name="Phòng Voice", value=f"`{selected_name}` (`{target_cid}`)", inline=False)
    embed.add_field(name="Alt Viewer Tự động", value="`Bật (Opcode 20)`" if state.has_viewer_token else "`Tắt (Cần người ngồi xem)`", inline=True)
    embed.add_field(name="Trạng thái", value="🟢 **Đang phát sóng**", inline=True)
    embed.set_footer(text="Dùng /stream status để theo dõi hoặc /stream stop để dừng.")
    await interaction.followup.send(embed=embed)


@stream_group.command(name="stop", description="Dừng phiên phát Live Stream hiện tại và lưu lại số giờ")
async def stream_stop(interaction: discord.Interaction):
    with bot.stream_lock:
        engine = bot.active_engine

    if not engine or not engine.running:
        embed = discord.Embed(
            title="ℹ️ Không có phiên Stream nào đang chạy",
            color=discord.Color.light_grey(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    await interaction.response.defer()

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


@stream_group.command(name="status", description="Xem trạng thái phiên phát Stream hiện tại")
async def stream_status(interaction: discord.Interaction):
    with bot.stream_lock:
        engine = bot.active_engine
        started_at = bot.session_started_at

    state = State.load()

    if not engine or not engine.running:
        embed = discord.Embed(
            title="⚪ Trạng thái: Đang nghỉ (Idle)",
            color=discord.Color.light_grey(),
        )
        embed.add_field(name="Tổng giờ đã cày", value=f"**{state.total_stream_hours_claimed:,.1f}h** ({state.total_stream_sessions} phiên)", inline=False)
        embed.set_footer(text="Dùng /stream start để bắt đầu phát!")
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
        embed.set_footer(text="⚠️ Cảnh báo: Hiện có 0 người xem. Cần có ít nhất 1 người xem (hoặc Alt Viewer) để giờ stream được tính!")
    else:
        embed.set_footer(text="✅ Đang tích lũy giờ stream đều đặn!")

    await interaction.response.send_message(embed=embed)


# ==========================================
# 3. GAME PLAYTIME SPOOFING SLASH COMMAND
# ==========================================

@tree.command(name="farm_games", description="Cày thời gian chơi game cho tài khoản phụ (Tăng Game Variety & Badges)")
@app_commands.describe(
    count="Số lượng game cần cày (Mặc định: 50)",
    hours="Số giờ cho mỗi game (Mặc định: 1.0)"
)
async def farm_games_cmd(interaction: discord.Interaction, count: int = 50, hours: float = 1.0):
    state = State.load()
    if not state.has_token:
        embed = discord.Embed(
            title="❌ Chưa có Token tài khoản phụ",
            description="Vui lòng dùng lệnh `/set_token` trước.",
            color=discord.Color.red(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    if not state.has_cookie:
        embed = discord.Embed(
            title="❌ Thiếu Cookie cf_clearance",
            description="Cày giờ game bắt buộc cần cookie `cf_clearance`. Vui lòng dùng `/set_token cookie:...` để bổ sung.",
            color=discord.Color.red(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    await interaction.response.defer()

    # Run games.py ingestion in a thread pool to avoid blocking asyncio event loop
    controller = ApplicationController()
    result = await asyncio.to_thread(controller.start_headless, games_count=count, hours=hours)

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
    embed.add_field(name="Thời gian mỗi game", value=f"`{hours:g}h`", inline=True)
    embed.add_field(name="Tổng số giờ vừa cộng", value=f"`+{hours_added:,.0f}h`", inline=True)
    embed.add_field(name="Tổng tích lũy hiện tại", value=f"**{fresh_game_state.total_hours_claimed:,.0f}h** trên **{fresh_game_state.total_games_claimed}** titles", inline=False)
    embed.set_footer(text="Lưu ý: Hệ thống Discord Analytics đồng bộ tiến độ badge sau 24-48 giờ.")
    await interaction.followup.send(embed=embed)


# ==========================================
# 4. PROFILE & BADGES INSPECT SLASH COMMAND
# ==========================================

@tree.command(name="profile", description="Kiểm tra thông tin tài khoản phụ và danh sách huy hiệu Discord")
async def profile_cmd(interaction: discord.Interaction):
    state = State.load()
    if not state.has_token:
        embed = discord.Embed(
            title="❌ Chưa có Token tài khoản phụ",
            description="Vui lòng dùng lệnh `/set_token` trước.",
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
# 5. HELP SLASH COMMAND
# ==========================================

@tree.command(name="help", description="Xem hướng dẫn toàn bộ lệnh Slash")
async def help_cmd(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📖 Hướng dẫn sử dụng Bot Badge Maximizer",
        description="Toàn bộ thao tác đều được điều khiển trực tiếp qua các lệnh Slash (/):",
        color=discord.Color.blue(),
    )
    embed.add_field(
        name="🔑 Thiết lập & Cấu hình",
        value=(
            "`/set_token` - Nhập Token acc phụ, cookie, alt viewer (Chỉ mình bạn thấy)\n"
            "`/config` - Xem cấu hình hiện tại và số giờ đã cày\n"
            "`/clear_config` - Xóa thông tin token khỏi bot"
        ),
        inline=False
    )
    embed.add_field(
        name="🔴 Cày Streaming Badge",
        value=(
            "`/stream start [channel_id]` - Bắt đầu Live Stream vào phòng voice\n"
            "`/stream stop` - Dừng phát stream và lưu lại số giờ\n"
            "`/stream status` - Xem thời gian đã stream và số lượng viewer"
        ),
        inline=False
    )
    embed.add_field(
        name="🎮 Cày Game Playtime & Profile",
        value=(
            "`/farm_games [count] [hours]` - Cày playtime cho nhiều game tự động\n"
            "`/profile` - Kiểm tra huy hiệu hiện có trên tài khoản phụ"
        ),
        inline=False
    )
    embed.set_footer(text="Discord Badge Maximizer · All-in-One Bot Control")
    await interaction.response.send_message(embed=embed)


# ==========================================
# MAIN RUNNER
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
