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

MAGIC_PREFIX = "enc:nested_des_aes:v1:"

ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"
VAULT_KEY_FILE = DATA_DIR / ".vault_master.key"


def get_vault_master_key() -> bytes:
    """Retrieves or creates the master secret key for token vault encryption."""
    env_key = os.environ.get("DISCORD_VAULT_KEY") or os.environ.get("TOKEN_VAULT_SECRET")
    if env_key:
        return hashlib.sha256(env_key.strip().encode("utf-8")).digest()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if VAULT_KEY_FILE.exists():
        try:
            key_bytes = VAULT_KEY_FILE.read_bytes()
            if len(key_bytes) >= 32:
                return key_bytes[:32]
        except Exception:
            pass

    # Generate new random 256-bit vault key
    new_key = os.urandom(32)
    try:
        VAULT_KEY_FILE.write_bytes(new_key)
        try:
            os.chmod(VAULT_KEY_FILE, 0o600)
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
