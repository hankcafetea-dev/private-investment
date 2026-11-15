"""
私人資料加密（持股、成本、提醒價、警示紀錄）
============================================
repo 與 GitHub Pages 都是公開的，所以私人資料一律用 SITE_PASSWORD 加密後才放上去：
  PBKDF2-SHA256（31 萬次）導出金鑰 → AES-256-GCM 加密。
網頁端（site/app.js）用瀏覽器內建 WebCrypto 以同一套參數解密，密碼不會離開瀏覽器。
"""
from __future__ import annotations

import base64
import json
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

ITERATIONS = 310_000


def _key(password: str, salt: bytes, iterations: int) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iterations)
    return kdf.derive(password.encode("utf-8"))


def encrypt_json(obj, password: str) -> dict:
    salt, iv = os.urandom(16), os.urandom(12)
    data = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ct = AESGCM(_key(password, salt, ITERATIONS)).encrypt(iv, data, None)
    b64 = lambda x: base64.b64encode(x).decode("ascii")  # noqa: E731
    return {"v": 1, "alg": "AES-256-GCM", "kdf": "PBKDF2-SHA256", "iter": ITERATIONS,
            "salt": b64(salt), "iv": b64(iv), "ct": b64(ct)}


def decrypt_json(env: dict, password: str):
    d = lambda k: base64.b64decode(env[k])  # noqa: E731
    pt = AESGCM(_key(password, d("salt"), int(env["iter"]))).decrypt(d("iv"), d("ct"), None)
    return json.loads(pt.decode("utf-8"))


def site_password() -> str:
    return os.getenv("SITE_PASSWORD", "").strip()
