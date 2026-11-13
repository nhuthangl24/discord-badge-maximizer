#!/usr/bin/env python3
"""
Discord Auto-Quests Engine
Ported & Adapted directly from https://github.com/Nguoibianhz/Discord-Auto-Quests
Architecture: HieuTool Quest Automation Engine with full protocol support:
- Exact User-Agent, Client Props, x-super-properties Base64 payload.
- All Quest Task Types: Video (7s step maxAllowed pacing), Desktop (60s heartbeat),
  Console (console-heartbeat), Stream (stream-progress), Mobile (mobile-heartbeat),
  Social actions (follow, share, join), and Generic poll progress.
- Discord Orbs virtual currency balance detection.
- Exclusions: 1412491570820812933, expired quests, completed quests.
- Automatic reward claim immediately upon task completion.
- Support for live 5-second public progress tracking in Discord channels.
"""

from __future__ import annotations

import base64
import json
import random
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Optional

DISCORD_API_BASE = "https://discord.com/api/v10"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) discord/1.0.9215 Chrome/138.0.7204.251 Electron/37.6.0 Safari/537.36"
)

CLIENT_PROPS = {
    "os": "Windows",
    "browser": "Discord Client",
    "release_channel": "stable",
    "client_version": "1.0.9215",
    "os_version": "10.0.19045",
    "os_arch": "x64",
    "app_arch": "x64",
    "system_locale": "en-US",
    "has_client_mods": False,
    "client_launch_id": str(uuid.uuid4()),
    "browser_user_agent": USER_AGENT,
    "browser_version": "37.6.0",
    "os_sdk_version": "19045",
    "client_build_number": 471091,
    "native_build_number": 72186,
    "client_event_source": None,
    "launch_signature": str(uuid.uuid4()),
    "client_heartbeat_session_id": str(uuid.uuid4()),
    "client_app_state": "focused",
}


def build_super_properties() -> str:
    props = dict(CLIENT_PROPS)
    props["client_launch_id"] = str(uuid.uuid4())
    props["launch_signature"] = str(uuid.uuid4())
    props["client_heartbeat_session_id"] = str(uuid.uuid4())
    return base64.b64encode(json.dumps(props).encode("utf-8")).decode("utf-8")


def build_headers(token: str) -> dict[str, str]:
    clean_token = token.strip()
    if clean_token.startswith("Bot "):
        clean_token = clean_token[4:]
    return {
        "authorization": clean_token,
        "user-agent": USER_AGENT,
        "content-type": "application/json",
        "accept": "*/*",
        "accept-language": "vi",
        "origin": "https://discord.com",
        "pragma": "no-cache",
        "priority": "u=1, i",
        "referer": "https://discord.com/channels/@me",
        "sec-ch-ua": '"Not)A;Brand";v="8", "Chromium";v="138"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "x-debug-options": "bugReporterEnabled",
        "x-discord-locale": "en-US",
        "x-discord-timezone": "Asia/Saigon",
        "x-super-properties": build_super_properties(),
    }


def quest_api_call(
    url: str,
    token: str,
    method: str = "GET",
    body: Optional[dict[str, Any]] = None,
    timeout: int = 15,
    retries_left: int = 2,
) -> tuple[int, Any]:
    """Helper to perform REST calls to Discord API v10 endpoints with automatic rate-limit backoff."""
    headers = build_headers(token)
    data_bytes = None
    if body is not None:
        data_bytes = json.dumps(body).encode("utf-8")

    req = urllib.request.Request(url, headers=headers, data=data_bytes, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            data = json.loads(raw) if raw else {}
            return resp.status, data
    except urllib.error.HTTPError as exc:
        err_data = {}
        try:
            raw_err = exc.read().decode("utf-8")
            err_data = json.loads(raw_err) if raw_err else {}
        except Exception:
            err_data = {"message": str(exc.reason)}

        # Rate Limit (HTTP 429) Automatic Backoff
        if exc.code == 429 and retries_left > 0:
            retry_after = 3.0
            if isinstance(err_data, dict) and "retry_after" in err_data:
                try:
                    retry_after = float(err_data["retry_after"])
                except Exception:
                    pass
            elif "Retry-After" in exc.headers:
                try:
                    retry_after = float(exc.headers["Retry-After"])
                except Exception:
                    pass
            wait_time = min(max(retry_after + 0.5, 1.0), 30.0)
            print(f"⏳ [Discord Rate Limit 429] Tạm dừng {wait_time:.1f}s trước khi thử lại...")
            time.sleep(wait_time)
            return quest_api_call(
                url=url,
                token=token,
                method=method,
                body=body,
                timeout=timeout,
                retries_left=retries_left - 1,
            )

        return exc.code, err_data
    except Exception as exc:
        return 0, {"message": str(exc)}


def parse_expiration(val: Any) -> Optional[datetime]:
    if not val:
        return None
    if isinstance(val, (int, float)):
        try:
            return datetime.fromtimestamp(val, tz=timezone.utc)
        except Exception:
            return None
    if isinstance(val, str):
        val = val.strip()
        if not val:
            return None
        if val.isdigit():
            try:
                return datetime.fromtimestamp(int(val), tz=timezone.utc)
            except Exception:
                return None
        clean = val.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(clean)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return None
    return None


class TaskType(str, Enum):
    # Video & Media
    WATCH_VIDEO = "WATCH_VIDEO"
    WATCH_VIDEO_ON_MOBILE = "WATCH_VIDEO_ON_MOBILE"
    WATCH_STREAM = "WATCH_STREAM"

    # Gaming - Desktop/PC
    PLAY_ON_DESKTOP = "PLAY_ON_DESKTOP"
    STREAM_ON_DESKTOP = "STREAM_ON_DESKTOP"

    # Gaming - Console
    PLAY_ON_XBOX = "PLAY_ON_XBOX"
    PLAY_ON_PLAYSTATION = "PLAY_ON_PLAYSTATION"
    PLAY_ON_NINTENDO = "PLAY_ON_NINTENDO"

    # Gaming - Mobile/Social
    PLAY_ON_MOBILE = "PLAY_ON_MOBILE"
    PLAY_ACTIVITY = "PLAY_ACTIVITY"
    PLAY_SOCIAL_GAME = "PLAY_SOCIAL_GAME"

    # Engagement
    JOIN_COMMUNITY = "JOIN_COMMUNITY"
    SHARE_CONTENT = "SHARE_CONTENT"
    FOLLOW_SOCIAL = "FOLLOW_SOCIAL"
    COMPLETE_SURVEY = "COMPLETE_SURVEY"

    # Purchase/Monetization
    MAKE_PURCHASE = "MAKE_PURCHASE"
    REDEEM_CODE = "REDEEM_CODE"


def task_icon(t: Optional[str]) -> str:
    if not t:
        return "❓"
    mapping = {
        "WATCH_VIDEO": "🎬",
        "WATCH_VIDEO_ON_MOBILE": "🎬",
        "WATCH_STREAM": "📹",
        "PLAY_ON_DESKTOP": "🎮",
        "STREAM_ON_DESKTOP": "📡",
        "PLAY_ON_XBOX": "🎯",
        "PLAY_ON_PLAYSTATION": "🎪",
        "PLAY_ON_NINTENDO": "🔴",
        "PLAY_ON_MOBILE": "📱",
        "PLAY_ACTIVITY": "🕹️",
        "PLAY_SOCIAL_GAME": "🕹️",
        "FOLLOW_SOCIAL": "👥",
        "SHARE_CONTENT": "📤",
        "JOIN_COMMUNITY": "🏘️",
        "COMPLETE_SURVEY": "📋",
        "MAKE_PURCHASE": "💳",
        "REDEEM_CODE": "🎁",
    }
    return mapping.get(t, "❓")


def task_label_vn(t: Optional[str]) -> str:
    if not t:
        return "Không rõ"
    mapping = {
        "WATCH_VIDEO": "Xem Video",
        "WATCH_VIDEO_ON_MOBILE": "Xem Video (Mobile)",
        "WATCH_STREAM": "Xem Stream",
        "PLAY_ON_DESKTOP": "Chơi Game PC (Desktop)",
        "STREAM_ON_DESKTOP": "Stream Màn Hình (PC)",
        "PLAY_ON_XBOX": "Chơi Xbox",
        "PLAY_ON_PLAYSTATION": "Chơi PlayStation",
        "PLAY_ON_NINTENDO": "Chơi Nintendo",
        "PLAY_ON_MOBILE": "Chơi Game Mobile",
        "PLAY_ACTIVITY": "Chơi Activity",
        "PLAY_SOCIAL_GAME": "Chơi Social Game",
        "FOLLOW_SOCIAL": "Theo dõi Mạng Xã Hội",
        "SHARE_CONTENT": "Chia sẻ nội dung",
        "JOIN_COMMUNITY": "Tham gia cộng đồng",
        "COMPLETE_SURVEY": "Làm khảo sát",
        "MAKE_PURCHASE": "Mua hàng",
        "REDEEM_CODE": "Nhập Code",
    }
    return mapping.get(t, t)


def make_progress_bar(current: float, target: float, length: int = 10) -> str:
    """Creates a visual text progress bar, e.g. [████████░░] 80%"""
    if target <= 0:
        return f"`[{'░' * length}] 0%`"
    ratio = min(1.0, max(0.0, current / target))
    filled = int(round(length * ratio))
    bar = "█" * filled + "░" * (length - filled)
    percent = int(ratio * 100)
    return f"`[{bar}] {percent}%`"


def format_countdown(sec: int) -> str:
    if sec <= 0:
        return "● DONE"
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    parts = []
    if h > 0:
        parts.append(f"{h:02d}h")
    if m > 0 or h > 0:
        parts.append(f"{m:02d}m")
    parts.append(f"{s:02d}s")
    return " ".join(parts)


class BoolFunc:
    """Helper enabling methods to be accessed either as a boolean property or called with ()."""
    def __init__(self, val: bool):
        self._val = bool(val)

    def __bool__(self) -> bool:
        return self._val

    def __call__(self, *args, **kwargs) -> bool:
        return self._val

    def __eq__(self, other: Any) -> bool:
        return self._val == bool(other)

    def __repr__(self) -> str:
        return str(self._val)


@dataclass
class QuestTaskItem:
    task_name: str
    target: int
    progress: float
    applications: list[dict[str, Any]] = field(default_factory=list)

    @property
    def is_completed(self) -> bool:
        return self.progress >= self.target if self.target > 0 else False


@dataclass
class Quest:
    raw: dict[str, Any]

    @property
    def id(self) -> str:
        return str(self.raw.get("id", ""))

    @property
    def config(self) -> dict[str, Any]:
        return self.raw.get("config") or {}

    @property
    def user_status(self) -> Optional[dict[str, Any]]:
        return self.raw.get("user_status")

    @property
    def preview(self) -> bool:
        return bool(self.raw.get("preview"))

    @property
    def name(self) -> str:
        cfg = self.config
        msgs = cfg.get("messages") or {}
        return (
            msgs.get("quest_name", "").strip()
            or msgs.get("game_title", "").strip()
            or f"Quest #{self.id[:6]}"
        )

    @property
    def game_title(self) -> str:
        cfg = self.config
        msgs = cfg.get("messages") or {}
        return msgs.get("game_title", "").strip() or self.name

    @property
    def expires_at(self) -> str:
        return str(self.config.get("expires_at") or "")

    @property
    def tasks(self) -> list[QuestTaskItem]:
        tasks_map = self.get_tasks()
        prog_map = (self.user_status.get("progress") or {}) if self.user_status else {}
        out = []
        for tname, tdata in tasks_map.items():
            if not isinstance(tdata, dict):
                continue
            target_sec = int(tdata.get("target") or tdata.get("seconds_needed") or tdata.get("secondsNeeded") or 900)
            p_val = 0.0
            if tname in prog_map:
                p_item = prog_map[tname]
                if isinstance(p_item, dict):
                    p_val = float(p_item.get("value") or 0.0)
                elif isinstance(p_item, (int, float)):
                    p_val = float(p_item)
            apps = tdata.get("applications") or []
            out.append(QuestTaskItem(
                task_name=tname,
                target=target_sec,
                progress=p_val,
                applications=apps if isinstance(apps, list) else [],
            ))
        return out

    @property
    def is_expired(self) -> BoolFunc:
        exp_str = self.config.get("expires_at")
        if not exp_str:
            return BoolFunc(False)
        dt = parse_expiration(exp_str)
        if dt is None:
            return BoolFunc(False)
        return BoolFunc(datetime.now(timezone.utc) > dt)

    def isExpired(self, now: Optional[datetime] = None) -> bool:
        return bool(self.is_expired)

    @property
    def is_completed(self) -> BoolFunc:
        status = self.user_status
        if not status:
            return BoolFunc(False)
        return BoolFunc(bool(status.get("completed_at")))

    def isCompleted(self) -> bool:
        return bool(self.is_completed)

    @property
    def is_enrolled(self) -> BoolFunc:
        status = self.user_status
        if not status:
            return BoolFunc(False)
        return BoolFunc(bool(status.get("enrolled_at") or status.get("enrollment_time")))

    def isEnrolled(self) -> bool:
        return bool(self.is_enrolled)

    @property
    def is_claimed(self) -> BoolFunc:
        status = self.user_status
        if not status:
            return BoolFunc(False)
        return BoolFunc(bool(status.get("claimed_at")))

    def isClaimed(self) -> bool:
        return bool(self.is_claimed)

    def refresh_status(self, status: Optional[dict[str, Any]]) -> None:
        if status is not None:
            self.raw["user_status"] = status

    def get_tasks(self) -> dict[str, Any]:
        cfg = self.config
        return (
            (cfg.get("task_config_v2") or {}).get("tasks")
            or (cfg.get("task_config") or {}).get("tasks")
            or {}
        )

    def detect_task_type(self) -> Optional[str]:
        tasks = self.get_tasks()
        if not tasks:
            return None
        # Exact Priority matching HieuTool Discord-Auto-Quests engine:
        priority = [
            "PLAY_ON_DESKTOP",
            "PLAY_ON_XBOX",
            "PLAY_ON_PLAYSTATION",
            "PLAY_ON_NINTENDO",
            "PLAY_ON_MOBILE",
            "PLAY_SOCIAL_GAME",
            "PLAY_ACTIVITY",
            "STREAM_ON_DESKTOP",
            "WATCH_STREAM",
            "WATCH_VIDEO",
            "WATCH_VIDEO_ON_MOBILE",
            "FOLLOW_SOCIAL",
            "SHARE_CONTENT",
            "JOIN_COMMUNITY",
            "COMPLETE_SURVEY",
            "REDEEM_CODE",
            "MAKE_PURCHASE",
        ]
        for t in priority:
            if t in tasks:
                return t
        return None

    def get_target(self) -> int:
        ttype = self.detect_task_type()
        if not ttype:
            return 900
        tdata = self.get_tasks().get(ttype) or {}
        return int(tdata.get("target") or tdata.get("seconds_needed") or tdata.get("secondsNeeded") or 900)

    def get_progress(self) -> float:
        ttype = self.detect_task_type()
        if not ttype or not self.user_status:
            return 0.0
        prog_map = self.user_status.get("progress") or {}
        tprog = prog_map.get(ttype) or {}
        if isinstance(tprog, dict):
            return float(tprog.get("value") or 0.0)
        if isinstance(tprog, (int, float)):
            return float(tprog)
        return 0.0

    def get_remaining(self) -> int:
        return max(0, int(self.get_target() - self.get_progress()))

    def get_reward_label(self) -> str:
        cfg = self.config
        rewards_cfg = cfg.get("rewards_config") or {}
        rewards = rewards_cfg.get("rewards") or []
        if not rewards:
            return "Phần thưởng"
        r0 = rewards[0]
        if isinstance(r0, dict):
            if r0.get("orb_quantity"):
                return f"{r0.get('orb_quantity')} Orbs"
            msgs = r0.get("messages") or {}
            return msgs.get("name") or "Phần thưởng"
        return "Phần thưởng"

    @property
    def reward_name(self) -> str:
        return self.get_reward_label()

    @property
    def status_label(self) -> str:
        if self.is_claimed:
            return "🎁 Đã nhận quà (Claimed)"
        if self.is_completed:
            return "✅ Đã xong (Chờ claim)"
        if self.is_expired:
            return f"⏰ Đã hết hạn ({self.expires_at[:10] if self.expires_at else 'N/A'})"
        if self.is_enrolled:
            return "⏳ Đang thực hiện"
        return "✨ Mới (Chưa nhận)"

    @property
    def status_emoji(self) -> str:
        if self.is_claimed:
            return "🎁"
        if self.is_completed:
            return "✅"
        if self.is_expired:
            return "⌛"
        if self.is_enrolled:
            return "⏳"
        return "🆕"

    @property
    def has_video_task(self) -> bool:
        t = self.detect_task_type()
        return t in ("WATCH_VIDEO", "WATCH_VIDEO_ON_MOBILE")

    @property
    def has_desktop_task(self) -> bool:
        t = self.detect_task_type()
        return t in ("PLAY_ON_DESKTOP", "STREAM_ON_DESKTOP", "PLAY_ACTIVITY")


# Aliasing QuestItem for full backward compatibility
QuestItem = Quest


class QuestStore:
    def __init__(self, token: str):
        self.token = token.strip()
        self.pool: dict[str, Quest] = {}

    def load_quests(self) -> list[Quest]:
        status, data = quest_api_call(f"{DISCORD_API_BASE}/quests/@me", self.token, method="GET")
        if status != 200 or not isinstance(data, dict):
            return []
        raw_list = data.get("quests") or []
        self.pool.clear()
        for q_raw in raw_list:
            if isinstance(q_raw, dict):
                q = Quest(q_raw)
                self.pool[q.id] = q
        return list(self.pool.values())

    def all(self) -> list[Quest]:
        return list(self.pool.values())

    def find(self, quest_id: str) -> Optional[Quest]:
        return self.pool.get(quest_id)

    def pending(self) -> list[Quest]:
        # Filter out 1412491570820812933, completed quests, and expired quests
        return [
            q for q in self.all()
            if q.id != "1412491570820812933" and not q.is_completed() and not q.is_expired()
        ]

    def claimable(self) -> list[Quest]:
        return [q for q in self.all() if q.is_completed() and not q.is_claimed()]

    def enroll(self, quest_id: str) -> bool:
        status, res = quest_api_call(
            f"{DISCORD_API_BASE}/quests/{quest_id}/enroll",
            self.token,
            method="POST",
            body={"location": 11, "is_targeted": False, "metadata_raw": None},
        )
        if status in (200, 201) and isinstance(res, dict):
            q = self.find(quest_id)
            if q:
                q.refresh_status(res)
            return True
        return False

    def grab_reward(self, quest_id: str) -> bool:
        status, res = quest_api_call(
            f"{DISCORD_API_BASE}/quests/{quest_id}/claim-reward",
            self.token,
            method="POST",
            body={},
        )
        if status in (200, 201):
            q = self.find(quest_id)
            if q and q.user_status:
                q.user_status["claimed_at"] = datetime.utcnow().isoformat()
            return True
        return False

    def grab_all_rewards(self) -> int:
        claimed = 0
        for q in self.claimable():
            if self.grab_reward(q.id):
                claimed += 1
            time.sleep(random.uniform(0.6, 1.0))
        return claimed

    def execute_video_watch(
        self,
        quest: Quest,
        on_tick: Optional[Callable[[dict[str, Any]], None]] = None,
    ) -> bool:
        target = quest.get_target()
        done = quest.get_progress()

        enrolled_at_str = quest.user_status.get("enrolled_at") if quest.user_status else None
        enrolled_at_dt = parse_expiration(enrolled_at_str)
        enrolled_at_sec = enrolled_at_dt.timestamp() if enrolled_at_dt else time.time()

        finished = False
        while True:
            now_sec = time.time()
            max_allowed = int(now_sec - enrolled_at_sec) + 10
            diff = max_allowed - done
            next_val = done + 7

            if diff >= 7:
                timestamp_val = min(float(target), next_val + round(random.uniform(0.1, 0.9), 2))
                status, res = quest_api_call(
                    f"{DISCORD_API_BASE}/quests/{quest.id}/video-progress",
                    self.token,
                    method="POST",
                    body={"timestamp": timestamp_val},
                )
                if isinstance(res, dict) and (res.get("completed_at") or res.get("completedAt")):
                    finished = True
                    quest.refresh_status(res)
                done = min(target, next_val)
                if on_tick:
                    on_tick({
                        "quest_id": quest.id,
                        "quest_name": quest.name,
                        "task_type": quest.detect_task_type(),
                        "current": done,
                        "target": target,
                        "percent": int((done / target) * 100) if target > 0 else 100,
                        "bar": make_progress_bar(done, target),
                        "countdown": max(0, int(target - done)),
                        "state": "working",
                    })

            if next_val >= target or finished:
                break
            time.sleep(1.0)

        if not finished:
            status, res = quest_api_call(
                f"{DISCORD_API_BASE}/quests/{quest.id}/video-progress",
                self.token,
                method="POST",
                body={"timestamp": float(target)},
            )
            if isinstance(res, dict):
                quest.refresh_status(res)

        return True

    def execute_desktop_gameplay(
        self,
        quest: Quest,
        task_type: str,
        on_tick: Optional[Callable[[dict[str, Any]], None]] = None,
    ) -> bool:
        tasks = quest.get_tasks()
        tdef = tasks.get(task_type) or {}
        apps = tdef.get("applications") or []
        app_id = (apps[0].get("id") if apps and isinstance(apps, list) and isinstance(apps[0], dict) else None) or quest.config.get("application", {}).get("id")
        target = quest.get_target()

        while not quest.is_completed():
            status, res = quest_api_call(
                f"{DISCORD_API_BASE}/quests/{quest.id}/heartbeat",
                self.token,
                method="POST",
                body={"application_id": app_id, "terminal": False},
            )
            if isinstance(res, dict):
                quest.refresh_status(res)

            done = quest.get_progress()
            if on_tick:
                on_tick({
                    "quest_id": quest.id,
                    "quest_name": quest.name,
                    "task_type": task_type,
                    "current": done,
                    "target": target,
                    "percent": int((done / target) * 100) if target > 0 else 0,
                    "bar": make_progress_bar(done, target),
                    "countdown": max(0, int(target - done)),
                    "state": "working",
                })

            if quest.is_completed():
                break

            # Sleep 60 seconds with 5-second tick callbacks
            for _ in range(12):
                if quest.is_completed():
                    break
                time.sleep(5.0)
                if on_tick:
                    on_tick({
                        "quest_id": quest.id,
                        "quest_name": quest.name,
                        "task_type": task_type,
                        "current": quest.get_progress(),
                        "target": target,
                        "percent": int((quest.get_progress() / target) * 100) if target > 0 else 0,
                        "bar": make_progress_bar(quest.get_progress(), target),
                        "countdown": max(0, int(target - quest.get_progress())),
                        "state": "working",
                    })

        # Terminal heartbeat ping
        status, res = quest_api_call(
            f"{DISCORD_API_BASE}/quests/{quest.id}/heartbeat",
            self.token,
            method="POST",
            body={"application_id": app_id, "terminal": True},
        )
        if isinstance(res, dict):
            quest.refresh_status(res)
        return True

    def execute_console_gameplay(self, quest: Quest, task_type: str) -> bool:
        tasks = quest.get_tasks()
        tdef = tasks.get(task_type) or {}
        apps = tdef.get("applications") or []
        app_id = (apps[0].get("id") if apps and isinstance(apps, list) and isinstance(apps[0], dict) else None) or quest.config.get("application", {}).get("id")
        platform = task_type.replace("PLAY_ON_", "")

        while not quest.is_completed():
            status, res = quest_api_call(
                f"{DISCORD_API_BASE}/quests/{quest.id}/console-heartbeat",
                self.token,
                method="POST",
                body={"application_id": app_id, "platform": platform, "terminal": False},
            )
            if isinstance(res, dict):
                quest.refresh_status(res)
            if quest.is_completed():
                break
            time.sleep(60.0)

        quest_api_call(
            f"{DISCORD_API_BASE}/quests/{quest.id}/console-heartbeat",
            self.token,
            method="POST",
            body={"application_id": app_id, "platform": platform, "terminal": True},
        )
        return True

    def execute_stream_watch(self, quest: Quest) -> bool:
        enrolled_at_str = quest.user_status.get("enrolled_at") if quest.user_status else None
        enrolled_at_dt = parse_expiration(enrolled_at_str)
        enrolled_at_sec = enrolled_at_dt.timestamp() if enrolled_at_dt else time.time()
        target = quest.get_target()
        done = quest.get_progress()

        while done < target:
            elapsed = int(time.time() - enrolled_at_sec)
            next_val = min(done + 30, target)
            status, res = quest_api_call(
                f"{DISCORD_API_BASE}/quests/{quest.id}/stream-progress",
                self.token,
                method="POST",
                body={"timestamp": next_val, "elapsed": elapsed},
            )
            done = min(target, next_val)
            if isinstance(res, dict) and (res.get("completed_at") or res.get("completedAt")):
                quest.refresh_status(res)
                break
            time.sleep(30.0)
        return True

    def execute_mobile_gameplay(self, quest: Quest, task_type: str) -> bool:
        tasks = quest.get_tasks()
        tdef = tasks.get(task_type) or {}
        apps = tdef.get("applications") or []
        app_id = (apps[0].get("id") if apps and isinstance(apps, list) and isinstance(apps[0], dict) else None) or quest.config.get("application", {}).get("id")

        while not quest.is_completed():
            status, res = quest_api_call(
                f"{DISCORD_API_BASE}/quests/{quest.id}/mobile-heartbeat",
                self.token,
                method="POST",
                body={"application_id": app_id, "session_id": str(uuid.uuid4())},
            )
            if isinstance(res, dict):
                quest.refresh_status(res)
            if quest.is_completed():
                break
            time.sleep(45.0)
        return True

    def execute_social_action(self, quest: Quest, action: str, platform: str) -> bool:
        status, res = quest_api_call(
            f"{DISCORD_API_BASE}/quests/{quest.id}/social-action",
            self.token,
            method="POST",
            body={"action": action, "platform": platform},
        )
        if isinstance(res, dict):
            quest.refresh_status(res)
        return status in (200, 201)

    def execute_generic(self, quest: Quest) -> bool:
        attempts = 0
        while not quest.is_completed() and attempts < 60:
            status, res = quest_api_call(f"{DISCORD_API_BASE}/quests/{quest.id}/progress", self.token, method="GET")
            if isinstance(res, dict):
                quest.refresh_status(res)
            attempts += 1
            time.sleep(10.0)
        return quest.is_completed()

    def execute_quest(
        self,
        quest: Quest,
        on_tick: Optional[Callable[[dict[str, Any]], None]] = None,
    ) -> bool:
        task_type = quest.detect_task_type()
        if not task_type:
            return False

        if not quest.is_enrolled():
            self.enroll(quest.id)
            time.sleep(1.0)

        # Dispatch execution
        if task_type in ("WATCH_VIDEO", "WATCH_VIDEO_ON_MOBILE"):
            self.execute_video_watch(quest, on_tick=on_tick)
        elif task_type in ("PLAY_ON_DESKTOP", "STREAM_ON_DESKTOP"):
            self.execute_desktop_gameplay(quest, task_type, on_tick=on_tick)
        elif task_type in ("PLAY_ON_XBOX", "PLAY_ON_PLAYSTATION", "PLAY_ON_NINTENDO"):
            self.execute_console_gameplay(quest, task_type)
        elif task_type in ("PLAY_ON_MOBILE", "PLAY_SOCIAL_GAME", "PLAY_ACTIVITY"):
            self.execute_mobile_gameplay(quest, task_type)
        elif task_type == "WATCH_STREAM":
            self.execute_stream_watch(quest)
        elif task_type == "FOLLOW_SOCIAL":
            self.execute_social_action(quest, "follow", "social")
        elif task_type == "SHARE_CONTENT":
            self.execute_social_action(quest, "share", "social")
        elif task_type == "JOIN_COMMUNITY":
            self.execute_social_action(quest, "join", "community")
        elif task_type in ("COMPLETE_SURVEY", "REDEEM_CODE", "MAKE_PURCHASE"):
            self.execute_generic(quest)

        # Grab reward automatically!
        time.sleep(1.0)
        self.grab_reward(quest.id)
        return True


def get_orbs_balance(token: str) -> Optional[int]:
    """Fetches user virtual currency (Orbs) balance from Discord API."""
    status, data = quest_api_call(f"{DISCORD_API_BASE}/users/@me/virtual-currency/balance", token, method="GET")
    if status == 200 and isinstance(data, dict):
        return data.get("balance")
    return None


def fetch_all_quests(token: str, super_props: str = "") -> tuple[bool, list[Quest], str]:
    store = QuestStore(token)
    quests = store.load_quests()
    if not quests:
        return False, [], "Không tìm thấy nhiệm vụ nào hoặc Token không hợp lệ."
    active_cnt = len([q for q in quests if not q.is_expired()])
    expired_cnt = len(quests) - active_cnt
    return True, quests, f"Tìm thấy {len(quests)} nhiệm vụ ({active_cnt} khả dụng, {expired_cnt} đã hết hạn)."


def enroll_quest(token: str, quest_id: str, super_props: str = "") -> tuple[bool, str]:
    store = QuestStore(token)
    if store.enroll(quest_id):
        return True, "Đã nhận nhiệm vụ thành công!"
    return False, "Không thể nhận quest (Lỗi Discord hoặc quest đã hết hạn)."


def auto_enroll_all_quests(token: str, super_props: str = "") -> tuple[int, int, list[str]]:
    store = QuestStore(token)
    store.load_quests()
    pending = store.pending()
    unaccepted = [q for q in pending if not q.is_enrolled()]

    if not unaccepted:
        return 0, len(pending), ["✨ Tất cả nhiệm vụ còn hạn đều đã được nhận trước đó!"]

    enrolled_count = 0
    logs = []
    for q in unaccepted:
        if store.enroll(q.id):
            enrolled_count += 1
            logs.append(f"✅ Đã nhận: **{q.name}**")
        else:
            logs.append(f"⚠️ Không thể nhận: **{q.name}**")
        time.sleep(random.uniform(0.6, 1.2))

    return enrolled_count, len(unaccepted), logs


def complete_video_quest(
    token: str,
    quest: Quest,
    super_props: str = "",
    speed_step: int = 10,
    on_progress: Optional[Any] = None,
) -> tuple[bool, str]:
    store = QuestStore(token)
    store.pool[quest.id] = quest
    ok = store.execute_video_watch(quest, on_tick=on_progress)
    store.grab_reward(quest.id)
    return ok, f"✅ Đã hoàn thành xem video: **{quest.name}**"


def simulate_desktop_game_quest(
    token: str,
    quest: Quest,
    super_props: str = "",
    channel_id: str = "",
    on_progress: Optional[Any] = None,
    step_seconds: int = 30,
) -> tuple[bool, str]:
    store = QuestStore(token)
    store.pool[quest.id] = quest
    ok = store.execute_desktop_gameplay(quest, "PLAY_ON_DESKTOP", on_tick=on_progress)
    store.grab_reward(quest.id)
    return ok, f"✅ Đã hoàn thành game PC: **{quest.name}**"


def claim_quest_reward(token: str, quest_id: str, platform: int = 0, super_props: str = "") -> tuple[bool, str]:
    store = QuestStore(token)
    if store.grab_reward(quest_id):
        return True, "Đã nhận phần thưởng thành công!"
    return False, "Không thể nhận quà (Chưa hoàn thành hoặc đã claim rồi)."


def claim_all_completed_quests(token: str, super_props: str = "") -> tuple[int, list[str]]:
    store = QuestStore(token)
    store.load_quests()
    claimable = store.claimable()
    if not claimable:
        return 0, ["ℹ️ Không có nhiệm vụ nào đang chờ claim quà."]
    logs = []
    claimed_count = 0
    for q in claimable:
        if store.grab_reward(q.id):
            claimed_count += 1
            logs.append(f"🎁 **{q.name}**: Đã nhận phần thưởng (`{q.get_reward_label()}`)")
        else:
            logs.append(f"⚠️ **{q.name}**: Không thể claim quà")
        time.sleep(random.uniform(0.6, 1.0))
    return claimed_count, logs


def auto_run_quest_flow(
    token: str,
    channel_id: str = "",
    super_props: str = "",
    on_progress: Optional[Any] = None,
) -> dict[str, Any]:
    """Compatibility wrapper that runs the HieuTool engine."""
    store = QuestStore(token)
    all_quests = store.load_quests()
    pending = store.pending()
    claimable = store.claimable()

    summary = {
        "total_quests": len(all_quests),
        "active_quests": len(pending),
        "expired_quests": len(all_quests) - len(pending),
        "enrolled_new": 0,
        "video_completed": 0,
        "desktop_simulated": 0,
        "claimed": 0,
        "logs": [],
        "quests": all_quests,
    }

    # Claim existing claimables first
    if claimable:
        c_count = store.grab_all_rewards()
        summary["claimed"] += c_count
        summary["logs"].append(f"🎁 Đã tự động claim **+{c_count}** phần thưởng trước khi làm nhiệm vụ mới.")

    if not pending:
        summary["logs"].append("✨ Không có nhiệm vụ nào đang chờ xử lý!")
        return summary

    for q in pending:
        ttype = q.detect_task_type()
        if not q.is_enrolled():
            if store.enroll(q.id):
                summary["enrolled_new"] += 1
                summary["logs"].append(f"✅ Đã nhận: **{q.name}**")
            time.sleep(1.0)

        store.execute_quest(q, on_tick=on_progress)
        if ttype in ("WATCH_VIDEO", "WATCH_VIDEO_ON_MOBILE"):
            summary["video_completed"] += 1
            summary["logs"].append(f"🎬 Video: Hoàn thành **{q.name}**")
        elif ttype in ("PLAY_ON_DESKTOP", "STREAM_ON_DESKTOP"):
            summary["desktop_simulated"] += 1
            summary["logs"].append(f"🎮 Desktop: Hoàn thành **{q.name}**")
        else:
            summary["logs"].append(f"✨ Hoàn thành **{q.name}** ({task_label_vn(ttype)})")

        summary["claimed"] += 1

    return summary
