#!/usr/bin/env python3
"""
Discord Badge Maximizer - Modern Server Control Panel & GUI Manager
Provides a complete graphical interface for launching, managing, and monitoring:
  1. 🤖 Discord Bot Server (bot.py) - 1-Click Start, Stop, Restart & Live Status
  2. 👥 Users & Activity Monitor - Real-time statistics across all users
  3. 📜 Server Console & Live Logs - Real-time color-coded streaming log output
  4. 🌐 Web Dashboard Integration - One-click access to Web UI Dashboard
"""

import json
import os
from pathlib import Path
import queue
import re
import signal
import subprocess
import sys
import threading
import time
from typing import Optional
import uuid
import webbrowser

# Determine bundle environment vs local script environment
if getattr(sys, "frozen", False):
    BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    APP_DIR = Path(sys.executable).parent
else:
    BUNDLE_DIR = Path(__file__).resolve().parent
    APP_DIR = BUNDLE_DIR

for p in [str(APP_DIR), str(BUNDLE_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, messagebox

# Core backend modules
import crypto_vault
from games import AccountSessionState, ApplicationController
import user_manager
import web_dashboard

# Appearance setup
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

ENV_FILE = APP_DIR / ".env"
USER_DATA_DIR = crypto_vault.get_user_data_dir()


def format_timer(seconds: float) -> str:
    secs = int(seconds)
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}h:{m:02d}m:{s:02d}s"


class GUIConsoleLogger:
    """Thread-safe pipe that intercepts backend console logs and queues them for the GUI."""

    def __init__(self, log_queue: queue.Queue):
        self.queue = log_queue
        self.ansi_regex = re.compile(r"\x1b\[[0-9;]*[mGKHJ]")

    def write(self, text: str):
        if not text:
            return
        clean = self.ansi_regex.sub("", text)
        for line in clean.splitlines():
            line_str = line.strip()
            if not line_str:
                continue

            tag = "normal"
            low = line_str.lower()
            if "✓" in line_str or "thành công" in low or "success" in low:
                tag = "success"
            elif "!" in line_str or "cảnh báo" in low or "warn" in low:
                tag = "warning"
            elif "✗" in line_str or "lỗi" in low or "error" in low or "failed" in low:
                tag = "error"
            elif "●" in line_str or "live" in low or "stream" in low:
                tag = "live"
            elif "ℹ" in line_str or "info" in low or "đang" in low:
                tag = "info"

            self.queue.put((line_str, tag))

    def flush(self):
        pass


class DiscordServerManagerApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Discord Badge Maximizer & Quests Hub · Control Manager")
        self.geometry("1100, 720")
        self.minsize(960, 640)

        # Process management states
        self.server_proc: Optional[subprocess.Popen] = None
        self.server_thread: Optional[threading.Thread] = None
        self.server_running = False
        self.server_start_time: float = 0.0

        # Background Queues
        self.log_queue = queue.Queue()
        self.gui_logger = GUIConsoleLogger(self.log_queue)

        # Load environment variables
        self.env_config = self._read_env_file()

        # Build UI layout
        self._build_top_banner()
        self._build_main_tabs()

        # Start periodic telemetry loops
        self.after(100, self._poll_log_queue)
        self.after(1000, self._update_telemetry_loop)

        # Window protocol
        self.protocol("WM_DELETE_WINDOW", self._on_close_window)

    def _read_env_file(self) -> dict[str, str]:
        data = {}
        if ENV_FILE.exists():
            try:
                for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    data[k.strip()] = v.strip().strip("'\"")
            except Exception:
                pass
        return data

    def _write_env_file(self):
        self.env_config["DISCORD_BOT_TOKEN"] = self.entry_bot_token.get().strip()
        self.env_config["PRIMARY_ADMIN_ID"] = self.entry_admin_id.get().strip()
        lines = [
            "# Discord Badge Maximizer & Entertainment Hub Configuration",
            f"DISCORD_BOT_TOKEN={self.env_config.get('DISCORD_BOT_TOKEN', '')}",
            f"PRIMARY_ADMIN_ID={self.env_config.get('PRIMARY_ADMIN_ID', '707934327467671643')}",
            f"ADMIN_SECRET={web_dashboard.ADMIN_SECRET}",
        ]
        ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ----------------------------------------------------
    # Header Banner
    # ----------------------------------------------------
    def _build_top_banner(self):
        banner = ctk.CTkFrame(self, fg_color="#1e1f22", corner_radius=0, height=75)
        banner.pack(fill="x", side="top", padx=0, pady=0)
        banner.pack_propagate(False)

        brand_frame = ctk.CTkFrame(banner, fg_color="transparent")
        brand_frame.pack(side="left", padx=20, pady=10)

        title_lbl = ctk.CTkLabel(
            brand_frame,
            text="⚡ DISCORD MAXIMIZER & QUESTS HUB",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color="#ffffff",
        )
        title_lbl.pack(anchor="w")

        sub_lbl = ctk.CTkLabel(
            brand_frame,
            text="Tự động Quests (phút chống 429), Cày Game Playtime, Mở Hòm Lọ Gì & Web Dashboard Admin",
            font=ctk.CTkFont(size=12),
            text_color="#949ba4",
        )
        sub_lbl.pack(anchor="w")

        status_box = ctk.CTkFrame(banner, fg_color="#2b2d31", corner_radius=20)
        status_box.pack(side="right", padx=20, pady=16)

        self.lbl_server_badge = ctk.CTkLabel(
            status_box,
            text="● SERVER BOT: ĐÃ DỪNG",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#da373c",
            padx=16,
            pady=6,
        )
        self.lbl_server_badge.pack()

    # ----------------------------------------------------
    # Tabview Container
    # ----------------------------------------------------
    def _build_main_tabs(self):
        self.tabview = ctk.CTkTabview(self, corner_radius=10, fg_color="#18191c")
        self.tabview.pack(fill="both", expand=True, padx=15, pady=(10, 15))

        self.tab_dashboard = self.tabview.add("📊 Quản Lý Server & Bot")
        self.tab_users = self.tabview.add("👥 Danh Sách Users & Game")
        self.tab_logs = self.tabview.add("📜 Nhật Ký Server (Live Logs)")
        self.tab_web = self.tabview.add("🌐 Web Dashboard & Quick Launch")

        self._build_dashboard_tab()
        self._build_users_tab()
        self._build_logs_tab()
        self._build_web_tab()

    # ----------------------------------------------------
    # TAB 1: Server Management & Configuration
    # ----------------------------------------------------
    def _build_dashboard_tab(self):
        tab = self.tab_dashboard
        tab.grid_columnconfigure(0, weight=3)
        tab.grid_columnconfigure(1, weight=2)
        tab.grid_rowconfigure(0, weight=1)

        left_frame = ctk.CTkFrame(tab, fg_color="#1e1f22", corner_radius=10)
        left_frame.grid(row=0, column=0, padx=(5, 10), pady=5, sticky="nsew")
        left_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(left_frame, text="⚙️ Cấu Hình Thông Số Bot Server", font=ctk.CTkFont(size=15, weight="bold"), text_color="#ffffff").grid(row=0, column=0, columnspan=2, padx=15, pady=(15, 10), sticky="w")

        ctk.CTkLabel(left_frame, text="Discord Bot Token:").grid(row=1, column=0, padx=15, pady=8, sticky="w")
        self.entry_bot_token = ctk.CTkEntry(left_frame, placeholder_text="Điền Bot Application Token...", show="•")
        self.entry_bot_token.grid(row=1, column=1, padx=(5, 15), pady=8, sticky="ew")

        ctk.CTkLabel(left_frame, text="Admin User ID:").grid(row=2, column=0, padx=15, pady=8, sticky="w")
        self.entry_admin_id = ctk.CTkEntry(left_frame, placeholder_text="ID Discord tài khoản Admin...")
        self.entry_admin_id.grid(row=2, column=1, padx=(5, 15), pady=8, sticky="ew")

        ctk.CTkLabel(left_frame, text="Thư Mục Lưu Trữ:").grid(row=3, column=0, padx=15, pady=8, sticky="w")
        dir_box = ctk.CTkFrame(left_frame, fg_color="transparent")
        dir_box.grid(row=3, column=1, padx=(5, 15), pady=8, sticky="ew")
        dir_box.grid_columnconfigure(0, weight=1)

        self.entry_data_dir = ctk.CTkEntry(dir_box)
        self.entry_data_dir.insert(0, str(USER_DATA_DIR))
        self.entry_data_dir.configure(state="readonly")
        self.entry_data_dir.grid(row=0, column=0, sticky="ew", padx=(0, 5))

        btn_browse = ctk.CTkButton(dir_box, text="📂 Đổi", width=60, command=self._browse_data_dir_action)
        btn_browse.grid(row=0, column=1)

        self.show_bot_token_var = ctk.BooleanVar(value=False)
        chk_show = ctk.CTkCheckBox(left_frame, text="Hiển thị Bot Token", variable=self.show_bot_token_var, command=self._toggle_bot_token_visibility)
        chk_show.grid(row=4, column=1, padx=(5, 15), pady=4, sticky="w")

        btn_save_env = ctk.CTkButton(left_frame, text="💾 Lưu Cấu Hình Vào .env", fg_color="#4e5058", hover_color="#6d6f78", command=self._save_env_action)
        btn_save_env.grid(row=5, column=0, columnspan=2, padx=15, pady=12, sticky="ew")

        ctk.CTkLabel(left_frame, text="🚀 Điều Khiển Máy Chủ (Subprocess)", font=ctk.CTkFont(size=15, weight="bold"), text_color="#ffffff").grid(row=6, column=0, columnspan=2, padx=15, pady=(15, 8), sticky="w")

        btn_grid = ctk.CTkFrame(left_frame, fg_color="transparent")
        btn_grid.grid(row=7, column=0, columnspan=2, padx=15, pady=(5, 15), sticky="ew")
        btn_grid.grid_columnconfigure((0, 1, 2), weight=1)

        self.btn_start_srv = ctk.CTkButton(btn_grid, text="▶️ KHỞI ĐỘNG BOT", fg_color="#23a55a", hover_color="#1b8547", height=42, font=ctk.CTkFont(size=13, weight="bold"), command=self._start_server_action)
        self.btn_start_srv.grid(row=0, column=0, padx=4, sticky="ew")

        self.btn_stop_srv = ctk.CTkButton(btn_grid, text="⏹️ DỪNG BOT", fg_color="#da373c", hover_color="#a82b2f", height=42, state="disabled", font=ctk.CTkFont(size=13, weight="bold"), command=self._stop_server_action)
        self.btn_stop_srv.grid(row=0, column=1, padx=4, sticky="ew")

        self.btn_restart_srv = ctk.CTkButton(btn_grid, text="🔄 KHỞI ĐỘNG LẠI", fg_color="#f0b232", hover_color="#c48f21", height=42, state="disabled", font=ctk.CTkFont(size=13, weight="bold"), text_color="#000000", command=self._restart_server_action)
        self.btn_restart_srv.grid(row=0, column=2, padx=4, sticky="ew")

        self._load_env_into_ui()

        # Right Telemetry Panel
        right_frame = ctk.CTkFrame(tab, fg_color="#1e1f22", corner_radius=10)
        right_frame.grid(row=0, column=1, rowspan=3, padx=(10, 5), pady=5, sticky="nsew")
        right_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(right_frame, text="📊 Trạng Thái Trực Tuyến & Thống Kê", font=ctk.CTkFont(size=15, weight="bold"), text_color="#ffffff").grid(row=0, column=0, padx=15, pady=(15, 10), sticky="w")

        self.card_srv_status = self._create_telemetry_card(right_frame, row=1, title="TRẠNG THÁI SERVER BOT", value="🔴 ĐÃ DỪNG (OFFLINE)", color="#da373c")
        self.card_srv_uptime = self._create_telemetry_card(right_frame, row=2, title="THỜI GIAN HOẠT ĐỘNG (UPTIME)", value="00h:00m:00s", color="#5865F2")
        self.card_total_users = self._create_telemetry_card(right_frame, row=3, title="TỔNG NGƯỜI DÙNG ĐÃ ĐĂNG KÝ", value="0 users", color="#23a55a")
        self.card_web_status = self._create_telemetry_card(right_frame, row=4, title="WEB DASHBOARD QUẢN TRỊ", value="CỔNG 5000", color="#f0b232")
        self.card_total_hours = self._create_telemetry_card(right_frame, row=5, title="TỔNG GIỜ GAME TOÀN BOT", value="0.0h", color="#f47fff")

        lbl_footer = ctk.CTkLabel(
            right_frame,
            text="👑 Admin có quyền kiểm soát tuyệt đối qua Web Dashboard & /admin.\n🔒 Toàn bộ token user được mã hóa DES+AES an toàn.",
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
    # TAB 2: Users & Activity Monitor
    # ----------------------------------------------------
    def _build_users_tab(self):
        tab = self.tab_users
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)

        top_bar = ctk.CTkFrame(tab, fg_color="transparent")
        top_bar.grid(row=0, column=0, padx=10, pady=(10, 5), sticky="ew")
        top_bar.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(top_bar, text="👥 Danh Sách Người Dùng & Hoạt Động Trong Hệ Thống", font=ctk.CTkFont(size=15, weight="bold")).grid(row=0, column=0, sticky="w")

        btn_refresh = ctk.CTkButton(top_bar, text="🔄 Làm Mới", width=100, command=self._refresh_users_table)
        btn_refresh.grid(row=0, column=1, sticky="e")

        self.users_scroll = ctk.CTkScrollableFrame(tab, fg_color="#313338", corner_radius=10)
        self.users_scroll.grid(row=1, column=0, padx=10, pady=5, sticky="nsew")
        self.users_scroll.grid_columnconfigure((0, 1, 2, 3), weight=1)

    def _refresh_users_table(self):
        for w in self.users_scroll.winfo_children():
            w.destroy()

        headers = ["Người Dùng (Discord ID)", "Trạng Thái Token", "Số Game Đã Cày", "Tổng Giờ Game"]
        for col, h in enumerate(headers):
            lbl = ctk.CTkLabel(self.users_scroll, text=h, font=ctk.CTkFont(size=12, weight="bold"), text_color="#949ba4")
            lbl.grid(row=0, column=col, padx=10, pady=(8, 10), sticky="w")

        user_files = list(crypto_vault.get_user_data_dir().glob("*.json"))
        if not user_files:
            empty_lbl = ctk.CTkLabel(self.users_scroll, text="Chưa có người dùng nào đăng ký cấu hình trên bot.", font=ctk.CTkFont(size=13), text_color="#949ba4")
            empty_lbl.grid(row=1, column=0, columnspan=4, padx=20, pady=30)
            return

        for idx, f in enumerate(user_files, start=1):
            uid = f.stem
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                tok = data.get("token", "")
                tok_status = "🟢 Đã cài đặt" if tok else "🔴 Chưa có"
                games_cnt = int(data.get("total_games_claimed", 0))
                hours = float(data.get("total_hours_claimed", 0.0))
            except Exception:
                tok_status, games_cnt, hours = "Lỗi đọc", 0, 0.0

            bg_col = "#2b2d31" if idx % 2 == 0 else "#26282c"
            row_frame = ctk.CTkFrame(self.users_scroll, fg_color=bg_col, corner_radius=6)
            row_frame.grid(row=idx, column=0, columnspan=4, padx=5, pady=3, sticky="ew")
            row_frame.grid_columnconfigure((0, 1, 2, 3), weight=1)

            ctk.CTkLabel(row_frame, text=f"👤 {uid}", font=ctk.CTkFont(size=12, weight="bold")).grid(row=0, column=0, padx=10, pady=6, sticky="w")
            ctk.CTkLabel(row_frame, text=tok_status, font=ctk.CTkFont(size=12)).grid(row=0, column=1, padx=10, pady=6, sticky="w")
            ctk.CTkLabel(row_frame, text=f"🎮 {games_cnt} games", font=ctk.CTkFont(size=12)).grid(row=0, column=2, padx=10, pady=6, sticky="w")
            ctk.CTkLabel(row_frame, text=f"⏱️ {hours:,.0f}h", font=ctk.CTkFont(size=12, weight="bold"), text_color="#23a55a").grid(row=0, column=3, padx=10, pady=6, sticky="w")

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

        self.txt_logs.tag_config("success", foreground="#23a55a")
        self.txt_logs.tag_config("warning", foreground="#f0b232")
        self.txt_logs.tag_config("error", foreground="#f23f43")
        self.txt_logs.tag_config("live", foreground="#f47fff")
        self.txt_logs.tag_config("info", foreground="#5865F2")

        bottom_bar = ctk.CTkFrame(frame, fg_color="transparent")
        bottom_bar.grid(row=1, column=0, padx=10, pady=(2, 10), sticky="ew")
        bottom_bar.grid_columnconfigure(0, weight=1)

        self.auto_scroll_var = ctk.BooleanVar(value=True)
        chk_scroll = ctk.CTkCheckBox(bottom_bar, text="Tự động cuộn xuống", variable=self.auto_scroll_var)
        chk_scroll.grid(row=0, column=0, padx=10, sticky="w")

        btn_copy = ctk.CTkButton(bottom_bar, text="📋 Sao Chép", width=90, fg_color="#4e5058", hover_color="#6d6f78", command=self._copy_logs)
        btn_copy.grid(row=0, column=1, padx=5)

        btn_clear = ctk.CTkButton(bottom_bar, text="🧹 Xóa Màn Hình", width=90, fg_color="#4e5058", hover_color="#6d6f78", command=self._clear_logs)
        btn_clear.grid(row=0, column=2, padx=(5, 10))

    # ----------------------------------------------------
    # TAB 4: Web Dashboard & Quick Launch
    # ----------------------------------------------------
    def _build_web_tab(self):
        tab = self.tab_web
        tab.grid_columnconfigure(0, weight=1)

        frame = ctk.CTkFrame(tab, fg_color="#1e1f22", corner_radius=10)
        frame.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")
        frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(frame, text="🌐 Bảng Điều Khiển Web Dashboard (Admin Web Control)", font=ctk.CTkFont(size=17, weight="bold")).grid(row=0, column=0, padx=20, pady=(20, 10), sticky="w")

        info_text = (
            "Web Dashboard tích hợp trực tiếp bên trong Discord Bot, cho phép bạn:\n"
            "• Gửi thông báo đến mọi kênh Discord với Embed tùy biến và tùy chọn tag @everyone / @here\n"
            "• Xem trước trực tiếp (Live Discord Preview) dạng WYSIWYG trước khi gửi\n"
            "• Mở hòm CS:GO gacha Tối Nay Lọ Gì (zennomi/toinaylogi) ngay trên trình duyệt\n"
            "• Quản lý Quests (cập nhật theo phút chống rate-limit) và xem Live Console Logs thời gian thực\n"
        )
        ctk.CTkLabel(frame, text=info_text, font=ctk.CTkFont(size=13), text_color="#dbdee1", justify="left").grid(row=1, column=0, padx=20, pady=10, sticky="w")

        btn_box = ctk.CTkFrame(frame, fg_color="transparent")
        btn_box.grid(row=2, column=0, padx=20, pady=20, sticky="ew")

        btn_open_web = ctk.CTkButton(
            btn_box,
            text="🚀 MỞ WEB DASHBOARD TRÊN TRÌNH DUYỆT",
            fg_color="#5865F2",
            hover_color="#4752c4",
            height=48,
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._open_web_dashboard,
        )
        btn_open_web.pack(fill="x", pady=6)

    def _open_web_dashboard(self):
        port = int(self.env_config.get("PORT") or self.env_config.get("WEB_PORT") or 5000)
        token = web_dashboard.ADMIN_SECRET
        url = f"http://localhost:{port}/?token={token}"
        webbrowser.open(url)
        self.gui_logger.write(f"🌐 Đã mở Web Dashboard tại: {url}\n")

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

    def _browse_data_dir_action(self):
        chosen = filedialog.askdirectory(title="Chọn Thư Mục Chứa Dữ Liệu Bot")
        if chosen:
            new_dir = crypto_vault.set_custom_data_dir(chosen)
            self.entry_data_dir.configure(state="normal")
            self.entry_data_dir.delete(0, "end")
            self.entry_data_dir.insert(0, str(new_dir))
            self.entry_data_dir.configure(state="readonly")
            self._refresh_users_table()

    def _start_server_action(self):
        token = self.entry_bot_token.get().strip()
        if not token:
            messagebox.showerror("Thiếu Token", "Vui lòng nhập Discord Bot Token trong mục Cấu Hình!")
            return

        self._write_env_file()

        bot_script = APP_DIR / "bot.py"
        if not bot_script.exists():
            messagebox.showerror("Lỗi", "Không tìm thấy file bot.py trong thư mục làm việc!")
            return

        self.btn_start_srv.configure(state="disabled")
        self.btn_stop_srv.configure(state="normal")
        self.btn_restart_srv.configure(state="normal")

        self.server_running = True
        self.server_start_time = time.time()
        self.card_srv_status.configure(text="🟢 ĐANG CHẠY (ONLINE)", text_color="#23a55a")
        self.lbl_server_badge.configure(text="● SERVER BOT: ĐANG CHẠY", text_color="#23a55a")

        self.tabview.set("📜 Server Console (Live Logs)")
        self.gui_logger.write("🚀 Khởi động Bot Server (bot.py)...\n")

        def _runner():
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            env["DISCORD_BOT_TOKEN"] = token

            proc = subprocess.Popen(
                [sys.executable, str(bot_script)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=str(APP_DIR),
                env=env,
            )
            self.server_proc = proc

            for line in proc.stdout:
                self.gui_logger.write(line)

            proc.wait()
            self.server_running = False
            if self.server_proc == proc:
                self.after(0, self._stop_server_action)

        self.server_thread = threading.Thread(target=_runner, daemon=True)
        self.server_thread.start()

    def _stop_server_action(self):
        self.server_running = False
        if self.server_proc and self.server_proc.poll() is None:
            try:
                self.server_proc.terminate()
                self.server_proc.wait(timeout=3)
            except Exception:
                try:
                    self.server_proc.kill()
                except Exception:
                    pass

        self.server_proc = None
        self.btn_start_srv.configure(state="normal")
        self.btn_stop_srv.configure(state="disabled")
        self.btn_restart_srv.configure(state="disabled")

        self.card_srv_status.configure(text="🔴 ĐÃ DỪNG (OFFLINE)", text_color="#da373c")
        self.lbl_server_badge.configure(text="● SERVER BOT: ĐÃ DỪNG", text_color="#da373c")
        self.gui_logger.write("⏹️ Server Bot đã dừng.\n")

    def _restart_server_action(self):
        self.gui_logger.write("🔄 Đang khởi động lại Bot Server...\n")
        self._stop_server_action()
        self.after(1500, self._start_server_action)

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
        if self.server_running and self.server_start_time > 0:
            uptime_secs = time.time() - self.server_start_time
            self.card_srv_uptime.configure(text=format_timer(uptime_secs))

        user_files = list(USER_DATA_DIR.glob("*.json")) if USER_DATA_DIR.exists() else []
        tot_users = len(user_files)
        tot_game_hours = 0.0

        for f in user_files:
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                tot_game_hours += float(d.get("total_hours_claimed", 0.0))
            except Exception:
                pass

        self.card_total_users.configure(text=f"{tot_users} users")
        self.card_total_hours.configure(text=f"{tot_game_hours:,.0f}h")

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
