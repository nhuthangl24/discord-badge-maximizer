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
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from stream import USER_AGENT, Session, build_super_props

QUESTS_URL = "https://discord.com/api/v9/quests/@me"
DISCORD_API_BASE = "https://discord.com/api/v9"


@dataclass
class QuestTask:
    task_name: str
    target: int  # in seconds
    progress: float  # in seconds
    applications: list[dict[str, Any]] = field(default_factory=list)

    @property
    def is_completed(self) -> bool:
        return self.progress >= self.target if self.target > 0 else False


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
        if self.is_enrolled:
            return "⏳ Đang thực hiện"
        return "✨ Mới (Chưa nhận)"

    @property
    def status_emoji(self) -> str:
        if self.is_claimed:
            return "🎁"
        if self.is_completed:
            return "✅"
        if self.is_enrolled:
            return "⏳"
        return "🆕"


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
) -> tuple[int, Any]:
    """Helper to perform REST calls to Discord Quest API endpoints."""
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
        try:
            err_data = json.loads(exc.read().decode("utf-8"))
        except Exception:
            err_data = {"message": str(exc.reason)}
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

    expires_at = str(cfg.get("expires_at") or cfg.get("expiresAt") or "")

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
    enrolled_at = status_obj.get("enrolled_at") or status_obj.get("enrolledAt")
    completed_at = status_obj.get("completed_at") or status_obj.get("completedAt")
    claimed_at = status_obj.get("claimed_at") or status_obj.get("claimedAt")
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
            target_sec = int(tdata.get("target") or 0)
            p_val = 0.0
            if isinstance(progress_dict, dict) and tname in progress_dict:
                p_item = progress_dict[tname]
                if isinstance(p_item, dict):
                    p_val = float(p_item.get("value") or 0.0)
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

    return QuestItem(
        id=qid,
        name=name,
        game_title=game_title,
        application_id=app_id,
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
    Fetches all available/active quests for the user token from Discord REST API.
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

    return True, items, f"Đã tìm thấy {len(items)} nhiệm vụ."


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
    return False, f"Không thể nhận quest (HTTP {status}: {data})"


def auto_enroll_all_quests(token: str, super_props: str = "") -> tuple[int, int, list[str]]:
    """
    Fetches all quests and automatically enrolls in all quests that are not yet accepted.
    Returns: (enrolled_count, total_available, log_messages)
    """
    ok, quests, msg = fetch_all_quests(token, super_props)
    if not ok:
        return 0, 0, [f"❌ {msg}"]

    logs: list[str] = []
    enrolled_count = 0
    to_enroll = [q for q in quests if not q.is_enrolled]

    if not to_enroll:
        return 0, len(quests), ["✨ Tất cả nhiệm vụ hiện có đều đã được nhận trước đó!"]

    for q in to_enroll:
        success, emsg = enroll_quest(token, q.id, super_props)
        if success:
            enrolled_count += 1
            logs.append(f"✅ Đã nhận: **{q.name}**")
        else:
            logs.append(f"⚠️ {q.name}: {emsg}")
        # Small delay to mimic human behavior and avoid rate limits
        time.sleep(random.uniform(0.6, 1.2))

    return enrolled_count, len(to_enroll), logs


def complete_video_quest(
    token: str,
    quest: QuestItem,
    super_props: str = "",
    speed_step: int = 5,
) -> tuple[bool, str]:
    """
    Simulates video viewing progress for WATCH_VIDEO / WATCH_VIDEO_ON_MOBILE tasks.
    Sends incremental video progress updates to Discord until completion.
    """
    video_task = next(
        (t for t in quest.tasks if t.task_name in ("WATCH_VIDEO", "WATCH_VIDEO_ON_MOBILE")),
        None,
    )
    if not video_task:
        return False, "Nhiệm vụ này không phải dạng xem Video."

    target = video_task.target or 30
    current = int(video_task.progress or 0)
    url = f"{DISCORD_API_BASE}/quests/{quest.id}/video-progress"

    # Enroll first if not enrolled
    if not quest.is_enrolled:
        enroll_quest(token, quest.id, super_props)
        time.sleep(1.0)

    while current < target:
        current = min(target, current + speed_step)
        timestamp_val = current + round(random.uniform(0.1, 0.9), 2)
        payload = {"timestamp": min(float(target), timestamp_val)}
        status, data = _quest_api_call(url, token, method="POST", body=payload, super_props=super_props)
        if status not in (200, 201):
            return False, f"Lỗi gửi tiến độ video (HTTP {status})"

        if isinstance(data, dict) and (data.get("completed_at") or data.get("completedAt")):
            return True, f"✅ Đã hoàn thành xem video: **{quest.name}**"

        time.sleep(random.uniform(1.0, 2.0))

    # Send final completion ping
    payload = {"timestamp": float(target)}
    _quest_api_call(url, token, method="POST", body=payload, super_props=super_props)
    return True, f"✅ Đã hoàn thành xem video: **{quest.name}** ({target}s)"


def complete_activity_quest(
    token: str,
    quest: QuestItem,
    channel_id: str,
    super_props: str = "",
) -> tuple[bool, str]:
    """
    Sends heartbeat pings for PLAY_ACTIVITY quests.
    """
    act_task = next((t for t in quest.tasks if t.task_name == "PLAY_ACTIVITY"), None)
    if not act_task:
        return False, "Nhiệm vụ này không phải dạng Play Activity."

    if not channel_id:
        return False, "Chưa cung cấp Channel ID để chạy Activity."

    stream_key = f"call:{channel_id}:1"
    url = f"{DISCORD_API_BASE}/quests/{quest.id}/heartbeat"

    # Enroll first
    if not quest.is_enrolled:
        enroll_quest(token, quest.id, super_props)
        time.sleep(1.0)

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
    return False, f"Không thể claim quà (HTTP {status})"


def claim_all_completed_quests(token: str, super_props: str = "") -> tuple[int, list[str]]:
    """
    Finds all completed but unclaimed quests and claims their rewards.
    Returns: (claimed_count, log_messages)
    """
    ok, quests, msg = fetch_all_quests(token, super_props)
    if not ok:
        return 0, [f"❌ {msg}"]

    logs: list[str] = []
    claimed_count = 0
    to_claim = [q for q in quests if q.is_completed and not q.is_claimed]

    if not to_claim:
        return 0, ["ℹ️ Không có nhiệm vụ nào đang chờ claim quà."]

    for q in to_claim:
        success, cmsg = claim_quest_reward(token, q.id, super_props=super_props)
        if success:
            claimed_count += 1
            logs.append(f"🎁 **{q.name}**: {cmsg}")
        else:
            logs.append(f"⚠️ **{q.name}**: {cmsg}")
        time.sleep(random.uniform(0.8, 1.5))

    return claimed_count, logs


def auto_run_quest_flow(
    token: str,
    channel_id: str = "",
    super_props: str = "",
) -> dict[str, Any]:
    """
    Full automated Quest suite:
    1. Fetch all quests.
    2. Auto enroll all available quests.
    3. Auto complete video quests.
    4. Auto claim any ready rewards.
    5. Return full diagnostic summary.
    """
    summary = {
        "total_quests": 0,
        "enrolled_new": 0,
        "video_completed": 0,
        "claimed": 0,
        "logs": [],
        "quests": [],
    }

    ok, quests, msg = fetch_all_quests(token, super_props)
    if not ok:
        summary["logs"].append(f"❌ {msg}")
        return summary

    summary["total_quests"] = len(quests)
    summary["quests"] = quests

    # 1. Auto Enroll
    unaccepted = [q for q in quests if not q.is_enrolled]
    for q in unaccepted:
        enrolled_ok, emsg = enroll_quest(token, q.id, super_props)
        if enrolled_ok:
            summary["enrolled_new"] += 1
            summary["logs"].append(f"✅ Đã nhận: **{q.name}**")
            q.enrolled_at = datetime.utcnow().isoformat()
        else:
            summary["logs"].append(f"⚠️ Không thể nhận {q.name}: {emsg}")
        time.sleep(random.uniform(0.5, 1.0))

    # 2. Auto Complete Video Tasks
    for q in quests:
        has_video = any(t.task_name in ("WATCH_VIDEO", "WATCH_VIDEO_ON_MOBILE") for t in q.tasks)
        if has_video and not q.is_completed:
            v_ok, v_msg = complete_video_quest(token, q, super_props)
            if v_ok:
                summary["video_completed"] += 1
                summary["logs"].append(f"🎬 {v_msg}")
                q.completed_at = datetime.utcnow().isoformat()
            else:
                summary["logs"].append(f"⚠️ Video {q.name}: {v_msg}")
            time.sleep(1.0)

    # 3. Auto Claim Ready Quests
    ready_to_claim = [q for q in quests if q.is_completed and not q.is_claimed]
    for q in ready_to_claim:
        c_ok, c_msg = claim_quest_reward(token, q.id, super_props=super_props)
        if c_ok:
            summary["claimed"] += 1
            summary["logs"].append(f"🎁 {q.name}: {c_msg}")
            q.claimed_at = datetime.utcnow().isoformat()
        else:
            summary["logs"].append(f"⚠️ Claim {q.name}: {c_msg}")
        time.sleep(random.uniform(0.5, 1.0))

    return summary
