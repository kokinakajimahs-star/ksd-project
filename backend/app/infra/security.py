"""認証の基盤（設計書 8 章 NFR-04・15 章）。

- パスワードのハッシュは標準ライブラリの ``hashlib.scrypt``（n=2^14, r=8, p=1, 塩 16 バイト）
- 認証トークンは Bearer。DB には SHA-256 のハッシュだけを保存する。有効期間は設定の ``token_ttl_hours``

認証の方式（R2 の SSO など）をこのモジュールに閉じ込める。乱数（塩・トークン）はここだけで作る（ドメイン層は乱数に触れない）。
トークンのハッシュはドメインの ``hash_token``（RULE-AUTH-002 と同じ関数）を使う。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from app.domain.authz import hash_token as hash_token

# 設計書 8 章 NFR-04 の係数
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16
KEY_BYTES = 64
_SCHEME = "scrypt"

# トークンの乱数の長さ（256 ビット）
TOKEN_BYTES = 32

# 存在しないログイン名でも照合と同じ時間をかけるためのハッシュ（中身に意味は無い）
_DUMMY_SALT = b"\x00" * SALT_BYTES


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=KEY_BYTES)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def hash_password(password: str) -> str:
    """パスワードのハッシュ。形式は ``scrypt$<n>$<r>$<p>$<塩の base64>$<鍵の base64>``（``users.password_hash``）。"""
    if not password:
        raise ValueError("空のパスワードはハッシュにしない")
    salt = secrets.token_bytes(SALT_BYTES)
    key = _scrypt(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    return f"{_SCHEME}${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(key)}"


def verify_password(password: str, stored: str) -> bool:
    """パスワードが保存したハッシュと合うか。形式の壊れたハッシュは合わないとする（例外にしない）。"""
    try:
        scheme, n_text, r_text, p_text, salt_text, key_text = stored.split("$")
        if scheme != _SCHEME:
            return False
        salt = base64.b64decode(salt_text, validate=True)
        expected = base64.b64decode(key_text, validate=True)
        actual = _scrypt(password, salt, int(n_text), int(r_text), int(p_text))
    except ValueError:
        return False
    return hmac.compare_digest(actual, expected)


def spend_password_check_time(password: str) -> None:
    """照合する相手がいないときに、照合と同じだけの時間をかける（ログイン名の有無を応答時間で漏らさない）。"""
    _scrypt(password, _DUMMY_SALT, SCRYPT_N, SCRYPT_R, SCRYPT_P)


def generate_token() -> str:
    """認証トークン・共有リンクのトークン（URL に載せられる 256 ビットの乱数）。保存するのは ``hash_token`` の値だけ。"""
    return secrets.token_urlsafe(TOKEN_BYTES)
