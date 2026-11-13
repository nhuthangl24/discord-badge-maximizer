#!/usr/bin/env python3
"""
Discord Quest Manager & Automation Tool
Inspired by discord-quest-completer & Discord Internal APIs.

Supports:
- Auto GET: Fetch all active quests available for the user from Discord API (/api/v9/quests/@me).
- Auto ACCEPT/ENROLL: Automatically accept/enroll in all uncompleted quests.
- Auto VIDEO PROGRESS: Automatically simulate video watching for WATCH_VIDEO / WATCH_VIDEO_ON_MOBILE quests.
- Auto HEARTBEAT: Send activity heartbeat for PLAY_ACTIVITY quests.
- Auto CLAIM: Automatically claim rewards for completed quests.
- Expiration & Error Guard: Filters out expired quests to prevent 404s, handles 429 rate-limiting with auto backoff.
- Live Notifications: Detailed real-time notifications during auto quest execution.
- Multi-User Isolation: Operates safely with user tokens stored securely via crypto_vault.
- Admin Bulk Control: Allows administrators to auto-enroll quests for all users in the system.
"""

from __future__ import annotations

import json
import random
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from stream import USER_AGENT, Session, build_super_props

QUESTS_URL = "https://discord.com/api/v9/quests/@me"
DISCORD_API_BASE = "https://discord.com/api/v9"


def parse_expiration(val: Any) -> Optional[datetime]:
    """Parses various date/time formats into UTC datetime for expiration comparisons."""
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


def is_quest_expired(expires_at: Any) -> bool:
    """Returns True if the quest expiration date is in the past."""
    dt = parse_expiration(expires_at)
    if dt is None:
        return False
    return dt < datetime.now(timezone.utc)


def is_quest_started(starts_at: Any) -> bool:
    """Returns True if the quest has started (starts_at is in past or empty)."""
    dt = parse_expiration(starts_at)
    if dt is None:
        return True
    return dt <= datetime.now(timezone.utc)


@dataclass
class QuestTask:
    task_name: str
    target: int  # in seconds
    progress: float  # in seconds
    applications: list[dict[str, Any]] = field(default_factory=list)

    @property
    def is_completed(self) -> bool:
        return self.progress >= self.target if self.target > 0 else False


def make_progress_bar(current: float, target: float, length: int = 10) -> str:
    """Creates a visual text progress bar, e.g. [████████░░░░] 67%"""
    if target <= 0:
        return f"`[{'░' * length}] 0%`"
    ratio = min(1.0, max(0.0, current / target))
    filled = int(round(length * ratio))
    bar = "█" * filled + "░" * (length - filled)
    percent = int(ratio * 100)
    return f"`[{bar}] {percent}%`"


@dataclass
class QuestItem:
    id: str
    name: str
    game_title: str
    application_id: str
    expires_at: str
    tasks: list[QuestTask]
    enrolled_at: Optional[str] = None
    completed_at: Optional[str] = None
    claimed_at: Optional[str] = None
    reward_name: str = ""
    raw_data: dict[str, Any] = field(default_factory=dict)
    starts_at: str = ""

    @property
    def progress_bar(self) -> str:
        if not self.tasks:
            return "`[░░░░░░░░░░] 0%`"
        t0 = self.tasks[0]
        return make_progress_bar(t0.progress, t0.target)

    @property
    def is_expired(self) -> bool:
        return is_quest_expired(self.expires_at)

    @property
    def is_active(self) -> bool:
        return not self.is_expired and is_quest_started(self.starts_at)

    @property
    def is_enrolled(self) -> bool:
        return bool(self.enrolled_at)

    @property
    def is_completed(self) -> bool:
        return bool(self.completed_at) or any(t.is_completed for t in self.tasks if self.tasks)

    @property
    def is_claimed(self) -> bool:
        return bool(self.claimed_at)

    @property
    def status_label(self) -> str:
        if self.is_claimed:
            return "🎁 Đã nhận quà (Claimed)"
        if self.is_completed:
            return "✅ Đã hoàn thành (Chờ claim)"
        if self.is_expired:
            exp_date = self.expires_at[:10] if self.expires_at else "Hết hạn"
            return f"⏰ Đã hết hạn ({exp_date})"
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
        return any(t.task_name in ("WATCH_VIDEO", "WATCH_VIDEO_ON_MOBILE") for t in self.tasks)

    @property
    def has_desktop_task(self) -> bool:
        return any(t.task_name in ("PLAY_ON_DESKTOP", "PLAY_ACTIVITY") for t in self.tasks)


def _build_headers(token: str, super_props: str = "") -> dict[str, str]:
    if not super_props:
        try:
            sess = Session.new()
            super_props = sess.super_props
        except Exception:
            super_props = ""

    headers = {
        "authorization": token.strip(),
        "user-agent": USER_AGENT,
        "content-type": "application/json",
        "accept": "*/*",
        "accept-language": "en-US,en;q=0.9,vi;q=0.8",
    }
    if super_props:
        headers["x-super-properties"] = super_props
    return headers


def _quest_api_call(
    url: str,
    token: str,
    method: str = "GET",
    body: Optional[dict[str, Any]] = None,
    super_props: str = "",
    timeout: int = 15,
    retries_left: int = 2,
) -> tuple[int, Any]:
    """
    Helper to perform REST calls to Discord Quest API endpoints with automatic rate-limit backoff.
    If Discord returns HTTP 429, this waits for retry_after and transparently retries.
    """
    headers = _build_headers(token, super_props)
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

        # Rate Limit (HTTP 429) Automatic Backoff & Retry
        if exc.code == 429 and retries_left > 0:
            retry_after = 2.5
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
            print(f"⏳ [Discord Rate Limit 429] Đang tạm dừng {wait_time:.1f}s trước khi thử lại...")
            time.sleep(wait_time)
            return _quest_api_call(
                url=url,
                token=token,
                method=method,
                body=body,
                super_props=super_props,
                timeout=timeout,
                retries_left=retries_left - 1,
            )

        if exc.code == 404:
            if isinstance(err_data, dict) and not err_data.get("message"):
                err_data["message"] = "Nhiệm vụ không tồn tại hoặc đã hết hạn trên Discord (404)."
        elif exc.code == 403:
            if isinstance(err_data, dict) and not err_data.get("message"):
                err_data["message"] = "Bị từ chối quyền truy cập hoặc tài khoản bị giới hạn tạm thời (403)."
        elif exc.code == 400:
            if isinstance(err_data, dict) and not err_data.get("message"):
                err_data["message"] = "Yêu cầu không hợp lệ hoặc nhiệm vụ không thể nhận (400)."

        return exc.code, err_data
    except Exception as exc:
        return 0, {"message": str(exc)}


def parse_quest(q: dict[str, Any]) -> QuestItem:
    """Safely extracts Quest details handling both camelCase and snake_case variations."""
    qid = str(q.get("id", ""))
    cfg = q.get("config") or {}
    messages = cfg.get("messages") or {}
    name = (
        messages.get("quest_name")
        or messages.get("questName")
        or messages.get("game_title")
        or messages.get("gameTitle")
        or f"Quest #{qid[:6]}"
    )
    game_title = (
        messages.get("game_title")
        or messages.get("gameTitle")
        or messages.get("quest_name")
        or messages.get("questName")
        or "Discord Game"
    )

    app_info = cfg.get("application") or {}
    app_id = str(app_info.get("id") or "")

    starts_at = str(cfg.get("starts_at") or cfg.get("startsAt") or q.get("starts_at") or q.get("startsAt") or "")
    expires_at = str(cfg.get("expires_at") or cfg.get("expiresAt") or q.get("expires_at") or q.get("expiresAt") or "")

    # Rewards
    rewards_cfg = cfg.get("rewards_config") or cfg.get("rewardsConfig") or {}
    rewards_list = rewards_cfg.get("rewards") or []
    reward_name = ""
    if rewards_list and isinstance(rewards_list, list):
        r0 = rewards_list[0]
        if isinstance(r0, dict):
            reward_messages = r0.get("messages") or {}
            reward_name = (
                reward_messages.get("name")
                or reward_messages.get("title")
                or str(r0.get("type", "Reward"))
            )

    # User Status
    status_obj = q.get("user_status") or q.get("userStatus") or {}
    enrolled_at = (
        status_obj.get("enrolled_at")
        or status_obj.get("enrolledAt")
        or status_obj.get("enrollment_time")
        or status_obj.get("enrollmentTime")
    )
    completed_at = status_obj.get("completed_at") or status_obj.get("completedAt")
    claimed_at = status_obj.get("claimed_at") or status_obj.get("claimedAt")
    if not claimed_at and status_obj.get("claimed_status"):
        claimed_at = "CLAIMED"

    progress_dict = status_obj.get("progress") or {}

    # Tasks Config
    task_cfg = (
        cfg.get("task_config")
        or cfg.get("taskConfig")
        or cfg.get("task_config_v2")
        or cfg.get("taskConfigV2")
        or {}
    )
    raw_tasks = task_cfg.get("tasks") or {}

    parsed_tasks: list[QuestTask] = []
    if isinstance(raw_tasks, dict):
        for tname, tdata in raw_tasks.items():
            if not isinstance(tdata, dict):
                continue
            target_sec = int(tdata.get("target") or tdata.get("seconds_needed") or tdata.get("secondsNeeded") or 0)
            p_val = 0.0
            if isinstance(progress_dict, dict) and tname in progress_dict:
                p_item = progress_dict[tname]
                if isinstance(p_item, dict):
                    p_val = float(p_item.get("value") or 0.0)
                    if p_item.get("completed") is True and target_sec > 0 and p_val < target_sec:
                        p_val = float(target_sec)
                elif isinstance(p_item, (int, float)):
                    p_val = float(p_item)
            apps = tdata.get("applications") or []
            if not app_id and apps and isinstance(apps, list) and isinstance(apps[0], dict):
                app_id = str(apps[0].get("id") or "")
            parsed_tasks.append(QuestTask(
                task_name=tname,
                target=target_sec,
                progress=p_val,
                applications=apps if isinstance(apps, list) else [],
            ))
    elif isinstance(raw_tasks, list):
        for tdata in raw_tasks:
            if not isinstance(tdata, dict):
                continue
            tname = str(tdata.get("type") or tdata.get("task_name") or tdata.get("name") or "TASK")
            target_sec = int(tdata.get("target") or tdata.get("seconds_needed") or tdata.get("secondsNeeded") or 0)
            p_val = 0.0
            if isinstance(progress_dict, dict) and tname in progress_dict:
                p_item = progress_dict[tname]
                if isinstance(p_item, dict):
                    p_val = float(p_item.get("value") or 0.0)
                    if p_item.get("completed") is True and target_sec > 0 and p_val < target_sec:
                        p_val = float(target_sec)
                elif isinstance(p_item, (int, float)):
                    p_val = float(p_item)
            parsed_tasks.append(QuestTask(
                task_name=tname,
                target=target_sec,
                progress=p_val,
                applications=[],
            ))

    return QuestItem(
        id=qid,
        name=name,
        game_title=game_title,
        application_id=app_id,
        starts_at=starts_at,
        expires_at=expires_at,
        tasks=parsed_tasks,
        enrolled_at=enrolled_at,
        completed_at=completed_at,
        claimed_at=claimed_at,
        reward_name=reward_name,
        raw_data=q,
    )


def fetch_all_quests(token: str, super_props: str = "") -> tuple[bool, list[QuestItem], str]:
    """
    Fetches all quests for the user token from Discord REST API.
    Returns: (success, list_of_quests, error_or_status_message)
    """
    if not token or not token.strip():
        return False, [], "Token chưa được thiết lập."

    status, data = _quest_api_call(QUESTS_URL, token, method="GET", super_props=super_props)
    if status == 401:
        return False, [], "Token Discord không hợp lệ hoặc đã hết hạn (401 Unauthorized)."
    if status != 200:
        msg = data.get("message") if isinstance(data, dict) else f"HTTP {status}"
        return False, [], f"Không thể lấy danh sách Quest từ Discord ({msg})."

    raw_quests = []
    if isinstance(data, dict):
        raw_quests = data.get("quests") or []
    elif isinstance(data, list):
        raw_quests = data

    items: list[QuestItem] = []
    for q in raw_quests:
        if isinstance(q, dict):
            try:
                items.append(parse_quest(q))
            except Exception as e:
                print(f"[Quest Parser Warning] {e}")

    active_cnt = len([q for q in items if not q.is_expired])
    expired_cnt = len(items) - active_cnt
    msg_summary = f"Đã tìm thấy {len(items)} nhiệm vụ ({active_cnt} còn hạn, {expired_cnt} đã hết hạn)."
    return True, items, msg_summary


def enroll_quest(token: str, quest_id: str, super_props: str = "") -> tuple[bool, str]:
    """
    Enrolls / accepts a specific Discord quest by ID.
    Returns: (success, message)
    """
    url = f"{DISCORD_API_BASE}/quests/{quest_id}/enroll"
    payload = {
        "location": 11,
        "is_targeted": False,
        "metadata_raw": None,
    }
    status, data = _quest_api_call(url, token, method="POST", body=payload, super_props=super_props)
    if status in (200, 201):
        return True, "Đã nhận nhiệm vụ thành công!"
    if status == 400:
        msg = data.get("message", "Nhiệm vụ đã được nhận hoặc không thể nhận.") if isinstance(data, dict) else str(data)
        return False, f"Lỗi yêu cầu: {msg}"
    if status == 401:
        return False, "Token không hợp lệ (401)."
    if status == 404:
        return False, "Nhiệm vụ không tồn tại hoặc đã hết hạn trên Discord (404)."
    if status == 429:
        return False, "Bị giới hạn tần suất yêu cầu Discord (429)."
    return False, f"Không thể nhận quest (HTTP {status})"


def auto_enroll_all_quests(token: str, super_props: str = "") -> tuple[int, int, list[str]]:
    """
    Fetches all quests and automatically enrolls in all ACTIVE quests that are not yet accepted.
    Returns: (enrolled_count, total_available, log_messages)
    """
    ok, quests, msg = fetch_all_quests(token, super_props)
    if not ok:
        return 0, 0, [f"❌ {msg}"]

    logs: list[str] = []
    active_quests = [q for q in quests if not q.is_expired]
    expired_quests = [q for q in quests if q.is_expired]

    if expired_quests:
        logs.append(f"ℹ️ Đã tự động bỏ qua {len(expired_quests)} nhiệm vụ cũ đã hết hạn.")

    to_enroll = [q for q in active_quests if not q.is_enrolled]

    if not to_enroll:
        if not active_quests:
            return 0, 0, logs + ["ℹ️ Không có nhiệm vụ nào còn hạn để nhận."]
        return 0, len(active_quests), logs + ["✨ Tất cả nhiệm vụ còn hạn đều đã được nhận trước đó!"]

    enrolled_count = 0
    for q in to_enroll:
        success, emsg = enroll_quest(token, q.id, super_props)
        if success:
            enrolled_count += 1
            logs.append(f"✅ Đã nhận: **{q.name}**")
            q.enrolled_at = datetime.utcnow().isoformat()
        else:
            logs.append(f"⚠️ {q.name}: {emsg}")
        time.sleep(random.uniform(0.6, 1.2))

    return enrolled_count, len(to_enroll), logs


def complete_video_quest(
    token: str,
    quest: QuestItem,
    super_props: str = "",
    speed_step: int = 10,
    on_progress: Optional[Any] = None,
) -> tuple[bool, str]:
    """
    Simulates video viewing progress for active WATCH_VIDEO / WATCH_VIDEO_ON_MOBILE tasks.
    Filters expired quests, handles rate limits, and sends incremental video progress updates.
    """
    if quest.is_expired:
        exp_date = quest.expires_at[:10] if quest.expires_at else "Hết hạn"
        return False, f"Bỏ qua: Nhiệm vụ '{quest.name}' đã hết hạn ({exp_date})."

    video_task = next(
        (t for t in quest.tasks if t.task_name in ("WATCH_VIDEO", "WATCH_VIDEO_ON_MOBILE")),
        None,
    )
    if not video_task:
        return False, f"Nhiệm vụ '{quest.name}' không phải dạng xem Video."

    if quest.is_completed:
        return True, f"Nhiệm vụ '{quest.name}' đã hoàn thành trước đó."

    target = video_task.target if video_task.target > 0 else 30
    current = int(video_task.progress or 0)
    url = f"{DISCORD_API_BASE}/quests/{quest.id}/video-progress"

    # Enroll first if not enrolled
    if not quest.is_enrolled:
        ok_enroll, emsg = enroll_quest(token, quest.id, super_props)
        if not ok_enroll:
            return False, f"Không thể nhận nhiệm vụ '{quest.name}': {emsg}"
        quest.enrolled_at = datetime.utcnow().isoformat()
        time.sleep(1.2)

    print(f"🎬 [Quest Video Process] Bắt đầu xem video: {quest.name} ({current}s/{target}s)")
    if on_progress:
        try:
            on_progress({
                "quest_id": quest.id,
                "quest_name": quest.name,
                "game_title": quest.game_title,
                "task_type": "WATCH_VIDEO",
                "current": current,
                "target": target,
                "percent": int((current / target) * 100) if target > 0 else 0,
                "bar": make_progress_bar(current, target),
                "status": "RUNNING",
                "log": f"Bắt đầu phát video: `{quest.name}`",
            })
        except Exception:
            pass

    while current < target:
        current = min(target, current + speed_step)
        timestamp_val = current + round(random.uniform(0.1, 0.9), 2)
        payload = {"timestamp": min(float(target), timestamp_val)}
        status, data = _quest_api_call(url, token, method="POST", body=payload, super_props=super_props)
        
        if status == 404:
            err_msg = f"Nhiệm vụ '{quest.name}' không còn tồn tại hoặc đã hết hạn trên Discord (404)."
            print(f"❌ [Quest Video Process] {err_msg}")
            if on_progress:
                try:
                    on_progress({
                        "quest_id": quest.id,
                        "quest_name": quest.name,
                        "status": "ERROR",
                        "current": current,
                        "target": target,
                        "percent": int((current / target) * 100),
                        "bar": make_progress_bar(current, target),
                        "log": f"⚠️ {err_msg}",
                    })
                except Exception:
                    pass
            return False, err_msg

        if status not in (200, 201):
            err_msg = f"Lỗi gửi tiến độ video (HTTP {status})"
            print(f"❌ [Quest Video Process] {err_msg}")
            if on_progress:
                try:
                    on_progress({
                        "quest_id": quest.id,
                        "quest_name": quest.name,
                        "game_title": quest.game_title,
                        "task_type": "WATCH_VIDEO",
                        "current": current,
                        "target": target,
                        "percent": int((current / target) * 100),
                        "bar": make_progress_bar(current, target),
                        "status": "ERROR",
                        "log": f"⚠️ {err_msg}",
                    })
                except Exception:
                    pass
            return False, err_msg

        percent = int(min(100.0, (current / target) * 100))
        bar = make_progress_bar(current, target)
        print(f"🎬 [Quest Video Process] {quest.name}: {current}/{target}s ({percent}%) {bar}")
        if on_progress:
            try:
                on_progress({
                    "quest_id": quest.id,
                    "quest_name": quest.name,
                    "game_title": quest.game_title,
                    "task_type": "WATCH_VIDEO",
                    "current": current,
                    "target": target,
                    "percent": percent,
                    "bar": bar,
                    "status": "RUNNING",
                    "log": f"Đang xem video: `{current}s/{target}s` ({percent}%)",
                })
            except Exception:
                pass

        if isinstance(data, dict) and (data.get("completed_at") or data.get("completedAt")):
            print(f"✅ [Quest Video Process] Đã hoàn thành: {quest.name}")
            quest.completed_at = datetime.utcnow().isoformat()
            if on_progress:
                try:
                    on_progress({
                        "quest_id": quest.id,
                        "quest_name": quest.name,
                        "game_title": quest.game_title,
                        "task_type": "WATCH_VIDEO",
                        "current": target,
                        "target": target,
                        "percent": 100,
                        "bar": make_progress_bar(target, target),
                        "status": "COMPLETED",
                        "log": f"✅ Hoàn thành xem video: `{quest.name}`!",
                    })
                except Exception:
                    pass
            return True, f"✅ Đã hoàn thành xem video: **{quest.name}**"

        time.sleep(random.uniform(1.2, 1.8))

    # Send final completion ping
    payload = {"timestamp": float(target)}
    _quest_api_call(url, token, method="POST", body=payload, super_props=super_props)
    quest.completed_at = datetime.utcnow().isoformat()
    print(f"✅ [Quest Video Process] Hoàn tất: {quest.name} ({target}s)")
    if on_progress:
        try:
            on_progress({
                "quest_id": quest.id,
                "quest_name": quest.name,
                "game_title": quest.game_title,
                "task_type": "WATCH_VIDEO",
                "current": target,
                "target": target,
                "percent": 100,
                "bar": make_progress_bar(target, target),
                "status": "COMPLETED",
                "log": f"✅ Hoàn tất video: `{quest.name}` ({target}s)!",
            })
        except Exception:
            pass
    return True, f"✅ Đã hoàn thành xem video: **{quest.name}** ({target}s)"


def simulate_desktop_game_quest(
    token: str,
    quest: QuestItem,
    super_props: str = "",
    channel_id: str = "",
    on_progress: Optional[Any] = None,
    step_seconds: int = 30,
) -> tuple[bool, str]:
    """
    Simulates playing a verified desktop game process for PLAY_ON_DESKTOP / PLAY_ACTIVITY quests.
    Generates a spoofed OS Process PID, simulates game activity, and sends periodic heartbeat pings.
    """
    if quest.is_expired:
        return False, f"Bỏ qua: Nhiệm vụ '{quest.name}' đã hết hạn."

    task = next(
        (t for t in quest.tasks if t.task_name in ("PLAY_ON_DESKTOP", "PLAY_ACTIVITY")),
        None,
    )
    if not task:
        return False, f"Nhiệm vụ '{quest.name}' không yêu cầu chơi game trên máy tính (Desktop/Activity)."

    if quest.is_completed:
        return True, f"Nhiệm vụ '{quest.name}' đã hoàn thành trước đó."

    target = task.target or 900
    current = int(task.progress or 0)
    fake_pid = random.randint(12000, 58000)
    clean_title = quest.game_title.replace(" ", "")
    exe_name = f"{clean_title}.exe"
    if task.applications and isinstance(task.applications, list) and isinstance(task.applications[0], dict):
        app_obj = task.applications[0]
        exe_name = app_obj.get("name", clean_title).replace(" ", "") + ".exe"

    print(f"🎮 [Quest Desktop Process] Khởi tạo giả lập tiến trình `{exe_name}` (PID: {fake_pid}) cho `{quest.name}`")
    if on_progress:
        try:
            on_progress({
                "quest_id": quest.id,
                "quest_name": quest.name,
                "game_title": quest.game_title,
                "task_type": task.task_name,
                "current": current,
                "target": target,
                "percent": int((current / target) * 100) if target > 0 else 0,
                "bar": make_progress_bar(current, target),
                "pid": fake_pid,
                "exe_name": exe_name,
                "status": "RUNNING",
                "log": f"Khởi động tiến trình `{exe_name}` (PID: `{fake_pid}`)...",
            })
        except Exception:
            pass

    # Enroll first if needed
    if not quest.is_enrolled:
        ok_enroll, emsg = enroll_quest(token, quest.id, super_props)
        if not ok_enroll:
            return False, f"Không thể nhận nhiệm vụ '{quest.name}': {emsg}"
        quest.enrolled_at = datetime.utcnow().isoformat()
        time.sleep(1.2)

    url = f"{DISCORD_API_BASE}/quests/{quest.id}/heartbeat"
    stream_key = f"call:{channel_id}:1" if channel_id else ""

    while current < target:
        current = min(target, current + step_seconds)
        payload = {"stream_key": stream_key, "terminal": (current >= target)}
        status, data = _quest_api_call(url, token, method="POST", body=payload, super_props=super_props)
        
        if status == 404:
            err_msg = f"Nhiệm vụ '{quest.name}' không còn tồn tại hoặc đã hết hạn trên Discord (404)."
            print(f"❌ [Quest Desktop Process] {err_msg}")
            return False, err_msg

        if status not in (200, 201):
            err_msg = f"Lỗi gửi heartbeat tiến trình game (HTTP {status})"
            print(f"❌ [Quest Desktop Process] {err_msg}")
            return False, err_msg

        if isinstance(data, dict):
            prog_dict = data.get("progress") or {}
            task_prog = prog_dict.get(task.task_name)
            if isinstance(task_prog, dict):
                current = max(current, int(task_prog.get("value", current)))

        percent = int(min(100.0, (current / target) * 100))
        bar = make_progress_bar(current, target)
        print(f"🎮 [Quest Desktop Process] {exe_name} (PID: {fake_pid}): {current}/{target}s ({percent}%) {bar}")

        if on_progress:
            try:
                on_progress({
                    "quest_id": quest.id,
                    "quest_name": quest.name,
                    "game_title": quest.game_title,
                    "task_type": task.task_name,
                    "current": current,
                    "target": target,
                    "percent": percent,
                    "bar": bar,
                    "pid": fake_pid,
                    "exe_name": exe_name,
                    "status": "RUNNING",
                    "log": f"Tiến trình `{exe_name}` (PID: `{fake_pid}`) đang chạy: `{current}s/{target}s` ({percent}%)",
                })
            except Exception:
                pass

        if current >= target:
            break

        time.sleep(min(8.0, float(step_seconds)))

    # Terminal final ping
    _quest_api_call(url, token, method="POST", body={"stream_key": stream_key, "terminal": True}, super_props=super_props)
    quest.completed_at = datetime.utcnow().isoformat()
    print(f"✅ [Quest Desktop Process] Tiến trình `{exe_name}` (PID: {fake_pid}) hoàn thành nhiệm vụ `{quest.name}`!")
    if on_progress:
        try:
            on_progress({
                "quest_id": quest.id,
                "quest_name": quest.name,
                "game_title": quest.game_title,
                "task_type": task.task_name,
                "current": target,
                "target": target,
                "percent": 100,
                "bar": make_progress_bar(target, target),
                "pid": fake_pid,
                "exe_name": exe_name,
                "status": "COMPLETED",
                "log": f"✅ Tiến trình `{exe_name}` hoàn thành {target}s!",
            })
        except Exception:
            pass

    return True, f"✅ Đã chạy giả lập tiến trình game `{exe_name}` (PID: `{fake_pid}`) hoàn tất **{quest.name}**!"


def complete_activity_quest(
    token: str,
    quest: QuestItem,
    channel_id: str,
    super_props: str = "",
) -> tuple[bool, str]:
    """
    Sends heartbeat pings for PLAY_ACTIVITY quests.
    """
    if quest.is_expired:
        return False, f"Bỏ qua: Nhiệm vụ '{quest.name}' đã hết hạn."

    act_task = next((t for t in quest.tasks if t.task_name == "PLAY_ACTIVITY"), None)
    if not act_task:
        return False, "Nhiệm vụ này không phải dạng Play Activity."

    if not channel_id:
        return False, "Chưa cung cấp Channel ID để chạy Activity."

    stream_key = f"call:{channel_id}:1"
    url = f"{DISCORD_API_BASE}/quests/{quest.id}/heartbeat"

    # Enroll first
    if not quest.is_enrolled:
        ok_enroll, emsg = enroll_quest(token, quest.id, super_props)
        if not ok_enroll:
            return False, f"Không thể nhận quest: {emsg}"
        quest.enrolled_at = datetime.utcnow().isoformat()
        time.sleep(1.2)

    target = act_task.target or 300
    current = int(act_task.progress or 0)

    while current < target:
        payload = {"stream_key": stream_key, "terminal": False}
        status, data = _quest_api_call(url, token, method="POST", body=payload, super_props=super_props)
        if status not in (200, 201):
            return False, f"Lỗi gửi heartbeat activity (HTTP {status})"

        if isinstance(data, dict):
            prog = data.get("progress", {}).get("PLAY_ACTIVITY", {}).get("value", current)
            current = int(prog)

        time.sleep(15.0)

    # Terminal ping
    _quest_api_call(url, token, method="POST", body={"stream_key": stream_key, "terminal": True}, super_props=super_props)
    quest.completed_at = datetime.utcnow().isoformat()
    return True, f"✅ Đã hoàn thành Play Activity: **{quest.name}**"


def claim_quest_reward(
    token: str,
    quest_id: str,
    platform: int = 0,
    super_props: str = "",
) -> tuple[bool, str]:
    """
    Claims reward for a completed quest.
    """
    url = f"{DISCORD_API_BASE}/quests/{quest_id}/claim-reward"
    payload = {"platform": platform}
    status, data = _quest_api_call(url, token, method="POST", body=payload, super_props=super_props)
    if status in (200, 201):
        code = ""
        if isinstance(data, dict):
            code = data.get("code") or data.get("reward_code") or ""
        msg = f"Đã nhận quà thành công! Mã thưởng: `{code}`" if code else "Đã nhận phần thưởng thành công!"
        return True, msg
    if status == 400:
        return False, "Nhiệm vụ chưa đủ điều kiện hoặc đã nhận quà rồi."
    if status == 404:
        return False, "Nhiệm vụ không tồn tại hoặc đã hết hạn trên Discord (404)."
    return False, f"Không thể claim quà (HTTP {status})"


def claim_all_completed_quests(token: str, super_props: str = "") -> tuple[int, list[str]]:
    """
    Finds all active, completed but unclaimed quests and claims their rewards.
    Returns: (claimed_count, log_messages)
    """
    ok, quests, msg = fetch_all_quests(token, super_props)
    if not ok:
        return 0, [f"❌ {msg}"]

    logs: list[str] = []
    claimed_count = 0
    to_claim = [q for q in quests if not q.is_expired and q.is_completed and not q.is_claimed]

    if not to_claim:
        return 0, ["ℹ️ Không có nhiệm vụ nào đang chờ claim quà."]

    for q in to_claim:
        success, cmsg = claim_quest_reward(token, q.id, super_props=super_props)
        if success:
            claimed_count += 1
            logs.append(f"🎁 **{q.name}**: {cmsg}")
            q.claimed_at = datetime.utcnow().isoformat()
        else:
            logs.append(f"⚠️ **{q.name}**: {cmsg}")
        time.sleep(random.uniform(0.8, 1.5))

    return claimed_count, logs


def auto_run_quest_flow(
    token: str,
    channel_id: str = "",
    super_props: str = "",
    on_progress: Optional[Any] = None,
) -> dict[str, Any]:
    """
    Full automated Quest suite with notifications:
    1. Fetch all quests and filter active vs expired quests (bỏ qua quest hết hạn).
    2. Auto enroll all active unaccepted quests.
    3. Auto complete video quests (with live progress).
    4. Auto simulate desktop game process for PLAY_ON_DESKTOP / PLAY_ACTIVITY.
    5. Auto claim any ready rewards.
    6. Return full diagnostic summary.
    """
    summary = {
        "total_quests": 0,
        "active_quests": 0,
        "expired_quests": 0,
        "enrolled_new": 0,
        "video_completed": 0,
        "desktop_simulated": 0,
        "claimed": 0,
        "logs": [],
        "quests": [],
    }

    if on_progress:
        try:
            on_progress({
                "quest_name": "Đang khởi tạo hệ thống...",
                "status": "RUNNING",
                "percent": 5,
                "current": 1,
                "target": 100,
                "bar": make_progress_bar(5, 100),
                "log": "🔍 [Bước 1/5] Đang quét danh sách Quest từ Discord & phân loại...",
            })
        except Exception:
            pass

    ok, all_quests, msg = fetch_all_quests(token, super_props)
    if not ok:
        summary["logs"].append(f"❌ {msg}")
        return summary

    summary["total_quests"] = len(all_quests)
    summary["quests"] = all_quests

    active_quests = [q for q in all_quests if not q.is_expired]
    expired_quests = [q for q in all_quests if q.is_expired]
    summary["active_quests"] = len(active_quests)
    summary["expired_quests"] = len(expired_quests)

    if expired_quests:
        summary["logs"].append(f"ℹ️ Đã tự động bỏ qua **{len(expired_quests)}** nhiệm vụ cũ đã hết hạn để tránh lỗi 404.")

    if not active_quests:
        msg_no_act = f"ℹ️ Tất cả {len(all_quests)} nhiệm vụ đều đã hết hạn hoặc không có nhiệm vụ mới."
        summary["logs"].append(msg_no_act)
        if on_progress:
            try:
                on_progress({
                    "quest_name": "Không có quest khả dụng",
                    "status": "COMPLETED",
                    "percent": 100,
                    "current": 100,
                    "target": 100,
                    "bar": make_progress_bar(100, 100),
                    "log": f"✨ Hoàn tất: Đã bỏ qua {len(expired_quests)} quest hết hạn. Không có quest nào cần chạy.",
                })
            except Exception:
                pass
        return summary

    # 1. Auto Enroll
    unaccepted = [q for q in active_quests if not q.is_enrolled]
    if unaccepted:
        for idx, q in enumerate(unaccepted, 1):
            if on_progress:
                try:
                    on_progress({
                        "quest_name": q.name,
                        "status": "RUNNING",
                        "percent": int((idx / max(1, len(unaccepted))) * 20),
                        "current": idx,
                        "target": len(unaccepted),
                        "bar": make_progress_bar(idx, len(unaccepted)),
                        "log": f"⚡ [Bước 2/5] Đang nhận quest: `{q.name}` ({idx}/{len(unaccepted)})",
                    })
                except Exception:
                    pass

            enrolled_ok, emsg = enroll_quest(token, q.id, super_props)
            if enrolled_ok:
                summary["enrolled_new"] += 1
                summary["logs"].append(f"✅ Đã nhận: **{q.name}**")
                q.enrolled_at = datetime.utcnow().isoformat()
            else:
                summary["logs"].append(f"⚠️ Không thể nhận {q.name}: {emsg}")
            time.sleep(random.uniform(0.6, 1.2))
    else:
        summary["logs"].append("✨ Tất cả nhiệm vụ còn hạn đều đã được nhận trước đó.")

    # 2. Auto Complete Video Tasks
    video_list = [
        q for q in active_quests
        if any(t.task_name in ("WATCH_VIDEO", "WATCH_VIDEO_ON_MOBILE") for t in q.tasks) and not q.is_completed
    ]
    if video_list:
        if on_progress:
            try:
                on_progress({
                    "quest_name": f"{len(video_list)} Video Quests",
                    "status": "RUNNING",
                    "percent": 30,
                    "current": 30,
                    "target": 100,
                    "bar": make_progress_bar(30, 100),
                    "log": f"🎬 [Bước 3/5] Bắt đầu xử lý {len(video_list)} Video Quests...",
                })
            except Exception:
                pass

        for q in video_list:
            v_ok, v_msg = complete_video_quest(token, q, super_props, on_progress=on_progress)
            if v_ok:
                summary["video_completed"] += 1
                summary["logs"].append(f"🎬 {v_msg}")
                q.completed_at = datetime.utcnow().isoformat()
            else:
                summary["logs"].append(f"⚠️ Video {q.name}: {v_msg}")
            time.sleep(1.2)
    else:
        summary["logs"].append("ℹ️ Không có Video Quest mới nào cần xem.")

    # 3. Auto Simulate Desktop Game Process for PLAY_ON_DESKTOP / PLAY_ACTIVITY
    desktop_list = [
        q for q in active_quests
        if any(t.task_name in ("PLAY_ON_DESKTOP", "PLAY_ACTIVITY") for t in q.tasks) and not q.is_completed
    ]
    if desktop_list:
        if on_progress:
            try:
                on_progress({
                    "quest_name": f"{len(desktop_list)} Desktop Quests",
                    "status": "RUNNING",
                    "percent": 60,
                    "current": 60,
                    "target": 100,
                    "bar": make_progress_bar(60, 100),
                    "log": f"🎮 [Bước 4/5] Khởi động giả lập game PC cho {len(desktop_list)} quests...",
                })
            except Exception:
                pass

        for q in desktop_list:
            d_ok, d_msg = simulate_desktop_game_quest(
                token=token,
                quest=q,
                super_props=super_props,
                channel_id=channel_id,
                on_progress=on_progress,
                step_seconds=30,
            )
            if d_ok:
                summary["desktop_simulated"] += 1
                summary["logs"].append(f"🎮 {d_msg}")
                q.completed_at = datetime.utcnow().isoformat()
            else:
                summary["logs"].append(f"⚠️ Desktop {q.name}: {d_msg}")
            time.sleep(1.2)
    else:
        summary["logs"].append("ℹ️ Không có Game PC Quest nào cần chạy.")

    # 4. Auto Claim Ready Quests
    ready_to_claim = [q for q in active_quests if q.is_completed and not q.is_claimed]
    if ready_to_claim:
        for q in ready_to_claim:
            if on_progress:
                try:
                    on_progress({
                        "quest_name": q.name,
                        "status": "RUNNING",
                        "percent": 85,
                        "current": 85,
                        "target": 100,
                        "bar": make_progress_bar(85, 100),
                        "log": f"🎁 [Bước 5/5] Đang nhận thưởng: `{q.name}`",
                    })
                except Exception:
                    pass

            c_ok, c_msg = claim_quest_reward(token, q.id, super_props=super_props)
            if c_ok:
                summary["claimed"] += 1
                summary["logs"].append(f"🎁 {q.name}: {c_msg}")
                q.claimed_at = datetime.utcnow().isoformat()
            else:
                summary["logs"].append(f"⚠️ Claim {q.name}: {c_msg}")
            time.sleep(random.uniform(0.6, 1.2))
    else:
        summary["logs"].append("ℹ️ Không có phần thưởng mới đang chờ claim.")

    if on_progress:
        try:
            on_progress({
                "quest_name": "Tất cả nhiệm vụ",
                "status": "COMPLETED",
                "percent": 100,
                "current": 100,
                "target": 100,
                "bar": make_progress_bar(100, 100),
                "log": "🎉 Hoàn tất toàn bộ quy trình Auto Quests!",
            })
        except Exception:
            pass

    return summary
