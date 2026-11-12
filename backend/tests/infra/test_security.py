"""認証の基盤の試験（設計書 8 章 NFR-04）：scrypt のパスワードのハッシュとトークン。"""

from __future__ import annotations

import base64

from app.infra.security import generate_token, hash_password, hash_token, verify_password


def test_password_hash_uses_scrypt_parameters_of_nfr_04() -> None:
    """NFR-04：scrypt（n=2^14, r=8, p=1, 塩 16 バイト）。同じパスワードでも塩が違えばハッシュは違う。"""
    first = hash_password("pass")
    second = hash_password("pass")
    scheme, n, r, p, salt, key = first.split("$")
    assert (scheme, n, r, p) == ("scrypt", "16384", "8", "1")
    assert len(base64.b64decode(salt)) == 16
    assert len(base64.b64decode(key)) == 64
    assert first != second
    assert len(first) <= 255  # users.password_hash の長さ


def test_verify_password() -> None:
    stored = hash_password("正しいパスワード")
    assert verify_password("正しいパスワード", stored) is True
    assert verify_password("違うパスワード", stored) is False
    assert verify_password("", stored) is False


def test_verify_password_rejects_malformed_hash_without_error() -> None:
    for stored in ("", "scrypt$dummy", "bcrypt$16384$8$1$AAAA$AAAA", "scrypt$x$8$1$AAAA$AAAA", "scrypt$16384$8$1$@@$AAAA"):
        assert verify_password("pass", stored) is False


def test_generated_tokens_are_random_and_stored_as_sha256() -> None:
    """NFR-04：トークンは 256 ビットの乱数。保存するのは SHA-256 の 16 進 64 文字だけ。"""
    tokens = {generate_token() for _ in range(20)}
    assert len(tokens) == 20
    token = tokens.pop()
    assert len(token) >= 43
    digest = hash_token(token)
    assert len(digest) == 64 and digest != token
