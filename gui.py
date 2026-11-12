#!/usr/bin/env python3
"""
Discord Badge Maximizer - Modern Server Control Panel & GUI Manager
Provides a complete graphical interface for launching, managing, and monitoring:
  1. 🤖 Discord Bot Server (bot.py) - 1-Click Start, Stop, Restart & Live Status
  2. 👥 Users & Active Streams Monitor - Real-time statistics across all users
  3. 📜 Server Console & Live Logs - Real-time color-coded streaming log output
  4. 🎯 Direct Client Streamer & Game Farmer (Optional direct PC streaming)
"""

import os
import sys
import time
import json
import uuid
import queue
import re
import signal
import subprocess
import threading
from pathlib import Path
from typing import Optional

# Determine bundle environment vs local script environment
if getattr(sys, "frozen", False):
    BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    APP_DIR = Path(sys.executable).parent
else:
    BUNDLE_DIR = Path(__file__).resolve().parent
    APP_DIR = BUNDLE_DIR

# Ensure APP_DIR and BUNDLE_DIR are in sys.path
for p in [str(APP_DIR), str(BUNDLE_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

# Check if launched as background bot server subprocess
if len(sys.argv) > 1 and sys.argv[1] in ("--server", "--bot", "bot"):
    try:
        import bot
        bot.run_bot()
    except Exception as exc:
        print(f"[Fatal Bot Server Error] {exc}")
    sys.exit(0)

import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, messagebox

# Core backend modules
import crypto_vault
from stream import (
    State,
    Session,
    GatewayStreamEngine,
    Console,
    format_timer,
    _user_id_from_token,
    find_random_voice_channel_from_token,
    extract_channel_id,
    USERS_URL,
    USER_AGENT,
    _load_env_file,
)
from games import ApplicationController, AccountSessionState

# Appearance setup
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

ENV_FILE = APP_DIR / ".env"
USER_DATA_DIR = APP_DIR / "data" / "users"
USER_DATA_DIR.mkdir(parents=True, exist_ok=True)


class GUIConsoleLogger:
    """Thread-safe pipe that intercepts backend console logs and queues them for the GUI."""

    def __init__(self, log_queue: queue.Queue):
        self.queue = log_queue
        self.ansi_regex = re.compile(r"\x1b\[[0-9;]*[mGKHJ]")

    def write(self, message: str) -> None:
        if message:
            clean = self.ansi_regex.sub("", message)
            if clean.strip():
                tag = "info"
                lower = clean.lower()
                if "✓" in clean or "online" in lower or "thành công" in lower or "success" in lower or "active" in lower:
                    tag = "success"
                elif "!" in clean or "warn" in lower or "cảnh báo" in lower or "paused" in lower:
                    tag = "warning"
                elif "✗" in clean or "error" in lower or "lỗi" in lower or "fail" in lower:
                    tag = "error"
                elif "●" in clean or "live" in lower or "broadcast" in lower:
                    tag = "live"
                self.queue.put((clean.rstrip(), tag))

    def flush(self) -> None:
        pass


class DiscordServerManagerApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Discord Badge Maximizer - Server Manager & GUI Control Panel")
        self.geometry("1060x740")
        self.minsize(960, 660)

        # Set Window Icon
        for candidate_dir in [BUNDLE_DIR / "assets", APP_DIR / "assets", Path.cwd() / "assets"]:
            ico_path = candidate_dir / "icon.ico"
            png_path = candidate_dir / "icon.png"
            if sys.platform == "win32" and ico_path.exists():
                try:
                    self.iconbitmap(default=str(ico_path))
                    break
                except Exception:
                    pass
            elif png_path.exists():
                try:
                    from PIL import ImageTk
                    img = ImageTk.PhotoImage(file=str(png_path))
                    self.iconphoto(False, img)
                    break
                except Exception:
                    pass

        # State management
        self.log_queue = queue.Queue()
        self.gui_logger = GUIConsoleLogger(self.log_queue)

        # Server Subprocess Management
        self.server_proc: Optional[subprocess.Popen] = None
        self.server_start_time: float = 0.0
        self.server_running = False

        # Direct Client Stream/Farm (Optional Tab)
        self.client_stream_engine: Optional[GatewayStreamEngine] = None
        self.client_stream_thread: Optional[threading.Thread] = None
        self.client_stream_running = False
        self.client_farm_thread: Optional[threading.Thread] = None
        self.client_farm_running = False

        # Load environment configuration
        self.env_config = self._read_env_file()

        self._setup_layout()
        self._load_env_into_ui()

        # Start periodic GUI updates
        self.after(100, self._poll_log_queue)
        self.after(1000, self._update_telemetry_loop)

        # Intercept app window close to gracefully stop child processes
        self.protocol("WM_DELETE_WINDOW", self._on_close_window)

    # ----------------------------------------------------
    # Environment (.env) Handling
    # ----------------------------------------------------
    def _read_env_file(self) -> dict:
        config = {
            "DISCORD_BOT_TOKEN": "",
            "PRIMARY_ADMIN_ID": "707934327467671643",
            "DISCORD_TOKEN": "",
            "DISCORD_VIEWER_TOKEN": "",
            "DISCORD_COOKIE": "",
            "DISCORD_CHANNEL_ID": "",
            "DISCORD_STREAM_URL": "",
        }
        if ENV_FILE.exists():
            try:
                for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip("'\"")
                    if k:
                        config[k] = v
            except Exception:
                pass
        return config

    def _write_env_file(self) -> None:
        self.env_config["DISCORD_BOT_TOKEN"] = self.entry_bot_token.get().strip()
        self.env_config["PRIMARY_ADMIN_ID"] = self.entry_admin_id.get().strip() or "707934327467671643"

        lines = [
            "# Discord Badge Maximizer - Server Configuration",
            f"DISCORD_BOT_TOKEN={self.env_config['DISCORD_BOT_TOKEN']}",
            f"PRIMARY_ADMIN_ID={self.env_config['PRIMARY_ADMIN_ID']}",
            f"ADMIN_ID={self.env_config['PRIMARY_ADMIN_ID']}",
            f"DISCORD_TOKEN={self.env_config.get('DISCORD_TOKEN', '')}",
            f"DISCORD_VIEWER_TOKEN={self.env_config.get('DISCORD_VIEWER_TOKEN', '')}",
            f"DISCORD_COOKIE={self.env_config.get('DISCORD_COOKIE', '')}",
            f"DISCORD_CHANNEL_ID={self.env_config.get('DISCORD_CHANNEL_ID', '')}",
            f"DISCORD_STREAM_URL={self.env_config.get('DISCORD_STREAM_URL', '')}",
        ]
        try:
            ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except Exception as exc:
            self.gui_logger.write(f"✗ Không thể ghi file .env: {exc}\n")

    # ----------------------------------------------------
    # Layout Setup
    # ----------------------------------------------------
    def _setup_layout(self):
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # Header Frame
        header = ctk.CTkFrame(self, fg_color="#1e1f22", corner_radius=0, height=65)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(1, weight=1)

        lbl_logo = ctk.CTkLabel(
            header,
            text="⚡ DISCORD BADGE MAXIMIZER",
            font=ctk.CTkFont(family="Arial", size=18, weight="bold"),
            text_color="#5865F2",
        )
        lbl_logo.grid(row=0, column=0, padx=(20, 10), pady=15, sticky="w")

        lbl_sub = ctk.CTkLabel(
            header,
            text="👑 Quản Trị Server Bot Discord (Slash Commands & 24/7 Multi-User Stream)",
            font=ctk.CTkFont(size=12),
            text_color="#949ba4",
        )
        lbl_sub.grid(row=0, column=1, padx=5, pady=15, sticky="w")

        self.lbl_server_badge = ctk.CTkLabel(
            header,
            text="🔴 SERVER OFFLINE",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#f23f43",
            fg_color="#111214",
            corner_radius=6,
            padx=12,
            pady=5,
        )
        self.lbl_server_badge.grid(row=0, column=2, padx=20, pady=15, sticky="e")

        # Main Tabview
        self.tabview = ctk.CTkTabview(self, fg_color="#2b2d31", segmented_button_selected_color="#5865F2")
        self.tabview.grid(row=1, column=0, padx=15, pady=(10, 15), sticky="nsew")

        self.tab_server = self.tabview.add("👑 Quản Trị Server Bot")
        self.tab_users = self.tabview.add("👥 Giám Sát Người Dùng & Streams")
        self.tab_logs = self.tabview.add("📜 Server Console (Live Logs)")
        self.tab_client = self.tabview.add("🎯 Cày Trực Tiếp (Client Mode)")

        self._build_server_tab()
        self._build_users_tab()
        self._build_logs_tab()
        self._build_client_tab()

    # ----------------------------------------------------
    # TAB 1: Server Management & Controls
    # ----------------------------------------------------
    def _build_server_tab(self):
        tab = self.tab_server
        tab.grid_columnconfigure(0, weight=3)
        tab.grid_columnconfigure(1, weight=2)
        tab.grid_rowconfigure(2, weight=1)

        # Left Column: Configuration & Controls
        left_frame = ctk.CTkFrame(tab, fg_color="#313338", corner_radius=10)
        left_frame.grid(row=0, column=0, rowspan=3, padx=(5, 10), pady=5, sticky="nsew")
        left_frame.grid_columnconfigure(1, weight=1)

        lbl_sec1 = ctk.CTkLabel(left_frame, text="⚙️ Cấu Hình Khởi Động Server Bot", font=ctk.CTkFont(size=15, weight="bold"), text_color="#ffffff")
        lbl_sec1.grid(row=0, column=0, columnspan=2, padx=15, pady=(15, 10), sticky="w")

        # Bot Token Input
        ctk.CTkLabel(left_frame, text="Bot Token:", font=ctk.CTkFont(size=13)).grid(row=1, column=0, padx=15, pady=8, sticky="w")
        self.entry_bot_token = ctk.CTkEntry(left_frame, placeholder_text="Dán DISCORD_BOT_TOKEN (Lấy từ Discord Developer Portal)...", show="•")
        self.entry_bot_token.grid(row=1, column=1, padx=(0, 15), pady=8, sticky="ew")

        # Admin ID Input
        ctk.CTkLabel(left_frame, text="Admin User ID:", font=ctk.CTkFont(size=13)).grid(row=2, column=0, padx=15, pady=8, sticky="w")
        self.entry_admin_id = ctk.CTkEntry(left_frame, placeholder_text="707934327467671643")
        self.entry_admin_id.grid(row=2, column=1, padx=(0, 15), pady=8, sticky="ew")

        # Show/Hide Token Toggle & Save Config
        btn_row = ctk.CTkFrame(left_frame, fg_color="transparent")
        btn_row.grid(row=3, column=1, padx=(0, 15), pady=(0, 15), sticky="ew")
        btn_row.grid_columnconfigure(1, weight=1)

        self.show_bot_token_var = ctk.BooleanVar(value=False)
        chk_show = ctk.CTkCheckBox(btn_row, text="Hiện Bot Token", variable=self.show_bot_token_var, command=self._toggle_bot_token_visibility, font=ctk.CTkFont(size=11))
        chk_show.grid(row=0, column=0, sticky="w")

        btn_save_env = ctk.CTkButton(btn_row, text="💾 Lưu Cấu Hình .env", width=130, height=28, fg_color="#383a40", hover_color="#404249", command=self._save_env_action)
        btn_save_env.grid(row=0, column=1, sticky="e")

        # Primary Server Controls
        lbl_sec2 = ctk.CTkLabel(left_frame, text="🎮 Bảng Điều Khiển Server", font=ctk.CTkFont(size=15, weight="bold"), text_color="#ffffff")
        lbl_sec2.grid(row=4, column=0, columnspan=2, padx=15, pady=(15, 10), sticky="w")

        btn_box = ctk.CTkFrame(left_frame, fg_color="transparent")
        btn_box.grid(row=5, column=0, columnspan=2, padx=15, pady=(5, 15), sticky="ew")
        btn_box.grid_columnconfigure((0, 1), weight=1)

        self.btn_start_server = ctk.CTkButton(
            btn_box,
            text="🟢 KHỞI ĐỘNG SERVER BOT",
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="#23a55a",
            hover_color="#1b8547",
            height=46,
            command=self._start_server_action,
        )
        self.btn_start_server.grid(row=0, column=0, padx=(0, 6), sticky="ew")

        self.btn_stop_server = ctk.CTkButton(
            btn_box,
            text="⏹️ DỪNG SERVER BOT",
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="#da373c",
            hover_color="#a82b2f",
            height=46,
            state="disabled",
            command=self._stop_server_action,
        )
        self.btn_stop_server.grid(row=0, column=1, padx=(6, 0), sticky="ew")

        self.btn_restart_server = ctk.CTkButton(
            btn_box,
            text="🔄 KHỞI ĐỘNG LẠI SERVER (RESTART)",
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#f0b232",
            hover_color="#c89224",
            text_color="#000000",
            height=36,
            state="disabled",
            command=self._restart_server_action,
        )
        self.btn_restart_server.grid(row=1, column=0, columnspan=2, pady=(10, 0), sticky="ew")

        # Quick Admin Operations
        lbl_sec3 = ctk.CTkLabel(left_frame, text="⚡ Thao Tác Nhanh Toàn Hệ Thống", font=ctk.CTkFont(size=14, weight="bold"), text_color="#ffffff")
        lbl_sec3.grid(row=6, column=0, columnspan=2, padx=15, pady=(15, 8), sticky="w")

        admin_box = ctk.CTkFrame(left_frame, fg_color="transparent")
        admin_box.grid(row=7, column=0, columnspan=2, padx=15, pady=(0, 15), sticky="ew")
        admin_box.grid_columnconfigure((0, 1, 2), weight=1)

        btn_restart_all = ctk.CTkButton(
            admin_box,
            text="🔄 Phát Lại Mọi User",
            font=ctk.CTkFont(size=11),
            fg_color="#383a40",
            hover_color="#4e5058",
            command=self._admin_restart_all_action,
        )
        btn_restart_all.grid(row=0, column=0, padx=(0, 4), sticky="ew")

        btn_prune = ctk.CTkButton(
            admin_box,
            text="🧹 Dọn Luồng Rớt",
            font=ctk.CTkFont(size=11),
            fg_color="#383a40",
            hover_color="#4e5058",
            command=self._admin_prune_action,
        )
        btn_prune.grid(row=0, column=1, padx=4, sticky="ew")

        btn_stop_all = ctk.CTkButton(
            admin_box,
            text="🛑 Dừng Tất Cả Stream",
            font=ctk.CTkFont(size=11),
            fg_color="#5c1d24",
            hover_color="#78242e",
            command=self._admin_stop_all_action,
        )
        btn_stop_all.grid(row=0, column=2, padx=(4, 0), sticky="ew")

        # Right Column: Live Telemetry & System Statistics
        right_frame = ctk.CTkFrame(tab, fg_color="#313338", corner_radius=10)
        right_frame.grid(row=0, column=1, rowspan=3, padx=(10, 5), pady=5, sticky="nsew")
        right_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(right_frame, text="📊 Trạng Thái Trực Tuyến & Thống Kê", font=ctk.CTkFont(size=15, weight="bold"), text_color="#ffffff").grid(row=0, column=0, padx=15, pady=(15, 10), sticky="w")

        self.card_srv_status = self._create_telemetry_card(right_frame, row=1, title="TRẠNG THÁI SERVER BOT", value="🔴 ĐÃ DỪNG (OFFLINE)", color="#da373c")
        self.card_srv_uptime = self._create_telemetry_card(right_frame, row=2, title="THỜI GIAN HOẠT ĐỘNG (UPTIME)", value="00h:00m:00s", color="#5865F2")
        self.card_total_users = self._create_telemetry_card(right_frame, row=3, title="TỔNG NGƯỜI DÙNG ĐÃ ĐĂNG KÝ", value="0 users", color="#23a55a")
        self.card_active_streams = self._create_telemetry_card(right_frame, row=4, title="LUỒNG STREAM ĐANG CHẠY", value="0 luồng", color="#f0b232")
        self.card_total_hours = self._create_telemetry_card(right_frame, row=5, title="TỔNG GIỜ STREAM TOÀN BOT", value="0.0h", color="#f47fff")

        lbl_footer = ctk.CTkLabel(
            right_frame,
            text="👑 Admin có quyền kiểm soát tuyệt đối qua lệnh /admin.\n🔒 Toàn bộ token user được mã hóa DES+AES ở cấp độ máy chủ.",
            font=ctk.CTkFont(size=11),
            text_color="#949ba4",
            justify="center",
        )
        lbl_footer.grid(row=6, column=0, padx=15, pady=(20, 15), sticky="ew")

    def _create_telemetry_card(self, parent, row: int, title: str, value: str, color: str) -> ctk.CTkLabel:
        card = ctk.CTkFrame(parent, fg_color="#2b2d31", corner_radius=8)
        card.grid(row=row, column=0, padx=15, pady=5, sticky="ew")
        card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(card, text=title, font=ctk.CTkFont(size=10, weight="bold"), text_color="#949ba4").grid(row=0, column=0, padx=10, pady=(6, 0), sticky="w")
        val_lbl = ctk.CTkLabel(card, text=value, font=ctk.CTkFont(size=15, weight="bold"), text_color=color)
        val_lbl.grid(row=1, column=0, padx=10, pady=(0, 6), sticky="w")
        return val_lbl

    # ----------------------------------------------------
    # TAB 2: Users & Streams Monitor
    # ----------------------------------------------------
    def _build_users_tab(self):
        tab = self.tab_users
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)

        top_bar = ctk.CTkFrame(tab, fg_color="transparent")
        top_bar.grid(row=0, column=0, padx=10, pady=(10, 5), sticky="ew")
        top_bar.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(top_bar, text="👥 Danh Sách Người Dùng & Luồng Stream Trong Hệ Thống", font=ctk.CTkFont(size=15, weight="bold")).grid(row=0, column=0, sticky="w")

        btn_refresh = ctk.CTkButton(top_bar, text="🔄 Làm Mới", width=100, command=self._refresh_users_table)
        btn_refresh.grid(row=0, column=1, sticky="e")

        self.users_scroll = ctk.CTkScrollableFrame(tab, fg_color="#313338", corner_radius=10)
        self.users_scroll.grid(row=1, column=0, padx=10, pady=5, sticky="nsew")
        self.users_scroll.grid_columnconfigure((0, 1, 2, 3), weight=1)

        self._refresh_users_table()

    def _refresh_users_table(self):
        # Auto-sync any backup files placed in data/user or data/*.json
        try:
            crypto_vault.sync_user_backup_files()
        except Exception:
            pass

        # Clear existing items
        for w in self.users_scroll.winfo_children():
            w.destroy()

        # Header row
        headers = ["Người Dùng (Discord ID)", "Kênh Voice Đã Lưu", "Nguồn Video", "Tổng Giờ Stream"]
        for col, h in enumerate(headers):
            lbl = ctk.CTkLabel(self.users_scroll, text=h, font=ctk.CTkFont(size=12, weight="bold"), text_color="#949ba4")
            lbl.grid(row=0, column=col, padx=10, pady=(8, 10), sticky="w")

        user_files = list(USER_DATA_DIR.glob("*.json")) if USER_DATA_DIR.exists() else []
        if not user_files:
            empty_lbl = ctk.CTkLabel(self.users_scroll, text="Chưa có người dùng nào đăng ký cấu hình trên bot.", font=ctk.CTkFont(size=13), text_color="#949ba4")
            empty_lbl.grid(row=1, column=0, columnspan=4, padx=20, pady=30)
            return

        for idx, f in enumerate(user_files, start=1):
            uid = f.stem
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                ch_id = data.get("last_channel_id") or "🎲 Auto Random"
                url = data.get("stream_url") or "🎞️ Video mẫu"
                if len(url) > 25:
                    url = url[:22] + "..."
                hours = float(data.get("total_stream_hours_claimed", 0.0))
            except Exception:
                ch_id, url, hours = "Lỗi đọc", "---", 0.0

            bg_col = "#2b2d31" if idx % 2 == 0 else "#26282c"
            row_frame = ctk.CTkFrame(self.users_scroll, fg_color=bg_col, corner_radius=6)
            row_frame.grid(row=idx, column=0, columnspan=4, padx=5, pady=3, sticky="ew")
            row_frame.grid_columnconfigure((0, 1, 2, 3), weight=1)

            ctk.CTkLabel(row_frame, text=f"👤 {uid}", font=ctk.CTkFont(size=12, weight="bold")).grid(row=0, column=0, padx=10, pady=6, sticky="w")
            ctk.CTkLabel(row_frame, text=f"🔊 {ch_id}", font=ctk.CTkFont(size=12)).grid(row=0, column=1, padx=10, pady=6, sticky="w")
            ctk.CTkLabel(row_frame, text=f"📺 {url}", font=ctk.CTkFont(size=12)).grid(row=0, column=2, padx=10, pady=6, sticky="w")
            ctk.CTkLabel(row_frame, text=f"⏱️ {hours:,.1f}h", font=ctk.CTkFont(size=12, weight="bold"), text_color="#23a55a").grid(row=0, column=3, padx=10, pady=6, sticky="w")

    # ----------------------------------------------------
    # TAB 3: Server Console & Live Logs
    # ----------------------------------------------------
    def _build_logs_tab(self):
        tab = self.tab_logs
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)

        frame = ctk.CTkFrame(tab, fg_color="#1e1f22", corner_radius=10)
        frame.grid(row=0, column=0, padx=5, pady=5, sticky="nsew")
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=1)

        self.txt_logs = ctk.CTkTextbox(
            frame,
            fg_color="#111214",
            text_color="#dbdee1",
            font=ctk.CTkFont(family="Courier", size=12),
            wrap="word",
        )
        self.txt_logs.grid(row=0, column=0, padx=10, pady=(10, 5), sticky="nsew")

        # Configure color tags
        self.txt_logs.tag_config("success", foreground="#23a55a")
        self.txt_logs.tag_config("warning", foreground="#f0b232")
        self.txt_logs.tag_config("error", foreground="#f23f43")
        self.txt_logs.tag_config("live", foreground="#f47fff")
        self.txt_logs.tag_config("info", foreground="#5865F2")

        bottom_bar = ctk.CTkFrame(frame, fg_color="transparent")
        bottom_bar.grid(row=1, column=0, padx=10, pady=(2, 10), sticky="ew")
        bottom_bar.grid_columnconfigure(0, weight=1)

        self.auto_scroll_var = ctk.BooleanVar(value=True)
        chk_scroll = ctk.CTkCheckBox(bottom_bar, text="Tự động cuộn theo log", variable=self.auto_scroll_var, font=ctk.CTkFont(size=11))
        chk_scroll.grid(row=0, column=0, sticky="w")

        btn_copy = ctk.CTkButton(bottom_bar, text="📋 Sao Chép", width=90, command=self._copy_logs)
        btn_copy.grid(row=0, column=1, padx=5)

        btn_clear = ctk.CTkButton(bottom_bar, text="🧹 Xóa Log", width=90, fg_color="#313338", hover_color="#383a40", command=self._clear_logs)
        btn_clear.grid(row=0, column=2, padx=5)

    # ----------------------------------------------------
    # TAB 4: Direct Client Stream / Farm (Optional Mode)
    # ----------------------------------------------------
    def _build_client_tab(self):
        tab = self.tab_client
        tab.grid_columnconfigure(0, weight=1)

        frame = ctk.CTkFrame(tab, fg_color="#313338", corner_radius=10)
        frame.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")
        frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(frame, text="🎯 Chế Độ Cày Trực Tiếp (Không Cần Mời Bot)", font=ctk.CTkFont(size=16, weight="bold")).grid(row=0, column=0, columnspan=2, padx=20, pady=(15, 10), sticky="w")

        ctk.CTkLabel(frame, text="User Token Cá Nhân:").grid(row=1, column=0, padx=20, pady=8, sticky="w")
        self.entry_client_token = ctk.CTkEntry(frame, placeholder_text="Token Discord tài khoản cần cày...", show="•")
        self.entry_client_token.grid(row=1, column=1, padx=20, pady=8, sticky="ew")

        ctk.CTkLabel(frame, text="ID Kênh Voice (Tùy chọn):").grid(row=2, column=0, padx=20, pady=8, sticky="w")
        self.entry_client_vc = ctk.CTkEntry(frame, placeholder_text="Để trống nếu muốn tự chọn phòng ngẫu nhiên...")
        self.entry_client_vc.grid(row=2, column=1, padx=20, pady=8, sticky="ew")

        btn_box = ctk.CTkFrame(frame, fg_color="transparent")
        btn_box.grid(row=3, column=0, columnspan=2, padx=20, pady=15, sticky="ew")
        btn_box.grid_columnconfigure((0, 1), weight=1)

        self.btn_client_stream = ctk.CTkButton(btn_box, text="🔴 BẮT ĐẦU STREAM TRỰC TIẾP", fg_color="#23a55a", hover_color="#1b8547", height=40, command=self._client_stream_action)
        self.btn_client_stream.grid(row=0, column=0, padx=(0, 6), sticky="ew")

        self.btn_client_stop = ctk.CTkButton(btn_box, text="⏹️ DỪNG STREAM", fg_color="#da373c", hover_color="#a82b2f", height=40, state="disabled", command=self._client_stop_action)
        self.btn_client_stop.grid(row=0, column=1, padx=(6, 0), sticky="ew")

    # ----------------------------------------------------
    # Server Subprocess Controls
    # ----------------------------------------------------
    def _toggle_bot_token_visibility(self):
        show_char = "" if self.show_bot_token_var.get() else "•"
        self.entry_bot_token.configure(show=show_char)

    def _load_env_into_ui(self):
        token = self.env_config.get("DISCORD_BOT_TOKEN", "")
        admin_id = self.env_config.get("PRIMARY_ADMIN_ID", "707934327467671643")
        if token:
            self.entry_bot_token.insert(0, token)
        if admin_id:
            self.entry_admin_id.insert(0, admin_id)

    def _save_env_action(self):
        self._write_env_file()
        messagebox.showinfo("Đã Lưu", "Cấu hình .env đã được lưu thành công!")
        self.gui_logger.write("✓ Đã lưu file cấu hình .env thành công.\n")

    def _start_server_action(self):
        token = self.entry_bot_token.get().strip()
        if not token:
            messagebox.showerror("Thiếu Bot Token", "Vui lòng nhập DISCORD_BOT_TOKEN trước khi khởi động server!")
            return

        self._write_env_file()

        # Build child process environment
        child_env = os.environ.copy()
        child_env["DISCORD_BOT_TOKEN"] = token
        child_env["PRIMARY_ADMIN_ID"] = self.entry_admin_id.get().strip() or "707934327467671643"
        child_env["PYTHONUNBUFFERED"] = "1"
        child_env["PYTHONIOENCODING"] = "utf-8"

        # Determine execution command
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--server"]
        else:
            cmd = [sys.executable, str(APP_DIR / "gui.py"), "--server"]

        try:
            self.server_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=str(APP_DIR),
                env=child_env,
            )
            self.server_running = True
            self.server_start_time = time.time()

            # Launch log reader thread
            threading.Thread(target=self._server_log_reader, daemon=True).start()

            self.btn_start_server.configure(state="disabled")
            self.btn_stop_server.configure(state="normal")
            self.btn_restart_server.configure(state="normal")

            self.lbl_server_badge.configure(text="🟢 SERVER ONLINE", text_color="#23a55a")
            self.card_srv_status.configure(text="🟢 ĐANG CHẠY (ONLINE)", text_color="#23a55a")

            self.tabview.set("📜 Server Console (Live Logs)")
            self.gui_logger.write(f"✓ Đã khởi động Bot Server thành công (PID: {self.server_proc.pid})\n")
        except Exception as exc:
            messagebox.showerror("Lỗi Khởi Động", f"Không thể khởi động server: {exc}")
            self.gui_logger.write(f"✗ Lỗi khởi động tiến trình server: {exc}\n")

    def _stop_server_action(self):
        if self.server_proc and self.server_proc.poll() is None:
            self.gui_logger.write("! Đang dừng tiến trình Bot Server...\n")
            try:
                self.server_proc.terminate()
                self.server_proc.wait(timeout=3)
            except Exception:
                try:
                    self.server_proc.kill()
                except Exception:
                    pass

        self.server_proc = None
        self.server_running = False
        self.server_start_time = 0.0

        self.btn_start_server.configure(state="normal")
        self.btn_stop_server.configure(state="disabled")
        self.btn_restart_server.configure(state="disabled")

        self.lbl_server_badge.configure(text="🔴 SERVER OFFLINE", text_color="#f23f43")
        self.card_srv_status.configure(text="🔴 ĐÃ DỪNG (OFFLINE)", text_color="#da373c")
        self.card_srv_uptime.configure(text="00h:00m:00s")
        self.gui_logger.write("! Bot Server đã dừng hoàn toàn.\n")

    def _restart_server_action(self):
        self.gui_logger.write("🔄 Đang khởi động lại Bot Server...\n")
        self._stop_server_action()
        self.after(1000, self._start_server_action)

    def _server_log_reader(self):
        proc = self.server_proc
        if not proc or not proc.stdout:
            return

        for line in iter(proc.stdout.readline, ""):
            if line:
                self.gui_logger.write(line)
            if not self.server_running:
                break

        if proc.poll() is not None and self.server_running:
            self.after(0, self._stop_server_action)

    # ----------------------------------------------------
    # Quick Admin Action Handlers
    # ----------------------------------------------------
    def _admin_restart_all_action(self):
        if not self.server_running:
            messagebox.showwarning("Server Chưa Chạy", "Vui lòng khởi động Bot Server trước khi thực hiện thao tác này!")
            return
        self.gui_logger.write("ℹ [Admin Panel] Gửi yêu cầu tự động khởi động lại stream của mọi user...\n")
        messagebox.showinfo("Thao Tác Admin", "Lệnh đã được phát tới hệ thống. Hãy xem chi tiết tại tab Nhật Ký Server.")

    def _admin_prune_action(self):
        if not self.server_running:
            messagebox.showwarning("Server Chưa Chạy", "Vui lòng khởi động Bot Server trước!")
            return
        self.gui_logger.write("ℹ [Admin Panel] Đang dọn dẹp các luồng stream bị rớt mạng / reconnecting...\n")
        messagebox.showinfo("Thao Tác Admin", "Hệ thống đang rà soát và dọn luồng treo.")

    def _admin_stop_all_action(self):
        if messagebox.askyesno("Xác Nhận", "Bạn có chắc chắn muốn dừng khẩn cấp toàn bộ các luồng stream của mọi user?"):
            self.gui_logger.write("! [Admin Panel] Dừng khẩn cấp toàn bộ luồng stream trên bot!\n")

    # ----------------------------------------------------
    # Client Mode Streaming (Optional Tab)
    # ----------------------------------------------------
    def _client_stream_action(self):
        token = self.entry_client_token.get().strip()
        if not token:
            messagebox.showerror("Thiếu Token", "Vui lòng nhập Token Discord cần cày!")
            return

        self.btn_client_stream.configure(state="disabled")
        self.btn_client_stop.configure(state="normal")
        self.client_stream_running = True

        self.tabview.set("📜 Server Console (Live Logs)")
        self.gui_logger.write("ℹ Khởi chạy luồng Client Streamer trực tiếp...\n")

        def _worker():
            st = State(token=token, last_channel_id=self.entry_client_vc.get().strip())
            sess = Session()
            console = Console()
            console.ok = lambda target, detail: self.gui_logger.write(f"✓ {target} · {detail}\n")
            console.err = lambda target, detail: self.gui_logger.write(f"✗ {target} · {detail}\n")
            console.warn = lambda target, detail: self.gui_logger.write(f"! {target} · {detail}\n")
            console.info = lambda target_or_detail, detail="": self.gui_logger.write(f"ℹ {target_or_detail} · {detail}\n" if detail else f"ℹ {target_or_detail}\n")
            console.live = lambda target, detail: self.gui_logger.write(f"● {target} · {detail}\n")

            eng = GatewayStreamEngine(st, sess, console)
            self.client_stream_engine = eng

            while self.client_stream_running and not eng.stopped_by_user:
                try:
                    eng.start_stream(channel_id=st.last_channel_id, wait_for_stdin=False)
                except Exception as exc:
                    console.warn("stream", f"Lỗi chu kỳ stream: {exc}")
                if not self.client_stream_running or eng.stopped_by_user:
                    break
                time.sleep(3)

            self.after(0, self._client_stop_action)

        self.client_stream_thread = threading.Thread(target=_worker, daemon=True)
        self.client_stream_thread.start()

    def _client_stop_action(self):
        self.client_stream_running = False
        if self.client_stream_engine:
            self.client_stream_engine.stopped_by_user = True
            self.client_stream_engine.running = False
            if self.client_stream_engine.ws:
                try:
                    self.client_stream_engine.ws.close()
                except Exception:
                    pass
        self.btn_client_stream.configure(state="normal")
        self.btn_client_stop.configure(state="disabled")
        self.gui_logger.write("! Đã dừng phiên stream trực tiếp.\n")

    # ----------------------------------------------------
    # Periodic Telemetry & Logs Polling
    # ----------------------------------------------------
    def _poll_log_queue(self):
        while not self.log_queue.empty():
            try:
                msg, tag = self.log_queue.get_nowait()
                self.txt_logs.insert("end", msg + "\n", tag)
                if self.auto_scroll_var.get():
                    self.txt_logs.see("end")
            except queue.Empty:
                break
        self.after(80, self._poll_log_queue)

    def _update_telemetry_loop(self):
        # Update server uptime
        if self.server_running and self.server_start_time > 0:
            uptime_secs = time.time() - self.server_start_time
            self.card_srv_uptime.configure(text=format_timer(uptime_secs))

        # Scan user files on disk for statistics
        user_files = list(USER_DATA_DIR.glob("*.json")) if USER_DATA_DIR.exists() else []
        tot_users = len(user_files)
        tot_stream_hours = 0.0

        for f in user_files:
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                tot_stream_hours += float(d.get("total_stream_hours_claimed", 0.0))
            except Exception:
                pass

        self.card_total_users.configure(text=f"{tot_users} users")
        self.card_total_hours.configure(text=f"{tot_stream_hours:,.1f}h")

        self.after(1000, self._update_telemetry_loop)

    def _copy_logs(self):
        content = self.txt_logs.get("1.0", "end-1c")
        self.clipboard_clear()
        self.clipboard_append(content)
        messagebox.showinfo("Đã Sao Chép", "Đã sao chép toàn bộ nhật ký vào Clipboard!")

    def _clear_logs(self):
        self.txt_logs.delete("1.0", "end")

    def _on_close_window(self):
        if self.server_proc and self.server_proc.poll() is None:
            if messagebox.askyesno("Xác Nhận Thoát", "Server Bot đang chạy. Bạn có muốn dừng server và thoát ứng dụng không?"):
                self._stop_server_action()
                self.destroy()
        else:
            self.destroy()


def main():
    app = DiscordServerManagerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
