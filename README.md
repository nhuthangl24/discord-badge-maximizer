<div align="center">

# Discord Badge Maximizer

**A lightweight, pure Python telemetry and activity engine for Discord profile badges**  
*Tự động cày huy hiệu Discord (Streaming, Game Time, Game Variety) qua Discord Bot (/slash commands) hoặc Terminal.*

<p align="center">
  <a href="https://discord.gg/JaBugGHQ4m"><img src="https://img.shields.io/badge/Discord-Join%20Support-5865F2?style=for-the-badge&logo=discord&logoColor=white" alt="Discord Support" /></a>
  <a href="https://python.org"><img src="https://img.shields.io/badge/Python-3.9+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python Version" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-success?style=for-the-badge" alt="License: MIT" /></a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Discord%20Bot%20(/slash)-Supported-blue?style=for-the-badge&logo=discord" alt="Slash Commands Supported" />
  <img src="https://img.shields.io/badge/Auto%20Random%20Room-Enabled-success?style=for-the-badge" alt="Auto Random Room" />
  <img src="https://img.shields.io/badge/DAVE%20E2EE-Supported-brightgreen?style=for-the-badge" alt="DAVE E2EE" />
</p>

</div>

---

## 📌 Mục lục / Table of Contents

- [🇻🇳 HƯỚNG DẪN TIẾNG VIỆT](#-hướng-dẫn-tiếng-việt)
  - [1. Chế độ điều khiển qua Discord Bot (`bot.py`)](#1-chế-độ-điều-khiển-qua-discord-bot-botpy-khuyên-dùng) ⭐ *(Khuyên dùng)*
  - [2. Danh sách lệnh Slash (`/slash`)](#2-danh-sách-lệnh-slash-slash)
  - [3. Chế độ chạy tự động trên VPS / Terminal (CLI Mode)](#3-chế-độ-chạy-tự-động-trên-vps--terminal-cli-mode)
  - [4. Cách lấy User Token & Cookie](#4-cách-lấy-user-token--cookie-cf_clearance)
- [🇺🇸 ENGLISH GUIDE](#-english-guide)
  - [Features Overview](#features-overview-)
  - [Bot Mode Setup](#bot-mode-setup)
  - [CLI & Terminal Usage](#cli--terminal-usage)
- [Disclaimer & Security](#-disclaimer--security)

---

# 🇻🇳 HƯỚNG DẪN TIẾNG VIỆT

Dự án hỗ trợ 2 cách sử dụng:
1. **Chế độ Discord Bot (`bot.py`):** Điều khiển 100% qua lệnh Slash (`/`) trên Discord, tự động bốc phòng voice ngẫu nhiên mà không cần nhập ID kênh.
2. **Chế độ Terminal / Script (`stream.py` & `games.py`):** Chạy trực tiếp qua menu dòng lệnh hoặc tự động hóa bằng CLI flag trên VPS.

---

### 1. Chế độ điều khiển qua Discord Bot (`bot.py`) ⭐ *(Khuyên dùng)*

Chế độ này cho phép bạn mời một con Bot Discord vào server của mình để điều khiển việc cày huy hiệu cho tài khoản phụ bất cứ lúc nào bằng lệnh Slash.

#### Bước 1: Cài đặt môi trường
Mở terminal trong thư mục dự án:
```bash
# Tạo môi trường ảo
python3 -m venv .venv

# Kích hoạt môi trường và cài đặt thư viện
source .venv/bin/activate
pip install -r requirements.txt
```

#### Bước 2: Tạo Bot Discord & Cấu hình Token
1. Truy cập [Discord Developer Portal](https://discord.com/developers/applications) và bấm **New Application**.
2. Vào mục **Bot** bên thanh menu trái ➔ Bấm **Reset Token** để lấy **Bot Token**.
3. Vào mục **OAuth2** ➔ **URL Generator**:
   * Tích chọn scopes: `bot`, `applications.commands`.
   * Tích chọn permissions: `Administrator` (hoặc các quyền kết nối Voice, gửi tin nhắn).
   * Mở link vừa tạo để mời Bot vào server Discord của bạn.
4. Copy file mẫu `.env.example` thành `.env`:
   ```bash
   cp .env.example .env
   ```
5. Mở file `.env` và dán Token bot:
   ```ini
   DISCORD_BOT_TOKEN=token_bot_vua_lay_o_tren
   ```

#### Bước 3: Khởi chạy Bot
```bash
.venv/bin/python3 bot.py
```
Khi thấy dòng `[Bot] Đã đồng bộ thành công 8 lệnh Slash. Bot đã online...`, bot đã sẵn sàng nhận lệnh trên Discord!

---

### 2. Danh sách lệnh Slash (`/slash`)

| Lệnh Slash | Chức năng | Chi tiết |
| :--- | :--- | :--- |
| **`/panel`** | **Bảng Điều Khiển Nút Bấm** | Mở Dashboard có sẵn các nút bấm trực tiếp: Cài đặt Popup, Bật/Dừng Stream, Cày Game... |
| **`/setup`** | **Mở Popup Cài đặt Tài khoản** | **Hiện cửa sổ Popup (Modal) trên Discord** để điền Token tài khoản chính & phụ, cookie, phòng voice mà không lộ tin nhắn. |
| **`/setup_main`** | **Popup Tài khoản Chính** | Cửa sổ Popup cài đặt riêng cho tài khoản chính (Acc cày). |
| **`/setup_alt`** | **Popup Tài khoản Phụ** | Cửa sổ Popup cài đặt riêng cho tài khoản phụ (Alt Viewer xem stream). |
| **`/stream start`** | **Bắt đầu phát Live Stream** | **Không cần nhập ID phòng!** Bot sẽ tự động tìm và bốc ngẫu nhiên một phòng voice có sẵn để vào phát trực tiếp ngay. |
| **`/stream stop`** | **Dừng Live Stream** | Dừng phát stream và lưu lại tổng số giờ đã cày vào thống kê. |
| **`/stream status`** | **Xem trạng thái Stream** | Xem thời gian đã phát phiên hiện tại, số người xem (viewers), và tổng số giờ trọn đời. |
| **`/farm_games`** | **Cày thời gian chơi Game** | Mở Popup chọn số lượng game và số giờ để cày tự động. |
| **`/profile`** | **Xem Huy hiệu tài khoản** | Hiển thị thông tin acc phụ cùng danh sách các huy hiệu Discord hiện có. |
| **`/config`** | **Xem cấu hình đã lưu** | Xem lại thông tin cấu hình hiện tại (token được che bằng dấu `***`). |
| **`/clear_config`** | **Xóa dữ liệu cấu hình** | Xóa toàn bộ token và cookie đã lưu trong máy. |
| **`/help`** | **Xem hướng dẫn** | Hiển thị danh sách các lệnh và mẹo sử dụng. |

---

### 3. Chế độ chạy tự động trên VPS / Terminal (CLI Mode)

Nếu không muốn chạy bot Discord mà muốn chạy trực tiếp bằng dòng lệnh:

#### Cày Streaming Badge tự động:
```bash
# Tự động vào phòng voice ngẫu nhiên và stream 24/7:
.venv/bin/python3 stream.py --auto

# Hoặc chỉ định phòng voice cụ thể:
.venv/bin/python3 stream.py --auto --channel <CHANNEL_ID>
```

#### Cày Game Time & Game Variety tự động:
```bash
# Cày 50 game, mỗi game 1 giờ:
.venv/bin/python3 games.py --auto --games 50 --hours 1.0
```

#### Chạy giao diện Menu tương tác (CLI Menu):
```bash
.venv/bin/python3 stream.py
# hoặc
.venv/bin/python3 games.py
```

---

### 4. Cách lấy User Token & Cookie `cf_clearance`

#### Cách lấy Discord User Token:
1. Mở Discord trên trình duyệt web (Chrome, Edge, Brave...) tại: [https://discord.com/channels/@me](https://discord.com/channels/@me).
2. Nhấn `F12` (hoặc `Ctrl + Shift + I` / `Cmd + Option + I` trên Mac) để mở **Developer Tools**.
3. Chuyển sang tab **Network**.
4. Trong ô bộ lọc (filter), gõ `science` hoặc `messages`.
5. Nhấp vào bất kỳ request nào xuất hiện ➔ Xem tab **Headers** bên phải.
6. Kéo xuống mục **Request Headers** ➔ Tìm dòng **`authorization`** ➔ Copy chuỗi mã đó (đây là Token của bạn).

#### Cách lấy Cookie `cf_clearance` (cho cày giờ game):
1. Trong cửa sổ Developer Tools (`F12`), chuyển sang tab **Application** (hoặc **Storage** trên Firefox).
2. Bên thanh trái, mở rộng mục **Cookies** ➔ chọn `https://discord.com`.
3. Tìm dòng có tên **`cf_clearance`** và copy giá trị của nó.

> [!NOTE]
> Khi cày giờ Stream, Discord yêu cầu phải có **ít nhất 1 người xem** trong phòng voice thì giờ stream mới bắt đầu được tính. Bạn có thể dùng tính năng **Alt Viewer** (`viewer_token`) để acc phụ thứ 2 tự động ngồi xem stream 24/7 hoàn toàn tự động!

---

# 🇺🇸 ENGLISH GUIDE

## Features Overview 🌟

- ☑️ **Discord Application Bot (`bot.py`):** Control the entire streaming and game farming pipeline directly inside Discord with modern Slash Commands (`/slash`).
- ☑️ **Auto Random Room Discovery:** Automatically scans and joins random accessible voice rooms—no need to manually provide channel or guild IDs.
- ☑️ **DAVE E2EE & RTCP Synchronizer (`stream.py`):** Full implementation of Discord's DAVE (MLS End-to-End Encryption) protocol and 1-second RTCP Sender Reports for seamless viewing on PC, iOS, and Android.
- ☑️ **Headless Automated Alt Viewer:** Automatically controls a secondary alt account to watch the stream via Opcode 20 (`STREAM_WATCH`), accumulating badge hours 24/7 without extra devices.
- ☑️ **Telemetry Engine (`games.py`):** Emits authentic client-side game telemetry events directly to `/api/v9/science` to claim Tier 10 Game Time (5,000h) and Tier 10 Game Variety (100+ titles).
- ☑️ **Built-in Credential Protection:** Full `.gitignore` and `.env` template integration prevents token leakage.

---

## Bot Mode Setup

1. **Install Dependencies:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
2. **Configure Bot Token:**
   Create an Application Bot at the [Discord Developer Portal](https://discord.com/developers/applications), invite it with `bot` and `applications.commands` scopes, and add your bot token to `.env`:
   ```bash
   cp .env.example .env
   # Add DISCORD_BOT_TOKEN=your_token_here inside .env
   ```
3. **Launch the Bot:**
   ```bash
   .venv/bin/python3 bot.py
   ```
4. **Use Slash Commands in Discord:**
   * `/set_token token:<user_token>` — Save alt account token securely (ephemeral).
   * `/stream start` — Automatically joins a random voice room and begins streaming!
   * `/stream status` — View active viewers, stream uptime, and cumulative badge hours.
   * `/stream stop` — End the stream and save hours.
   * `/farm_games count:50 hours:1.0` — Spoof playtime across multiple games.
   * `/profile` — View equipped Discord profile badges.

---

## CLI & Terminal Usage

Run headlessly or with flags on servers / VPS:
```bash
# Auto stream in random accessible channel:
.venv/bin/python3 stream.py --auto

# Target specific channel:
.venv/bin/python3 stream.py --auto --channel <CHANNEL_ID>

# Farm 100 games for 50 hours each (Tier 10 milestones):
.venv/bin/python3 games.py --auto --games 100 --hours 50.0

# Interactive terminal menu:
.venv/bin/python3 stream.py
```

---

## ⚠️ Disclaimer & Security

> [!WARNING]
> This project is designed strictly for educational and security research purposes. Automating user account actions and spoofing telemetry metrics violates Discord's Terms of Service and may result in account termination. Always use secondary/throwaway accounts (alt accounts) that you are comfortable risking. Never use your primary account.

---

## 📜 License

Released under the [MIT License](LICENSE).
