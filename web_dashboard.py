#!/usr/bin/env python3
"""
Web Dashboard Module - Discord Bot Master Control
Provides an integrated asynchronous web dashboard using aiohttp.web running
directly inside the discord.py asyncio event loop.
Features:
  - Announcement & Broadcast Center with live Discord Embed Builder
  - Tag @everyone, @here, or roles with embed
  - Discord Auto-Quests management & live minute-by-minute tracking
  - Tối Nay Lọ Gì CS:GO case opening gacha & actress catalog
  - Real-time bot stats, server explorer, and console logs
"""

from __future__ import annotations

import asyncio
import collections
import datetime
import json
import logging
import os
import secrets
import sys
import time
from pathlib import Path
from typing import Any, Optional

import discord
from aiohttp import web
from discord.ext import commands

import quest_manager
import toinaylogi

# Root and Web directories
ROOT_DIR = Path(__file__).resolve().parent
WEB_DIR = ROOT_DIR / "web"
INDEX_HTML = WEB_DIR / "index.html"

# Admin Secret Token
ADMIN_SECRET = os.environ.get("ADMIN_SECRET") or os.environ.get("WEB_SECRET") or secrets.token_hex(16)

# In-memory log buffer for live console streaming
LOG_BUFFER: collections.deque[dict[str, Any]] = collections.deque(maxlen=1000)


def push_log(level: str, message: str) -> None:
    now_str = datetime.datetime.now().strftime("%H:%M:%S")
    LOG_BUFFER.append({
        "time": now_str,
        "level": level.upper(),
        "msg": str(message),
    })


# Custom logging handler to redirect logger messages into LOG_BUFFER
class BufferLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            level = record.levelname
            push_log(level, msg)
        except Exception:
            pass


class WebDashboard:
    def __init__(self, bot: commands.Bot, host: str = "0.0.0.0", port: int = 5000):
        self.bot = bot
        self.host = host
        self.port = int(os.environ.get("PORT") or os.environ.get("WEB_PORT") or port)
        self.app = web.Application(middlewares=[self.auth_middleware])
        self.runner: Optional[web.AppRunner] = None
        self.site: Optional[web.TCPSite] = None
        self.start_time = time.time()
        self.active_quest_tasks: dict[str, Any] = {}

        self.setup_routes()

    @web.middleware
    async def auth_middleware(self, request: web.Request, handler: Any) -> web.Response:
        path = request.path
        # Allow static index page & login endpoint without auth
        if path == "/" or path.startswith("/static") or path == "/api/login":
            return await handler(request)

        # Verify token via Authorization header or query param or cookie
        auth_header = request.headers.get("Authorization", "")
        token = ""
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
        elif "token" in request.query:
            token = request.query["token"].strip()
        elif "bot_admin_token" in request.cookies:
            token = request.cookies["bot_admin_token"]

        if not token or token != ADMIN_SECRET:
            return web.json_response({"detail": "Unauthorized: Invalid or missing admin token"}, status=401)

        return await handler(request)

    def setup_routes(self) -> None:
        # Static & Views
        self.app.router.add_get("/", self.handle_index)

        # Auth & Meta
        self.app.router.add_get("/api/me", self.api_me)
        self.app.router.add_post("/api/login", self.api_login)
        self.app.router.add_get("/api/stats", self.api_stats)
        self.app.router.add_get("/api/logs", self.api_logs)

        # Discord Controls
        self.app.router.add_get("/api/guilds", self.api_guilds)
        self.app.router.add_get("/api/guilds/{guild_id}/channels", self.api_guild_channels)
        self.app.router.add_post("/api/broadcast", self.api_broadcast)
        self.app.router.add_post("/api/admin/sync_commands", self.api_sync_commands)

        # Quests Controls
        self.app.router.add_post("/api/quests/scan", self.api_quests_scan)
        self.app.router.add_post("/api/quests/start", self.api_quests_start)

        # Tối Nay Lọ Gì Controls
        self.app.router.add_get("/api/toinaylogi/roll", self.api_toinaylogi_roll)
        self.app.router.add_get("/api/toinaylogi/catalog", self.api_toinaylogi_catalog)

    async def handle_index(self, request: web.Request) -> web.Response:
        if INDEX_HTML.exists():
            content = INDEX_HTML.read_text(encoding="utf-8")
            return web.Response(text=content, content_type="text/html")
        return web.Response(text="<h1>Web UI template index.html not found</h1>", content_type="text/html", status=404)

    async def api_me(self, request: web.Request) -> web.Response:
        return web.json_response({
            "ok": True,
            "admin": True,
            "bot_user": str(self.bot.user) if self.bot.user else "Discord Bot",
        })

    async def api_login(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
            token = data.get("token", "").strip()
            if token == ADMIN_SECRET:
                resp = web.json_response({"ok": True, "message": "Login successful"})
                resp.set_cookie("bot_admin_token", token, max_age=86400 * 30, httponly=True)
                return resp
            return web.json_response({"ok": False, "detail": "Sai mã bí mật Admin!"}, status=403)
        except Exception as e:
            return web.json_response({"ok": False, "detail": str(e)}, status=400)

    async def api_stats(self, request: web.Request) -> web.Response:
        uptime_sec = int(time.time() - self.start_time)
        hours, rem = divmod(uptime_sec, 3600)
        minutes, seconds = divmod(rem, 60)
        uptime_str = f"{hours}h {minutes}m"

        guild_count = len(self.bot.guilds)
        user_count = sum(g.member_count or 0 for g in self.bot.guilds)
        latency_ms = round(self.bot.latency * 1000, 1) if self.bot.latency and not (self.bot.latency != self.bot.latency) else 0

        return web.json_response({
            "bot_name": self.bot.user.name if self.bot.user else "Maximizer Bot",
            "bot_id": str(self.bot.user.id) if self.bot.user else "",
            "latency_ms": latency_ms,
            "uptime": uptime_str,
            "guild_count": guild_count,
            "user_count": user_count,
            "active_quests": len(self.active_quest_tasks),
        })

    async def api_logs(self, request: web.Request) -> web.Response:
        return web.json_response(list(LOG_BUFFER))

    async def api_guilds(self, request: web.Request) -> web.Response:
        guilds = []
        for g in self.bot.guilds:
            guilds.append({
                "id": str(g.id),
                "name": g.name,
                "member_count": g.member_count,
                "icon_url": g.icon.url if g.icon else None,
            })
        return web.json_response(guilds)

    async def api_guild_channels(self, request: web.Request) -> web.Response:
        guild_id_str = request.match_info.get("guild_id", "")
        if not guild_id_str.isdigit():
            return web.json_response([], status=400)

        guild = self.bot.get_guild(int(guild_id_str))
        if not guild:
            return web.json_response([], status=404)

        channels = []
        for c in guild.text_channels:
            perms = c.permissions_for(guild.me)
            if perms.send_messages:
                channels.append({"id": str(c.id), "name": c.name})

        return web.json_response(channels)

    async def api_broadcast(self, request: web.Request) -> web.Response:
        """
        Sends announcement embed into Discord with optional @everyone / @here / role tags.
        """
        try:
            payload = await request.json()
            channel_id_str = payload.get("channel_id")
            if not channel_id_str or not str(channel_id_str).isdigit():
                return web.json_response({"detail": "Channel ID không hợp lệ"}, status=400)

            channel = self.bot.get_channel(int(channel_id_str))
            if not channel or not isinstance(channel, discord.TextChannel):
                return web.json_response({"detail": "Không tìm thấy kênh văn bản hoặc bot thiếu quyền"}, status=404)

            # Build tag content
            mention_type = payload.get("mention", "none")
            content = None
            if mention_type == "everyone":
                content = "@everyone"
            elif mention_type == "here":
                content = "@here"
            elif mention_type == "role":
                role_val = payload.get("custom_mention", "").strip()
                if role_val.isdigit():
                    content = f"<@&{role_val}>"
                elif role_val:
                    content = f"@{role_val}"

            # Build Discord Embed
            edata = payload.get("embed", {})
            title = edata.get("title", "").strip() or None
            description = edata.get("description", "").strip() or None

            # Parse color
            color_val = discord.Color.blurple()
            c_str = edata.get("color", "")
            if c_str and c_str.startswith("#") and len(c_str) == 7:
                try:
                    color_val = discord.Color(int(c_str[1:], 16))
                except Exception:
                    pass

            embed = discord.Embed(
                title=title,
                description=description,
                color=color_val,
            )

            # Author
            author_data = edata.get("author") or {}
            author_name = author_data.get("name", "").strip()
            if author_name:
                embed.set_author(name=author_name)

            # Thumbnail
            thumb_url = edata.get("thumbnail", "").strip()
            if thumb_url and (thumb_url.startswith("http://") or thumb_url.startswith("https://")):
                embed.set_thumbnail(url=thumb_url)

            # Image
            img_url = edata.get("image", "").strip()
            if img_url and (img_url.startswith("http://") or img_url.startswith("https://")):
                embed.set_image(url=img_url)

            # Footer
            footer_data = edata.get("footer") or {}
            footer_text = footer_data.get("text", "").strip()
            if footer_text:
                embed.set_footer(text=footer_text)

            # Timestamp
            if edata.get("timestamp"):
                embed.timestamp = discord.utils.utcnow()

            # Fields
            fields = edata.get("fields") or []
            for f in fields:
                fname = f.get("name", "").strip()
                fval = f.get("value", "").strip()
                if fname and fval:
                    embed.add_field(name=fname, value=fval, inline=f.get("inline", True))

            # Send message
            msg = await channel.send(content=content, embed=embed)
            push_log("SUCCESS", f"📢 [Web Broadcast] Đã gửi thông báo đến #{channel.name} ({channel.guild.name}) bởi Admin!")

            return web.json_response({
                "ok": True,
                "message_id": str(msg.id),
                "jump_url": msg.jump_url,
            })
        except Exception as e:
            push_log("ERROR", f"❌ [Web Broadcast Error] {str(e)}")
            return web.json_response({"detail": str(e)}, status=500)

    async def api_sync_commands(self, request: web.Request) -> web.Response:
        try:
            synced = await self.bot.tree.sync()
            push_log("SUCCESS", f"🔄 [Slash Sync] Đã đồng bộ {len(synced)} lệnh Slash từ Web UI.")
            return web.json_response({"ok": True, "synced_count": len(synced)})
        except Exception as e:
            push_log("ERROR", f"❌ [Slash Sync Error] {str(e)}")
            return web.json_response({"detail": str(e)}, status=500)

    async def api_quests_scan(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
            token = data.get("token", "").strip()
            if not token:
                return web.json_response({"detail": "Token không được để trống"}, status=400)

            ok, quests, msg = await asyncio.to_thread(quest_manager.fetch_all_quests, token)
            if not ok:
                return web.json_response({"detail": msg}, status=400)

            orbs = await asyncio.to_thread(quest_manager.get_orbs_balance, token)
            quest_list = []
            for q in quests:
                quest_list.append({
                    "id": q.id,
                    "name": q.name,
                    "task_type": q.detect_task_type(),
                    "reward": q.get_reward_label(),
                    "completed": q.is_completed(),
                    "progress": q.get_progress(),
                    "target": q.get_target(),
                })

            push_log("INFO", f"🎯 [Quests Scan] Đã quét {len(quests)} quests. Orbs: {orbs}")
            return web.json_response({
                "ok": True,
                "orbs": orbs or 0,
                "quests": quest_list,
            })
        except Exception as e:
            return web.json_response({"detail": str(e)}, status=500)

    async def api_quests_start(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
            token = data.get("token", "").strip()
            if not token:
                return web.json_response({"detail": "Token không được để trống"}, status=400)

            task_id = str(int(time.time()))

            async def _run_bg():
                push_log("INFO", f"⚡ [Auto Quests] Khởi động flow cày Quests (Cập nhật theo phút)...")
                store = quest_manager.QuestStore(token)
                ok, quests, _ = await asyncio.to_thread(store.fetch_quests)
                if not ok:
                    push_log("WARN", "❌ [Auto Quests] Không lấy được danh sách quests.")
                    return
                # Enroll
                await asyncio.to_thread(store.enroll_all)
                for q in quests:
                    if not q.is_completed():
                        push_log("INFO", f"⏳ [Auto Quests] Đang thực hiện: {q.name}")
                        await asyncio.to_thread(store.execute_quest, q, None)
                # Claim
                await asyncio.to_thread(store.claim_all)
                push_log("SUCCESS", f"✔ [Auto Quests] Đã hoàn thành toàn bộ quests!")
                self.active_quest_tasks.pop(task_id, None)

            task = asyncio.create_task(_run_bg())
            self.active_quest_tasks[task_id] = task

            return web.json_response({"ok": True, "task_id": task_id})
        except Exception as e:
            return web.json_response({"detail": str(e)}, status=500)

    async def api_toinaylogi_roll(self, request: web.Request) -> web.Response:
        count = int(request.query.get("count", 1))
        if count <= 1:
            winner = toinaylogi.default_engine.roll_actress()
            return web.json_response({
                "winner": winner.to_dict(),
            })
        else:
            multi = toinaylogi.default_engine.roll_multi(count)
            best = max(multi, key=lambda a: a.tier)
            return web.json_response({
                "best": best.to_dict(),
                "all": [a.to_dict() for a in multi],
            })

    async def api_toinaylogi_catalog(self, request: web.Request) -> web.Response:
        return web.json_response([a.to_dict() for a in toinaylogi.default_engine.db])

    async def start(self) -> None:
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()

        # Try designated port, fallback if in use
        for test_port in [self.port, self.port + 1, 5001, 8080]:
            try:
                self.site = web.TCPSite(self.runner, self.host, test_port)
                await self.site.start()
                self.port = test_port
                break
            except OSError:
                continue

        access_url = f"http://localhost:{self.port}/?token={ADMIN_SECRET}"
        push_log("SUCCESS", f"🌐 Web Dashboard đã mở tại: http://localhost:{self.port}")
        print("\n" + "=" * 65)
        print(f"🚀 [WEB DASHBOARD] Đang chạy tại cổng {self.port}")
        print(f"🔗 Link truy cập Admin: {access_url}")
        print(f"🔑 Admin Secret Token : {ADMIN_SECRET}")
        print("=" * 65 + "\n")

    async def stop(self) -> None:
        if self.runner:
            await self.runner.cleanup()


# Setup root logger capture
root_logger = logging.getLogger()
handler = BufferLogHandler()
handler.setFormatter(logging.Formatter("%(message)s"))
root_logger.addHandler(handler)
