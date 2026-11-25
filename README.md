<div align="center">

# Discord Badge Maximizer & Entertainment Hub

**Hệ Thống Tự Động Hóa Discord Quests, Cày Game Playtime, Mở Hòm "Tối Nay Lọ Gì" & Web Dashboard Admin**

<p align="center">
  <a href="https://discord.gg/JaBugGHQ4m"><img src="https://img.shields.io/badge/Discord-Join%20Support-5865F2?style=for-the-badge&logo=discord&logoColor=white" alt="Discord Support" /></a>
  <a href="https://python.org"><img src="https://img.shields.io/badge/Python-3.9+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python Version" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-success?style=for-the-badge" alt="License: MIT" /></a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Web%20Dashboard-Built--in%20(Port%205000)-blue?style=for-the-badge&logo=googlechrome" alt="Web Dashboard" />
  <img src="https://img.shields.io/badge/Discord%20Auto--Quests-Minute--based%20Pacing-green?style=for-the-badge&logo=discord" alt="Auto Quests" />
  <img src="https://img.shields.io/badge/Tối%20Nay%20Lọ%20Gì-CS:GO%20Case%20Opening-pink?style=for-the-badge" alt="Toi Nay Lo Gi" />
</p>

</div>

---

## 📌 Tính Năng Nổi Bật

1. 🎯 **Discord Auto-Quests Engine (Chống 429 Rate-Limit)**
   - Tự động nhận nhiệm vụ, xem Video Quests, mô phỏng Desktop Heartbeat & Console Gameplay.
   - **Cập nhật tiến trình theo phút** (thay vì vài giây) giúp tin nhắn không bị Discord chặn do rate-limit (429 Too Many Requests).
   - Tự động nhận thưởng (Claim rewards) ngay khi hoàn thành & hiển thị số dư Discord Orbs.

2. 🎰 **Tối Nay Lọ Gì (zennomi/toinaylogi)**
   - Cơ chế mở hòm gacha ngẫu nhiên Idol JAV & Mã phim theo tiêu chuẩn quay số CS:GO Panorama.
   - Hệ thống phẩm cấp (Tier): Mythic (1%), Legendary (4%), Epic (10%), Rare (25%), Common (60%).
   - Kho dữ liệu diễn viên chi tiết: ảnh chân dung, số đo 3 vòng, cup ngực, năm debut và mã phim tiêu biểu.

3. 🌐 **Web Dashboard Quản Trị Trực Tuyến**
   - Chạy trên nền `aiohttp` bất đồng bộ trực tiếp trong tiến trình Bot (cổng mặc định `5000`).
   - **Gửi thông báo & Soạn Embed**:
     - Lựa chọn máy chủ và kênh văn bản trong thời gian thực.
     - Tùy chọn tag: `@everyone`, `@here`, hoặc Role ID.
     - Trình tạo Embed trực quan (Title, Description, Color, Author, Thumbnail, Image, Footer, Timestamp).
     - **Live WYSIWYG Discord Preview**: Xem trước chính xác giao diện Discord Dark Theme trước khi bấm gửi.
   - Quản lý Quests từ xa, xem danh sách máy chủ bot tham gia, mở hòm Lọ Gì và xem Live Console Logs.

4. 🎮 **Cày Game Playtime & Game Variety Badge**
   - Giả lập telemetry hoạt động chơi game Discord để tích lũy giờ chơi và huy hiệu game.

5. 🔒 **Bảo Mật Cấp Độ Doanh Nghiệp (Crypto Vault)**
   - Toàn bộ User Token và Cookie được mã hóa lồng TripleDES + AES-256 trước khi lưu vào đĩa.

---

## 🚀 Hướng Dẫn Sử Dụng Nhanh

### 1. Cài đặt môi trường
Yêu cầu Python 3.9 trở lên:
```bash
python3 -m venv .venv
source .venv/bin/activate  # Trên Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Cấu hình file `.env`
Sao chép `.env.example` thành `.env` và điền Bot Token của bạn:
```ini
DISCORD_BOT_TOKEN=your_bot_token_here
PRIMARY_ADMIN_ID=707934327467671643
PORT=5000
ADMIN_SECRET=mat_khau_admin_tuy_chon
```

### 3. Khởi động Bot
```bash
python3 bot.py
```
Khi bot chạy thành công, console sẽ hiển thị link đăng nhập Web Dashboard:
```text
🚀 [WEB DASHBOARD] Đang chạy tại cổng 5000
🔗 Link truy cập Admin: http://localhost:5000/?token=<ADMIN_SECRET>
```

---

## 📋 Danh Sách Lệnh Slash (`/slash`)

| Lệnh | Phân Loại | Chức Năng |
| :--- | :--- | :--- |
| **`/panel`** | **Giao Diện Chung** | Mở Bảng Điều Khiển nút bấm: Quests, Cày Game, Mở Hòm, Web Dashboard... |
| **`/setup`** | **Cài Đặt** | Mở Popup điền Discord User Token & Cookie cá nhân (mã hóa DES+AES). |
| **`/quest auto`** | **Quests** | Tự động quét và hoàn thành tất cả Quests (cập nhật tiến trình theo phút). |
| **`/quest list`** | **Quests** | Xem danh sách nhiệm vụ hiện có và số dư Discord Orbs. |
| **`/quest claim`** | **Quests** | Nhận phần thưởng cho tất cả nhiệm vụ đã hoàn thành. |
| **`/quest orbs`** | **Quests** | Kiểm tra số dư tiền ảo Discord Orbs của tài khoản. |
| **`/quest panel`** | **Quests** | Mở Bảng Điều Khiển Auto-Quests riêng biệt. |
| **`/toinaylogi quay`** | **Tối Nay Lọ Gì** | Mở 1 hòm CS:GO gacha ngẫu nhiên Idol & Mã phim gợi ý. |
| **`/toinaylogi x10`** | **Tối Nay Lọ Gì** | Mở liền tay 10 hòm gacha và xem diễn viên đỉnh nhất. |
| **`/toinaylogi catalog`** | **Tối Nay Lọ Gì** | Tra cứu kho dữ liệu diễn viên lọc theo Tier (Mythic, Legendary...). |
| **`/toinaylogi ti-le`** | **Tối Nay Lọ Gì** | Xem bảng tỉ lệ phẩm cấp rơi hòm. |
| **`/farm_games`** | **Game Playtime** | Tự động cày giờ chơi game (mở Popup nếu không điền tham số). |
| **`/profile`** | **Hồ Sơ** | Kiểm tra thông tin tài khoản và danh sách huy hiệu Discord. |
| **`/config`** | **Cấu Hình** | Xem cấu hình cá nhân hiện tại (Token đã che giấu). |
| **`/clear_config`** | **Bảo Mật** | Xóa sạch toàn bộ token và cookie của bạn khỏi hệ thống. |
| **`/admin web`** | **Admin** | Lấy link và mã bí mật truy cập Web Dashboard. |
| **`/admin broadcast`** | **Admin** | Gửi thông báo kèm Embed và tag @everyone trực tiếp từ Discord. |
| **`/admin stats`** | **Admin** | Xem thống kê toàn bộ hệ thống Bot (server, user, giờ game). |
| **`/admin quests_auto_all`** | **Admin** | Chạy Auto-Quests cho tất cả users đã lưu Token. |
| **`/admin sync`** | **Admin** | Buộc đồng bộ toàn bộ Slash Commands với Discord API. |

---

## 🌐 Web Dashboard Admin

Web Dashboard cho phép bạn quản trị bot trực quan từ trình duyệt máy tính hoặc điện thoại:
1. **📢 Gửi Thông Báo (Broadcast Center)**:
   - Chọn Server & Kênh bất kỳ.
   - Tag `@everyone`, `@here`, hoặc Role ID.
   - Soạn Embed chuyên nghiệp (Tiêu đề, Mô tả hỗ trợ Markdown, Màu sắc, Ảnh lớn, Thumbnail, Footer).
   - Xem trước trực tiếp dạng Live Discord Dark Theme trước khi gửi.
2. **🎯 Điều Khiển Quests**: Quét Quests và kích hoạt auto flow từ xa.
3. **🎰 Mở Hòm Tối Nay Lọ Gì**: Quay roulette hòm gacha trực quan trên web.
4. **📜 Live Console Logs**: Theo dõi nhật ký hoạt động thời gian thực của Bot.

---

## 🔒 Giấy Phép & Bảo Mật

- **Mã Hóa DES+AES**: Thông tin token cá nhân của mỗi người dùng được lưu trữ riêng biệt tại `data/users/<USER_ID>.json` dưới dạng mã hóa hai tầng đối xứng.
- **MIT License**: Mã nguồn mở hoàn toàn, có thể tùy biến và triển khai tự do.
