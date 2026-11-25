#!/usr/bin/env python3
"""
User Manager Module - Discord Badge Maximizer
Handles isolated, encrypted storage for Discord user configurations and credentials.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

import crypto_vault

ROOT_DIR = Path(__file__).resolve().parent
USER_DATA_DIR = crypto_vault.get_user_data_dir()
STATE_FILE = USER_DATA_DIR / "default.json"


@dataclass
class State:
    token: str = ""
    cookie: str = ""
    fingerprint: str = ""
    analytics_token: str = ""
    fetched_at: int = 0
    used_games: list[str] = field(default_factory=list)
    total_games_claimed: int = 0
    total_hours_claimed: float = 0.0
    _filepath: Optional[Path] = field(default=None, repr=False)

    @property
    def has_token(self) -> bool:
        return bool(self.token and self.token.strip())

    @property
    def has_cookie(self) -> bool:
        return bool(self.cookie and self.cookie.strip())

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "State":
        target = path or STATE_FILE
        inst = cls()
        inst._filepath = target
        if target.exists():
            try:
                data = json.loads(target.read_text(encoding="utf-8"))
                known_keys = set(cls().__dict__.keys())
                filtered = {
                    k: v
                    for k, v in data.items()
                    if k in known_keys and k != "used_games" and not k.startswith("_")
                }
                inst = cls(**filtered)
                inst.used_games = data.get("used_games", [])
                inst._filepath = target

                # Transparently decrypt nested DES+AES credentials into memory
                uid_str = target.stem if (target and target.stem.isdigit()) else ""
                inst.token = crypto_vault.decrypt_token(inst.token, uid_str)
                inst.cookie = crypto_vault.decrypt_token(inst.cookie, uid_str)
            except Exception:
                pass

        # Fallback to environment variables only if no explicit path is passed
        if not path:
            if not inst.token and os.environ.get("DISCORD_TOKEN"):
                inst.token = os.environ["DISCORD_TOKEN"].strip()
            if not inst.cookie and os.environ.get("DISCORD_COOKIE"):
                inst.cookie = os.environ["DISCORD_COOKIE"].strip()

        return inst

    def save(self, path: Optional[Path] = None) -> None:
        target = path or getattr(self, "_filepath", None) or STATE_FILE
        target.parent.mkdir(parents=True, exist_ok=True)
        existing: dict[str, Any] = {}
        if target.exists():
            try:
                existing = json.loads(target.read_text(encoding="utf-8"))
            except Exception:
                pass

        save_data = {k: v for k, v in asdict(self).items() if not k.startswith("_")}

        # Always encrypt sensitive tokens with nested DES+AES before saving to disk
        uid_str = target.stem if (target and target.stem.isdigit()) else ""
        if save_data.get("token"):
            save_data["token"] = crypto_vault.encrypt_token(save_data["token"], uid_str)
        if save_data.get("cookie"):
            save_data["cookie"] = crypto_vault.encrypt_token(save_data["cookie"], uid_str)

        existing.update(save_data)
        target.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")


def get_user_state_file(user_id: int) -> Path:
    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    return USER_DATA_DIR / f"{user_id}.json"


def get_user_state(user_id: int) -> State:
    p = get_user_state_file(user_id)
    if not p.exists():
        state = State()
        state._filepath = p
        return state
    state = State.load(p)
    state._filepath = p
    return state


def save_user_state(user_id: int, state: State) -> None:
    p = get_user_state_file(user_id)
    state._filepath = p
    state.save(p)


def clear_user_state(user_id: int) -> bool:
    p = get_user_state_file(user_id)
    if p.exists():
        p.unlink(missing_ok=True)
        return True
    return False


def list_all_users() -> list[int]:
    """Returns a list of all user IDs with stored state."""
    if not USER_DATA_DIR.exists():
        return []
    uids: list[int] = []
    for f in USER_DATA_DIR.glob("*.json"):
        if f.stem.isdigit():
            try:
                uids.append(int(f.stem))
            except ValueError:
                pass
    return uids


def mask_token(token: str) -> str:
    if not token:
        return "*(Chưa thiết lập)*"
    if len(token) <= 12:
        return "********"
    return f"`{token[:6]}...{token[-4:]}` 🔒 *(Mã hóa DES+AES)*"
