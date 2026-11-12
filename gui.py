#!/usr/bin/env python3
"""
Discord Badge Maximizer - Modern Desktop GUI Application
Provides full graphical controls for:
  1. 🔴 Live Streamer 24/7 (Streaming Badge)
  2. 🎮 Game Hours Farmer (Gamer Badge)
  3. 🔒 Token & Credential Vault (TripleDES + AES-256)
  4. 📜 Live Console Terminal (Color-Coded Real-Time Logs)
"""

import os
import sys
import time
import json
import uuid
import queue
import re
import base64
import urllib.request
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
)
from games import ApplicationController, AccountSessionState

# UI Appearance
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

CONFIG_FILE = APP_DIR / "data" / "gui_config.json"
CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)


class GUIConsoleLogger:
    """Thread-safe pipe that intercepts backend console logs and queues them for the GUI."""

    def __init__(self, log_queue: queue.Queue):
        self.queue = log_queue
        self.ansi_regex = re.compile(r"\x1b\[[0-9;]*[mGKHJ]")

    def write(self, message: str) -> None:
        if message:
            # Strip ANSI escape codes for clean GUI display, but detect tag color
            raw_clean = self.ansi_regex.sub("", message)
            if raw_clean.strip():
                tag = "info"
                if "✓" in message or "establish" in message.lower() or "active" in message.lower() or "success" in message.lower():
                    tag = "success"
                elif "!" in message or "warn" in message.lower() or "paused" in message.lower():
                    tag = "warning"
                elif "✗" in message or "error" in message.lower() or "fail" in message.lower():
                    tag = "error"
                elif "●" in message or "live" in message.lower():
                    tag = "live"
                self.queue.put((raw_clean.rstrip(), tag))

    def flush(self) -> None:
        pass


class DiscordBadgeMaximizerApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Discord Badge Maximizer - GUI Edition")
        self.geometry("1020x720")
        self.minsize(920, 640)

        # State management
        self.log_queue = queue.Queue()
        self.gui_logger = GUIConsoleLogger(self.log_queue)

        self.stream_engine: Optional[GatewayStreamEngine] = None
        self.stream_thread: Optional[threading.Thread] = None
        self.stream_running = False

        self.farm_thread: Optional[threading.Thread] = None
        self.farm_running = False

        # Load persisted config
        self.saved_state = self._load_persisted_state()

        self._setup_layout()
        self._load_values_into_ui()

        # Start background polling timer for logs and stream status
        self.after(100, self._poll_log_queue)
        self.after(500, self._update_stream_telemetry)

    # ----------------------------------------------------
    # Configuration Persistence
    # ----------------------------------------------------
    def _load_persisted_state(self) -> State:
        try:
            if CONFIG_FILE.exists():
                return State.load(CONFIG_FILE)
        except Exception:
            pass
        return State()

    def _save_current_state(self) -> None:
        self.saved_state.token = self.entry_main_token.get().strip()
        self.saved_state.viewer_token = self.entry_alt_token.get().strip()
        self.saved_state.cookie = self.entry_cookie.get().strip()

        cid = self.entry_channel_id.get().strip()
        self.saved_state.last_channel_id = "" if self.switch_random_vc.get() else extract_channel_id(cid)

        # Video source
        src_mode = self.video_source_mode.get()
        if src_mode == "youtube":
            self.saved_state.stream_url = self.entry_stream_url.get().strip()
        elif src_mode == "custom_file":
            self.saved_state.stream_url = self.entry_custom_file.get().strip()
        else:
            self.saved_state.stream_url = ""

        try:
            self.saved_state.save(CONFIG_FILE)
            # Sync with games.py session file
            game_state = AccountSessionState.restore(CONFIG_FILE)
            game_state.token = self.saved_state.token
            game_state.cookie = self.saved_state.cookie
            game_state.persist(CONFIG_FILE)
        except Exception as exc:
            self.gui_logger.write(f"✗ Lỗi lưu file cấu hình: {exc}\n")

    # ----------------------------------------------------
    # GUI Layout Setup
    # ----------------------------------------------------
    def _setup_layout(self):
        # Configure grid
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
            text="🔴 Live Stream 24/7 & 🎮 Game Hours Farmer",
            font=ctk.CTkFont(size=12),
            text_color="#949ba4",
        )
        lbl_sub.grid(row=0, column=1, padx=5, pady=15, sticky="w")

        self.lbl_vault_badge = ctk.CTkLabel(
            header,
            text="🔒 Vault: TripleDES + AES-256",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#23a55a",
            fg_color="#111214",
            corner_radius=6,
            padx=10,
            pady=4,
        )
        self.lbl_vault_badge.grid(row=0, column=2, padx=20, pady=15, sticky="e")

        # Main Tabview
        self.tabview = ctk.CTkTabview(self, fg_color="#2b2d31", segmented_button_selected_color="#5865F2")
        self.tabview.grid(row=1, column=0, padx=15, pady=(10, 15), sticky="nsew")

        self.tab_stream = self.tabview.add("🔴 Live Streamer (Badge)")
        self.tab_games = self.tabview.add("🎮 Cày Giờ Game (Gamer)")
        self.tab_config = self.tabview.add("⚙️ Cấu hình & Vault")
        self.tab_logs = self.tabview.add("📜 Nhật Ký Hoạt Động (Logs)")

        self._build_stream_tab()
        self._build_games_tab()
        self._build_config_tab()
        self._build_logs_tab()

    # ----------------------------------------------------
    # TAB 1: Live Streamer
    # ----------------------------------------------------
    def _build_stream_tab(self):
        tab = self.tab_stream
        tab.grid_columnconfigure(0, weight=3)
        tab.grid_columnconfigure(1, weight=2)
        tab.grid_rowconfigure(2, weight=1)

        # Left Column: Inputs & Controls
        left_frame = ctk.CTkFrame(tab, fg_color="#313338", corner_radius=10)
        left_frame.grid(row=0, column=0, rowspan=3, padx=(5, 10), pady=5, sticky="nsew")
        left_frame.grid_columnconfigure(1, weight=1)

        lbl_sec1 = ctk.CTkLabel(left_frame, text="🔑 Thông Tin Tài Khoản", font=ctk.CTkFont(size=14, weight="bold"), text_color="#ffffff")
        lbl_sec1.grid(row=0, column=0, columnspan=2, padx=15, pady=(15, 10), sticky="w")

        # Token Main
        ctk.CTkLabel(left_frame, text="Token Chính:", font=ctk.CTkFont(size=12)).grid(row=1, column=0, padx=15, pady=6, sticky="w")
        self.entry_main_token = ctk.CTkEntry(left_frame, placeholder_text="Dán Discord token tài khoản chính...", show="•")
        self.entry_main_token.grid(row=1, column=1, padx=(0, 15), pady=6, sticky="ew")

        # Token Alt
        ctk.CTkLabel(left_frame, text="Token Phụ (Alt):", font=ctk.CTkFont(size=12)).grid(row=2, column=0, padx=15, pady=6, sticky="w")
        self.entry_alt_token = ctk.CTkEntry(left_frame, placeholder_text="Token acc phụ tự động xem stream (tùy chọn)...", show="•")
        self.entry_alt_token.grid(row=2, column=1, padx=(0, 15), pady=6, sticky="ew")

        # Show/Hide Tokens
        self.show_tokens_var = ctk.BooleanVar(value=False)
        chk_show = ctk.CTkCheckBox(left_frame, text="Hiện Token", variable=self.show_tokens_var, command=self._toggle_token_visibility, font=ctk.CTkFont(size=11))
        chk_show.grid(row=3, column=1, padx=(0, 15), pady=(0, 10), sticky="w")

        # Voice channel settings
        lbl_sec2 = ctk.CTkLabel(left_frame, text="🎙️ Kênh Voice & Video Nguồn", font=ctk.CTkFont(size=14, weight="bold"), text_color="#ffffff")
        lbl_sec2.grid(row=4, column=0, columnspan=2, padx=15, pady=(10, 6), sticky="w")

        ctk.CTkLabel(left_frame, text="ID Phòng Voice:").grid(row=5, column=0, padx=15, pady=6, sticky="w")
        self.entry_channel_id = ctk.CTkEntry(left_frame, placeholder_text="ID kênh voice (bỏ trống nếu bật auto)...")
        self.entry_channel_id.grid(row=5, column=1, padx=(0, 15), pady=6, sticky="ew")

        self.switch_random_vc = ctk.CTkSwitch(left_frame, text="🎲 Tự động chọn phòng Voice ngẫu nhiên", command=self._on_random_switch)
        self.switch_random_vc.grid(row=6, column=1, padx=(0, 15), pady=(0, 10), sticky="w")

        # Video source options
        self.video_source_mode = ctk.StringVar(value="default")
        rad_def = ctk.CTkRadioButton(left_frame, text="🎞️ Video mẫu lặp vô tận (assets/video.mp4)", variable=self.video_source_mode, value="default", command=self._on_video_source_change)
        rad_def.grid(row=7, column=0, columnspan=2, padx=15, pady=4, sticky="w")

        rad_yt = ctk.CTkRadioButton(left_frame, text="📺 Phát từ Link YouTube / Video URL:", variable=self.video_source_mode, value="youtube", command=self._on_video_source_change)
        rad_yt.grid(row=8, column=0, columnspan=2, padx=15, pady=4, sticky="w")

        self.entry_stream_url = ctk.CTkEntry(left_frame, placeholder_text="https://www.youtube.com/watch?v=...")
        self.entry_stream_url.grid(row=9, column=0, columnspan=2, padx=35, pady=(2, 6), sticky="ew")

        rad_file = ctk.CTkRadioButton(left_frame, text="📁 Chọn file video MP4 trên máy tính:", variable=self.video_source_mode, value="custom_file", command=self._on_video_source_change)
        rad_file.grid(row=10, column=0, columnspan=2, padx=15, pady=4, sticky="w")

        file_box = ctk.CTkFrame(left_frame, fg_color="transparent")
        file_box.grid(row=11, column=0, columnspan=2, padx=35, pady=(2, 15), sticky="ew")
        file_box.grid_columnconfigure(0, weight=1)

        self.entry_custom_file = ctk.CTkEntry(file_box, placeholder_text="C:\\path\\to\\video.mp4")
        self.entry_custom_file.grid(row=0, column=0, sticky="ew")
        btn_browse = ctk.CTkButton(file_box, text="Duyệt...", width=70, command=self._browse_custom_video)
        btn_browse.grid(row=0, column=1, padx=(6, 0))

        # Stream Action Buttons
        btn_box = ctk.CTkFrame(left_frame, fg_color="transparent")
        btn_box.grid(row=12, column=0, columnspan=2, padx=15, pady=(10, 15), sticky="ew")
        btn_box.grid_columnconfigure((0, 1), weight=1)

        self.btn_start_stream = ctk.CTkButton(
            btn_box,
            text="🔴 BẮT ĐẦU STREAM",
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="#23a55a",
            hover_color="#1b8547",
            height=42,
            command=self._start_stream_action,
        )
        self.btn_start_stream.grid(row=0, column=0, padx=(0, 6), sticky="ew")

        self.btn_stop_stream = ctk.CTkButton(
            btn_box,
            text="⏹️ DỪNG STREAM",
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="#da373c",
            hover_color="#a82b2f",
            height=42,
            state="disabled",
            command=self._stop_stream_action,
        )
        self.btn_stop_stream.grid(row=0, column=1, padx=(6, 0), sticky="ew")

        # Right Column: Dashboard & Real-Time Telemetry Cards
        right_frame = ctk.CTkFrame(tab, fg_color="#313338", corner_radius=10)
        right_frame.grid(row=0, column=1, rowspan=3, padx=(10, 5), pady=5, sticky="nsew")
        right_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(right_frame, text="📊 Trạng Thái Trực Tuyến", font=ctk.CTkFont(size=14, weight="bold"), text_color="#ffffff").grid(row=0, column=0, padx=15, pady=(15, 10), sticky="w")

        # Card: Live Status
        self.card_status = self._create_telemetry_card(right_frame, row=1, title="TRẠNG THÁI STREAM", value="⚪ Chưa kết nối", color="#949ba4")
        self.card_session_time = self._create_telemetry_card(right_frame, row=2, title="PHIÊN HIỆN TẠI (TÍCH LŨY)", value="00h:00m:00s (0.00h)", color="#5865F2")
        self.card_total_time = self._create_telemetry_card(right_frame, row=3, title="TỔNG GIỜ STREAM ĐÃ CLAIM", value="0.0h", color="#23a55a")
        self.card_channel = self._create_telemetry_card(right_frame, row=4, title="PHÒNG VOICE", value="Chưa vào phòng", color="#f0b232")
        self.card_viewers = self._create_telemetry_card(right_frame, row=5, title="NGƯỜI XEM (VIEWERS)", value="0 người xem", color="#949ba4")

        # Auto-resilience badge
        lbl_auto_res = ctk.CTkLabel(
            right_frame,
            text="🔄 Chế độ Auto-Resilience: BẬT\nStream chạy liên tục 24/7, tự kết nối lại nếu rớt mạng.",
            font=ctk.CTkFont(size=11),
            text_color="#949ba4",
            justify="center",
        )
        lbl_auto_res.grid(row=6, column=0, padx=15, pady=(20, 15), sticky="ew")

    def _create_telemetry_card(self, parent, row: int, title: str, value: str, color: str) -> ctk.CTkLabel:
        card = ctk.CTkFrame(parent, fg_color="#2b2d31", corner_radius=8)
        card.grid(row=row, column=0, padx=15, pady=5, sticky="ew")
        card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(card, text=title, font=ctk.CTkFont(size=10, weight="bold"), text_color="#949ba4").grid(row=0, column=0, padx=10, pady=(6, 0), sticky="w")
        val_lbl = ctk.CTkLabel(card, text=value, font=ctk.CTkFont(size=15, weight="bold"), text_color=color)
        val_lbl.grid(row=1, column=0, padx=10, pady=(0, 6), sticky="w")
        return val_lbl

    # ----------------------------------------------------
    # TAB 2: Game Hours Farmer
    # ----------------------------------------------------
    def _build_games_tab(self):
        tab = self.tab_games
        tab.grid_columnconfigure(0, weight=1)

        frame = ctk.CTkFrame(tab, fg_color="#313338", corner_radius=10)
        frame.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")
        frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(frame, text="🎮 Cày Giờ Chơi Game (Gamer Badge)", font=ctk.CTkFont(size=16, weight="bold")).grid(row=0, column=0, columnspan=2, padx=20, pady=(15, 10), sticky="w")

        ctk.CTkLabel(frame, text="Số lượng Game cần cày:").grid(row=1, column=0, padx=20, pady=8, sticky="w")
        self.spin_game_count = ctk.CTkComboBox(frame, values=["10", "25", "50", "100", "200"], width=160)
        self.spin_game_count.set("50")
        self.spin_game_count.grid(row=1, column=1, padx=20, pady=8, sticky="w")

        ctk.CTkLabel(frame, text="Số giờ cho mỗi Game:").grid(row=2, column=0, padx=20, pady=8, sticky="w")
        self.spin_game_hours = ctk.CTkComboBox(frame, values=["1.0", "2.0", "5.0", "10.0", "50.0"], width=160)
        self.spin_game_hours.set("1.0")
        self.spin_game_hours.grid(row=2, column=1, padx=20, pady=8, sticky="w")

        ctk.CTkLabel(frame, text="Cookie cf_clearance (Tùy chọn):").grid(row=3, column=0, padx=20, pady=8, sticky="w")
        self.entry_cookie = ctk.CTkEntry(frame, placeholder_text="cf_clearance cookie header (nếu Cloudflare chặn API)...")
        self.entry_cookie.grid(row=3, column=1, padx=20, pady=8, sticky="ew")

        # Action Buttons
        btn_box = ctk.CTkFrame(frame, fg_color="transparent")
        btn_box.grid(row=4, column=0, columnspan=2, padx=20, pady=15, sticky="ew")
        btn_box.grid_columnconfigure((0, 1), weight=1)

        self.btn_start_farm = ctk.CTkButton(
            btn_box,
            text="🚀 BẮT ĐẦU CÀY GAME",
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="#5865F2",
            hover_color="#4752c4",
            height=42,
            command=self._start_game_farming_action,
        )
        self.btn_start_farm.grid(row=0, column=0, padx=(0, 8), sticky="ew")

        self.btn_stop_farm = ctk.CTkButton(
            btn_box,
            text="⏹️ DỪNG CÀY",
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="#da373c",
            hover_color="#a82b2f",
            height=42,
            state="disabled",
            command=self._stop_game_farming_action,
        )
        self.btn_stop_farm.grid(row=0, column=1, padx=(8, 0), sticky="ew")

        # Progress bar
        self.progress_games = ctk.CTkProgressBar(frame, height=12)
        self.progress_games.grid(row=5, column=0, columnspan=2, padx=20, pady=(10, 5), sticky="ew")
        self.progress_games.set(0)

        self.lbl_farm_status = ctk.CTkLabel(frame, text="Trạng thái: Sẵn sàng", font=ctk.CTkFont(size=12), text_color="#949ba4")
        self.lbl_farm_status.grid(row=6, column=0, columnspan=2, padx=20, pady=(2, 15), sticky="w")

    # ----------------------------------------------------
    # TAB 3: Configuration & Profile
    # ----------------------------------------------------
    def _build_config_tab(self):
        tab = self.tab_config
        tab.grid_columnconfigure(0, weight=1)

        frame = ctk.CTkFrame(tab, fg_color="#313338", corner_radius=10)
        frame.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")
        frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(frame, text="🔒 Quản Lý Bảo Mật & Thông Tin Tài Khoản", font=ctk.CTkFont(size=16, weight="bold")).grid(row=0, column=0, padx=20, pady=(15, 10), sticky="w")

        info_box = ctk.CTkFrame(frame, fg_color="#2b2d31", corner_radius=8)
        info_box.grid(row=1, column=0, padx=20, pady=10, sticky="ew")
        info_box.grid_columnconfigure(0, weight=1)

        self.lbl_profile_info = ctk.CTkLabel(
            info_box,
            text="Nhấn nút 'Kiểm Tra Profile Discord' bên dưới để nạp thông tin huy hiệu và tài khoản...",
            font=ctk.CTkFont(size=13),
            text_color="#949ba4",
            justify="left",
        )
        self.lbl_profile_info.grid(row=0, column=0, padx=15, pady=15, sticky="w")

        btn_box = ctk.CTkFrame(frame, fg_color="transparent")
        btn_box.grid(row=2, column=0, padx=20, pady=15, sticky="ew")
        btn_box.grid_columnconfigure((0, 1, 2), weight=1)

        btn_check_profile = ctk.CTkButton(btn_box, text="👤 Kiểm Tra Profile Discord", command=self._fetch_discord_profile)
        btn_check_profile.grid(row=0, column=0, padx=5, sticky="ew")

        btn_save_cfg = ctk.CTkButton(btn_box, text="💾 Lưu Cấu Hình", fg_color="#23a55a", hover_color="#1b8547", command=self._save_config_action)
        btn_save_cfg.grid(row=0, column=1, padx=5, sticky="ew")

        btn_clear_cfg = ctk.CTkButton(btn_box, text="🗑️ Xóa Sạch Token", fg_color="#da373c", hover_color="#a82b2f", command=self._clear_config_action)
        btn_clear_cfg.grid(row=0, column=2, padx=5, sticky="ew")

    # ----------------------------------------------------
    # TAB 4: Live Logs Terminal
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

        # Color tags for log levels
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
    # UI Callbacks & Logic
    # ----------------------------------------------------
    def _toggle_token_visibility(self):
        show_char = "" if self.show_tokens_var.get() else "•"
        self.entry_main_token.configure(show=show_char)
        self.entry_alt_token.configure(show=show_char)

    def _on_random_switch(self):
        if self.switch_random_vc.get():
            self.entry_channel_id.configure(state="disabled")
        else:
            self.entry_channel_id.configure(state="normal")

    def _on_video_source_change(self):
        mode = self.video_source_mode.get()
        if mode == "youtube":
            self.entry_stream_url.configure(state="normal")
            self.entry_custom_file.configure(state="disabled")
        elif mode == "custom_file":
            self.entry_stream_url.configure(state="disabled")
            self.entry_custom_file.configure(state="normal")
        else:
            self.entry_stream_url.configure(state="disabled")
            self.entry_custom_file.configure(state="disabled")

    def _browse_custom_video(self):
        file_path = filedialog.askopenfilename(
            title="Chọn file Video MP4",
            filetypes=[("Video files", "*.mp4 *.mkv *.avi *.mov *.flv"), ("All files", "*.*")],
        )
        if file_path:
            self.entry_custom_file.delete(0, "end")
            self.entry_custom_file.insert(0, file_path)
            self.video_source_mode.set("custom_file")
            self._on_video_source_change()

    def _load_values_into_ui(self):
        st = self.saved_state
        if st.token:
            self.entry_main_token.insert(0, st.token)
        if st.viewer_token:
            self.entry_alt_token.insert(0, st.viewer_token)
        if st.cookie:
            self.entry_cookie.insert(0, st.cookie)
        if st.last_channel_id:
            self.entry_channel_id.insert(0, st.last_channel_id)
            self.switch_random_vc.deselect()
        else:
            self.switch_random_vc.select()
            self._on_random_switch()

        if st.stream_url:
            if st.stream_url.startswith("http://") or st.stream_url.startswith("https://"):
                self.video_source_mode.set("youtube")
                self.entry_stream_url.insert(0, st.stream_url)
            else:
                self.video_source_mode.set("custom_file")
                self.entry_custom_file.insert(0, st.stream_url)
        else:
            self.video_source_mode.set("default")
        self._on_video_source_change()

        self.card_total_time.configure(text=f"{st.total_stream_hours_claimed:,.1f}h")

    # ----------------------------------------------------
    # Streaming Thread Controls
    # ----------------------------------------------------
    def _start_stream_action(self):
        token = self.entry_main_token.get().strip()
        if not token:
            messagebox.showerror("Thiếu Token", "Vui lòng nhập Token Discord của tài khoản chính!")
            return

        self._save_current_state()
        self.btn_start_stream.configure(state="disabled")
        self.btn_stop_stream.configure(state="normal")
        self.stream_running = True

        self.tabview.set("📜 Nhật Ký Hoạt Động (Logs)")
        self.gui_logger.write("ℹ Đang khởi tạo luồng Gateway Live Streamer 24/7...\n")

        # Launch worker thread
        self.stream_thread = threading.Thread(target=self._stream_worker, daemon=True)
        self.stream_thread.start()

    def _stop_stream_action(self):
        if self.stream_engine:
            self.stream_engine.stopped_by_user = True
            self.stream_engine.running = False
            if self.stream_engine.ws:
                try:
                    self.stream_engine.ws.close()
                except Exception:
                    pass

        self.stream_running = False
        self.btn_start_stream.configure(state="normal")
        self.btn_stop_stream.configure(state="disabled")
        self.card_status.configure(text="⚪ Đã dừng phát", text_color="#949ba4")
        self.gui_logger.write("! Người dùng đã bấm dừng stream.\n")

    def _stream_worker(self):
        state = self.saved_state
        session = Session()

        # Build custom GUI console that forwards lines to self.gui_logger
        console = Console()
        console.ok = lambda target, detail: self.gui_logger.write(f"✓ {target} · {detail}\n")
        console.err = lambda target, detail: self.gui_logger.write(f"✗ {target} · {detail}\n")
        console.warn = lambda target, detail: self.gui_logger.write(f"! {target} · {detail}\n")
        console.info = lambda target_or_detail, detail="": self.gui_logger.write(f"ℹ {target_or_detail} · {detail}\n" if detail else f"ℹ {target_or_detail}\n")
        console.live = lambda target, detail: self.gui_logger.write(f"● {target} · {detail}\n")

        target_cid = state.last_channel_id
        target_gid = state.last_guild_id

        # Auto-random channel resolution if empty
        if not target_cid:
            console.info("channel", "tự động dò tìm phòng voice ngẫu nhiên...")
            rcid, rgid, rname = find_random_voice_channel_from_token(state.token, session.super_props)
            if rcid:
                target_cid = rcid
                target_gid = rgid
                console.ok("channel", f"đã chọn phòng voice: #{rname} ({rcid})")
            else:
                console.err("channel", "không tìm thấy phòng voice nào có thể vào!")
                self.after(0, self._stop_stream_action)
                return

        engine = GatewayStreamEngine(state, session, console)
        self.stream_engine = engine
        engine.stopped_by_user = False

        restarts = 0
        while self.stream_running and not engine.stopped_by_user:
            try:
                engine.start_stream(guild_id=target_gid, channel_id=target_cid, wait_for_stdin=False)
            except Exception as exc:
                console.warn("stream", f"lỗi chu kỳ stream: {exc}")

            if not self.stream_running or engine.stopped_by_user:
                break

            restarts += 1
            delay = min(8.0, 2.0 + (restarts % 4) * 1.5)
            console.info("stream", f"🔄 [Auto-Resilience] Tự động kết nối và phát lại sau {delay:.1f}s...")
            time.sleep(delay)

        self.after(0, self._stop_stream_action)

    # ----------------------------------------------------
    # Game Farming Thread Controls
    # ----------------------------------------------------
    def _start_game_farming_action(self):
        token = self.entry_main_token.get().strip()
        if not token:
            messagebox.showerror("Thiếu Token", "Vui lòng nhập Token Discord ở mục Tài Khoản trước!")
            return

        self._save_current_state()
        self.btn_start_farm.configure(state="disabled")
        self.btn_stop_farm.configure(state="normal")
        self.farm_running = True

        self.tabview.set("📜 Nhật Ký Hoạt Động (Logs)")
        self.gui_logger.write("ℹ Đang bắt đầu phiên cày giờ game...\n")

        self.farm_thread = threading.Thread(target=self._farm_worker, daemon=True)
        self.farm_thread.start()

    def _stop_game_farming_action(self):
        self.farm_running = False
        self.btn_start_farm.configure(state="normal")
        self.btn_stop_farm.configure(state="disabled")
        self.lbl_farm_status.configure(text="Trạng thái: Đã dừng cày.")
        self.gui_logger.write("! Người dùng đã dừng phiên cày game.\n")

    def _farm_worker(self):
        try:
            count = int(self.spin_game_count.get())
            hours = float(self.spin_game_hours.get())
        except ValueError:
            count = 50
            hours = 1.0

        controller = ApplicationController(session_path=CONFIG_FILE)
        controller.state.token = self.saved_state.token
        controller.state.cookie = self.saved_state.cookie

        self.lbl_farm_status.configure(text=f"Đang gửi hoạt động chơi game ({count} games, {hours}h/game)...")
        result = controller.start_headless(games_count=count, hours=hours)

        if result.get("success"):
            acc = result.get("accepted", 0)
            tot_h = result.get("total_hours", 0.0)
            self.lbl_farm_status.configure(text=f"✅ Hoàn thành: Đã cày {acc}/{count} game ({tot_h:.1f} giờ)!")
            self.progress_games.set(1.0)
            self.gui_logger.write(f"✓ Cày game thành công: {acc} games được Discord ghi nhận ({tot_h:.1f}h)!\n")
        else:
            err = result.get("error", "Lỗi không xác định")
            self.lbl_farm_status.configure(text=f"❌ Thất bại: {err}")
            self.gui_logger.write(f"✗ Lỗi cày game: {err}\n")

        self.after(0, self._stop_game_farming_action)

    # ----------------------------------------------------
    # Profile & Vault Actions
    # ----------------------------------------------------
    def _fetch_discord_profile(self):
        token = self.entry_main_token.get().strip()
        if not token:
            messagebox.showerror("Thiếu Token", "Vui lòng nhập Token trước khi kiểm tra profile!")
            return

        uid = _user_id_from_token(token)
        if not uid:
            messagebox.showerror("Lỗi", "Không thể giải mã User ID từ token Discord!")
            return

        self.lbl_profile_info.configure(text="⏳ Đang tải thông tin profile từ Discord API...")
        session = Session()

        def _do_fetch():
            try:
                url = f"{USERS_URL}/{uid}/profile?with_mutual_guilds=false&with_mutual_friends_count=false"
                req = urllib.request.Request(
                    url,
                    headers={
                        "authorization": token,
                        "user-agent": USER_AGENT,
                        "x-super-properties": session.super_props,
                    },
                )
                with urllib.request.urlopen(req, timeout=12) as resp:
                    pdata = json.loads(resp.read().decode("utf-8"))

                user = pdata.get("user", {})
                badges = pdata.get("badges", [])
                username = user.get("username", "Unknown")
                global_name = user.get("global_name", "")
                display = f"{global_name} (@{username})" if global_name else f"@{username}"

                badge_names = [b.get("id", "badge") for b in badges]
                badge_str = ", ".join(badge_names) if badge_names else "(Chưa có huy hiệu công khai)"

                msg = (
                    f"👤 Tài khoản: {display}\n"
                    f"🆔 User ID: {uid}\n"
                    f"⏱️ Tổng giờ Stream đã lưu: {self.saved_state.total_stream_hours_claimed:,.1f}h\n"
                    f"🏆 Danh sách Huy hiệu ({len(badges)}): {badge_str}\n"
                    f"🔒 Kho mã hóa: Đã bảo vệ 2 lớp TripleDES + AES-256 an toàn."
                )
                self.after(0, lambda: self.lbl_profile_info.configure(text=msg))
                self.gui_logger.write(f"✓ Đã tải thành công profile của {display} (ID: {uid})\n")
            except Exception as exc:
                self.after(0, lambda: self.lbl_profile_info.configure(text=f"❌ Lỗi tải Profile: {exc}"))
                self.gui_logger.write(f"✗ Lỗi nạp profile: {exc}\n")

        threading.Thread(target=_do_fetch, daemon=True).start()

    def _save_config_action(self):
        self._save_current_state()
        messagebox.showinfo("Đã Lưu", "Cấu hình đã được lưu và mã hóa an toàn trên máy tính!")
        self.gui_logger.write("✓ Cấu hình đã được lưu và mã hóa DES+AES thành công.\n")

    def _clear_config_action(self):
        if messagebox.askyesno("Xác nhận", "Bạn có chắc chắn muốn xóa sạch toàn bộ token và cấu hình đã lưu?"):
            self.entry_main_token.delete(0, "end")
            self.entry_alt_token.delete(0, "end")
            self.entry_cookie.delete(0, "end")
            self.entry_channel_id.delete(0, "end")
            self.entry_stream_url.delete(0, "end")
            self.entry_custom_file.delete(0, "end")
            self._save_current_state()
            messagebox.showinfo("Đã Xóa", "Đã xóa toàn bộ cấu hình!")

    # ----------------------------------------------------
    # Log Terminal Controls & Real-Time Polling
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

    def _update_stream_telemetry(self):
        if self.stream_engine and self.stream_running:
            eng = self.stream_engine
            viewers = eng.get_viewer_count()
            secs = eng.counted_stream_seconds
            t_str = format_timer(secs)
            hours = secs / 3600.0

            if eng.stream_connected:
                lbl = f"🔴 LIVE ({viewers} viewer)" if viewers == 1 else f"🔴 LIVE ({viewers} viewers)"
                self.card_status.configure(text=lbl, text_color="#f47fff")
            elif eng.voice_connected:
                self.card_status.configure(text="⏳ Đang bắt đầu phát...", text_color="#5865F2")
            else:
                self.card_status.configure(text="⏳ Đang kết nối...", text_color="#f0b232")

            self.card_session_time.configure(text=f"{t_str} ({hours:.2f}h)")
            self.card_total_time.configure(text=f"{eng.state.total_stream_hours_claimed:,.1f}h")
            self.card_viewers.configure(text=f"{viewers} người xem")
            if eng.channel_name:
                self.card_channel.configure(text=f"#{eng.channel_name} ({eng.channel_id})")

        self.after(1000, self._update_stream_telemetry)

    def _copy_logs(self):
        content = self.txt_logs.get("1.0", "end-1c")
        self.clipboard_clear()
        self.clipboard_append(content)
        messagebox.showinfo("Đã Sao Chép", "Đã sao chép toàn bộ nhật ký vào Clipboard!")

    def _clear_logs(self):
        self.txt_logs.delete("1.0", "end")


def main():
    app = DiscordBadgeMaximizerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
