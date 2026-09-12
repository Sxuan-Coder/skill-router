"""usage 数据的本地标识保护、原子写入与跨进程锁。"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


def token(key: bytes, kind: str, value: str) -> str:
    payload = f"{kind}:{value}".encode("utf-8")
    return hmac.new(key, payload, hashlib.sha256).hexdigest()[:32]


def normalized_project(project: Path) -> str:
    return os.path.normcase(str(project.expanduser().resolve(strict=False)))


def ensure_key(usage_dir: Path) -> bytes:
    path = usage_dir / "local.key"
    if not path.exists():
        try:
            with path.open("x", encoding="ascii") as handle:
                handle.write(secrets.token_hex(32))
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError:
            pass
    return load_key(usage_dir)


def load_key(usage_dir: Path) -> bytes:
    try:
        raw = (usage_dir / "local.key").read_text(encoding="ascii").strip()
        key = bytes.fromhex(raw)
    except (OSError, ValueError) as exc:
        raise ValueError("本地 usage 密钥缺失或损坏") from exc
    if len(key) != 32:
        raise ValueError("本地 usage 密钥长度无效")
    return key


def optional_key(usage_dir: Path) -> bytes | None:
    path = usage_dir / "local.key"
    return load_key(usage_dir) if path.exists() else None


def atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False)
    try:
        with handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, path)
    finally:
        if os.path.exists(handle.name):
            os.unlink(handle.name)


@contextmanager
def usage_lock(usage_dir: Path, timeout_seconds: float = 10.0) -> Iterator[None]:
    usage_dir.mkdir(parents=True, exist_ok=True)
    with (usage_dir / ".write.lock").open("a+b") as handle:
        _ensure_lock_byte(handle)
        _acquire_lock(handle, timeout_seconds)
        try:
            yield
        finally:
            _release_lock(handle)


def _ensure_lock_byte(handle) -> None:
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)


def _acquire_lock(handle, timeout_seconds: float) -> None:
    if os.name != "nt":
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        return
    import msvcrt

    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return
        except OSError as exc:
            if time.monotonic() >= deadline:
                raise TimeoutError("等待本地 usage 写锁超时") from exc
            time.sleep(0.02)


def _release_lock(handle) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
