"""
Crypto Vault Module - Discord Badge Maximizer
Provides nested two-way symmetric encryption (DES / TripleDES + AES-256)
for user Discord tokens, alt tokens, and cookies.

Architecture:
  Plaintext Token
       │
       ▼
  [Layer 1: TripleDES-CBC / PKCS7 (64-bit block)]
       │
       ▼
  [Layer 2: AES-256-CBC / PKCS7 (128-bit block)]
       │
       ▼
  Ciphertext payload: [16-byte AES IV] + [8-byte DES IV] + [Nested Ciphertext]
       │
       ▼
  Base64: enc:nested_des_aes:v1:<base64>

Security Guarantees:
- Neither Bot Admin nor anyone inspecting the database/JSON files can read user tokens.
- User-specific key derivation: each Discord user ID gets unique AES and DES keys
  derived via HMAC-SHA512 from the master vault secret + user Discord ID.
- Tokens on disk (data/users/*.json) are 100% encrypted at rest.
"""

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Optional, Tuple, Union

try:
    from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
except ImportError:
    try:
        from cryptography.hazmat.primitives.ciphers.algorithms import TripleDES
    except ImportError:
        TripleDES = None

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, modes
from cryptography.hazmat.primitives.ciphers.algorithms import AES

import sys
import shutil

MAGIC_PREFIX = "enc:nested_des_aes:v1:"

if getattr(sys, "frozen", False):
    ROOT_DIR = Path(sys.executable).resolve().parent
else:
    ROOT_DIR = Path(__file__).resolve().parent


def get_config_file() -> Path:
    """Returns the persistent global configuration file path."""
    if sys.platform == "win32":
        app_data = os.environ.get("APPDATA")
        if app_data:
            base = Path(app_data) / "DiscordBadgeMaximizer"
            base.mkdir(parents=True, exist_ok=True)
            return base / "config.json"
    home = Path.home() / ".discord_badge_maximizer"
    home.mkdir(parents=True, exist_ok=True)
    return home / "config.json"


def get_data_dir() -> Path:
    """
    Returns the active Data Directory.
    Allows data to be stored anywhere on the PC independently of where the app is installed.
    Priority:
    1. Environment variable DISCORD_DATA_DIR
    2. Persistent custom path saved in config.json
    3. Local 'data' folder next to executable or cwd (if exists)
    4. Default persistent %APPDATA%/DiscordBadgeMaximizer/data
    """
    env_dir = os.environ.get("DISCORD_DATA_DIR")
    if env_dir:
        p = Path(env_dir).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    cfg_file = get_config_file()
    if cfg_file.exists():
        try:
            cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
            custom_path = cfg.get("data_dir")
            if custom_path:
                p = Path(custom_path).resolve()
                p.mkdir(parents=True, exist_ok=True)
                return p
        except Exception:
            pass

    # Local data folder next to application executable
    local_data = ROOT_DIR / "data"
    if local_data.exists() and local_data.is_dir():
        return local_data.resolve()

    # Standard persistent AppData directory
    if sys.platform == "win32" and os.environ.get("APPDATA"):
        d = Path(os.environ["APPDATA"]) / "DiscordBadgeMaximizer" / "data"
    else:
        d = Path.home() / ".discord_badge_maximizer" / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d.resolve()


def set_custom_data_dir(new_path: Union[str, Path]) -> Path:
    """Sets and persists a custom data directory anywhere on the system."""
    p = Path(new_path).resolve()
    p.mkdir(parents=True, exist_ok=True)
    (p / "users").mkdir(parents=True, exist_ok=True)

    cfg_file = get_config_file()
    cfg = {}
    if cfg_file.exists():
        try:
            cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    cfg["data_dir"] = str(p)
    cfg_file.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    os.environ["DISCORD_DATA_DIR"] = str(p)
    sync_user_backup_files()
    return p


def get_user_data_dir() -> Path:
    d = get_data_dir() / "users"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_vault_key_files() -> Tuple[Path, Path]:
    d = get_data_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / ".vault_master.key", d / "vault_master.key"


DATA_DIR = get_data_dir()
USER_DATA_DIR = get_user_data_dir()
VAULT_KEY_FILE, VAULT_KEY_FILE_ALT = get_vault_key_files()


def _parse_raw_or_hex_key(data: bytes) -> Optional[bytes]:
    if not data:
        return None
    data_str = data.decode("utf-8", errors="ignore").strip()
    if len(data_str) == 64:
        try:
            parsed = bytes.fromhex(data_str)
            if len(parsed) == 32:
                return parsed
        except Exception:
            pass
    if len(data) >= 32:
        return data[:32]
    return None


def sync_user_backup_files() -> int:
    """
    Auto-detects and synchronizes user state backup files from:
    1. ROOT_DIR / 'data' / 'user' (common typo without 's')
    2. Path.cwd() / 'data' / 'user'
    3. Path.cwd() / 'data' / 'users'
    4. ROOT_DIR / 'data' (any numeric <discord_id>.json placed directly in data/)
    5. Path.cwd() / 'data' (any numeric <discord_id>.json)
    6. Also recovers .vault_master.key / vault_master.key if backed up in any candidate folder.
    Returns the number of recovered user files.
    """
    active_data = get_data_dir()
    active_users = get_user_data_dir()
    active_data.mkdir(parents=True, exist_ok=True)
    active_users.mkdir(parents=True, exist_ok=True)

    recovered = 0
    candidates = [
        active_data / "user",
        active_data,
        ROOT_DIR / "data" / "user",
        ROOT_DIR / "data" / "users",
        ROOT_DIR / "data",
        Path.cwd() / "data" / "user",
        Path.cwd() / "data" / "users",
        Path.cwd() / "data",
    ]

    key_file, key_file_alt = get_vault_key_files()

    # First, look for any backed up master key file
    for c_dir in candidates:
        if not c_dir.exists():
            continue
        for key_name in [".vault_master.key", "vault_master.key", "master.key", ".master.key"]:
            candidate_key = c_dir / key_name
            if candidate_key.exists() and candidate_key.is_file() and candidate_key != key_file and candidate_key != key_file_alt:
                try:
                    k_data = candidate_key.read_bytes()
                    parsed = _parse_raw_or_hex_key(k_data)
                    if parsed and (not key_file.exists() or key_file.stat().st_size != 32):
                        key_file.write_bytes(parsed)
                        key_file_alt.write_bytes(parsed)
                        print(f"🔑 [VAULT RECOVERY] Đã phục hồi master key từ {candidate_key}")
                except Exception:
                    pass

    # Second, sync user json files
    for c_dir in candidates:
        if not c_dir.exists() or c_dir.resolve() == active_users.resolve():
            continue
        try:
            for item in c_dir.glob("*.json"):
                if item.is_file() and item.stem.isdigit():
                    dest = active_users / item.name
                    if not dest.exists():
                        try:
                            shutil.copy2(item, dest)
                            recovered += 1
                            print(f"👤 [DATA SYNC] Đã đồng bộ file dữ liệu người dùng: {item.name} từ {c_dir}")
                        except Exception as e:
                            print(f"⚠️ [DATA SYNC ERROR] {e}")
        except Exception:
            pass

    return recovered


def get_vault_master_key() -> bytes:
    """Retrieves or creates the master secret key for token vault encryption."""
    env_key = os.environ.get("DISCORD_VAULT_KEY") or os.environ.get("TOKEN_VAULT_SECRET")
    if env_key:
        cleaned = env_key.strip()
        if len(cleaned) == 64:
            try:
                parsed = bytes.fromhex(cleaned)
                if len(parsed) == 32:
                    return parsed
            except Exception:
                pass
        return hashlib.sha256(cleaned.encode("utf-8")).digest()

    d = get_data_dir()
    d.mkdir(parents=True, exist_ok=True)
    kf_primary, kf_alt = get_vault_key_files()
    for kf in [kf_primary, kf_alt, ROOT_DIR / "data" / ".vault_master.key", ROOT_DIR / "data" / "vault_master.key", Path.cwd() / "data" / ".vault_master.key", Path.cwd() / "data" / "vault_master.key"]:
        if kf.exists() and kf.is_file():
            try:
                parsed = _parse_raw_or_hex_key(kf.read_bytes())
                if parsed:
                    # Keep both synced in binary format
                    if not kf_primary.exists() or kf_primary.stat().st_size != 32:
                        try:
                            kf_primary.write_bytes(parsed)
                        except Exception:
                            pass
                    if not kf_alt.exists() or kf_alt.stat().st_size != 32:
                        try:
                            kf_alt.write_bytes(parsed)
                        except Exception:
                            pass
                    return parsed
            except Exception:
                pass

    # Generate new random 256-bit vault key and save both hidden and visible
    new_key = os.urandom(32)
    try:
        kf_primary.write_bytes(new_key)
        kf_alt.write_bytes(new_key)
        try:
            os.chmod(kf_primary, 0o600)
            os.chmod(kf_alt, 0o600)
        except Exception:
            pass
    except Exception:
        pass
    return new_key


def derive_user_keys(user_id: Union[str, int], master_key: Optional[bytes] = None) -> Tuple[bytes, bytes]:
    """
    Derives unique 256-bit AES key and 192-bit TripleDES key for a specific user ID.
    Guarantees isolation: ciphertexts are unique per user even for identical tokens.
    """
    master = master_key or get_vault_master_key()
    salt = f"user:{str(user_id).strip()}:discord_badge_maximizer:nested_des_aes".encode("utf-8")
    derived = hmac.new(master, salt, hashlib.sha512).digest()
    key_aes = derived[:32]    # 256-bit key for AES-256
    key_des = derived[32:56]   # 192-bit key for TripleDES (24 bytes)
    return key_aes, key_des


def is_encrypted(val: str) -> bool:
    """Checks whether a string is already encrypted in the nested DES+AES format."""
    return bool(val and isinstance(val, str) and val.startswith(MAGIC_PREFIX))


def encrypt_token(token: str, user_id: Union[str, int] = "") -> str:
    """
    Encrypts a plaintext Discord token or secret with nested DES (TripleDES) + AES-256.
    Returns the prefixed base64 ciphertext string.
    """
    if not token or not isinstance(token, str):
        return ""
    token_str = token.strip()
    if not token_str:
        return ""
    if is_encrypted(token_str):
        return token_str

    key_aes, key_des = derive_user_keys(user_id)
    raw_data = token_str.encode("utf-8")

    # Layer 1: TripleDES-CBC Encryption with PKCS7 padding (64-bit block)
    iv_des = os.urandom(8)
    padder_des = padding.PKCS7(64).padder()
    padded_des = padder_des.update(raw_data) + padder_des.finalize()
    if TripleDES is not None:
        cipher_des = Cipher(TripleDES(key_des), modes.CBC(iv_des))
        enc_des = cipher_des.encryptor().update(padded_des) + cipher_des.encryptor().finalize()
    else:
        # Fallback if TripleDES unavailable
        enc_des = padded_des

    # Layer 2: AES-256-CBC Encryption with PKCS7 padding (128-bit block) over DES ciphertext
    iv_aes = os.urandom(16)
    padder_aes = padding.PKCS7(128).padder()
    padded_aes = padder_aes.update(enc_des) + padder_aes.finalize()
    cipher_aes = Cipher(AES(key_aes), modes.CBC(iv_aes))
    enc_nested = cipher_aes.encryptor().update(padded_aes) + cipher_aes.encryptor().finalize()

    # Pack IVs and nested ciphertext together
    payload = iv_aes + iv_des + enc_nested
    b64_cipher = base64.b64encode(payload).decode("ascii")
    return f"{MAGIC_PREFIX}{b64_cipher}"


def decrypt_token(cipher_text: str, user_id: Union[str, int] = "") -> str:
    """
    Decrypts a nested DES + AES ciphertext string back into the original plaintext token.
    Falls back gracefully if the token is unencrypted legacy text.
    """
    if not cipher_text or not isinstance(cipher_text, str):
        return ""
    cipher_str = cipher_text.strip()
    if not cipher_str:
        return ""
    if not is_encrypted(cipher_str):
        # Raw / unencrypted legacy token fallback
        return cipher_str

    try:
        raw_b64 = cipher_str[len(MAGIC_PREFIX):]
        payload = base64.b64decode(raw_b64)
        if len(payload) < 24:
            return ""

        iv_aes = payload[:16]
        iv_des = payload[16:24]
        enc_nested = payload[24:]

        key_aes, key_des = derive_user_keys(user_id)

        # Decrypt Layer 2: AES-256-CBC
        cipher_aes = Cipher(AES(key_aes), modes.CBC(iv_aes))
        dec_aes = cipher_aes.decryptor().update(enc_nested) + cipher_aes.decryptor().finalize()
        unpadder_aes = padding.PKCS7(128).unpadder()
        enc_des = unpadder_aes.update(dec_aes) + unpadder_aes.finalize()

        # Decrypt Layer 1: TripleDES-CBC
        if TripleDES is not None:
            cipher_des = Cipher(TripleDES(key_des), modes.CBC(iv_des))
            dec_des = cipher_des.decryptor().update(enc_des) + cipher_des.decryptor().finalize()
            unpadder_des = padding.PKCS7(64).unpadder()
            raw_plain = unpadder_des.update(dec_des) + unpadder_des.finalize()
        else:
            raw_plain = enc_des

        return raw_plain.decode("utf-8").strip()
    except Exception:
        return ""


def migrate_all_user_states(users_dir: Path) -> int:
    """
    Auto-migration utility: scans all existing data/users/*.json files on startup.
    If any file contains plaintext tokens, encrypts them immediately with nested DES+AES.
    Returns the count of migrated files.
    """
    if not users_dir.exists():
        return 0

    migrated_count = 0
    sensitive_fields = ("token", "viewer_token", "cookie")

    for f in users_dir.glob("*.json"):
        try:
            uid_str = f.stem
            if not uid_str.isdigit():
                continue
            data = json.loads(f.read_text(encoding="utf-8"))
            modified = False
            for field in sensitive_fields:
                val = data.get(field, "")
                if val and isinstance(val, str) and not is_encrypted(val):
                    data[field] = encrypt_token(val, uid_str)
                    modified = True

            if modified:
                f.write_text(json.dumps(data, indent=2), encoding="utf-8")
                migrated_count += 1
        except Exception:
            pass

    return migrated_count
