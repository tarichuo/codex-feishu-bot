"""按会话维度串行执行异步任务，并跟踪活动 turn。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

from feishu_codex_bot.logging import ContextLoggerAdapter, get_logger


T = TypeVar("T")


@dataclass(slots=True)
class _LockEntry:
    lock: asyncio.Lock
    ref_count: int = 0


class SessionTurnConflictError(RuntimeError):
    """Raised when a session already has an active turn."""


class SessionExecutor:
    """保证同一 session_scope_key 下的任务串行执行。"""

    def __init__(
        self,
        *,
        logger: ContextLoggerAdapter | None = None,
    ) -> None:
        self._entries: dict[str, _LockEntry] = {}
        self._entries_guard = asyncio.Lock()
        self._active_turns: dict[str, str] = {}
        self._logger = logger or get_logger(__name__)

    async def run(
        self,
        session_scope_key: str,
        operation: Callable[[], Awaitable[T]],
    ) -> T:
        entry = await self._retain_entry(session_scope_key)
        lock = entry.lock
        self._logger.bind(
            event="session.executor.wait",
            session_scope_key=session_scope_key,
        ).info("Waiting for session execution slot")
        await lock.acquire()
        self._logger.bind(
            event="session.executor.enter",
            session_scope_key=session_scope_key,
        ).info("Entered session execution slot")
        try:
            return await operation()
        finally:
            lock.release()
            self._logger.bind(
                event="session.executor.leave",
                session_scope_key=session_scope_key,
            ).info("Released session execution slot")
            await self._release_entry(session_scope_key)

    async def _retain_entry(self, session_scope_key: str) -> _LockEntry:
        async with self._entries_guard:
            entry = self._entries.get(session_scope_key)
            if entry is None:
                entry = _LockEntry(lock=asyncio.Lock())
                self._entries[session_scope_key] = entry
            entry.ref_count += 1
            return entry

    async def _release_entry(self, session_scope_key: str) -> None:
        async with self._entries_guard:
            entry = self._entries.get(session_scope_key)
            if entry is None:
                return
            entry.ref_count -= 1
            if entry.ref_count <= 0 and not entry.lock.locked():
                self._entries.pop(session_scope_key, None)

    async def activate_turn(self, session_scope_key: str, turn_id: str) -> None:
        async with self._entries_guard:
            active_turn_id = self._active_turns.get(session_scope_key)
            if active_turn_id is not None and active_turn_id != turn_id:
                raise SessionTurnConflictError(
                    f"Session {session_scope_key!r} already has active turn {active_turn_id!r}"
                )
            self._active_turns[session_scope_key] = turn_id
        self._logger.bind(
            event="session.executor.turn_activated",
            session_scope_key=session_scope_key,
            turn_id=turn_id,
        ).info("Activated session turn")

    async def complete_turn(self, session_scope_key: str, turn_id: str) -> None:
        async with self._entries_guard:
            active_turn_id = self._active_turns.get(session_scope_key)
            if active_turn_id == turn_id:
                self._active_turns.pop(session_scope_key, None)
        self._logger.bind(
            event="session.executor.turn_completed",
            session_scope_key=session_scope_key,
            turn_id=turn_id,
        ).info("Completed session turn")

    async def get_active_turn(self, session_scope_key: str) -> str | None:
        async with self._entries_guard:
            return self._active_turns.get(session_scope_key)
