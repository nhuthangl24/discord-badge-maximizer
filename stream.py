#!/usr/bin/env python3
"""
Discord Stream Time Engine

Headless Gateway + Voice + Video Streamer + Go Live streamer that accumulates
real streaming hours for the Discord Streaming Badge.

Unlike game badges (tracked via client-side /science telemetry),
the streaming badge requires an ACTIVE gateway voice session with
a registered Go Live stream. This script:

1. Connects to Discord's main Gateway WebSocket
2. Joins a voice channel (Opcode 4 Voice State Update)
3. Completes the Voice WebSocket + UDP handshake
4. Initiates Go Live (Opcode 18 Stream Create)
5. Connects to the stream's dedicated Voice WebSocket
6. Streams real H.264 video and Opus audio from local media (or fallback silent Opus audio)
7. Handles DAVE E2EE (MLS protocol) and periodic RTCP Sender Reports
8. Manages heartbeats across all 3 WebSocket connections

Requirements:
- Python 3.10+
- pip install -r requirements.txt (cryptography>=41.0.0, dave.py>=1.1.0)
- FFmpeg on system PATH (required for real video streaming; silent audio fallback used if missing)
"""
from __future__ import annotations

import base64
import json
import os
import random
import re
import select
import shutil
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Sequence, Optional

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    HAS_CRYPTO = True
except ImportError:
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "cryptography"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        HAS_CRYPTO = True
    except Exception:
        HAS_CRYPTO = False

try:
    import dave
    HAS_DAVE = True
except ImportError:
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "dave.py"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        import dave
        HAS_DAVE = True
    except Exception:
        HAS_DAVE = False

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "session.json"

# Auto-expand system PATH to ensure ffmpeg and other tools in Homebrew/local paths are found
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


def find_ffmpeg_binary() -> str:
    """Finds the ffmpeg executable across system PATH, Homebrew, Linux locations, and imageio_ffmpeg."""
    found = shutil.which("ffmpeg")
    if found:
        return found

    # Check bundled python imageio-ffmpeg if available
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.isfile(exe):
            return exe
    except Exception:
        pass

    binary_name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    for d in _common_bin_dirs:
        candidate = Path(d) / binary_name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)

    local_candidate = ROOT / binary_name
    if local_candidate.is_file() and os.access(local_candidate, os.X_OK):
        return str(local_candidate)

    # Auto-fallback: attempt on-the-fly install of imageio-ffmpeg wheel
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "imageio-ffmpeg"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.isfile(exe):
            return exe
    except Exception:
        pass

    return "ffmpeg"


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


def safe_input(prompt: str = "") -> str:
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        return ""

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ME_URL = "https://discord.com/api/v9/users/@me?with_analytics_token=true"
USERS_URL = "https://discord.com/api/v9/users"
SCIENCE_URL = "https://discord.com/api/v9/science"
PROPERTIES_URL = "https://cordapi.dolfi.es/api/v2/properties/windows"
GATEWAY_URL = "gateway.discord.gg"

CLIENT_VERSION = "1.0.9253"
CLIENT_BUILD_NUMBER = 594031
NATIVE_BUILD_NUMBER = 88414
OS_VERSION = "10.0.26200"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) discord/1.0.9253 Chrome/148.0.7778.280 "
    "Electron/42.7.1 Safari/537.36"
)

AUTH_MAX_AGE = 12 * 3600

STREAM_TIERS = [
    (1, "Newcomer", 1),
    (2, "Fledgling", 5),
    (3, "Breakout", 20),
    (4, "Standout", 75),
    (5, "Trendsetter", 150),
    (6, "Headliner", 300),
    (7, "Star", 500),
    (8, "Sensation", 1000),
    (9, "Visionary", 2000),
    (10, "Phenomenon", 5000),
]

OPUS_SILENCE = b"\xf8\xff\xfe"


def _user_id_from_token(token: str) -> str:
    try:
        first = token.split(".", 1)[0]
        pad = "=" * (-len(first) % 4)
        return base64.b64decode(first + pad).decode("utf-8")
    except Exception:
        return ""


class Console:
    RED = "\x1b[91m"
    ORANGE = "\x1b[38;5;208m"
    GREEN = "\x1b[92m"
    YELLOW = "\x1b[93m"
    CYAN = "\x1b[96m"
    WHITE = "\x1b[97m"
    GREY = "\x1b[90m"
    MAGENTA = "\x1b[95m"
    RESET = "\x1b[0m"

    def __init__(self) -> None:
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
        if sys.platform == "win32":
            self._enable_ansi()

    @staticmethod
    def _enable_ansi() -> None:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)

    @staticmethod
    def get_terminal_size() -> int:
        try:
            columns, _ = shutil.get_terminal_size()
            return columns
        except Exception:
            return 80

    def create_border(self, char: str = "═") -> str:
        width = self.get_terminal_size()
        return char * max(10, width - 2)

    def _stamp(self) -> str:
        return f"{self.GREY}{time.strftime('%H:%M:%S')}{self.RESET}"

    def _line(self, glyph: str, target: str, detail: str, color: str) -> None:
        print(f"  {self._stamp()} {glyph} {self.WHITE}{target}{self.RESET} {self.GREY}·{self.RESET} {color}{detail}{self.RESET}")

    def ok(self, target: str, detail: str) -> None:
        self._line(f"{self.GREEN}✓{self.RESET}", target, detail, self.GREEN)

    def sent(self, target: str, detail: str) -> None:
        self._line(f"{self.CYAN}↑{self.RESET}", target, detail, self.CYAN)

    def live(self, target: str, detail: str) -> None:
        self._line(f"{self.MAGENTA}●{self.RESET}", target, detail, self.MAGENTA)

    def warn(self, target: str, detail: str) -> None:
        self._line(f"{self.YELLOW}!{self.RESET}", target, detail, self.YELLOW)

    def err(self, target: str, detail: str) -> None:
        self._line(f"{self.RED}✗{self.RESET}", target, detail, self.RED)

    def info(self, target_or_detail: str, detail: str = "") -> None:
        if detail:
            self._line(f"{self.CYAN}ℹ{self.RESET}", target_or_detail, detail, self.WHITE)
        else:
            print(f"  {self._stamp()} {self.GREY}·{self.RESET} {self.WHITE}{target_or_detail}{self.RESET}")

    def rule(self, char: str = "─", color: str | None = None) -> None:
        width = self.get_terminal_size()
        c = color if color is not None else self.CYAN
        print(f"{c}{char * width}{self.RESET}")

    def print_separator(self, char: str = "─") -> None:
        self.rule(char, self.CYAN)

    def clear(self) -> None:
        print("\x1b[2J\x1b[3J\x1b[H", end="")

    def banner(self) -> None:
        self.clear()
        width = self.get_terminal_size()
        border = self.create_border("═")
        title = "DISCORD STREAM MAXIMIZER"
        subtitle = "Gateway + Voice + Video Streamer + Go Live Streaming Engine"
        pad_t = " " * max(0, (width - len(title) - 2) // 2)
        pad_t_r = " " * max(0, width - len(title) - 2 - len(pad_t))
        pad_s = " " * max(0, (width - len(subtitle) - 2) // 2)
        pad_s_r = " " * max(0, width - len(subtitle) - 2 - len(pad_s))
        print()
        print(f"{self.CYAN}╔{border}╗{self.RESET}")
        print(f"{self.CYAN}║{self.RESET}{pad_t}{self.WHITE}{title}{self.RESET}{pad_t_r}{self.CYAN}║{self.RESET}")
        print(f"{self.CYAN}║{self.RESET}{pad_s}{self.GREY}{subtitle}{self.RESET}{pad_s_r}{self.CYAN}║{self.RESET}")
        print(f"{self.CYAN}╚{border}╝{self.RESET}")
        print()


@dataclass
class State:
    token: str = ""
    cookie: str = ""
    fingerprint: str = ""
    analytics_token: str = ""
    fetched_at: int = 0
    total_stream_hours_claimed: float = 0.0
    total_stream_sessions: int = 0
    last_guild_id: str = ""
    last_channel_id: str = ""
    viewer_token: str = ""
    stream_url: str = ""
    used_games: list[str] = field(default_factory=list)
    total_games_claimed: int = 0
    total_hours_claimed: float = 0.0
    _filepath: Optional[Path] = field(default=None, repr=False)

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "State":
        target = path or STATE_FILE
        inst = cls()
        inst._filepath = target
        if target.exists():
            try:
                data = json.loads(target.read_text(encoding="utf-8"))
                known_keys = set(cls().__dict__.keys())
                filtered = {k: v for k, v in data.items() if k in known_keys and k != "used_games" and not k.startswith("_")}
                inst = cls(**filtered)
                inst.used_games = data.get("used_games", [])
                inst._filepath = target
            except Exception:
                pass
        # Fallback to environment variables only if no explicit path is passed
        if not path:
            if not inst.token and os.environ.get("DISCORD_TOKEN"):
                inst.token = os.environ["DISCORD_TOKEN"].strip()
            if not inst.cookie and os.environ.get("DISCORD_COOKIE"):
                inst.cookie = os.environ["DISCORD_COOKIE"].strip()
            if not inst.viewer_token and os.environ.get("DISCORD_VIEWER_TOKEN"):
                inst.viewer_token = os.environ["DISCORD_VIEWER_TOKEN"].strip()
            if not inst.last_channel_id and os.environ.get("DISCORD_CHANNEL_ID"):
                inst.last_channel_id = os.environ["DISCORD_CHANNEL_ID"].strip()
            if not inst.last_guild_id and os.environ.get("DISCORD_GUILD_ID"):
                inst.last_guild_id = os.environ["DISCORD_GUILD_ID"].strip()
            if not inst.stream_url and os.environ.get("DISCORD_STREAM_URL"):
                inst.stream_url = os.environ["DISCORD_STREAM_URL"].strip()
        return inst

    def save(self, path: Optional[Path] = None) -> None:
        target = path or getattr(self, "_filepath", None) or STATE_FILE
        target.parent.mkdir(parents=True, exist_ok=True)
        existing = {}
        if target.exists():
            try:
                existing = json.loads(target.read_text(encoding="utf-8"))
            except Exception:
                pass
        save_data = {k: v for k, v in asdict(self).items() if not k.startswith("_")}
        existing.update(save_data)
        target.write_text(json.dumps(existing, indent=2), encoding="utf-8")

    @property
    def is_stale(self) -> bool:
        return not self.analytics_token or (time.time() - self.fetched_at) > AUTH_MAX_AGE

    @property
    def has_cookie(self) -> bool:
        return bool(self.cookie.strip())

    @property
    def has_token(self) -> bool:
        return bool(self.token.strip())

    @property
    def has_viewer_token(self) -> bool:
        return bool(self.viewer_token.strip())


def _local_props() -> dict:
    return {
        "os": "Windows",
        "browser": "Discord Client",
        "release_channel": "stable",
        "client_version": CLIENT_VERSION,
        "os_version": OS_VERSION,
        "os_arch": "x64",
        "app_arch": "x64",
        "system_locale": "en-US",
        "has_client_mods": False,
        "browser_user_agent": USER_AGENT,
        "browser_version": "42.7.1",
        "os_sdk_version": "26200",
        "client_build_number": CLIENT_BUILD_NUMBER,
        "native_build_number": NATIVE_BUILD_NUMBER,
        "client_event_source": None,
        "client_app_state": "focused",
    }


def build_super_props(launch_signature: str, heartbeat_session: str) -> tuple[str, dict]:
    try:
        req = urllib.request.Request(
            PROPERTIES_URL,
            method="POST",
            data=b"{}",
            headers={"content-type": "application/json", "user-agent": USER_AGENT},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            props = json.loads(resp.read().decode("utf-8"))["properties"]
    except Exception:
        props = _local_props()
    props["client_launch_id"] = str(uuid.uuid4())
    props["launch_signature"] = launch_signature
    props["client_heartbeat_session_id"] = heartbeat_session
    raw = json.dumps(props, separators=(",", ":")).encode("utf-8")
    return base64.b64encode(raw).decode("ascii"), props


def _read_analytics_token(auth_token: str, super_props: str) -> str:
    req = urllib.request.Request(
        ME_URL,
        headers={
            "authorization": auth_token,
            "user-agent": USER_AGENT,
            "x-super-properties": super_props,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"token rejected (HTTP {exc.code})") from exc
    token = data.get("analytics_token")
    if not token:
        raise RuntimeError("no analytics_token returned by discord")
    return token


def ensure_token(state: State, super_props: str) -> None:
    if not state.is_stale:
        return
    state.analytics_token = _read_analytics_token(state.token, super_props)
    state.fetched_at = int(time.time())
    state.save()


@dataclass(frozen=True)
class Session:
    heartbeat_session: str
    launch_signature: str
    super_props: str
    props_dict: dict

    @classmethod
    def new(cls) -> "Session":
        hb = str(uuid.uuid4())
        sig = str(uuid.uuid4())
        b64_props, raw_props = build_super_props(sig, hb)
        return cls(hb, sig, b64_props, raw_props)


class MiniWebSocket:
    """Pure stdlib websocket client (RFC 6455) for discord gateway communication."""

    def __init__(self, host: str, port: int = 443, path: str = "/") -> None:
        host_clean = host.replace("wss://", "").replace("https://", "").rstrip("/")
        if ":" in host_clean:
            h, p = host_clean.split(":", 1)
            self.host = h
            try:
                self.port = int(p)
            except ValueError:
                self.port = port
        else:
            self.host = host_clean
            self.port = port

        self.path = path
        self.sock: ssl.SSLSocket | None = None
        self.closed = True
        self._lock = threading.RLock()

    def connect(self) -> None:
        raw_sock = socket.create_connection((self.host, self.port), timeout=15)
        ctx = ssl.create_default_context()
        self.sock = ctx.wrap_socket(raw_sock, server_hostname=self.host)
        sec_key = base64.b64encode(os.urandom(16)).decode("ascii")

        host_hdr = self.host if self.port == 443 else f"{self.host}:{self.port}"
        req = (
            f"GET {self.path} HTTP/1.1\r\n"
            f"Host: {host_hdr}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {sec_key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
            f"User-Agent: {USER_AGENT}\r\n\r\n"
        )
        self.sock.sendall(req.encode("utf-8"))

        header_data = b""
        while b"\r\n\r\n" not in header_data:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("failed handshake")
            header_data += chunk

        first_line = header_data.split(b"\r\n")[0].decode("utf-8", "ignore")
        if "101" not in first_line:
            raise ConnectionError(f"handshake rejected: {first_line}")
        self.closed = False

    def send(self, data: str) -> None:
        with self._lock:
            if self.closed or not self.sock:
                raise ConnectionError("socket closed")
            try:
                payload = data.encode("utf-8")
                length = len(payload)
                mask_key = os.urandom(4)

                header = bytearray([0x81])
                if length <= 125:
                    header.append(0x80 | length)
                elif length <= 65535:
                    header.append(0x80 | 126)
                    header.extend(length.to_bytes(2, "big"))
                else:
                    header.append(0x80 | 127)
                    header.extend(length.to_bytes(8, "big"))

                header.extend(mask_key)
                masked = bytearray(payload[i] ^ mask_key[i % 4] for i in range(length))
                self.sock.sendall(header + masked)
            except Exception as e:
                self.close()
                raise ConnectionError(f"socket write error: {e}") from e

    def send_binary(self, data: bytes) -> None:
        with self._lock:
            if self.closed or not self.sock:
                raise ConnectionError("socket closed")
            try:
                length = len(data)
                mask_key = os.urandom(4)
                header = bytearray([0x82])
                if length <= 125:
                    header.append(0x80 | length)
                elif length <= 65535:
                    header.append(0x80 | 126)
                    header.extend(length.to_bytes(2, "big"))
                else:
                    header.append(0x80 | 127)
                    header.extend(length.to_bytes(8, "big"))
                header.extend(mask_key)
                masked = bytearray(data[i] ^ mask_key[i % 4] for i in range(length))
                self.sock.sendall(header + masked)
            except Exception as e:
                self.close()
                raise ConnectionError(f"socket write error: {e}") from e

    def _recv_exact(self, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("socket closed during read")
            buf.extend(chunk)
        return bytes(buf)

    def recv(self, timeout: float = 5.0) -> dict | None:
        if self.closed or not self.sock:
            return None
        try:
            r, _, _ = select.select([self.sock], [], [], timeout)
            if not r:
                return None
            head = self._recv_exact(2)
        except Exception:
            self.close()
            return None

        opcode = head[0] & 0x0F
        if opcode == 0x8:
            code = 0
            reason = ""
            try:
                c_len = head[1] & 0x7F
                if c_len >= 2:
                    p = self._recv_exact(c_len)
                    code = struct.unpack(">H", p[:2])[0]
                    reason = p[2:].decode("utf-8", "ignore")
            except Exception:
                pass
            self.close()
            return {"op": "close", "code": code, "reason": reason}
        if opcode == 0x9:
            try:
                with self._lock:
                    if self.sock and not self.closed:
                        pong = bytearray([0x8A, 0x80])
                        pong.extend(os.urandom(4))
                        self.sock.sendall(pong)
            except Exception:
                self.close()
            return {"op": "ping"}

        length = head[1] & 0x7F
        try:
            if length == 126:
                ext = self._recv_exact(2)
                length = int.from_bytes(ext, "big")
            elif length == 127:
                ext = self._recv_exact(8)
                length = int.from_bytes(ext, "big")

            payload = self._recv_exact(length)
            if opcode == 0x2:
                return {"op": "binary", "data": payload}
            return json.loads(payload.decode("utf-8"))
        except Exception:
            self.close()
            return None

    def close(self) -> None:
        self.closed = True
        with self._lock:
            if self.sock:
                try:
                    self.sock.close()
                except Exception:
                    pass
                self.sock = None


def resolve_media_source(source_url_or_path: str, console: Optional[Console] = None) -> str:
    """
    Resolves video source path or URL.
    - If empty, returns the default local assets/video.mp4.
    - If local file exists, returns it.
    - If YouTube URL, uses yt-dlp to extract the direct stream URL.
    - If direct HTTP/HTTPS URL, returns it.
    """
    src = (source_url_or_path or "").strip()
    default_video = str(ROOT / "assets" / "video.mp4")

    if not src:
        return default_video if os.path.isfile(default_video) else ""

    if os.path.isfile(src):
        return src

    if "youtube.com" in src or "youtu.be" in src:
        try:
            import yt_dlp
            if console:
                console.info("youtube", f"extracting live stream URL from {src}...")
            ydl_opts = {
                "extractor_args": {
                    "youtube": {
                        "player_client": ["android", "ios", "web", "mweb"]
                    }
                },
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
                "noplaylist": True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(src, download=False)
                if "entries" in info and info["entries"]:
                    info = info["entries"][0]
                stream_url = info.get("url")
                if not stream_url and info.get("formats"):
                    for f in reversed(info["formats"]):
                        if f.get("url") and f.get("vcodec") != "none" and f.get("acodec") != "none":
                            stream_url = f["url"]
                            break
                    if not stream_url:
                        for f in reversed(info["formats"]):
                            if f.get("url") and f.get("vcodec") != "none":
                                stream_url = f["url"]
                                break
                    if not stream_url:
                        for f in reversed(info["formats"]):
                            if f.get("url"):
                                stream_url = f["url"]
                                break

                if stream_url:
                    if console:
                        title = info.get("title", "YouTube Stream")
                        console.ok("youtube", f"resolved: {title}")
                    return stream_url
        except Exception as exc:
            if console:
                console.warn("youtube", f"yt-dlp extraction failed ({exc}), falling back to default video")
            return default_video if os.path.isfile(default_video) else src

    if src.startswith("http://") or src.startswith("https://"):
        return src

    return default_video if os.path.isfile(default_video) else ""


class MediaStreamPump:
    def __init__(self, voice_conn: "VoiceConnection", video_path: str, console: Console) -> None:
        self.conn = voice_conn
        self.video_path = video_path
        self.console = console
        self.running = False
        self.v_proc: subprocess.Popen | None = None
        self.a_proc: subprocess.Popen | None = None
        self.v_thread: threading.Thread | None = None
        self.a_thread: threading.Thread | None = None
        self.is_url = bool(self.video_path.startswith("http://") or self.video_path.startswith("https://"))

    def _send_rtcp_sr(self, cipher: AESGCM, ssrc: int, rtp_ts: int, pkt_count: int, byte_count: int) -> None:
        now_wall = time.time()
        ntp_now = now_wall + 2208988800.0
        ntp_msw = int(ntp_now)
        ntp_lsw = int((ntp_now - ntp_msw) * 0x100000000) & 0xFFFFFFFF
        header = struct.pack(">BBHI", 0x80, 200, 6, ssrc)
        payload = struct.pack(">IIIII", ntp_msw, ntp_lsw, rtp_ts & 0xFFFFFFFF, pkt_count & 0xFFFFFFFF, byte_count & 0xFFFFFFFF)
        with self.conn._nonce_lock:
            self.conn.nonce_counter += 1
            cnt = self.conn.nonce_counter
        nonce = struct.pack(">I", cnt) + b"\x00" * 8
        enc_sr = cipher.encrypt(nonce, payload, header)
        rtcp_packet = header + enc_sr + struct.pack(">I", cnt)
        try:
            self.conn.udp_sock.sendto(rtcp_packet, (self.conn.voice_ip, self.conn.voice_port))
        except Exception:
            pass

    def _spawn_video(self) -> subprocess.Popen | None:
        try:
            ffmpeg_bin = find_ffmpeg_binary()
            v_input = ["-stream_loop", "-1"]
            if self.is_url:
                v_input.extend([
                    "-reconnect", "1",
                    "-reconnect_at_eof", "1",
                    "-reconnect_streamed", "1",
                    "-reconnect_delay_max", "5",
                ])

            v_cmd = [
                ffmpeg_bin, "-hide_banner", "-loglevel", "error",
                *v_input,
                "-i", self.video_path,
                "-r", "30",
                "-vf", "scale=848:480",
                "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
                "-threads", "1",
                "-b:v", "1200k", "-maxrate", "1200k", "-bufsize", "2400k",
                "-g", "30", "-keyint_min", "30", "-sc_threshold", "0",
                "-bf", "0", "-b_strategy", "0", "-flags", "+low_delay",
                "-force_key_frames", "expr:gte(t,n_forced*1)",
                "-profile:v", "baseline", "-level", "3.1",
                "-x264opts", "b-pyramid=0:bframes=0:ref=1:no-scenecut=1:keyint=30:min-keyint=30:repeat-headers=1",
                "-pix_fmt", "yuv420p",
                "-bsf:v", "h264_mp4toannexb,dump_extra=freq=keyframe,h264_metadata=aud=insert",
                "-an", "-f", "h264", "pipe:1"
            ]
            return subprocess.Popen(v_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=1048576)
        except Exception as exc:
            self.console.err("stream", f"failed to spawn ffmpeg video process: {exc}")
            return None

    def _spawn_audio(self) -> subprocess.Popen | None:
        try:
            ffmpeg_bin = find_ffmpeg_binary()
            a_input = ["-stream_loop", "-1"]
            if self.is_url:
                a_input.extend([
                    "-reconnect", "1",
                    "-reconnect_at_eof", "1",
                    "-reconnect_streamed", "1",
                    "-reconnect_delay_max", "5",
                ])

            a_cmd = [
                ffmpeg_bin, "-hide_banner", "-loglevel", "error",
                *a_input,
                "-i", self.video_path,
                "-vn", "-c:a", "libopus", "-ar", "48000", "-ac", "2", "-b:a", "64k",
                "-frame_duration", "20",
                "-f", "opus", "pipe:1"
            ]
            return subprocess.Popen(a_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=65536)
        except Exception as exc:
            self.console.err("stream", f"failed to spawn ffmpeg audio process: {exc}")
            return None

    def start(self) -> bool:
        if not self.is_url and not os.path.exists(self.video_path):
            self.console.warn("stream", f"video file not found at {self.video_path}, falling back to silence")
            return False
        if not HAS_CRYPTO:
            self.console.err("crypto", "cryptography package is missing — live video streaming will NOT work! Run: pip install -r requirements.txt")
            return False
        if not self.conn.secret_key or not self.conn.udp_sock:
            self.console.warn("stream", "encryption key or udp socket missing, falling back to silence")
            return False
        try:
            if sys.platform == "win32":
                try:
                    import ctypes
                    ctypes.windll.winmm.timeBeginPeriod(1)
                except Exception:
                    pass
            self.running = True

            self.v_proc = self._spawn_video()
            self.a_proc = self._spawn_audio()
            if not self.v_proc and not self.a_proc:
                self.stop()
                return False

            self.v_thread = threading.Thread(target=self._pump_video, daemon=True)
            self.a_thread = threading.Thread(target=self._pump_audio, daemon=True)
            self.v_thread.start()
            self.a_thread.start()
            src_label = self.video_path if self.is_url else os.path.basename(self.video_path)
            self.console.ok("stream", f"transmitting real video and audio from {src_label} (loop enabled)")
            return True
        except Exception as exc:
            self.console.err("stream", f"failed to start ffmpeg pump: {exc}")
            self.stop()
            return False

    def _pump_video(self) -> None:
        cipher = AESGCM(self.conn.secret_key[:32])
        v_seq = random.randint(0, 0x7FFF)
        v_ts = random.randint(0, 0x7FFFFFFF)
        video_ssrc = self.conn.video_ssrc or (self.conn.ssrc + 1)
        buf = bytearray()
        cur_frame: list[bytes] = []
        next_time = time.monotonic()
        frame_interval = 1.0 / 30.0
        v_pkts = 0
        v_bytes = 0
        last_sr = time.monotonic()

        def send_frame(nalus: list[bytes]) -> None:
            nonlocal v_seq, v_ts, next_time, v_pkts, v_bytes, last_sr
            if not nalus or not self.running:
                return

            if HAS_DAVE and self.conn.dave_encryptor and not self.conn.dave_encryptor.is_passthrough_mode():
                try:
                    annex_b = b"".join(b"\x00\x00\x00\x01" + n for n in nalus)
                    enc_frame = self.conn.dave_encryptor.encrypt(dave.MediaType.video, video_ssrc, annex_b)
                    if enc_frame is not None:
                        parsed_nalus = []
                        ebuf = bytearray(enc_frame)
                        while True:
                            p4 = ebuf.find(b"\x00\x00\x00\x01", 4)
                            p3 = ebuf.find(b"\x00\x00\x01", 3)
                            poss = [x for x in (p4, p3) if x != -1]
                            if not poss:
                                sc = 4 if ebuf.startswith(b"\x00\x00\x00\x01") else (3 if ebuf.startswith(b"\x00\x00\x01") else 0)
                                rem = bytes(ebuf[sc:])
                                if rem:
                                    parsed_nalus.append(rem)
                                break
                            next_p = min(poss)
                            nal = bytes(ebuf[:next_p])
                            ebuf = ebuf[next_p:]
                            sc = 4 if nal.startswith(b"\x00\x00\x00\x01") else 3
                            rem = nal[sc:]
                            if rem:
                                parsed_nalus.append(rem)
                        if parsed_nalus:
                            nalus = parsed_nalus
                except Exception:
                    pass

            for n_idx, data in enumerate(nalus):
                is_last_nalu = (n_idx == len(nalus) - 1)
                hdr = data[0]
                ntype = hdr & 0x1F

                if len(data) <= 1100:
                    marker = 0x80 if is_last_nalu else 0x00
                    rtp_hdr = struct.pack(">BBHII", 0x90, marker | 101, v_seq & 0xFFFF, v_ts & 0xFFFFFFFF, video_ssrc)
                    packet_header = rtp_hdr + b"\xbe\xde\x00\x01"
                    plaintext = b"\x51\x00\x00\x00" + data
                    with self.conn._nonce_lock:
                        self.conn.nonce_counter += 1
                        cnt = self.conn.nonce_counter
                    nonce = struct.pack(">I", cnt) + b"\x00" * 8
                    enc = cipher.encrypt(nonce, plaintext, packet_header)
                    packet = packet_header + enc + struct.pack(">I", cnt)
                    try:
                        self.conn.udp_sock.sendto(packet, (self.conn.voice_ip, self.conn.voice_port))
                        v_pkts += 1
                        v_bytes += len(packet)
                    except Exception:
                        pass
                    v_seq = (v_seq + 1) & 0xFFFF
                else:
                    fnri = hdr & 0xE0
                    fu_ind = fnri | 28
                    body = data[1:]
                    csize = 1100
                    total_chunks = (len(body) + csize - 1) // csize
                    for idx in range(total_chunks):
                        c = body[idx * csize : (idx + 1) * csize]
                        if idx == 0:
                            fu_hdr = 0x80 | ntype
                        elif idx == total_chunks - 1:
                            fu_hdr = 0x40 | ntype
                        else:
                            fu_hdr = ntype
                        fu_payload = bytes([fu_ind, fu_hdr]) + c
                        is_last_packet = is_last_nalu and (idx == total_chunks - 1)
                        marker = 0x80 if is_last_packet else 0x00
                        rtp_hdr = struct.pack(">BBHII", 0x90, marker | 101, v_seq & 0xFFFF, v_ts & 0xFFFFFFFF, video_ssrc)
                        packet_header = rtp_hdr + b"\xbe\xde\x00\x01"
                        plaintext = b"\x51\x00\x00\x00" + fu_payload
                        with self.conn._nonce_lock:
                            self.conn.nonce_counter += 1
                            cnt = self.conn.nonce_counter
                        nonce = struct.pack(">I", cnt) + b"\x00" * 8
                        enc = cipher.encrypt(nonce, plaintext, packet_header)
                        packet = packet_header + enc + struct.pack(">I", cnt)
                        try:
                            self.conn.udp_sock.sendto(packet, (self.conn.voice_ip, self.conn.voice_port))
                            v_pkts += 1
                            v_bytes += len(packet)
                        except Exception:
                            pass
                        v_seq = (v_seq + 1) & 0xFFFF
            v_ts = (v_ts + 3000) & 0xFFFFFFFF
            now = time.monotonic()
            if now - last_sr >= 1.0:
                self._send_rtcp_sr(cipher, video_ssrc, v_ts, v_pkts, v_bytes)
                last_sr = now
            delay = next_time - now
            if delay > 0:
                time.sleep(delay)
            elif delay < -0.1:
                next_time = now
            next_time += frame_interval

        while self.running:
            if not self.v_proc or self.v_proc.poll() is not None:
                self.v_proc = self._spawn_video()
                if not self.v_proc:
                    time.sleep(1.0)
                    continue

            try:
                chunk = self.v_proc.stdout.read(4096)
                if not chunk:
                    # FFmpeg reached EOF or exited, restart seamless loop
                    try:
                        self.v_proc.terminate()
                        self.v_proc.wait(timeout=0.5)
                    except Exception:
                        pass
                    self.v_proc = None
                    time.sleep(0.1)
                    continue
                buf.extend(chunk)
                while True:
                    p4 = buf.find(b"\x00\x00\x00\x01", 4)
                    p3 = buf.find(b"\x00\x00\x01", 3)
                    positions = [p for p in (p4, p3) if p != -1]
                    if not positions:
                        break
                    next_pos = min(positions)
                    nalu = bytes(buf[:next_pos])
                    buf = buf[next_pos:]
                    sc_len = 4 if nalu.startswith(b"\x00\x00\x00\x01") else 3
                    data = nalu[sc_len:]
                    if not data:
                        continue
                    ntype = data[0] & 0x1F
                    if ntype == 9:
                        if cur_frame:
                            send_frame(cur_frame)
                            cur_frame = []
                    else:
                        cur_frame.append(data)
            except Exception:
                time.sleep(0.1)

        if cur_frame:
            send_frame(cur_frame)

    def _pump_audio(self) -> None:
        cipher = AESGCM(self.conn.secret_key[:32])
        a_seq = random.randint(0, 0x7FFF)
        a_ts = random.randint(0, 0x7FFFFFFF)
        buf = bytearray()
        next_time = time.monotonic()
        a_pkts = 0
        a_bytes = 0
        last_a_sr = time.monotonic()

        while self.running:
            if not self.a_proc or self.a_proc.poll() is not None:
                self.a_proc = self._spawn_audio()
                if not self.a_proc:
                    time.sleep(1.0)
                    continue

            try:
                chunk = self.a_proc.stdout.read(4096)
                if not chunk:
                    try:
                        self.a_proc.terminate()
                        self.a_proc.wait(timeout=0.5)
                    except Exception:
                        pass
                    self.a_proc = None
                    time.sleep(0.1)
                    continue
                buf.extend(chunk)
                while len(buf) >= 27:
                    if buf[:4] != b"OggS":
                        idx = buf.find(b"OggS")
                        if idx == -1:
                            buf.clear()
                            break
                        buf = buf[idx:]
                        if len(buf) < 27:
                            break
                    n_segs = buf[26]
                    if len(buf) < 27 + n_segs:
                        break
                    seg_table = buf[27 : 27 + n_segs]
                    body_len = sum(seg_table)
                    if len(buf) < 27 + n_segs + body_len:
                        break
                    body = buf[27 + n_segs : 27 + n_segs + body_len]
                    buf = buf[27 + n_segs + body_len:]
                    offset = 0
                    cur_pkt = bytearray()
                    for s in seg_table:
                        cur_pkt.extend(body[offset : offset + s])
                        offset += s
                        if s < 255:
                            p = bytes(cur_pkt)
                            cur_pkt = bytearray()
                            if p.startswith(b"OpusHead") or p.startswith(b"OpusTags"):
                                continue
                            if HAS_DAVE and self.conn.dave_encryptor and not self.conn.dave_encryptor.is_passthrough_mode():
                                try:
                                    enc_p = self.conn.dave_encryptor.encrypt(dave.MediaType.audio, self.conn.ssrc, p)
                                    if enc_p is not None:
                                        p = enc_p
                                except Exception:
                                    pass
                            rtp = struct.pack(">BBHII", 0x80, 120, a_seq & 0xFFFF, a_ts & 0xFFFFFFFF, self.conn.ssrc)
                            with self.conn._nonce_lock:
                                self.conn.nonce_counter += 1
                                cnt = self.conn.nonce_counter
                            nonce = struct.pack(">I", cnt) + b"\x00" * 8
                            enc = cipher.encrypt(nonce, p, rtp)
                            packet = rtp + enc + struct.pack(">I", cnt)
                            try:
                                self.conn.udp_sock.sendto(packet, (self.conn.voice_ip, self.conn.voice_port))
                                a_pkts += 1
                                a_bytes += len(packet)
                            except Exception:
                                pass
                            a_seq = (a_seq + 1) & 0xFFFF
                            a_ts = (a_ts + 960) & 0xFFFFFFFF
                            now = time.monotonic()
                            if now - last_a_sr >= 1.0:
                                self._send_rtcp_sr(cipher, self.conn.ssrc, a_ts, a_pkts, a_bytes)
                                last_a_sr = now
                            delay = next_time - now
                            if delay > 0:
                                time.sleep(delay)
                            elif delay < -0.1:
                                next_time = now
                            next_time += 0.020
            except Exception:
                time.sleep(0.1)

    def stop(self) -> None:
        self.running = False
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.winmm.timeEndPeriod(1)
            except Exception:
                pass
        if self.v_proc:
            try:
                self.v_proc.terminate()
                self.v_proc.wait(timeout=1.0)
            except Exception:
                try:
                    self.v_proc.kill()
                except Exception:
                    pass
            self.v_proc = None
        if self.a_proc:
            try:
                self.a_proc.terminate()
                self.a_proc.wait(timeout=1.0)
            except Exception:
                try:
                    self.a_proc.kill()
                except Exception:
                    pass
            self.a_proc = None


class VoiceConnection:
    def __init__(
        self,
        endpoint: str,
        server_id: str,
        user_id: str,
        session_id: str,
        token: str,
        console: Console,
        label: str = "voice",
        is_stream: bool = False,
        stream_key: str = "",
        channel_id: str = "",
    ) -> None:
        cleaned = endpoint.replace("wss://", "").replace("https://", "").rstrip("/")
        if ":" in cleaned:
            self.host, port_str = cleaned.split(":", 1)
            try:
                self.port = int(port_str)
            except ValueError:
                self.port = 443
        else:
            self.host = cleaned
            self.port = 443
        self.endpoint = f"{self.host}:{self.port}"
        self.server_id = server_id
        self.user_id = user_id
        self.session_id = session_id
        self.token = token
        self.console = console
        self.label = label
        self.is_stream = is_stream
        self.stream_key = stream_key
        self.channel_id = channel_id

        self.ws: MiniWebSocket | None = None
        self.udp_sock: socket.socket | None = None
        self.ssrc: int = 0
        self.video_ssrc: int = 0
        self.rtx_ssrc: int = 0
        self.voice_ip: str = ""
        self.voice_port: int = 0
        self.external_ip: str = ""
        self.external_port: int = 0
        self.secret_key: bytes = b""
        self.encryption_mode: str = ""
        self.heartbeat_interval: float = 13.75
        self.running: bool = False
        self.nonce_counter: int = 0
        self._nonce_lock = threading.Lock()
        self._media_pump: MediaStreamPump | None = None
        self._hb_thread: threading.Thread | None = None
        self._audio_thread: threading.Thread | None = None
        self.connected_users: set[str] = set()
        self.dave_session: Any = None
        self.dave_encryptor: Any = None
        self.dave_protocol_version: int = 0

    def _init_dave(self) -> None:
        if not HAS_DAVE:
            return
        try:
            ver = self.dave_protocol_version if self.dave_protocol_version > 0 else 1
            gid = (int(self.server_id) - 1) if (self.is_stream and str(self.server_id).isdigit()) else (int(self.channel_id) if (self.channel_id and str(self.channel_id).isdigit()) else (int(self.server_id) if str(self.server_id).isdigit() else 0))
            self.dave_session = dave.Session()
            self.dave_session.init(ver, gid, str(self.user_id))
            self.dave_encryptor = dave.Encryptor()
            self.dave_encryptor.set_passthrough_mode(True)
            if self.ssrc:
                self.dave_encryptor.assign_ssrc_to_codec(self.ssrc, dave.Codec.opus)
            v_ssrc = self.video_ssrc or (self.ssrc + 1)
            self.dave_encryptor.assign_ssrc_to_codec(v_ssrc, dave.Codec.h264)
            pkg = self.dave_session.get_marshalled_key_package()
            if pkg and self.ws and not self.ws.closed:
                self.ws.send_binary(bytes([26]) + pkg)
        except Exception:
            pass

    def _update_dave_ratchet(self) -> None:
        if not HAS_DAVE or not self.dave_session:
            return
        try:
            if self.dave_protocol_version == 0:
                if self.dave_encryptor:
                    self.dave_encryptor.set_passthrough_mode(True)
                return
            if self.dave_session.has_established_group():
                ratchet = self.dave_session.get_key_ratchet(str(self.user_id))
                if ratchet and self.dave_encryptor:
                    self.dave_encryptor.set_key_ratchet(ratchet)
                    self.dave_encryptor.set_passthrough_mode(False)
        except Exception:
            pass

    def connect(self) -> bool:
        C = self.console
        try:
            self.ws = MiniWebSocket(self.host, port=self.port, path="/?v=7")
            self.ws.connect()
        except Exception as exc:
            C.err(self.label, f"ws connect failed: {exc}")
            return False

        msg = self._recv_op(8, timeout=10)
        if not msg:
            C.err(self.label, "no Hello from voice server")
            return False
        self.heartbeat_interval = msg.get("d", {}).get("heartbeat_interval", 13750) / 1000.0

        identify_d: dict[str, Any] = {
            "server_id": self.server_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "token": self.token,
            "max_dave_protocol_version": 1,
        }
        if self.is_stream:
            if self.stream_key:
                identify_d["stream_key"] = self.stream_key
            identify_d["video"] = True
            identify_d["streams"] = [
                {"type": "screen", "rid": "100", "quality": 100}
            ]
        self.ws.send(json.dumps({"op": 0, "d": identify_d}))

        msg = self._recv_op(2, timeout=10)
        if not msg:
            C.err(self.label, "no Ready from voice server")
            return False

        d = msg.get("d", {})
        self.ssrc = d.get("ssrc", 0)
        streams = d.get("streams", [])
        if streams and isinstance(streams, list) and len(streams) > 0:
            st = streams[0]
            self.video_ssrc = st.get("ssrc", self.ssrc + 1)
            self.rtx_ssrc = st.get("rtx_ssrc", self.video_ssrc + 1)
        else:
            self.video_ssrc = self.ssrc + 1
            self.rtx_ssrc = self.ssrc + 2
        self.voice_ip = d.get("ip", "")
        self.voice_port = d.get("port", 0)
        modes = d.get("modes", [])

        preferred = [
            "aead_aes256_gcm_rtpsize",
            "aead_xchacha20_poly1305_rtpsize",
            "xsalsa20_poly1305_lite",
            "xsalsa20_poly1305",
        ]
        for mode in preferred:
            if mode in modes:
                self.encryption_mode = mode
                break
        if not self.encryption_mode and modes:
            self.encryption_mode = modes[0]

        if not self._udp_discovery():
            C.warn(self.label, "UDP discovery failed, using fallback IP")
            self.external_ip = "127.0.0.1"
            self.external_port = random.randint(10000, 60000)

        codecs = [
            {
                "name": "opus",
                "type": "audio",
                "priority": 1000,
                "payload_type": 120,
            },
        ]
        if self.is_stream:
            codecs.append({
                "name": "H264",
                "type": "video",
                "priority": 1000,
                "payload_type": 101,
                "rtx_payload_type": 102,
                "encode": True,
                "decode": True,
            })
            codecs.append({
                "name": "VP8",
                "type": "video",
                "priority": 2000,
                "payload_type": 103,
                "rtx_payload_type": 104,
                "encode": True,
                "decode": True,
            })

        self.ws.send(json.dumps({
            "op": 1,
            "d": {
                "protocol": "udp",
                "data": {
                    "address": self.external_ip,
                    "port": self.external_port,
                    "mode": self.encryption_mode,
                },
                "codecs": codecs,
            },
        }))

        msg = self._recv_op(4, timeout=10)
        if msg:
            d = msg.get("d", {})
            self.secret_key = bytes(d.get("secret_key", []))
            self.encryption_mode = d.get("mode", self.encryption_mode)
            a_codec = d.get("audio_codec", "opus")
            v_codec = d.get("video_codec", "none")
            self.dave_protocol_version = d.get("dave_protocol_version", 0)
            C.ok(self.label, f"media route ready (audio={a_codec}, video={v_codec}, mode={self.encryption_mode})")
            if HAS_DAVE:
                self._init_dave()

        self.running = True
        return True

    def _recv_op(self, target_op: int, timeout: float = 10) -> dict | None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            remaining = max(0.1, deadline - time.time())
            msg = self.ws.recv(timeout=min(remaining, 2.0))
            if msg is None:
                if self.ws.closed:
                    return None
                continue
            if msg.get("op") == "close":
                C = self.console
                code = msg.get("code")
                reason = msg.get("reason")
                C.err(self.label, f"voice server closed: {code} ({reason})")
                return None
            if msg.get("op") == target_op:
                return msg
            if msg.get("op") == 3:
                self._send_heartbeat()
        return None

    def _send_heartbeat(self) -> None:
        if self.ws and not self.ws.closed:
            try:
                nonce = int(time.time() * 1000)
                self.ws.send(json.dumps({"op": 3, "d": nonce}))
            except Exception:
                pass

    def _udp_discovery(self) -> bool:
        try:
            self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            if sys.platform == "win32":
                try:
                    self.udp_sock.ioctl(0x9800000C, False)
                except Exception:
                    pass
            self.udp_sock.settimeout(5)
            packet = bytearray(74)
            struct.pack_into(">HHI", packet, 0, 0x0001, 70, self.ssrc)
            self.udp_sock.sendto(bytes(packet), (self.voice_ip, self.voice_port))
            data, _ = self.udp_sock.recvfrom(256)
            if len(data) < 74:
                return False
            ip_start = 8
            ip_end = data.index(0, ip_start)
            self.external_ip = data[ip_start:ip_end].decode("utf-8")
            self.external_port = struct.unpack_from(">H", data, 72)[0]
            return bool(self.external_ip)
        except Exception:
            if self.udp_sock:
                try:
                    self.udp_sock.close()
                except Exception:
                    pass
                self.udp_sock = None
            return False

    def start_heartbeat(self) -> None:
        def _loop():
            if str(self.user_id):
                self.connected_users.add(str(self.user_id))
            last_hb = time.time()
            while self.running and self.ws and not self.ws.closed:
                now = time.time()
                if now - last_hb >= self.heartbeat_interval:
                    self._send_heartbeat()
                    last_hb = now
                try:
                    msg = self.ws.recv(timeout=1.0)
                    if not msg:
                        continue
                    op = msg.get("op")
                    if op == 3:
                        self._send_heartbeat()
                        last_hb = time.time()
                    elif op == 11:
                        d = msg.get("d") or {}
                        for uid in d.get("user_ids", []):
                            self.connected_users.add(str(uid))
                        if str(self.user_id):
                            self.connected_users.add(str(self.user_id))
                    elif op == 13:
                        d = msg.get("d") or {}
                        self.connected_users.discard(str(d.get("user_id", "")))
                    elif op == 21:
                        d = msg.get("d") or {}
                        t_id = d.get("transition_id")
                        p_ver = d.get("protocol_version", 1)
                        self.dave_protocol_version = p_ver
                        if not self.dave_session:
                            self._init_dave()
                        if t_id is not None:
                            if t_id == 0:
                                self._init_dave()
                                self._update_dave_ratchet()
                            else:
                                try:
                                    self.ws.send(json.dumps({"op": 23, "d": {"transition_id": t_id}}))
                                except Exception:
                                    pass
                    elif op == 22:
                        d = msg.get("d") or {}
                        self._update_dave_ratchet()
                    elif op == 24:
                        d = msg.get("d") or {}
                        self.dave_protocol_version = d.get("protocol_version", 1)
                        self._init_dave()
                    elif op == "binary":
                        raw = msg.get("data", b"")
                        if raw:
                            if not self.dave_session:
                                self._init_dave()
                            b_op = raw[0] if raw[0] in (25, 26, 27, 28, 29, 30) else (raw[2] if len(raw) >= 3 and raw[2] in (25, 26, 27, 28, 29, 30) else None)
                            offset = 1 if (b_op is not None and raw[0] == b_op) else 3
                            if b_op == 25 and self.dave_session:
                                try:
                                    self.dave_session.set_external_sender(raw[offset:])
                                except Exception:
                                    pass
                            elif b_op == 27 and self.dave_session:
                                try:
                                    commit = self.dave_session.process_proposals(raw[offset:], self.connected_users)
                                    if commit and self.ws and not self.ws.closed:
                                        self.ws.send_binary(bytes([28]) + commit)
                                except Exception:
                                    pass
                            elif b_op == 29:
                                t_id = struct.unpack(">H", raw[offset:offset+2])[0] if len(raw) >= offset + 2 else 0
                                if self.dave_session:
                                    try:
                                        self.dave_session.process_commit(raw[offset+2:])
                                    except Exception:
                                        if t_id and self.ws and not self.ws.closed:
                                            try:
                                                self.ws.send(json.dumps({"op": 31, "d": {"transition_id": t_id}}))
                                            except Exception:
                                                pass
                                        self._init_dave()
                                if t_id and self.ws and not self.ws.closed:
                                    try:
                                        self.ws.send(json.dumps({"op": 23, "d": {"transition_id": t_id}}))
                                    except Exception:
                                        pass
                                self._update_dave_ratchet()
                            elif b_op == 30:
                                t_id = struct.unpack(">H", raw[offset:offset+2])[0] if len(raw) >= offset + 2 else 0
                                if self.dave_session:
                                    try:
                                        self.dave_session.process_welcome(raw[offset+2:], self.connected_users)
                                    except Exception:
                                        if t_id and self.ws and not self.ws.closed:
                                            try:
                                                self.ws.send(json.dumps({"op": 31, "d": {"transition_id": t_id}}))
                                            except Exception:
                                                pass
                                        self._init_dave()
                                if t_id and self.ws and not self.ws.closed:
                                    try:
                                        self.ws.send(json.dumps({"op": 23, "d": {"transition_id": t_id}}))
                                    except Exception:
                                        pass
                                self._update_dave_ratchet()
                    elif op == "close":
                        code = msg.get("code")
                        reason = msg.get("reason")
                        self.console.warn(self.label, f"voice server closed: {code} ({reason})")
                        break
                except Exception:
                    pass

        self._hb_thread = threading.Thread(target=_loop, daemon=True)
        self._hb_thread.start()

    def send_speaking(self, speaking: bool = True, is_video: bool = True) -> None:
        if self.ws and not self.ws.closed:
            try:
                if self.is_stream:
                    mask = 2 if speaking else 0
                else:
                    mask = 5 if (speaking and is_video) else (1 if speaking else 0)
                self.ws.send(json.dumps({
                    "op": 5,
                    "d": {
                        "speaking": mask,
                        "delay": 0,
                        "ssrc": self.ssrc,
                    },
                }))
            except Exception:
                pass

    def start_media_stream(self, video_path: str) -> bool:
        if self._media_pump:
            self._media_pump.stop()
        self._media_pump = MediaStreamPump(self, video_path, self.console)
        return self._media_pump.start()

    def stop_media_stream(self) -> None:
        if self._media_pump:
            self._media_pump.stop()
            self._media_pump = None

    def start_silence_pump(self) -> None:
        if not self.udp_sock or not self.secret_key:
            return

        def _pump():
            seq = 0
            ts = 0
            while self.running:
                try:
                    if self._media_pump and self._media_pump.running:
                        time.sleep(1.0)
                        continue
                    if HAS_CRYPTO and "aes256_gcm" in self.encryption_mode:
                        self._send_encrypted_silence_gcm(seq, ts)
                    else:
                        keepalive = struct.pack(">I", seq) + b"\xc9"
                        try:
                            self.udp_sock.sendto(keepalive, (self.voice_ip, self.voice_port))
                        except Exception:
                            pass
                    seq = (seq + 1) & 0xFFFF
                    ts = (ts + 960) & 0xFFFFFFFF
                    time.sleep(1.0)
                except Exception:
                    break

        self._audio_thread = threading.Thread(target=_pump, daemon=True)
        self._audio_thread.start()

    def _send_encrypted_silence_gcm(self, seq: int, ts: int) -> None:
        if self._media_pump and self._media_pump.running:
            return
        if not self.udp_sock or not self.secret_key:
            return
        try:
            rtp_header = struct.pack(
                ">BBHII",
                0x80,
                0x78,
                seq & 0xFFFF,
                ts & 0xFFFFFFFF,
                self.ssrc,
            )
            with self._nonce_lock:
                self.nonce_counter += 1
                cnt = self.nonce_counter
            nonce = struct.pack(">I", cnt) + b"\x00" * 8
            cipher = AESGCM(self.secret_key[:32])
            encrypted = cipher.encrypt(nonce, OPUS_SILENCE, rtp_header)
            packet = rtp_header + encrypted + struct.pack(">I", cnt)
            self.udp_sock.sendto(packet, (self.voice_ip, self.voice_port))
        except Exception:
            pass

    def close(self) -> None:
        self.running = False
        self.stop_media_stream()
        if self.udp_sock:
            try:
                self.udp_sock.close()
            except Exception:
                pass
        if self.ws:
            self.ws.close()


PERMISSION_ADMINISTRATOR = 1 << 3
PERMISSION_STREAM = 1 << 9
PERMISSION_VIEW_CHANNEL = 1 << 10
PERMISSION_CONNECT = 1 << 20

def _discord_api_get(url: str, token: str, super_props: str = "") -> tuple[int, dict]:
    """Helper to perform GET request to Discord REST API."""
    headers = {
        "authorization": token,
        "user-agent": USER_AGENT,
    }
    if super_props:
        headers["x-super-properties"] = super_props
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return resp.status, data
    except urllib.error.HTTPError as exc:
        try:
            err_data = json.loads(exc.read().decode("utf-8"))
        except Exception:
            err_data = {"message": str(exc.reason)}
        return exc.code, err_data
    except Exception as exc:
        return 0, {"message": str(exc)}


def format_timer(seconds: float | int) -> str:
    total_secs = max(0, int(seconds))
    h = total_secs // 3600
    m = (total_secs % 3600) // 60
    s = total_secs % 60
    return f"{h:02d}h:{m:02d}m:{s:02d}s"


def extract_channel_id(raw_input: str) -> str:
    raw = (raw_input or "").strip()
    m = re.search(r"channels/(?:\d+|@me)/(\d{17,21})", raw)
    if m:
        return m.group(1)
    all_snowflakes = re.findall(r"\d{17,21}", raw)
    if all_snowflakes:
        return all_snowflakes[-1]
    return raw


def validate_stream_target(
    token: str,
    super_props: str,
    channel_id: str,
    console: Console,
    guild_id: str = "",
) -> tuple[bool, str, str]:
    C = console

    cid = extract_channel_id(channel_id)
    if not cid.isdigit():
        C.err("channel", f"invalid channel ID format: '{channel_id}' (must be numeric digits or discord channel link)")
        return False, "", "invalid channel id format"

    status, user_data = _discord_api_get("https://discord.com/api/v9/users/@me", token, super_props)
    if status == 401:
        C.err("auth", "authentication failed: your discord token is invalid or has expired (HTTP 401).")
        C.info(f"{C.GREY}please update your token using Option 4 or 5 in the menu.{C.RESET}")
        return False, "", "unauthorized"
    if status != 200:
        C.err("auth", f"could not reach discord API (HTTP {status}: {user_data.get('message', 'network error')})")
        return False, "", "api error"

    ch_status, ch_data = _discord_api_get(f"https://discord.com/api/v9/channels/{cid}", token, super_props)
    if ch_status in (403, 404):
        C.err("channel", f"channel not found ({cid})")
        C.err("channel", "either this channel does not exist or your account lacks access to view it.")
        return False, "", "channel not found"
    elif ch_status != 200:
        C.err("channel", f"channel lookup failed (HTTP {ch_status}: {ch_data.get('message', 'unknown error')})")
        return False, "", "channel lookup error"

    ch_name = ch_data.get("name", "channel")
    resolved_guild_id = str(ch_data.get("guild_id", ""))
    ch_type = ch_data.get("type")

    if not resolved_guild_id:
        C.err("channel", f"channel '{ch_name}' is not in a server (DM / Group DM channels not supported).")
        return False, "", "dm channel"

    if guild_id and str(guild_id) != resolved_guild_id:
        C.err("channel", f"channel mismatch: channel '{ch_name}' belongs to server {resolved_guild_id}, NOT {guild_id}.")
        return False, "", "channel guild mismatch"

    channel_type_names = {
        0: "Text Channel",
        2: "Voice Channel",
        4: "Category",
        5: "Announcement Channel",
        13: "Stage Channel",
        15: "Forum Channel",
    }
    if ch_type not in (2, 13):
        type_name = channel_type_names.get(ch_type, f"Type {ch_type}")
        C.err("channel", f"invalid channel type: '{ch_name}' is a {type_name}, NOT a Voice Channel.")
        C.info(f"{C.GREY}hint: right-click a voice channel (speaker icon) in Discord -> Copy Channel ID.{C.RESET}")
        return False, "", "not a voice channel"

    g_status, g_data = _discord_api_get(f"https://discord.com/api/v9/guilds/{resolved_guild_id}", token, super_props)
    if g_status in (403, 404):
        ug_status, ug_list = _discord_api_get("https://discord.com/api/v9/users/@me/guilds", token, super_props)
        guild_found = None
        if ug_status == 200 and isinstance(ug_list, list):
            guild_found = next((g for g in ug_list if str(g.get("id")) == resolved_guild_id), None)

        if not guild_found:
            C.err("server", f"server not found ({resolved_guild_id})")
            C.err("server", "either this server does not exist or your account is not a member of it.")
            return False, "", "server not found"
        guild_name = guild_found.get("name", "Server")
    elif g_status != 200:
        guild_name = "Server"
    else:
        guild_name = g_data.get("name", "Server")

    C.ok("server", f"server verified: {C.WHITE}{guild_name}{C.RESET} ({resolved_guild_id})")

    type_name = "Stage Channel" if ch_type == 13 else "Voice Channel"
    C.ok("channel", f"voice channel verified: {C.WHITE}#{ch_name}{C.RESET} ({type_name})")

    raw_perms = ch_data.get("permissions")
    if raw_perms is not None:
        try:
            perms = int(raw_perms)
            is_admin = bool(perms & PERMISSION_ADMINISTRATOR)
            can_view = is_admin or bool(perms & PERMISSION_VIEW_CHANNEL)
            can_connect = is_admin or bool(perms & PERMISSION_CONNECT)
            can_stream = is_admin or bool(perms & PERMISSION_STREAM)

            if not can_view:
                C.err("perms", f"missing permission: your account cannot view channel '#{ch_name}' (VIEW_CHANNEL missing).")
                return False, "", "cannot view channel"
            if not can_connect:
                C.err("perms", f"missing permission: your account cannot connect to channel '#{ch_name}' (CONNECT missing).")
                return False, "", "cannot connect"
            if not can_stream:
                C.err("perms", f"missing permission: your account cannot Go Live in channel '#{ch_name}' (STREAM / Video missing).")
                C.info(f"{C.GREY}hint: a server administrator must grant your role 'Video / Go Live' permission.{C.RESET}")
                return False, "", "cannot stream"

            C.ok("perms", f"permissions verified: {C.GREEN}View Channel ✓ · Connect ✓ · Go Live (Stream) ✓{C.RESET}")
            return True, resolved_guild_id, ch_name
        except Exception:
            pass

    C.ok("perms", "voice channel permissions check completed")
    return True, resolved_guild_id, ch_name


def find_random_voice_channel_from_token(token: str, super_props: str = "") -> tuple[str, str, str]:
    """
    Scans servers accessible by the user token and picks a random Voice Channel.
    Returns (channel_id, guild_id, channel_name) or ("", "", "") if none found.
    """
    import random
    status, guilds = _discord_api_get("https://discord.com/api/v9/users/@me/guilds", token, super_props)
    if status != 200 or not isinstance(guilds, list) or not guilds:
        return "", "", ""

    shuffled_guilds = list(guilds)
    random.shuffle(shuffled_guilds)

    for g in shuffled_guilds:
        gid = str(g.get("id", ""))
        if not gid:
            continue
        c_status, channels = _discord_api_get(f"https://discord.com/api/v9/guilds/{gid}/channels", token, super_props)
        if c_status != 200 or not isinstance(channels, list):
            continue
        vcs = [c for c in channels if c.get("type") in (2, 13)]
        if not vcs:
            continue
        random.shuffle(vcs)
        for vc in vcs:
            cid = str(vc.get("id", ""))
            name = str(vc.get("name", "voice"))
            raw_perms = vc.get("permissions")
            if raw_perms is not None:
                try:
                    perms = int(raw_perms)
                    if not (perms & PERMISSION_ADMINISTRATOR or perms & PERMISSION_CONNECT):
                        continue
                except Exception:
                    pass
            return cid, gid, name

    return "", "", ""



class ViewerEngine:
    def __init__(self, token: str, console: Console) -> None:
        self.token = token.strip()
        self.console = console
        self.ws: MiniWebSocket | None = None
        self.running = False
        self.guild_id = ""
        self.channel_id = ""
        self.stream_key = ""
        self.user_id = ""
        self.username = ""
        self.heartbeat_interval = 41.25
        self.sequence: int | None = None
        self._thread: threading.Thread | None = None
        self._is_watching = False

    def start(self, guild_id: str, channel_id: str) -> None:
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def watch(self, stream_key: str) -> None:
        self.stream_key = stream_key
        if not self.ws or self.ws.closed:
            return
        try:
            self.ws.send(json.dumps({
                "op": 20,
                "d": {
                    "stream_key": stream_key,
                },
            }))
            if not self._is_watching:
                self._is_watching = True
                self.console.ok("viewer", f"alt account ({self.username or 'alt'}) watching stream via Opcode 20 STREAM_WATCH")
        except Exception:
            pass

    def _run(self) -> None:
        C = self.console
        while self.running:
            try:
                self.ws = MiniWebSocket(GATEWAY_URL, path="/?v=9&encoding=json")
                self.ws.connect()
                last_hb = time.time()
                while self.running and self.ws and not self.ws.closed:
                    now = time.time()
                    if now - last_hb > self.heartbeat_interval:
                        self.ws.send(json.dumps({"op": 1, "d": self.sequence}))
                        last_hb = now

                    msg = self.ws.recv(timeout=1.0)
                    if not msg:
                        continue
                    if isinstance(msg, dict) and msg.get("op") == "close":
                        break

                    op = msg.get("op")
                    s = msg.get("s")
                    t = msg.get("t")
                    d = msg.get("d") or {}

                    if s is not None:
                        self.sequence = s

                    if op == 10:
                        self.heartbeat_interval = d.get("heartbeat_interval", 41250) / 1000.0
                        last_hb = time.time()
                        self.ws.send(json.dumps({"op": 1, "d": self.sequence}))
                        self.ws.send(json.dumps({
                            "op": 2,
                            "d": {
                                "token": self.token,
                                "capabilities": 16381,
                                "properties": _local_props(),
                                "presence": {
                                    "status": "online",
                                    "since": 0,
                                    "activities": [],
                                    "afk": False,
                                },
                            },
                        }))
                    elif op == 1:
                        self.ws.send(json.dumps({"op": 1, "d": self.sequence}))
                        last_hb = now
                    elif op == 0:
                        if t == "READY":
                            u = d.get("user") or {}
                            self.user_id = str(u.get("id", ""))
                            self.username = u.get("username", "alt")
                            C.ok("viewer", f"alt account connected ({self.username})")
                            self.ws.send(json.dumps({
                                "op": 4,
                                "d": {
                                    "guild_id": self.guild_id,
                                    "channel_id": self.channel_id,
                                    "self_mute": True,
                                    "self_deaf": True,
                                    "self_video": False,
                                },
                            }))
                            C.ok("viewer", f"alt account joined voice channel #{self.channel_id}")
                            if self.stream_key:
                                time.sleep(1.0)
                                self.watch(self.stream_key)
                        elif t == "STREAM_CREATE":
                            key = d.get("stream_key", "")
                            if key and (not self.stream_key or key == self.stream_key):
                                time.sleep(0.5)
                                self.watch(key)
            except Exception:
                pass
            if not self.running:
                break
            time.sleep(3.0)

    def stop(self) -> None:
        self.running = False
        if self.ws and not self.ws.closed:
            try:
                self.ws.send(json.dumps({
                    "op": 4,
                    "d": {
                        "guild_id": self.guild_id,
                        "channel_id": None,
                        "self_mute": True,
                        "self_deaf": True,
                        "self_video": False,
                    },
                }))
            except Exception:
                pass
            try:
                self.ws.close()
            except Exception:
                pass


class GatewayStreamEngine:
    def __init__(self, state: State, session: Session, console: Console) -> None:
        self.state = state
        self.session = session
        self.console = console
        self.ws: MiniWebSocket | None = None
        self.voice_conn: VoiceConnection | None = None
        self.stream_conn: VoiceConnection | None = None
        self.running = False
        self.stopped_by_user = False
        self.heartbeat_interval = 41.25
        self.sequence: int | None = None
        self.start_time = 0.0

        self.gateway_session_id = ""
        self.user_id = ""
        self.stream_key = ""

        self.voice_session_id = ""
        self.voice_token = ""
        self.voice_endpoint = ""
        self.voice_connected = False

        self.stream_token = ""
        self.stream_endpoint = ""
        self.rtc_server_id = ""
        self.stream_connected = False
        self.stream_ever_active = False
        self._stream_recreate_attempts = 0
        self._user_left_vc = False
        self._was_already_in_vc = False
        self.viewer_engine: ViewerEngine | None = None

        self._status_thread: threading.Thread | None = None
        self._ticker_thread: threading.Thread | None = None

        self.guild_id = ""
        self.channel_id = ""
        self.channel_name = ""
        self.detected_guild_id = ""
        self.detected_channel_id = ""
        self.channel_members: set[str] = set()
        self.stream_viewer_ids: set[str] = set()
        self.counted_stream_seconds = 0.0
        self.last_tick_time = 0.0
        self.ready_received = threading.Event()
        self._cached_supplemental_guilds: list[dict] = []

    def stop(self) -> None:
        """Signal the stream engine to gracefully stop broadcasting permanently by user."""
        self.stopped_by_user = True
        self.running = False
        if self.viewer_engine:
            self.viewer_engine.stop()
        if self.stream_conn:
            self.stream_conn.close()
        if self.voice_conn:
            self.voice_conn.close()
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass

    def get_viewer_count(self) -> int:
        members = set(self.channel_members) | set(self.stream_viewer_ids)
        if self.user_id:
            members.discard(str(self.user_id))
        if self.viewer_engine and self.viewer_engine.running:
            if self.viewer_engine.user_id:
                members.add(str(self.viewer_engine.user_id))
            else:
                return max(len(members), 1)
        return len(members)

    def _stream_ticker(self) -> None:
        self.last_tick_time = time.time()
        while self.running:
            time.sleep(1.0)
            if not self.running:
                break
            now = time.time()
            dt = now - self.last_tick_time
            self.last_tick_time = now
            if self.stream_connected and self.get_viewer_count() > 0:
                self.counted_stream_seconds += dt

    def _lookup_channel_name(self, channel_id: str) -> tuple[str, str]:
        try:
            status, data = _discord_api_get(f"https://discord.com/api/v9/channels/{channel_id}", self.state.token, self.session.super_props)
            if status == 200 and isinstance(data, dict):
                name = str(data.get("name", "channel"))
                guild_id = str(data.get("guild_id", ""))
                return name, guild_id
        except Exception:
            pass
        return "channel", ""

    def start_stream(self, guild_id: str = "", channel_id: str = "", wait_for_stdin: bool = True) -> None:
        C = self.console
        C.rule()
        C.info(f"{C.MAGENTA}Headless Gateway Live Streamer{C.WHITE}")
        C.rule()
        C.info("connecting to discord gateway (wss://gateway.discord.gg)...")

        self.ws = MiniWebSocket(GATEWAY_URL, path="/?v=9&encoding=json")
        try:
            self.ws.connect()
        except Exception as exc:
            C.err("gateway", f"connection failed: {exc}")
            C.rule()
            if wait_for_stdin:
                safe_input(f"  {C.ORANGE}›{C.RESET} press enter to return to menu")
            return

        C.ok("gateway", "connected")
        self.running = True
        self.start_time = time.time()
        if not hasattr(self, "counted_stream_seconds") or self.counted_stream_seconds is None:
            self.counted_stream_seconds = 0.0
        self.user_id = _user_id_from_token(self.state.token) or ""
        self.stream_ever_active = False
        self._stream_recreate_attempts = 0
        self._user_left_vc = False
        self._was_already_in_vc = False
        self.ready_received.clear()

        event_thread = threading.Thread(
            target=self._event_loop,
            daemon=True,
        )
        event_thread.start()

        self.ready_received.wait(timeout=5.0)

        if not self.running or self.stopped_by_user:
            C.rule()
            if wait_for_stdin:
                safe_input(f"  {C.ORANGE}›{C.RESET} press enter to return to menu")
            return

        if self.detected_channel_id:
            self._was_already_in_vc = True
            target_channel_id = self.detected_channel_id
            target_guild_id = self.detected_guild_id
            ch_name, api_gid = self._lookup_channel_name(target_channel_id)
            if not target_guild_id and api_gid:
                target_guild_id = api_gid
            self.channel_name = ch_name
            C.ok("voice", f"voice channel connection detected: {C.WHITE}#{ch_name}{C.RESET} ({target_channel_id})")
            C.info(f"{C.GREY}starting streaming there and next procedures...{C.RESET}")
        elif channel_id:
            cid = extract_channel_id(channel_id)
            valid, gid, name = validate_stream_target(
                token=self.state.token,
                super_props=self.session.super_props,
                channel_id=cid,
                console=C,
            )
            if not valid:
                C.err("input", f"specified channel ID ({cid}) is invalid or inaccessible")
                self._cleanup(self.guild_id)
                return
            target_channel_id = cid
            target_guild_id = gid or guild_id
            self.channel_name = name
            self.state.last_guild_id = target_guild_id
            self.state.last_channel_id = target_channel_id
            self.state.save()
        else:
            C.info("no active voice channel detected on discord client.")
            target_guild_id = ""
            target_channel_id = ""

            # In non-interactive mode, attempt auto-discovery of a random voice room
            if not sys.stdin.isatty():
                C.info("auto-discovering a random voice channel from accessible servers...")
                rcid, rgid, rname = find_random_voice_channel_from_token(self.state.token, self.session.super_props)
                if rcid:
                    target_channel_id = rcid
                    target_guild_id = rgid
                    self.channel_name = rname
                    C.ok("channel", f"random voice room selected: #{rname} ({rcid})")

            if not target_channel_id:
                while True:
                    default_cid = self.state.last_channel_id
                    prompt_str = f"  {C.GREY}voice channel id (or 'random')" + (f" [default: {default_cid}]:{C.RESET} " if default_cid else f":{C.RESET} ")
                    raw_in = safe_input(prompt_str).strip()
                    if raw_in.lower() in ("random", "r"):
                        rcid, rgid, rname = find_random_voice_channel_from_token(self.state.token, self.session.super_props)
                        if rcid:
                            target_channel_id = rcid
                            target_guild_id = rgid
                            self.channel_name = rname
                            C.ok("channel", f"random voice room selected: #{rname} ({rcid})")
                            break
                        else:
                            C.err("channel", "could not find any accessible voice channel")
                            continue

                    if not raw_in and default_cid:
                        raw_in = default_cid
                    if not raw_in:
                        C.err("input", "voice channel id is required")
                        self._cleanup(self.guild_id)
                        return
                    if raw_in.lower() in ("q", "quit", "exit"):
                        self._cleanup(self.guild_id)
                        return

                    cid = extract_channel_id(raw_in)
                    valid, gid, name = validate_stream_target(
                        token=self.state.token,
                        super_props=self.session.super_props,
                        channel_id=cid,
                        console=C,
                    )
                    if valid:
                        target_channel_id = cid
                        target_guild_id = gid
                        self.channel_name = name
                        self.state.last_guild_id = gid
                        self.state.last_channel_id = cid
                        self.state.save()
                        break

                    retry = safe_input(f"  {C.ORANGE}›{C.RESET} try entering channel ID again? (y/n, default: y): ").strip().lower()
                    if retry in ("n", "no", "q", "exit"):
                        self._cleanup(self.guild_id)
                        return

        self.guild_id = target_guild_id
        self.channel_id = target_channel_id

        for g in self._cached_supplemental_guilds:
            if str(g.get("id")) == str(self.guild_id):
                for vs in g.get("voice_states", []):
                    if str(vs.get("channel_id")) == str(self.channel_id):
                        u = str(vs.get("user_id"))
                        if u != str(self.user_id):
                            self.channel_members.add(u)

        if self.state.has_viewer_token:
            C.info("viewer", "launching automated alt viewer account...")
            self.viewer_engine = ViewerEngine(self.state.viewer_token, C)
            self.viewer_engine.start(self.guild_id, self.channel_id)
        else:
            C.info("viewer", f"{C.GREY}no alt viewer token set · run Option 3 in main menu to automate stream viewing{C.RESET}")

        try:
            self.ws.send(json.dumps({
                "op": 4,
                "d": {
                    "guild_id": self.guild_id,
                    "channel_id": self.channel_id,
                    "self_mute": False,
                    "self_deaf": False,
                    "self_video": False,
                },
            }))
            C.ok("voice", f"joining voice channel {C.ORANGE}#{self.channel_name}{C.RESET} ({self.channel_id})")
        except Exception as exc:
            C.err("voice", f"failed to join voice channel: {exc}")
            self._cleanup(self.guild_id)
            return

        self._ticker_thread = threading.Thread(
            target=self._stream_ticker,
            daemon=True,
        )
        self._ticker_thread.start()

        self._status_thread = threading.Thread(
            target=self._status_display,
            daemon=True,
        )
        self._status_thread.start()

        if wait_for_stdin:
            print()
            C.info(f"{C.GREY}press enter at any time to stop streaming...{C.RESET}")
            C.rule()
            try:
                import msvcrt
                while self.running:
                    if msvcrt.kbhit():
                        ch = msvcrt.getch()
                        if ch in (b'\r', b'\n', b'\x03'):
                            break
                    time.sleep(0.1)
            except Exception:
                while self.running:
                    try:
                        if sys.stdin.isatty():
                            r, _, _ = select.select([sys.stdin], [], [], 0.5)
                            if r:
                                _ = sys.stdin.readline()
                                break
                        else:
                            time.sleep(0.5)
                    except (Exception, KeyboardInterrupt):
                        break
        else:
            while self.running:
                time.sleep(0.5)

        self._cleanup(self.guild_id)

    def _status_display(self) -> None:
        while self.running and not self.stopped_by_user:
            time.sleep(10)
            if not self.running or self.stopped_by_user:
                break
            counted_hours = self.counted_stream_seconds / 3600.0
            timer_str = format_timer(self.counted_stream_seconds)
            viewers = self.get_viewer_count()
            if self.stream_connected:
                if viewers > 0:
                    lbl = f"{viewers} viewer" if viewers == 1 else f"{viewers} viewers"
                    status = f"{self.console.MAGENTA}🔴 LIVE ({lbl}){self.console.RESET}"
                    self.console.live("stream", f"{status} · {timer_str} ({counted_hours:.2f}h) counted")
                else:
                    status = f"{self.console.YELLOW}⚠️ LIVE (0 VIEWERS: PAUSED){self.console.RESET}"
                    self.console.live("stream", f"{status} · {timer_str} ({counted_hours:.2f}h) counted {self.console.GREY}(accumulation paused - waiting for viewer){self.console.RESET}")
            elif self.voice_connected:
                status = f"{self.console.CYAN}⏳ starting stream{self.console.RESET}"
                self.console.live("stream", f"{status} · {timer_str} ({counted_hours:.2f}h) counted")
            else:
                status = f"{self.console.YELLOW}⏳ reconnecting{self.console.RESET}"
                self.console.live("stream", f"{status} · {timer_str} ({counted_hours:.2f}h) counted")

            if self.stream_connected and self.stream_key and self.ws and not self.ws.closed:
                try:
                    self.ws.send(json.dumps({
                        "op": 22,
                        "d": {
                            "stream_key": self.stream_key,
                            "paused": False,
                        },
                    }))
                except Exception:
                    pass
                if self.viewer_engine:
                    self.viewer_engine.watch(self.stream_key)

    def _reconnect(self, can_resume: bool = True) -> bool:
        C = self.console
        if not self.running:
            return False

        self.stream_connected = False
        self.voice_connected = False

        if self.stream_conn:
            try:
                self.stream_conn.close()
            except Exception:
                pass
            self.stream_conn = None

        if self.voice_conn:
            try:
                self.voice_conn.close()
            except Exception:
                pass
            self.voice_conn = None

        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass
            self.ws = None

        attempts = 0
        backoff = 3
        while self.running and not self.stopped_by_user:
            attempts += 1
            if attempts > 3:
                C.info("gateway", "fast-recycling gateway session for a fresh auto-resume connection...")
                return False
            C.warn("gateway", f"reconnecting to gateway in {backoff}s (attempt {attempts}/3)...")
            for _ in range(backoff * 10):
                if not self.running or self.stopped_by_user:
                    return False
                time.sleep(0.1)

            try:
                self.ws = MiniWebSocket(GATEWAY_URL, path="/?v=9&encoding=json")
                self.ws.connect()

                hello_msg = None
                for _ in range(15):
                    m = self.ws.recv(timeout=1.0)
                    if isinstance(m, dict) and m.get("op") == "close":
                        code = m.get("code")
                        reason = m.get("reason", "")
                        if code == 4004:
                            C.err("auth", "fatal gateway authentication failure (code 4004: invalid token)")
                            C.err("auth", "please use Option 4 or 5 in the menu to update your token.")
                            self.running = False
                            return False
                    if m and m.get("op") == 10:
                        hello_msg = m
                        break
                    time.sleep(0.1)

                if not hello_msg:
                    C.warn("gateway", "did not receive hello from gateway, retrying...")
                    self.ws.close()
                    self.ws = None
                    backoff = min(15, backoff + 2)
                    continue

                d_hello = hello_msg.get("d", {})
                self.heartbeat_interval = d_hello.get("heartbeat_interval", 41250) / 1000.0

                self.ws.send(json.dumps({"op": 1, "d": self.sequence}))

                resumed = False
                if can_resume and self.gateway_session_id and self.sequence is not None:
                    C.info("attempting to resume gateway session (op 6)...")
                    try:
                        self.ws.send(json.dumps({
                            "op": 6,
                            "d": {
                                "token": self.state.token,
                                "session_id": self.gateway_session_id,
                                "seq": self.sequence,
                            },
                        }))

                        res_msg = self.ws.recv(timeout=4.0)
                        if res_msg:
                            op_res = res_msg.get("op")
                            t_res = res_msg.get("t")
                            if op_res == 0 and t_res == "RESUMED":
                                C.ok("gateway", "session resumed successfully")
                                resumed = True
                            elif op_res == 9:
                                C.warn("gateway", "resume failed (invalid session), falling back to identify")
                                time.sleep(1.0)
                            elif op_res == 0 and t_res == "READY":
                                self._handle_dispatch("READY", res_msg.get("d", {}))
                                resumed = True
                    except Exception:
                        resumed = False

                if not resumed:
                    C.info("sending identify (op 2)...")
                    self.ws.send(json.dumps({
                        "op": 2,
                        "d": {
                            "token": self.state.token,
                            "capabilities": 16381,
                            "properties": self.session.props_dict,
                            "presence": {
                                "status": "online",
                                "since": 0,
                                "activities": [],
                                "afk": False,
                            },
                        },
                    }))

                    test_msg = self.ws.recv(timeout=2.0)
                    if isinstance(test_msg, dict) and test_msg.get("op") == "close":
                        code = test_msg.get("code")
                        if code == 4004:
                            C.err("auth", "fatal gateway authentication failure (code 4004: invalid token)")
                            C.err("auth", "please use Option 4 or 5 in the menu to update your token.")
                            self.running = False
                            return False
                    elif isinstance(test_msg, dict) and test_msg.get("op") == 0 and test_msg.get("t") == "READY":
                        self._handle_dispatch("READY", test_msg.get("d", {}))
                        resumed = True

                if resumed and self.channel_id:
                    self.ws.send(json.dumps({
                        "op": 4,
                        "d": {
                            "guild_id": self.guild_id,
                            "channel_id": self.channel_id,
                            "self_mute": False,
                            "self_deaf": False,
                            "self_video": False,
                        },
                    }))
                    C.ok("voice", f"re-joining voice channel {C.ORANGE}#{self.channel_name}{C.RESET} ({self.channel_id})")

                C.ok("gateway", "gateway reconnected successfully")
                return True

            except Exception as exc:
                C.warn("gateway", f"reconnect attempt {attempts} failed: {exc}")
                if self.ws:
                    self.ws.close()
                    self.ws = None
                backoff = min(15, backoff + 2)

        return False

    def _event_loop(self) -> None:
        last_hb = time.time()

        while self.running:
            try:
                if not self.ws or self.ws.closed:
                    if not self._reconnect():
                        break
                    last_hb = time.time()
                    continue

                now = time.time()

                if now - last_hb > self.heartbeat_interval:
                    if self.ws and not self.ws.closed:
                        try:
                            self.ws.send(json.dumps({"op": 1, "d": self.sequence}))
                            last_hb = now
                        except Exception as exc:
                            self.console.warn("gateway", f"heartbeat failed ({exc})")
                            if not self._reconnect():
                                break
                            last_hb = time.time()
                            continue

                msg = self.ws.recv(timeout=1.0)
                if not msg:
                    continue

                if isinstance(msg, dict) and msg.get("op") == "close":
                    code = msg.get("code")
                    reason = msg.get("reason", "")
                    self.console.warn("gateway", f"gateway closed connection (code {code}: {reason})")
                    if code == 4004:
                        self.console.err("auth", "fatal: gateway authentication failed (code 4004: invalid token)")
                        self.console.err("auth", "please use Option 4 or 5 in the menu to update your token.")
                        self.running = False
                        break
                    elif code in (4010, 4011, 4012, 4013, 4014):
                        self.console.err("gateway", f"fatal gateway error (code {code}: {reason}) — stopping.")
                        self.running = False
                        break

                    if not self._reconnect():
                        break
                    last_hb = time.time()
                    continue

                op = msg.get("op")
                seq = msg.get("s")
                t = msg.get("t")
                d = msg.get("d") or {}

                if seq is not None:
                    self.sequence = seq

                if op == 10:
                    self.heartbeat_interval = d.get("heartbeat_interval", 41250) / 1000.0
                    last_hb = time.time()

                    self.ws.send(json.dumps({"op": 1, "d": self.sequence}))

                    self.ws.send(json.dumps({
                        "op": 2,
                        "d": {
                            "token": self.state.token,
                            "capabilities": 16381,
                            "properties": self.session.props_dict,
                            "presence": {
                                "status": "online",
                                "since": 0,
                                "activities": [],
                                "afk": False,
                            },
                        },
                    }))

                elif op == 1:
                    self.ws.send(json.dumps({"op": 1, "d": self.sequence}))
                    last_hb = now

                elif op == 11:
                    pass

                elif op == 0:
                    if t == "RESUMED":
                        self.console.ok("gateway", "gateway session resumed successfully")
                        if self.channel_id:
                            self.ws.send(json.dumps({
                                "op": 4,
                                "d": {
                                    "guild_id": self.guild_id,
                                    "channel_id": self.channel_id,
                                    "self_mute": False,
                                    "self_deaf": False,
                                    "self_video": False,
                                },
                            }))
                    else:
                        self._handle_dispatch(t, d)

                elif op == 7:
                    self.console.warn("gateway", "discord requested gateway reconnect (op 7)")
                    if not self._reconnect(can_resume=True):
                        break
                    last_hb = time.time()

                elif op == 9:
                    resumable = bool(d)
                    self.console.warn("gateway", f"session invalidated (op 9, resumable={resumable})")
                    if not self._reconnect(can_resume=resumable):
                        break
                    last_hb = time.time()

            except Exception as exc:
                if not self.running:
                    break
                self.console.warn("gateway", f"connection dropped: {exc}")
                if not self._reconnect():
                    break
                last_hb = time.time()

        self.running = False

    def _handle_dispatch(self, event_type: str, d: dict) -> None:
        C = self.console

        if event_type == "READY":
            self.gateway_session_id = d.get("session_id", "")
            user_data = d.get("user", {})
            username = user_data.get("username", "?")
            self.user_id = str(user_data.get("id", self.user_id))
            C.ok("auth", f"gateway authenticated ({username})")

        elif event_type == "READY_SUPPLEMENTAL":
            guilds = d.get("guilds", [])
            self._cached_supplemental_guilds = guilds
            for g in guilds:
                gid = str(g.get("id"))
                vstates = g.get("voice_states", [])
                for vs in vstates:
                    vs_uid = str(vs.get("user_id"))
                    vs_cid = str(vs.get("channel_id") or "")
                    if vs_uid == str(self.user_id) and vs_cid:
                        self.detected_guild_id = gid
                        self.detected_channel_id = vs_cid
                        if vs.get("session_id"):
                            self.voice_session_id = vs.get("session_id")
                    elif self.channel_id and vs_cid == str(self.channel_id) and vs_uid != str(self.user_id):
                        self.channel_members.add(vs_uid)
            self.ready_received.set()

        elif event_type == "VOICE_STATE_UPDATE":
            vs_user_id = str(d.get("user_id"))
            vs_channel_id = d.get("channel_id")
            vs_guild_id = str(d.get("guild_id") or "")

            if not self.detected_channel_id and not self.ready_received.is_set():
                if vs_user_id == str(self.user_id) and vs_channel_id:
                    self.detected_channel_id = str(vs_channel_id)
                    self.detected_guild_id = vs_guild_id

            if vs_user_id == str(self.user_id):
                new_session = d.get("session_id", "")
                if new_session:
                    self.voice_session_id = new_session

                if (self.voice_connected or self.stream_connected or self.stream_ever_active):
                    if vs_channel_id is None:
                        if self._user_left_vc or not self.running:
                            return
                        C.warn("voice", "voice channel connection dropped by server, auto-rejoining in 2s...")
                        def _rejoin_vc():
                            time.sleep(2.0)
                            if self.running and self.ws and not self.ws.closed and self.channel_id:
                                try:
                                    self.ws.send(json.dumps({
                                        "op": 4,
                                        "d": {
                                            "guild_id": self.guild_id,
                                            "channel_id": self.channel_id,
                                            "self_mute": False,
                                            "self_deaf": False,
                                            "self_video": False,
                                        },
                                    }))
                                except Exception:
                                    pass
                        threading.Thread(target=_rejoin_vc, daemon=True).start()

                    elif self.channel_id and str(vs_channel_id) != str(self.channel_id):
                        C.warn("voice", f"user moved to voice channel ({vs_channel_id}), moving stream to follow user...")
                        self.channel_id = str(vs_channel_id)
                        if vs_guild_id:
                            self.guild_id = vs_guild_id
                        if self.ws and not self.ws.closed:
                            try:
                                self.ws.send(json.dumps({
                                    "op": 18,
                                    "d": {
                                        "type": "guild",
                                        "guild_id": self.guild_id,
                                        "channel_id": self.channel_id,
                                        "preferred_region": None,
                                    },
                                }))
                            except Exception:
                                pass

                C.ok("voice", f"voice state updated (session: {self.voice_session_id[:8]}...)")
            else:
                if self.channel_id:
                    if vs_channel_id and str(vs_channel_id) == str(self.channel_id):
                        was_zero = (self.get_viewer_count() == 0)
                        self.channel_members.add(vs_user_id)
                        if was_zero and self.stream_connected:
                            C.ok("viewer", f"viewer joined voice channel ({len(self.channel_members)} member(s) present) — stream hour accumulation RESUMED!")
                    else:
                        if vs_user_id in self.channel_members:
                            self.channel_members.discard(vs_user_id)
                            if self.get_viewer_count() == 0 and self.stream_connected:
                                C.warn("viewer", "⚠️  all viewers left voice channel (0 present) — stream hour accumulation PAUSED!")

        elif event_type == "VOICE_SERVER_UPDATE":
            token = d.get("token", "")
            endpoint = d.get("endpoint") or ""
            v_guild = d.get("guild_id", self.guild_id)

            if not endpoint or not token:
                C.err("voice", "empty voice server endpoint/token")
                return

            if self.voice_connected:
                C.warn("voice", "voice server update received, re-establishing voice connection...")
                if self.voice_conn:
                    self.voice_conn.close()
                self.voice_connected = False

            self.voice_token = token
            self.voice_endpoint = endpoint
            C.ok("voice", f"voice server: {endpoint}")

            session_id = self.voice_session_id or self.gateway_session_id
            self.voice_conn = VoiceConnection(
                endpoint=self.voice_endpoint,
                server_id=v_guild,
                user_id=self.user_id,
                session_id=session_id,
                token=self.voice_token,
                console=C,
                label="voice",
                is_stream=False,
                channel_id=self.channel_id,
            )

            C.info("performing voice WebSocket handshake...")
            if self.voice_conn.connect():
                self.voice_conn.start_heartbeat()
                self.voice_conn.send_speaking(False)
                self.voice_conn.start_silence_pump()
                self.voice_connected = True
                C.ok("voice", f"{C.GREEN}voice connection established (ssrc={self.voice_conn.ssrc}){C.RESET}")

                time.sleep(0.5)
                C.info("requesting Go Live stream (op 18 Stream Create)...")
                try:
                    self.ws.send(json.dumps({
                        "op": 18,
                        "d": {
                            "type": "guild",
                            "guild_id": self.guild_id,
                            "channel_id": self.channel_id,
                            "preferred_region": None,
                        },
                    }))
                except Exception as exc:
                    C.warn("stream", f"stream create send error: {exc}")
            else:
                C.err("voice", "voice WebSocket handshake FAILED, retrying in 3s...")
                def _retry_voice():
                    time.sleep(3.0)
                    if self.running and self.ws and not self.ws.closed:
                        try:
                            self.ws.send(json.dumps({
                                "op": 4,
                                "d": {
                                    "guild_id": self.guild_id,
                                    "channel_id": self.channel_id,
                                    "self_mute": False,
                                    "self_deaf": False,
                                    "self_video": False,
                                },
                            }))
                        except Exception:
                            pass
                threading.Thread(target=_retry_voice, daemon=True).start()

        elif event_type == "STREAM_CREATE":
            self.stream_key = d.get("stream_key", "")
            self.rtc_server_id = d.get("rtc_server_id", "")
            C.ok("stream", f"Go Live created! stream_key: {C.CYAN}{self.stream_key}{C.RESET}")
            self.stream_connected = True
            self.stream_ever_active = True
            self._stream_recreate_attempts = 0

            viewer_ids = d.get("viewer_ids")
            if viewer_ids is not None:
                self.stream_viewer_ids = set(str(u) for u in viewer_ids if str(u) != str(self.user_id))

            if self.ws and not self.ws.closed and self.stream_key:
                try:
                    self.ws.send(json.dumps({
                        "op": 22,
                        "d": {
                            "stream_key": self.stream_key,
                            "paused": False,
                        },
                    }))
                    C.ok("stream", "stream marked unpaused (Opcode 22 STREAM_SET_PAUSED: paused=false)")
                except Exception:
                    pass

            if self.viewer_engine and self.stream_key:
                self.viewer_engine.watch(self.stream_key)

            print()
            C.rule()
            C.live("stream", f"{C.MAGENTA}● BROADCASTING LIVE{C.RESET}, discord shows your account as {C.MAGENTA}LIVE{C.RESET}")
            C.rule()
            print(f"  {C.YELLOW}⚠️  CRITICAL REQUIREMENTS FOR STREAM TIME TO ACCUMULATE:{C.RESET}")
            print(f"  {C.ORANGE}1.{C.RESET} {C.WHITE}An alt account (or friend) {C.YELLOW}MUST be in the voice channel{C.RESET}{C.WHITE} sitting or watching.{C.RESET}")
            print(f"     {C.GREY}› Discord will NOT count streaming hours if you stream alone in an empty channel!{C.RESET}")
            print(f"  {C.ORANGE}2.{C.RESET} {C.WHITE}Ensure {C.YELLOW}'Use data to personalize my Discord experience'{C.RESET}{C.WHITE} is turned {C.GREEN}ON{C.RESET}{C.WHITE}.{C.RESET}")
            print(f"     {C.GREY}› Check in Discord: User Settings (⚙) → Data & Privacy → Personalization.{C.RESET}")
            C.rule()
            if not HAS_CRYPTO:
                C.err("crypto", "cryptography package is missing — live video streaming will NOT work! Run: pip install -r requirements.txt")
            elif self.voice_conn and self.voice_conn.secret_key:
                C.ok("audio", "encrypted voice and video stream pump active")

            viewers = self.get_viewer_count()
            if viewers == 0:
                C.warn("viewer", "⚠️  NO VIEWERS DETECTED (0 in VC) - stream hour accumulation is PAUSED!")
                C.warn("viewer", "› Discord requires at least 1 other user in VC or watching stream to count hours.")
                C.info("viewer", f"{C.GREY}› Have an alt or friend join channel #{self.channel_name} or configure Option 3 (Alt Viewer).{C.RESET}")
            else:
                lbl = f"{viewers} viewer" if viewers == 1 else f"{viewers} viewers"
                C.ok("viewer", f"✓ {lbl} detected in voice channel - stream hour accumulation is ACTIVE")

        elif event_type == "STREAM_UPDATE":
            viewer_ids = d.get("viewer_ids")
            if viewer_ids is not None:
                self.stream_viewer_ids = set(str(u) for u in viewer_ids if str(u) != str(self.user_id))

        elif event_type == "STREAM_SERVER_UPDATE":
            token = d.get("token", "")
            endpoint = d.get("endpoint") or ""
            s_key = d.get("stream_key") or self.stream_key
            if endpoint and token:
                self.stream_token = token
                self.stream_endpoint = endpoint
                C.ok("stream", f"stream media route: {endpoint}")
                if self.stream_conn:
                    self.stream_conn.close()
                    self.stream_conn = None
                target_server = self.rtc_server_id or d.get("rtc_server_id") or d.get("guild_id") or self.guild_id or self.channel_id
                v_session = self.voice_session_id or self.gateway_session_id
                self.stream_conn = VoiceConnection(
                    endpoint=endpoint,
                    server_id=target_server,
                    user_id=self.user_id,
                    session_id=v_session,
                    token=token,
                    console=C,
                    label="stream_rtc",
                    is_stream=True,
                    stream_key=s_key,
                    channel_id=self.channel_id,
                )
                def _connect_stream():
                    if not self.stream_conn:
                        return
                    if self.stream_conn.connect():
                        self.stream_conn.start_heartbeat()
                        try:
                            v_ssrc = self.stream_conn.video_ssrc
                            r_ssrc = self.stream_conn.rtx_ssrc
                            stream_desc = {
                                "op": 12,
                                "d": {
                                    "audio_ssrc": self.stream_conn.ssrc,
                                    "video_ssrc": v_ssrc,
                                    "rtx_ssrc": r_ssrc,
                                    "streams": [
                                        {
                                            "type": "video",
                                            "rid": "100",
                                            "ssrc": v_ssrc,
                                            "rtx_ssrc": r_ssrc,
                                            "active": True,
                                            "quality": 100,
                                            "max_bitrate": 2500000,
                                            "max_framerate": 30,
                                            "max_resolution": {"type": "fixed", "width": 848, "height": 480},
                                        }
                                    ],
                                },
                            }
                            self.stream_conn.ws.send(json.dumps(stream_desc))
                            self.stream_conn.send_speaking(True)
                        except Exception:
                            pass
                        C.ok("stream_rtc", f"stream media connection active (ssrc={self.stream_conn.ssrc})")
                        media_source = resolve_media_source(self.state.stream_url, C)
                        if media_source:
                            if not self.stream_conn.start_media_stream(media_source):
                                C.warn("stream_rtc", "falling back to silent audio pump")
                                self.stream_conn.start_silence_pump()
                        else:
                            self.stream_conn.start_silence_pump()
                    else:
                        C.warn("stream_rtc", "stream media connection handshake failed")
                threading.Thread(target=_connect_stream, daemon=True).start()

        elif event_type == "STREAM_DELETE":
            self.stream_connected = False
            if self.voice_conn and self.voice_conn._media_pump:
                self.voice_conn._media_pump.stop()
            if self.stream_conn:
                self.stream_conn.close()
                self.stream_conn = None

            if not self.running or not self.voice_connected or self._user_left_vc:
                return

            self._stream_recreate_attempts += 1
            delay = min(15.0, 2.0 * min(self._stream_recreate_attempts, 5))
            C.warn("stream", f"stream interrupted by server, re-requesting Go Live in {delay:.1f}s (attempt {self._stream_recreate_attempts})...")
            def _recreate():
                time.sleep(delay)
                if self.running and self.ws and not self.ws.closed and self.voice_connected and not self._user_left_vc:
                    try:
                        self.ws.send(json.dumps({
                            "op": 18,
                            "d": {
                                "type": "guild",
                                "guild_id": self.guild_id,
                                "channel_id": self.channel_id,
                                "preferred_region": None,
                            },
                        }))
                    except Exception:
                        pass
            threading.Thread(target=_recreate, daemon=True).start()

        elif event_type == "VOICE_CONNECTION_STATUS":
            status = d.get("state", "")
            if status:
                C.info(f"voice connection status: {status}")

    def _cleanup(self, guild_id: str = "") -> None:
        C = self.console
        self.running = False
        target_guild = guild_id or self.guild_id

        if self.ws and not self.ws.closed and self.stream_key:
            try:
                self.ws.send(json.dumps({
                    "op": 19,
                    "d": {"stream_key": self.stream_key},
                }))
            except Exception:
                pass

        if self.ws and not self.ws.closed and not self._user_left_vc and not self._was_already_in_vc and target_guild:
            try:
                self.ws.send(json.dumps({
                    "op": 4,
                    "d": {
                        "guild_id": target_guild,
                        "channel_id": None,
                        "self_mute": False,
                        "self_deaf": False,
                        "self_video": False,
                    },
                }))
            except Exception:
                pass

        if self.viewer_engine:
            self.viewer_engine.stop()
            self.viewer_engine = None
        if self.stream_conn:
            self.stream_conn.close()
        if self.voice_conn:
            self.voice_conn.close()
        if self.ws:
            time.sleep(0.3)
            self.ws.close()

        if (self.stream_connected or self.stream_ever_active):
            counted_hours = self.counted_stream_seconds / 3600.0
            elapsed_wall = time.time() - self.start_time if self.start_time > 0 else 0.0
            new_seconds = max(0.0, self.counted_stream_seconds - getattr(self, "_saved_stream_seconds", 0.0))
            if new_seconds >= 5:
                self.state.total_stream_hours_claimed += new_seconds / 3600.0
                self._saved_stream_seconds = self.counted_stream_seconds
                if self.stopped_by_user:
                    self.state.total_stream_sessions += 1
                self.state.save()

            if self.stopped_by_user:
                print()
                if self.counted_stream_seconds >= 10:
                    C.ok("stream", f"ended · {format_timer(self.counted_stream_seconds)} ({counted_hours:.2f}h) counted streaming hours ({format_timer(elapsed_wall)} total wall time)")
                    C.info(f"{C.GREY}note: badge progression typically syncs within 24-48 hours.{C.RESET}")
                elif self.stream_ever_active and self.counted_stream_seconds < 10:
                    C.warn("stream", f"ended · {format_timer(0)} (0.00h) counted (0 viewers were present — stream hours were paused)")
                    C.info(f"{C.GREY}hint: have an alt or friend sit in the voice channel next time so hours count.{C.RESET}")
                else:
                    C.info("stream session ended.")
            else:
                C.info("stream", f"checkpoint saved: {format_timer(self.counted_stream_seconds)} ({counted_hours:.2f}h) counted. Auto-resuming fresh session...")
        else:
            print()
            C.info("stream session ended.")

        C.rule()
        safe_input(f"  {C.ORANGE}›{C.RESET} press enter to return to menu")



def inspect_profile(state: State, session: Session, console: Console) -> None:
    C = console
    if not state.has_token:
        C.err("auth", "no token saved")
        C.rule()
        safe_input(f"  {C.ORANGE}›{C.RESET} press enter to continue")
        return

    C.info("fetching profile data from discord...")
    uid = _user_id_from_token(state.token)
    if not uid:
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
                uid = json.loads(resp.read().decode("utf-8")).get("id", "")
        except Exception:
            pass

    if not uid:
        C.err("profile", "unable to resolve user id from token")
        C.rule()
        safe_input(f"  {C.ORANGE}›{C.RESET} press enter to continue")
        return

    url = f"{USERS_URL}/{uid}/profile?with_mutual_guilds=false&with_mutual_friends_count=false"
    req = urllib.request.Request(
        url,
        headers={
            "authorization": state.token,
            "user-agent": USER_AGENT,
            "x-super-properties": session.super_props,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        user = data.get("user", {})
        badges = data.get("badges", [])
        C.rule()
        username = user.get("username", "Unknown")
        global_name = user.get("global_name")
        display = f"{global_name} (@{username})" if global_name else f"@{username}"
        C.ok("account", f"{display} · ID: {uid}")

        if badges:
            C.info(f"badges ({len(badges)}):")
            for b in badges:
                bid = b.get("id", "badge")
                desc = b.get("description", "")
                C.info(f"  {C.CYAN}•{C.WHITE} {C.ORANGE}{bid}{C.WHITE}: {desc}")
        else:
            C.info("no public badges equipped on profile")
    except Exception as exc:
        C.err("profile", f"failed to load: {exc}")

    C.rule()
    safe_input(f"  {C.ORANGE}›{C.RESET} press enter to continue")



class StreamMaximizerApp:
    def __init__(self) -> None:
        self.console = Console()
        self.state = State.load()
        self.session: Session | None = None

    def start(self, auto_menu: bool = True) -> bool:
        C = self.console
        C.banner()
        time.sleep(0.3)

        if not self.state.has_token:
            self._prompt_token()
        else:
            C.ok("auth", "token loaded from state")
        if not self.state.has_token:
            C.err("token", "required to run")
            return False

        if not self.state.has_cookie:
            self._prompt_cookie()
        else:
            C.ok("auth", "cf_clearance cookie loaded")
        if not self.state.has_cookie:
            C.err("cookie", "required to run")
            return False

        C.info("validating session...")
        self._new_session()
        try:
            ensure_token(self.state, self.session.super_props)
            C.ok("auth", "ready · analytics token cached")
        except Exception as exc:
            C.err("auth", str(exc))
            return False

        if HAS_CRYPTO:
            C.ok("crypto", "cryptography library verified (live streaming engine ready)")
        else:
            C.err("crypto", "cryptography package is MISSING! Live video streaming will NOT work. Run: pip install -r requirements.txt")

        time.sleep(0.5)
        if auto_menu:
            self.menu()
        return True

    def _new_session(self) -> None:
        self.session = Session.new()

    def _prompt_token(self) -> None:
        self.console.info("paste your discord account token")
        entered = safe_input(f"  {self.console.GREY}token:{self.console.RESET} ").strip()
        if entered:
            self.state.token = entered
            self.state.save()
            self.console.ok("token", "saved")

    def _prompt_cookie(self) -> None:
        self.console.info("paste your cf_clearance cookie header")
        entered = safe_input(f"  {self.console.GREY}cookie:{self.console.RESET} ").strip()
        if entered:
            self.state.cookie = entered
            self.state.save()
            self.console.ok("cookie", "saved")

    def _refresh(self) -> None:
        C = self.console
        C.info("paste updated token (or leave empty to keep current):")
        tok = safe_input(f"  {C.GREY}token:{C.RESET} ").strip()
        if tok:
            self.state.token = tok
            C.ok("token", "updated")

        C.info("paste fresh cf_clearance cookie (or leave empty to keep current):")
        ck = safe_input(f"  {C.GREY}cookie:{C.RESET} ").strip()
        if ck:
            self.state.cookie = ck
            C.ok("cookie", "updated")

        C.info("refreshing analytics token...")
        try:
            self.state.fetched_at = 0
            ensure_token(self.state, self.session.super_props)
            self._new_session()
            self.state.save()
            C.ok("auth", "refreshed successfully")
        except Exception as exc:
            C.err("auth", str(exc))
        time.sleep(1)

    def _prompt_viewer_token(self) -> None:
        C = self.console
        C.rule()
        C.info(f"{C.CYAN}Automated Alt Viewer Configuration{C.RESET}")
        C.info("Discord requires at least 1 viewer in the voice channel for hours to count.")
        C.info("Provide an alt account token to automatically join the VC and send Opcode 20 STREAM_WATCH.")
        print()
        curr = (
            self.state.viewer_token[:10] + "..." + self.state.viewer_token[-4:]
            if len(self.state.viewer_token) > 15
            else (self.state.viewer_token or "Not set")
        )
        C.info(f"current alt token: {C.ORANGE}{curr}{C.RESET}")
        print()
        entered = safe_input(f"  {C.GREY}alt token (press enter to clear):{C.RESET} ").strip()
        self.state.viewer_token = entered
        self.state.save()
        if entered:
            C.ok("viewer", "alt viewer token saved")
        else:
            C.warn("viewer", "alt viewer token cleared (disabled)")
        time.sleep(1.2)

    def run_gateway_live_streamer(self, guild_id: str = "", channel_id: str = "") -> None:
        """Start a headless Go Live stream in a voice channel."""
        C = self.console
        C.rule()

        C.info(f"{C.CYAN}Streaming Badge Tiers:{C.RESET}")
        for tier, name, req_hours in STREAM_TIERS:
            symbol = "●" if self.state.total_stream_hours_claimed >= req_hours else "○"
            color = C.GREEN if self.state.total_stream_hours_claimed >= req_hours else C.GREY
            C.info(f"  {color}{symbol} Tier {tier:2d} · {C.ORANGE}{name:12s}{color} {req_hours:5,d}h{C.RESET}")
        print()

        C.info("this connects your account to discord gateway, joins a voice channel,")
        C.info("and broadcasts an active Go Live stream to accumulate streaming hours.")
        print()
        if self.state.has_viewer_token:
            C.ok("viewer", f"automated alt viewer is {C.GREEN}enabled{C.RESET} (will auto-watch via Opcode 20)")
        else:
            C.warn("rule 1", f"An alt or friend {C.YELLOW}MUST sit in the voice channel{C.RESET} - solo streams with 0 viewers do NOT count!")
            C.info("tip", f"{C.GREY}configure Option 3 in the main menu to automate the alt viewer{C.RESET}")
        C.warn("rule 2", f"Discord Settings → Data & Privacy → {C.YELLOW}'Personalize my experience'{C.RESET} must be {C.GREEN}ON{C.RESET}!")
        print()

        engine = GatewayStreamEngine(self.state, self.session, self.console)
        engine.start_stream(guild_id=guild_id, channel_id=channel_id)

    def _reset_main_token(self) -> None:
        C = self.console
        C.rule()
        C.info(f"{C.CYAN}Reset Main Account Token{C.RESET}")
        curr = (
            self.state.token[:10] + "..." + self.state.token[-4:]
            if len(self.state.token) > 15
            else (self.state.token or "Not set")
        )
        C.info(f"current main token: {C.ORANGE}{curr}{C.RESET}")
        print()
        entered = safe_input(f"  {C.GREY}new main token (press enter to clear):{C.RESET} ").strip()
        if entered:
            self.state.token = entered
            self.state.fetched_at = 0
            self.state.save()
            try:
                ensure_token(self.state, self.session.super_props)
                self._new_session()
                self.state.save()
                C.ok("token", "main account token updated and verified")
            except Exception as exc:
                C.warn("token", f"main token saved ({exc})")
        else:
            self.state.token = ""
            self.state.analytics_token = ""
            self.state.save()
            C.warn("token", "main account token reset (cleared)")
        time.sleep(1.2)

    def _reset_alt_token(self) -> None:
        C = self.console
        C.rule()
        C.info(f"{C.CYAN}Reset Alt Account Token{C.RESET}")
        curr = (
            self.state.viewer_token[:10] + "..." + self.state.viewer_token[-4:]
            if len(self.state.viewer_token) > 15
            else (self.state.viewer_token or "Not set")
        )
        C.info(f"current alt token: {C.ORANGE}{curr}{C.RESET}")
        print()
        entered = safe_input(f"  {C.GREY}new alt token (press enter to clear):{C.RESET} ").strip()
        self.state.viewer_token = entered
        self.state.save()
        if entered:
            C.ok("viewer", "alt account token updated")
        else:
            C.warn("viewer", "alt account token reset (cleared)")
        time.sleep(1.2)

    def menu(self) -> None:
        C = self.console
        while True:
            C.clear()
            C.banner()
            viewer_status = f"{C.GREEN}enabled{C.RESET}" if self.state.has_viewer_token else f"{C.GREY}none (optional){C.RESET}"
            C.info(f"total stream hours: {C.ORANGE}{self.state.total_stream_hours_claimed:,.1f}h{C.WHITE} ({self.state.total_stream_sessions} sessions) · alt viewer: {viewer_status}")
            C.rule()
            C.info(f"{C.ORANGE}1{C.RESET} · {C.MAGENTA}Start Live Stream{C.RESET} (Gateway AFK Go Live Broadcast)")
            C.info(f"{C.ORANGE}2{C.RESET} · Inspect Discord Profile & Badges")
            C.info(f"{C.ORANGE}3{C.RESET} · {C.CYAN}Configure Alt Viewer Token{C.RESET} ({C.GREEN}Auto Opcode 20 STREAM_WATCH{C.RESET})")
            C.info(f"{C.ORANGE}4{C.RESET} · Refresh Auth (Token & Cookie)")
            C.info(f"{C.ORANGE}5{C.RESET} · Reset Main Account Token")
            C.info(f"{C.ORANGE}6{C.RESET} · Reset Alt Account Token")
            C.info(f"{C.ORANGE}7{C.RESET} · Exit")
            C.rule()

            choice = safe_input(f"  {C.ORANGE}›{C.RESET} ").strip()
            print()
            if choice == "1":
                self.run_gateway_live_streamer()
            elif choice == "2":
                inspect_profile(self.state, self.session, self.console)
            elif choice == "3":
                self._prompt_viewer_token()
            elif choice == "4":
                self._refresh()
            elif choice == "5":
                self._reset_main_token()
            elif choice == "6":
                self._reset_alt_token()
            elif choice in ("7", "exit", "q", ""):
                break
            else:
                C.warn("input", "invalid option")
                time.sleep(1)


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Discord Stream Badge Maximizer (Headless Live Streamer)")
    parser.add_argument("--stream", "--auto", dest="auto_stream", action="store_true", help="Start streaming automatically without menu")
    parser.add_argument("--channel", type=str, default="", help="Target voice channel ID or link")
    parser.add_argument("--guild", type=str, default="", help="Target guild ID")
    parser.add_argument("--profile", action="store_true", help="Inspect Discord profile and exit")
    args, _ = parser.parse_known_args()

    try:
        app = StreamMaximizerApp()
        if args.auto_stream or args.profile:
            ready = app.start(auto_menu=False)
            if not ready:
                return
            if args.profile:
                inspect_profile(app.state, app.session, app.console)
            elif args.auto_stream:
                target_cid = args.channel or app.state.last_channel_id
                target_gid = args.guild or app.state.last_guild_id
                app.run_gateway_live_streamer(guild_id=target_gid, channel_id=target_cid)
        else:
            app.start(auto_menu=True)
    except KeyboardInterrupt:
        print()
        Console().warn("exit", "operation interrupted by user")


if __name__ == "__main__":
    main()
