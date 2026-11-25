#!/usr/bin/env python3
"""
Discord Badge Maximizer & Entertainment Bot
Next-Generation Discord Automation, Quests Engine, Game Badge Maximizer & Tối Nay Lọ Gì Hub.

Key Features:
- 🎯 Discord Auto-Quests Engine (Minute-by-minute updates to prevent Discord 429 rate-limits)
- 🎮 Game Playtime & Badge Maximizer
- 🎰 Tối Nay Lọ Gì (CS:GO Case Opening Gacha simulator ported from zennomi/toinaylogi)
- 🌐 Built-in Web UI Dashboard for full admin control, live embed builder, and @everyone announcement
- 🔒 Isolated DES+AES encryption for user tokens (crypto_vault)
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
import random
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

import discord
from discord import app_commands, ui
from discord.ext import commands

import crypto_vault
import games
from games import AccountSessionState, ApplicationController
import quest_manager
import toinaylogi
import user_manager
from user_manager import (
    State,
    clear_user_state,
    get_user_state,
    get_user_state_file,
    list_all_users,
    mask_token,
    save_user_state,
)
import web_dashboard

# Root directory setup
ROOT_DIR = Path(__file__).resolve().parent
DISCORD_API_BASE = "https://discord.com/api/v10"
USER_AGENT = quest_manager.USER_AGENT

# Load environment variables
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

_load_env_file(ROOT_DIR / ".env")


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
    """Returns True if the user is an authorized bot admin."""
    return user_id in ADMIN_IDS


# ==========================================
# DISCORD UI POPUP MODALS
# ==========================================

class UserSetupModal(ui.Modal, title="Cài Đặt Tài Khoản (Token & Cookie)"):
    token_input = ui.TextInput(
        label="Discord User Token (Bắt buộc)",
        placeholder="Dán User Token tài khoản cần cày vào đây...",
        style=discord.TextStyle.short,
        required=True,
        min_length=20,
    )
    cookie_input = ui.TextInput(
        label="Cookie cf_clearance (Tùy chọn, dùng cày game)",
        placeholder="Dán giá trị cf_clearance cookie nếu muốn cày giờ game...",
        style=discord.TextStyle.paragraph,
        required=False,
    )

    def __init__(self, current_state: Optional[State] = None):
        super().__init__()
        if current_state and current_state.has_token:
            self.token_input.default = current_state.token
        if current_state and current_state.has_cookie:
            self.cookie_input.default = current_state.cookie

    async def on_submit(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        state = get_user_state(user_id)

        raw_token = self.token_input.value.strip()
        raw_cookie = self.cookie_input.value.strip()

        state.token = raw_token
        state.cookie = raw_cookie
        save_user_state(user_id, state)

        # Sync with games controller session file
        user_file = get_user_state_file(user_id)
        try:
            game_state = AccountSessionState.restore(user_file)
            game_state.token = state.token
            game_state.cookie = state.cookie
            game_state.persist(user_file)
        except Exception:
            pass

        embed = discord.Embed(
            title="✅ Đã Lưu Cấu Hình Thành Công!",
            description=(
                f"👤 **Người dùng:** {interaction.user.mention}\n"
                f"🔑 **Token:** {mask_token(raw_token)}\n"
                f"🍪 **Cookie cf_clearance:** {'`Đã lưu`' if raw_cookie else '*(Chưa điền)*'}\n\n"
                "🛡️ *Dữ liệu token được bảo mật và mã hóa đa tầng DES+AES trong hệ thống.*"
            ),
            color=discord.Color.green(),
        )
        embed.set_footer(text="Bây giờ bạn có thể dùng /quest auto hoặc /farm_games để bắt đầu!")
        await interaction.response.send_message(embed=embed, ephemeral=True)


class FarmGamesModal(ui.Modal, title="Cày Giờ Chơi Game Discord"):
    games_count = ui.TextInput(
        label="Số Lượng Game Cần Cày (Mặc định: 50)",
        placeholder="Nhập số lượng game (ví dụ: 10, 25, 50)...",
        default="50",
        required=False,
    )
    hours = ui.TextInput(
        label="Số Giờ Mỗi Game (Mặc định: 1.0)",
        placeholder="Nhập số giờ mỗi game (ví dụ: 0.5, 1.0, 2.0)...",
        default="1.0",
        required=False,
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            count = int(self.games_count.value.strip() or "50")
            count = max(1, min(count, 500))
        except ValueError:
            count = 50

        try:
            hrs = float(self.hours.value.strip() or "1.0")
            hrs = max(0.1, min(hrs, 24.0))
        except ValueError:
            hrs = 1.0

        await run_farm_games(interaction, count, hrs)


class BroadcastModal(ui.Modal, title="📢 Gửi Thông Báo Kèm Embed"):
    channel_id = ui.TextInput(
        label="ID Kênh Cần Gửi (Channel ID)",
        placeholder="Dán ID kênh Discord muốn gửi thông báo vào đây...",
        style=discord.TextStyle.short,
        required=True,
    )
    tag_mode = ui.TextInput(
        label="Chế Độ Tag (everyone / here / none)",
        placeholder="Gõ everyone, here hoặc none...",
        default="none",
        required=False,
    )
    title_input = ui.TextInput(
        label="Tiêu Đề Thông Báo (Embed Title)",
        placeholder="Nhập tiêu đề thông báo...",
        style=discord.TextStyle.short,
        required=True,
    )
    desc_input = ui.TextInput(
        label="Nội Dung Thông Báo (Hỗ trợ Markdown)",
        placeholder="Nhập nội dung thông báo... Hỗ trợ in đậm, in nghiêng, gạch đầu dòng...",
        style=discord.TextStyle.paragraph,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        cid_str = self.channel_id.value.strip()
        if not cid_str.isdigit():
            return await interaction.response.send_message("❌ ID kênh không hợp lệ!", ephemeral=True)

        channel = interaction.client.get_channel(int(cid_str))
        if not channel or not isinstance(channel, discord.TextChannel):
            return await interaction.response.send_message("❌ Không tìm thấy kênh văn bản này trên Discord!", ephemeral=True)

        tag = self.tag_mode.value.strip().lower()
        content = "@everyone" if tag == "everyone" else ("@here" if tag == "here" else None)

        embed = discord.Embed(
            title=self.title_input.value.strip(),
            description=self.desc_input.value.strip(),
            color=discord.Color.blurple(),
        )
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        embed.set_footer(text=f"Thông báo từ {interaction.guild.name if interaction.guild else 'Admin'}")
        embed.timestamp = discord.utils.utcnow()

        try:
            msg = await channel.send(content=content, embed=embed)
            await interaction.response.send_message(
                f"✅ Đã gửi thông báo thành công đến {channel.mention}! [Xem tin nhắn]({msg.jump_url})",
                ephemeral=True,
            )
            web_dashboard.push_log("SUCCESS", f"📢 [Discord Broadcast] Thông báo gửi đến #{channel.name} bởi {interaction.user}")
        except Exception as e:
            await interaction.response.send_message(f"❌ Không thể gửi thông báo: `{e}`", ephemeral=True)


# ==========================================
# MINUTE-BASED QUEST PROGRESS TRACKER
# ==========================================

class PublicQuestProgressTracker:
    """
    Manages live PUBLIC process tracking for Discord Auto-Quests in a text channel.
    Updates the public message every 60 seconds (1 minute interval) instead of seconds,
    preventing Discord 429 Too Many Requests (Rate Limits).
    Includes rate-limit catch & exponential backoff protection.
    """
    def __init__(self, message: discord.Message, user: discord.User | discord.Member, channel: Any, title: str = "DISCORD AUTO QUESTS"):
        self.message = message
        self.user = user
        self.channel = channel
        self.title = title
        self.orbs: Optional[int] = None
        self.live_items: list[dict[str, Any]] = []
        self.is_done = False
        self.ticker_task: Optional[asyncio.Task] = None
        self.results: list[dict[str, Any]] = []
        self.start_time = time.time()

    def start(self):
        self.ticker_task = asyncio.create_task(self._ticker_loop())

    async def _ticker_loop(self):
        """
        Updates display once per minute (60 seconds) to avoid Discord rate limit errors.
        """
        while not self.is_done:
            await asyncio.sleep(60.0)  # Exactly 1-minute interval as requested
            if self.is_done:
                break

            for item in self.live_items:
                if item["state"] == "working" and item["countdown"] > 0:
                    item["countdown"] = max(0, item["countdown"] - 60)
                    if item["countdown"] == 0:
                        item["state"] = "done"

            await self.safe_update_display()

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
            status_line += f", `{failed_count}` tạm hoãn"

        embed.description = (
            f"👤 **Người dùng:** {self.user.mention} | 🔮 **Số dư Orbs:** {orbs_str}\n"
            f"⚡ **Trạng thái:** {status_line}\n"
            f"⏱️ **Tiến trình cập nhật mỗi 1 phút (Chống rate-limit Discord)**\n"
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

            state_badge = "⏳ Đang làm (Working)"
            if item["state"] == "done":
                state_badge = "✔ Hoàn thành (Done)"
            elif item["state"] == "claimed":
                state_badge = "★ Đã nhận quà (Claimed)"
            elif item["state"] == "failed":
                state_badge = "✖ Lỗi (Failed)"

            field_val = (
                f"{bar} `({pct}%)` · `{cur}/{tgt}` · Còn lại: `{cd_str}`\n"
                f"🏷️ `{ttype_vn}` | 🎁 **{reward}** | Trạng thái: **{state_badge}**"
            )
            embed.add_field(name=f"{icon} {name}", value=field_val, inline=False)

        elapsed = int(time.time() - self.start_time)
        m, s = divmod(elapsed, 60)
        embed.set_footer(text=f"Thời gian đã chạy: {m}m {s}s • Tự động nhận thưởng ngay khi hoàn thành")
        return embed

    async def safe_update_display(self):
        try:
            await self.message.edit(embed=self.build_live_embed())
        except discord.HTTPException as e:
            if e.status == 429:
                retry_after = getattr(e, "retry_after", 5.0) or 5.0
                await asyncio.sleep(retry_after)
            else:
                pass
        except Exception:
            pass

    async def finish(self, results: list[dict[str, Any]], final_orbs: Optional[int] = None):
        self.is_done = True
        if self.ticker_task:
            self.ticker_task.cancel()

        embed = discord.Embed(
            title=f"🎉 {self.title} · KẾT THÚC TIẾN TRÌNH",
            color=discord.Color.green(),
        )
        orbs_str = f"`{final_orbs:,}` Orbs" if final_orbs is not None else "Không xác định"
        embed.description = (
            f"👤 **Người dùng:** {self.user.mention} | 🔮 **Số dư Orbs hiện tại:** {orbs_str}\n"
            f"🏁 **Thời gian hoàn thành:** `{int(time.time() - self.start_time)}s`\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )

        for res in results:
            icon = res.get("icon", "🎯")
            name = res.get("name", "Nhiệm vụ")
            success = res.get("success", False)
            reward = res.get("reward", "Phần thưởng")
            note = res.get("note", "Thành công")

            status_str = "✔ **Hoàn thành & Đã Claim**" if success else f"✖ **Thất bại: {note}**"
            embed.add_field(
                name=f"{icon} {name}",
                value=f"{status_str}\n🎁 **Phần thưởng:** {reward}",
                inline=False,
            )

        embed.set_footer(text="Quests Engine đã hoàn tất toàn bộ chu trình an toàn.")
        try:
            await self.message.edit(embed=embed)
        except Exception:
            pass


# ==========================================
# INTERACTIVE VIEWS
# ==========================================

class ControlPanelView(ui.View):
    """Modern redesigned main control panel."""
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="🎯 Bật Auto Quests", style=discord.ButtonStyle.primary, row=0, emoji="⚡", custom_id="panel_btn_quests")
    async def btn_quests(self, interaction: discord.Interaction, button: ui.Button):
        await handle_auto_quests(interaction)

    @ui.button(label="🎮 Cày Giờ Game", style=discord.ButtonStyle.success, row=0, emoji="🕹️", custom_id="panel_btn_games")
    async def btn_games(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(FarmGamesModal())

    @ui.button(label="🎲 Tối Nay Lọ Gì", style=discord.ButtonStyle.secondary, row=0, emoji="🎰", custom_id="panel_btn_logi")
    async def btn_logi(self, interaction: discord.Interaction, button: ui.Button):
        actress = toinaylogi.default_engine.roll_actress()
        reel = toinaylogi.default_engine.generate_spin_reel(actress)
        embed = toinaylogi.build_actress_embed(actress, interaction.user)
        view = toinaylogi.ToiNayLoGiView(user_id=interaction.user.id)
        await interaction.response.send_message(content=f"🌀 *Đang quay hòm...*\n{reel}", embed=embed, view=view, ephemeral=False)

    @ui.button(label="⚙️ Cài Đặt Token", style=discord.ButtonStyle.secondary, row=1, emoji="🔑", custom_id="panel_btn_setup")
    async def btn_setup(self, interaction: discord.Interaction, button: ui.Button):
        state = get_user_state(interaction.user.id)
        await interaction.response.send_modal(UserSetupModal(current_state=state))

    @ui.button(label="🌐 Web Dashboard", style=discord.ButtonStyle.primary, row=1, emoji="💻", custom_id="panel_btn_web")
    async def btn_web(self, interaction: discord.Interaction, button: ui.Button):
        port = getattr(getattr(interaction.client, "dashboard", None), "port", 5000)
        web_url = f"http://localhost:{port}/?token={web_dashboard.ADMIN_SECRET}"
        embed = discord.Embed(
            title="🌐 Web Dashboard Quản Trị Bot",
            description=(
                f"Truy cập Dashboard trực tiếp trên trình duyệt:\n"
                f"🔗 **[Mở Web Dashboard Ngay]({web_url})**\n\n"
                f"• Cổng (Port): `{port}`\n"
                f"• Mã Token Admin: `{web_dashboard.ADMIN_SECRET}`\n"
                f"• Tính năng: Gửi thông báo @everyone bằng Embed, quản lý Quests, mở hòm Lọ Gì, xem Live Logs!"
            ),
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="📊 Thống Kê Cá Nhân", style=discord.ButtonStyle.secondary, row=1, emoji="📈", custom_id="panel_btn_profile")
    async def btn_profile(self, interaction: discord.Interaction, button: ui.Button):
        await handle_view_profile(interaction)

    @ui.button(label="🧹 Xóa Dữ Liệu", style=discord.ButtonStyle.danger, row=2, emoji="🗑️", custom_id="panel_btn_clear")
    async def btn_clear(self, interaction: discord.Interaction, button: ui.Button):
        user_id = interaction.user.id
        ok = clear_user_state(user_id)
        if ok:
            await interaction.response.send_message("✅ Đã xóa toàn bộ token và cookie của bạn khỏi hệ thống!", ephemeral=True)
        else:
            await interaction.response.send_message("ℹ️ Bạn chưa lưu cấu hình nào trước đó.", ephemeral=True)


class QuestControlView(ui.View):
    """View dedicated to Auto-Quests."""
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="⚡ Chạy Auto Flow Trọn Gói", style=discord.ButtonStyle.success, emoji="🚀")
    async def btn_auto(self, interaction: discord.Interaction, button: ui.Button):
        await handle_auto_quests(interaction)

    @ui.button(label="📋 Xem Danh Sách Quests", style=discord.ButtonStyle.primary, emoji="🔍")
    async def btn_list(self, interaction: discord.Interaction, button: ui.Button):
        await handle_list_quests(interaction)

    @ui.button(label="🎁 Nhận Toàn Bộ Quà (Claim)", style=discord.ButtonStyle.secondary, emoji="💎")
    async def btn_claim(self, interaction: discord.Interaction, button: ui.Button):
        await handle_claim_quests(interaction)


# ==========================================
# ACTION HANDLERS
# ==========================================

async def handle_auto_quests(interaction: discord.Interaction) -> None:
    """Executes Auto-Quests with minute-by-minute updates to avoid 429 rate limit."""
    user_id = interaction.user.id
    state = get_user_state(user_id)
    if not state.has_token:
        embed = discord.Embed(
            title="⚠️ Chưa Có Token Tài Khoản!",
            description="Vui lòng bấm nút **⚙️ Cài Đặt Token** hoặc gõ `/setup` để điền Token trước khi làm Quests.",
            color=discord.Color.gold(),
        )
        return await interaction.response.send_message(embed=embed, ephemeral=True)

    await interaction.response.defer(ephemeral=False)
    status_msg = await interaction.followup.send(
        embed=discord.Embed(
            title="🔍 Đang Quét Danh Sách Discord Quests...",
            description="Vui lòng đợi giây lát, hệ thống đang kết nối đến API Discord...",
            color=discord.Color.blurple(),
        )
    )

    store = quest_manager.QuestStore(state.token)
    ok, quests, err = await asyncio.to_thread(store.fetch_quests)
    if not ok:
        embed = discord.Embed(
            title="❌ Quét Quests Thất Bại",
            description=f"Lỗi: `{err}`\n*Vui lòng kiểm tra lại tính chính xác của User Token.*",
            color=discord.Color.red(),
        )
        return await status_msg.edit(embed=embed)

    pending = [q for q in quests if not q.is_completed()]
    if not pending:
        final_orbs = await asyncio.to_thread(quest_manager.get_orbs_balance, state.token)
        embed = discord.Embed(
            title="🎉 Tất Cả Quests Đã Hoàn Thành!",
            description=(
                f"👤 {interaction.user.mention}\n"
                f"🔮 **Số dư Orbs:** `{final_orbs:,}` Orbs\n"
                f"Không còn nhiệm vụ nào cần làm ở thời điểm hiện tại!"
            ),
            color=discord.Color.green(),
        )
        return await status_msg.edit(embed=embed)

    # Enroll in quests
    await asyncio.to_thread(store.enroll_all)

    # Initialize Public Progress Tracker with minute interval
    tracker = PublicQuestProgressTracker(status_msg, interaction.user, interaction.channel)
    tracker.orbs = await asyncio.to_thread(quest_manager.get_orbs_balance, state.token)
    tracker.live_items = [
        {
            "id": q.id,
            "name": q.name,
            "task_icon": quest_manager.task_icon(q.detect_task_type()),
            "task_type": q.detect_task_type(),
            "reward": q.get_reward_label(),
            "current": q.get_progress(),
            "target": q.get_target(),
            "percent": int((q.get_progress() / q.get_target()) * 100) if q.get_target() > 0 else 0,
            "bar": quest_manager.make_progress_bar(q.get_progress(), q.get_target()),
            "countdown": q.get_remaining(),
            "state": "working",
        }
        for q in pending
    ]

    tracker.start()
    await tracker.safe_update_display()

    # Execute quests sequentially
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

        tracker.results.append({
            "icon": quest_manager.task_icon(q.detect_task_type()),
            "name": q.name,
            "success": success,
            "reward": q.get_reward_label(),
            "note": note,
        })

    final_orbs = await asyncio.to_thread(quest_manager.get_orbs_balance, state.token)
    await tracker.finish(tracker.results, final_orbs=final_orbs)


async def handle_list_quests(interaction: discord.Interaction) -> None:
    user_id = interaction.user.id
    state = get_user_state(user_id)
    if not state.has_token:
        return await interaction.response.send_message("⚠️ Vui lòng lưu Token trước qua `/setup`!", ephemeral=True)

    await interaction.response.defer(ephemeral=False)
    ok, quests, err = await asyncio.to_thread(quest_manager.fetch_all_quests, state.token)
    if not ok:
        return await interaction.followup.send(f"❌ Không thể tải danh sách quests: `{err}`", ephemeral=False)

    orbs = await asyncio.to_thread(quest_manager.get_orbs_balance, state.token)
    embed = discord.Embed(
        title="📋 Danh Sách Discord Quests Của Bạn",
        description=f"🔮 **Số dư Orbs:** `{orbs:,}` Orbs\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        color=discord.Color.blurple(),
    )

    if not quests:
        embed.description += "\nHiện không có nhiệm vụ nào khả dụng."
    else:
        for q in quests[:10]:
            icon = quest_manager.task_icon(q.detect_task_type())
            ttype_vn = quest_manager.task_label_vn(q.detect_task_type())
            status = "✔ Đã xong & Nhận quà" if q.is_completed() else f"⏳ Tiến độ: `{int(q.get_progress())}/{int(q.get_target())}`"
            embed.add_field(
                name=f"{icon} {q.name}",
                value=f"• Loại: `{ttype_vn}`\n• Quà: **{q.get_reward_label()}**\n• Trạng thái: {status}",
                inline=False,
            )

    await interaction.followup.send(embed=embed, ephemeral=False)


async def handle_claim_quests(interaction: discord.Interaction) -> None:
    user_id = interaction.user.id
    state = get_user_state(user_id)
    if not state.has_token:
        return await interaction.response.send_message("⚠️ Vui lòng lưu Token trước qua `/setup`!", ephemeral=True)

    await interaction.response.defer(ephemeral=False)
    store = quest_manager.QuestStore(state.token)
    await asyncio.to_thread(store.fetch_quests)
    claimed_count = await asyncio.to_thread(store.claim_all)
    orbs = await asyncio.to_thread(quest_manager.get_orbs_balance, state.token)

    embed = discord.Embed(
        title="🎁 Nhận Quà Discord Quests",
        description=(
            f"✅ Đã kiểm tra và nhận thưởng thành công cho **{claimed_count}** nhiệm vụ!\n"
            f"🔮 **Số dư Orbs hiện tại:** `{orbs:,}` Orbs"
        ),
        color=discord.Color.green(),
    )
    await interaction.followup.send(embed=embed, ephemeral=False)


async def run_farm_games(interaction: discord.Interaction, count: int, hours: float) -> None:
    user_id = interaction.user.id
    state = get_user_state(user_id)
    if not state.has_token:
        return await interaction.response.send_message("❌ Chưa có User Token! Vui lòng lưu qua `/setup`.", ephemeral=True)
    if not state.has_cookie:
        return await interaction.response.send_message("❌ Cày giờ game cần cookie `cf_clearance`. Vui lòng cập nhật qua `/setup`.", ephemeral=True)

    await interaction.response.defer(ephemeral=False)

    user_file = get_user_state_file(user_id)
    controller = ApplicationController(session_path=user_file)
    controller.state.token = state.token
    controller.state.cookie = state.cookie

    res = await asyncio.to_thread(controller.start_headless, games_count=count, hours=hours)
    if not res.get("success"):
        return await interaction.followup.send(f"❌ Cày game thất bại: `{res.get('error')}`", ephemeral=False)

    fresh_state = get_user_state(user_id)
    accepted = res.get("accepted", 0)
    total_hours = res.get("total_hours", 0.0)

    embed = discord.Embed(
        title="🎮 Cày Giờ Chơi Game Thành Công!",
        description=(
            f"👤 **Người dùng:** {interaction.user.mention}\n"
            f"🕹️ **Số game đã gửi:** `{accepted}/{count}` games\n"
            f"⏱️ **Thời gian mỗi game:** `{hours:g}h`\n"
            f"📈 **Tổng giờ vừa cộng:** `+{total_hours:,.0f}h`\n"
            f"🏆 **Tổng tích lũy:** `{fresh_state.total_hours_claimed:,.0f}h` trên `{fresh_state.total_games_claimed}` games\n\n"
            "*(Discord Analytics sẽ đồng bộ badge sau 24-48 giờ)*"
        ),
        color=discord.Color.green(),
    )
    await interaction.followup.send(embed=embed, ephemeral=False)


async def handle_view_profile(interaction: discord.Interaction) -> None:
    user_id = interaction.user.id
    state = get_user_state(user_id)
    if not state.has_token:
        return await interaction.response.send_message("❌ Vui lòng lưu Token trước qua `/setup`!", ephemeral=True)

    await interaction.response.defer(ephemeral=False)

    try:
        req = urllib.request.Request(
            f"{DISCORD_API_BASE}/users/@me",
            headers={"Authorization": state.token, "User-Agent": USER_AGENT},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            me_data = json.loads(resp.read().decode("utf-8"))
            uid = me_data.get("id")
            uname = me_data.get("username", "Unknown")
            avatar = me_data.get("avatar")
            avatar_url = f"https://cdn.discordapp.com/avatars/{uid}/{avatar}.png" if avatar else None
    except Exception as e:
        return await interaction.followup.send(f"❌ Không thể đọc profile từ Discord API: `{e}`", ephemeral=False)

    embed = discord.Embed(
        title=f"👤 Hồ Sơ Tài Khoản: @{uname}",
        color=discord.Color.blurple(),
    )
    if avatar_url:
        embed.set_thumbnail(url=avatar_url)

    embed.add_field(name="User ID", value=f"`{uid}`", inline=True)
    embed.add_field(name="Game Hours Đã Cày", value=f"`{state.total_hours_claimed:,.0f}h`", inline=True)
    embed.add_field(name="Số Game Đã Tích Lũy", value=f"`{state.total_games_claimed}` games", inline=True)
    embed.set_footer(text="Discord Badge Maximizer • Bảo mật DES+AES")

    await interaction.followup.send(embed=embed, ephemeral=False)


# ==========================================
# BOT CLASS & SLASH COMMANDS
# ==========================================

class MaximizerBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)
        self.dashboard: Optional[web_dashboard.WebDashboard] = None

    async def setup_hook(self) -> None:
        # Start Web Dashboard
        self.dashboard = web_dashboard.WebDashboard(self)
        await self.dashboard.start()

    async def close(self) -> None:
        if self.dashboard:
            await self.dashboard.stop()
        await super().close()

    async def on_ready(self) -> None:
        print("\n" + "=" * 60)
        print(f"🤖 Bot đã đăng nhập thành công với tên: {self.user} (ID: {self.user.id})")
        print(f"🌐 Máy chủ đang tham gia: {len(self.guilds)}")
        print(f"👑 Admin ID: {PRIMARY_ADMIN_ID}")
        print("=" * 60 + "\n")
        web_dashboard.push_log("INFO", f"Bot {self.user} đã sẵn sàng trên {len(self.guilds)} server!")


bot = MaximizerBot()
tree = bot.tree

# Command groups
quest_group = app_commands.Group(name="quest", description="🎯 Tự động hoàn thành Discord Quests")
toinaylogi_group = app_commands.Group(name="toinaylogi", description="🎰 Tối Nay Lọ Gì CS:GO Case Opening Gacha")
admin_group = app_commands.Group(name="admin", description="👑 Lệnh quản trị Bot Admin")

tree.add_command(quest_group)
tree.add_command(toinaylogi_group)
tree.add_command(admin_group)


# --- GENERAL SLASH COMMANDS ---

@tree.command(name="setup", description="Mở Popup cài đặt User Token & Cookie cá nhân")
async def setup_cmd(interaction: discord.Interaction):
    state = get_user_state(interaction.user.id)
    await interaction.response.send_modal(UserSetupModal(current_state=state))


@tree.command(name="panel", description="Mở Bảng Điều Khiển tương tác với các nút bấm")
async def panel_cmd(interaction: discord.Interaction):
    user_id = interaction.user.id
    state = get_user_state(user_id)

    port = getattr(bot.dashboard, "port", 5000)
    web_url = f"http://localhost:{port}/?token={web_dashboard.ADMIN_SECRET}"

    embed = discord.Embed(
        title="⚡ BẢNG ĐIỀU KHIỂN · DISCORD MAXIMIZER HUB",
        description=(
            f"Chào mừng {interaction.user.mention} đến với trung tâm điều khiển Bot!\n"
            "Chọn các nút bấm bên dưới để thực hiện nhanh tác vụ:\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="🔑 Trạng Thái Token",
        value=f"{'🟢 Đã cài đặt' if state.has_token else '🔴 Chưa cài đặt'}\n`{mask_token(state.token)}`",
        inline=True,
    )
    embed.add_field(
        name="🎮 Giờ Game Đã Cày",
        value=f"**{state.total_hours_claimed:,.0f}h** ({state.total_games_claimed} games)",
        inline=True,
    )
    embed.add_field(
        name="🌐 Web Dashboard",
        value=f"**[Mở Trình Duyệt]({web_url})**\nCổng: `{port}`",
        inline=True,
    )

    embed.set_footer(text="Quests cập nhật theo phút để chống 429 Rate-limit Discord!")
    view = ControlPanelView()
    await interaction.response.send_message(embed=embed, view=view)


@tree.command(name="farm_games", description="Cày thời gian chơi game cho tài khoản")
@app_commands.describe(
    count="Số lượng game cần cày (Ví dụ: 50)",
    hours="Số giờ cho mỗi game (Ví dụ: 1.0)",
)
async def farm_games_cmd(interaction: discord.Interaction, count: Optional[int] = None, hours: Optional[float] = None):
    if count is None and hours is None:
        await interaction.response.send_modal(FarmGamesModal())
    else:
        c = count if count is not None else 50
        h = hours if hours is not None else 1.0
        await run_farm_games(interaction, c, h)


@tree.command(name="profile", description="Kiểm tra thông tin tài khoản và danh sách huy hiệu của bạn")
async def profile_cmd(interaction: discord.Interaction):
    await handle_view_profile(interaction)


@tree.command(name="config", description="Xem cấu hình hiện tại của bạn")
async def config_cmd(interaction: discord.Interaction):
    user_id = interaction.user.id
    state = get_user_state(user_id)
    embed = discord.Embed(title="⚙️ Cấu Hình Cá Nhân", color=discord.Color.blurple())
    embed.add_field(name="Token Tài Khoản", value=mask_token(state.token), inline=True)
    embed.add_field(name="Cookie cf_clearance", value="`Đã cấu hình`" if state.has_cookie else "*(Chưa điền)*", inline=True)
    embed.add_field(name="Tổng Giờ Game Đã Cày", value=f"`{state.total_hours_claimed:,.0f}h`", inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@tree.command(name="clear_config", description="Xóa toàn bộ token và cookie của bạn khỏi bot")
async def clear_config_cmd(interaction: discord.Interaction):
    ok = clear_user_state(interaction.user.id)
    if ok:
        await interaction.response.send_message("✅ Đã xóa toàn bộ dữ liệu cấu hình của bạn!", ephemeral=True)
    else:
        await interaction.response.send_message("ℹ️ Bạn chưa lưu cấu hình nào trước đó.", ephemeral=True)


@tree.command(name="help", description="Xem hướng dẫn toàn bộ lệnh và Web UI")
async def help_cmd(interaction: discord.Interaction):
    port = getattr(bot.dashboard, "port", 5000)
    web_url = f"http://localhost:{port}/?token={web_dashboard.ADMIN_SECRET}"

    embed = discord.Embed(
        title="📖 HƯỚNG DẪN SỬ DỤNG · DISCORD MAXIMIZER BOT",
        description=(
            "Bot hỗ trợ tự động Quests, Cày Game Playtime, Mở Hòm Lọ Gì và Bảng Điều Khiển Web UI.\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="🎯 Discord Auto-Quests",
        value=(
            "`/quest auto` - Chạy auto flow Quests trọn gói (Cập nhật theo phút chống rate-limit)\n"
            "`/quest list` - Xem danh sách nhiệm vụ hiện có & số dư Orbs\n"
            "`/quest claim` - Nhận thưởng toàn bộ nhiệm vụ đã hoàn thành\n"
            "`/quest panel` - Mở bảng điều khiển Quests riêng biệt"
        ),
        inline=False,
    )
    embed.add_field(
        name="🎰 Tối Nay Lọ Gì (zennomi/toinaylogi)",
        value=(
            "`/toinaylogi quay` - Mở 1 hòm CS:GO gacha ngẫu nhiên Idol\n"
            "`/toinaylogi x10` - Mở liền tay 10 hòm gacha\n"
            "`/toinaylogi catalog` - Xem kho diễn viên & mã phim theo Tier\n"
            "`/toinaylogi ti-le` - Xem bảng tỉ lệ rơi hòm"
        ),
        inline=False,
    )
    embed.add_field(
        name="🎮 Cày Giờ Chơi Game",
        value="`/farm_games [count] [hours]` - Tự động cày playtime Discord",
        inline=False,
    )
    embed.add_field(
        name="🌐 Web Dashboard & Admin",
        value=(
            f"🔗 **[Mở Web Dashboard]({web_url})** - Soạn Embed & Tag @everyone, Quản lý server, Live Logs\n"
            "`/admin web` - Lấy link đăng nhập Web Dashboard\n"
            "`/admin broadcast` - Gửi thông báo kèm Embed từ Discord\n"
            "`/admin panel` - Bảng điều khiển admin"
        ),
        inline=False,
    )
    await interaction.response.send_message(embed=embed, ephemeral=False)


# --- QUEST SUBCOMMANDS ---

@quest_group.command(name="panel", description="Mở Bảng Điều Khiển Discord Quests")
async def quest_panel_cmd(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🎯 TRUNG TÂM DISCORD AUTO-QUESTS",
        description=(
            "Hệ thống hỗ trợ Video Quests, Desktop Heartbeat & Console Gameplay.\n"
            "⏱️ **Tiến trình cập nhật mỗi 1 phút để chống rate-limit Discord.**"
        ),
        color=discord.Color.blurple(),
    )
    view = QuestControlView()
    await interaction.response.send_message(embed=embed, view=view)


@quest_group.command(name="auto", description="Tự động quét, nhận và làm nhiệm vụ Quests (Cập nhật mỗi 1 phút)")
async def quest_auto_cmd(interaction: discord.Interaction):
    await handle_auto_quests(interaction)


@quest_group.command(name="list", description="Xem danh sách tất cả Quests hiện có và số dư Orbs")
async def quest_list_cmd(interaction: discord.Interaction):
    await handle_list_quests(interaction)


@quest_group.command(name="claim", description="Nhận phần thưởng cho các nhiệm vụ đã hoàn thành")
async def quest_claim_cmd(interaction: discord.Interaction):
    await handle_claim_quests(interaction)


@quest_group.command(name="orbs", description="Kiểm tra số dư Discord Orbs")
async def quest_orbs_cmd(interaction: discord.Interaction):
    state = get_user_state(interaction.user.id)
    if not state.has_token:
        return await interaction.response.send_message("⚠️ Vui lòng lưu Token trước qua `/setup`!", ephemeral=True)

    await interaction.response.defer(ephemeral=False)
    orbs = await asyncio.to_thread(quest_manager.get_orbs_balance, state.token)
    embed = discord.Embed(
        title="🔮 Số Dư Discord Orbs",
        description=f"Số dư hiện tại của bạn: **{orbs:,} Orbs**",
        color=discord.Color.purple(),
    )
    await interaction.followup.send(embed=embed, ephemeral=False)


# --- TOI NAY LO GI SUBCOMMANDS ---

@toinaylogi_group.command(name="quay", description="Mở 1 hòm CS:GO gacha ngẫu nhiên Idol & Mã phim")
async def toinaylogi_quay_cmd(interaction: discord.Interaction):
    actress = toinaylogi.default_engine.roll_actress()
    reel = toinaylogi.default_engine.generate_spin_reel(actress)
    embed = toinaylogi.build_actress_embed(actress, interaction.user)
    view = toinaylogi.ToiNayLoGiView(user_id=interaction.user.id)
    await interaction.response.send_message(content=f"🌀 *Đang quay hòm...*\n{reel}", embed=embed, view=view)


@toinaylogi_group.command(name="x10", description="Mở liền tay 10 hòm gacha Tối Nay Lọ Gì")
async def toinaylogi_x10_cmd(interaction: discord.Interaction):
    actresses = toinaylogi.default_engine.roll_multi(10)
    embed = toinaylogi.build_multi_embed(actresses, interaction.user)
    view = toinaylogi.ToiNayLoGiView(user_id=interaction.user.id)
    await interaction.response.send_message(content="🎉 **MỞ LIỀN TAY 10 HÒM THÀNH CÔNG!**", embed=embed, view=view)


@toinaylogi_group.command(name="catalog", description="Xem danh sách diễn viên & mã phim theo Tier")
@app_commands.describe(tier="Chọn phẩm cấp cần lọc")
@app_commands.choices(tier=[
    app_commands.Choice(name="💎 Mythic (1%)", value=4),
    app_commands.Choice(name="👑 Legendary (4%)", value=3),
    app_commands.Choice(name="⭐ Epic (10%)", value=2),
    app_commands.Choice(name="🔹 Rare (25%)", value=1),
    app_commands.Choice(name="⚪ Common (60%)", value=0),
])
async def toinaylogi_catalog_cmd(interaction: discord.Interaction, tier: Optional[app_commands.Choice[int]] = None):
    t_val = tier.value if tier else toinaylogi.TIER_MYTHIC
    items = toinaylogi.default_engine.get_by_tier(t_val)
    tinfo = toinaylogi.TIER_INFO[t_val]

    embed = discord.Embed(
        title=f"{tinfo['icon']} KHO DIỄN VIÊN · {tinfo['name']}",
        description=f"Tỉ lệ rơi: **{toinaylogi.TIER_DROP_RATES[t_val]*100:.1f}%** | Tổng: **{len(items)}** idol\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        color=tinfo["color"],
    )
    for a in items[:12]:
        embed.add_field(
            name=f"{a.name} ({a.native_name})",
            value=f"Cup **{a.cup}** | Mã: [`{a.best_code}`](https://jav.guru/?s={a.best_code})\n*{a.quote}*",
            inline=True,
        )
    await interaction.response.send_message(embed=embed, ephemeral=False)


@toinaylogi_group.command(name="ti-le", description="Xem bảng tỉ lệ phẩm cấp và độ hiếm")
async def toinaylogi_tile_cmd(interaction: discord.Interaction):
    embed = toinaylogi.build_tier_rates_embed()
    await interaction.response.send_message(embed=embed, ephemeral=False)


# --- ADMIN SUBCOMMANDS ---

@admin_group.command(name="web", description="[Admin] Lấy link và token truy cập Web Dashboard")
async def admin_web_cmd(interaction: discord.Interaction):
    if not is_admin(interaction.user.id):
        return await interaction.response.send_message("⛔ Lệnh này chỉ dành riêng cho Bot Admin!", ephemeral=True)

    port = getattr(bot.dashboard, "port", 5000)
    web_url = f"http://localhost:{port}/?token={web_dashboard.ADMIN_SECRET}"

    embed = discord.Embed(
        title="🔐 Web Dashboard · Thông Tin Quản Trị",
        description=(
            f"🔗 **Link truy cập Web UI:**\n**[Nhấn vào đây để mở Web Dashboard]({web_url})**\n\n"
            f"• Cổng đang chạy: `{port}`\n"
            f"• Admin Secret Token: `{web_dashboard.ADMIN_SECRET}`\n"
            f"• Quyền hạn: Toàn quyền gửi thông báo @everyone bằng Embed, điều khiển Quests, Games & xem Live Logs!"
        ),
        color=discord.Color.gold(),
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@admin_group.command(name="broadcast", description="[Admin] Mở Popup gửi thông báo kèm Embed và tag @everyone")
async def admin_broadcast_cmd(interaction: discord.Interaction):
    if not is_admin(interaction.user.id):
        return await interaction.response.send_message("⛔ Lệnh này chỉ dành riêng cho Bot Admin!", ephemeral=True)
    await interaction.response.send_modal(BroadcastModal())


@admin_group.command(name="sync", description="[Admin] Buộc đồng bộ toàn bộ Slash Commands")
async def admin_sync_cmd(interaction: discord.Interaction):
    if not is_admin(interaction.user.id):
        return await interaction.response.send_message("⛔ Lệnh này chỉ dành riêng cho Bot Admin!", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    synced = await tree.sync()
    await interaction.followup.send(f"✅ Đã đồng bộ thành công `{len(synced)}` lệnh Slash!", ephemeral=True)


@admin_group.command(name="stats", description="[Admin] Xem thống kê toàn bộ hệ thống Bot")
async def admin_stats_cmd(interaction: discord.Interaction):
    if not is_admin(interaction.user.id):
        return await interaction.response.send_message("⛔ Lệnh này chỉ dành riêng cho Bot Admin!", ephemeral=True)

    all_users = list_all_users()
    total_hours = 0.0
    total_games = 0
    for uid in all_users:
        st = get_user_state(uid)
        total_hours += st.total_hours_claimed
        total_games += st.total_games_claimed

    embed = discord.Embed(
        title="📊 Thống Kê Hệ Thống · Discord Maximizer",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Số Server", value=f"`{len(bot.guilds)}` guilds", inline=True)
    embed.add_field(name="Số Thành Viên", value=f"`{sum(g.member_count or 0 for g in bot.guilds):,}` users", inline=True)
    embed.add_field(name="User Đã Lưu Token", value=f"`{len(all_users)}` users", inline=True)
    embed.add_field(name="Tổng Giờ Game Đã Cày", value=f"`{total_hours:,.0f}h`", inline=True)
    embed.add_field(name="Tổng Game Đã Farm", value=f"`{total_games:,}` games", inline=True)
    embed.add_field(name="Độ Trễ (Ping)", value=f"`{round(bot.latency * 1000, 1)} ms`", inline=True)

    await interaction.response.send_message(embed=embed, ephemeral=True)


@admin_group.command(name="quests_auto_all", description="[Admin] Chạy auto Quests cho tất cả users đã lưu Token")
async def admin_quests_auto_all_cmd(interaction: discord.Interaction):
    if not is_admin(interaction.user.id):
        return await interaction.response.send_message("⛔ Lệnh này chỉ dành riêng cho Bot Admin!", ephemeral=True)

    all_users = list_all_users()
    if not all_users:
        return await interaction.response.send_message("ℹ️ Hiện chưa có user nào lưu token trong hệ thống.", ephemeral=True)

    await interaction.response.defer(ephemeral=True)

    async def _run_bg():
        for uid in all_users:
            st = get_user_state(uid)
            if st.has_token:
                try:
                    store = quest_manager.QuestStore(st.token)
                    await asyncio.to_thread(store.fetch_quests)
                    await asyncio.to_thread(store.enroll_all)
                    for q in store.quests:
                        if not q.is_completed():
                            await asyncio.to_thread(store.execute_quest, q, None)
                    await asyncio.to_thread(store.claim_all)
                except Exception:
                    pass

    asyncio.create_task(_run_bg())
    await interaction.followup.send(f"⚡ Đã khởi động luồng Auto-Quests chạy nền cho `{len(all_users)}` users!", ephemeral=True)


@admin_group.command(name="reset_user", description="[Admin] Xóa dữ liệu của một user cụ thể")
@app_commands.describe(user="Chọn user cần reset")
async def admin_reset_user_cmd(interaction: discord.Interaction, user: discord.User):
    if not is_admin(interaction.user.id):
        return await interaction.response.send_message("⛔ Lệnh này chỉ dành riêng cho Bot Admin!", ephemeral=True)
    ok = clear_user_state(user.id)
    if ok:
        await interaction.response.send_message(f"✅ Đã xóa toàn bộ cấu hình của user {user.mention}!", ephemeral=True)
    else:
        await interaction.response.send_message(f"ℹ️ User {user.mention} chưa có dữ liệu lưu trữ.", ephemeral=True)


# ==========================================
# MAIN EXECUTION ENTRYPOINT
# ==========================================

def main() -> None:
    token = os.environ.get("DISCORD_BOT_TOKEN") or os.environ.get("BOT_TOKEN")
    if not token:
        print("\n❌ LỖI: Chưa cấu hình DISCORD_BOT_TOKEN trong file .env!")
        print("Vui lòng mở file .env và điền: DISCORD_BOT_TOKEN=your_token_here\n")
        sys.exit(1)

    try:
        bot.run(token)
    except KeyboardInterrupt:
        print("\n🛑 Đã nhận tín hiệu dừng Bot từ bàn phím.")
    except Exception as exc:
        print(f"\n❌ Lỗi khởi động Bot: {exc}")


if __name__ == "__main__":
    main()
