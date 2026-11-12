import asyncio
import logging
from collections import deque
from collections.abc import Awaitable, Callable
from functools import lru_cache
from time import perf_counter
from typing import TypeVar

from app.core.config import get_settings

logger = logging.getLogger(__name__)

T = TypeVar("T")


class TextAIQueueError(Exception):
    """Base error for controlled queue admission and execution limits."""


class TextAIQueueFullError(TextAIQueueError):
    pass


class TextAIQueueWaitTimeoutError(TextAIQueueError):
    pass


class TextAIProcessingTimeoutError(TextAIQueueError):
    pass


class TextAIQueue:
    """Process-local bounded FIFO that serializes complete text AI workflows."""

    def __init__(self, max_pending: int, wait_timeout: float, total_timeout: float) -> None:
        if max_pending < 0 or wait_timeout <= 0 or total_timeout <= 0:
            raise ValueError("Invalid text AI queue limits.")
        self._max_pending = max_pending
        self._wait_timeout = wait_timeout
        self._total_timeout = total_timeout
        self._condition = asyncio.Condition()
        self._active = False
        self._waiters: deque[int] = deque()
        self._sequence = 0

    @property
    def active(self) -> bool:
        return self._active

    @property
    def pending_count(self) -> int:
        return len(self._waiters)

    async def execute(self, operation: Callable[[], Awaitable[T]], request_kind: str) -> T:
        queued_at = perf_counter()
        ticket = await self._admit(request_kind, queued_at)
        wait_ms = int((perf_counter() - queued_at) * 1000)
        logger.info(
            "Text AI queue start ticket=%d kind=%s waitMs=%d pending=%d",
            ticket,
            request_kind,
            wait_ms,
            self.pending_count,
        )

        try:
            remaining = self._total_timeout - (perf_counter() - queued_at)
            if remaining <= 0:
                raise TextAIProcessingTimeoutError(
                    "The text AI request exhausted its total time budget."
                )
            try:
                result = await asyncio.wait_for(operation(), timeout=remaining)
            except TimeoutError:
                raise TextAIProcessingTimeoutError(
                    "The text AI request exhausted its total time budget."
                ) from None
        except BaseException as error:
            logger.warning(
                "Text AI queue error ticket=%d kind=%s category=%s elapsedMs=%d",
                ticket,
                request_kind,
                type(error).__name__,
                int((perf_counter() - queued_at) * 1000),
            )
            raise
        else:
            logger.info(
                "Text AI queue finish ticket=%d kind=%s elapsedMs=%d",
                ticket,
                request_kind,
                int((perf_counter() - queued_at) * 1000),
            )
            return result
        finally:
            async with self._condition:
                self._active = False
                self._condition.notify_all()

    async def _admit(self, request_kind: str, queued_at: float) -> int:
        async with self._condition:
            self._sequence += 1
            ticket = self._sequence
            if not self._active and not self._waiters:
                self._active = True
                return ticket

            if len(self._waiters) >= self._max_pending:
                logger.warning(
                    "Text AI queue full ticket=%d kind=%s pending=%d",
                    ticket,
                    request_kind,
                    len(self._waiters),
                )
                raise TextAIQueueFullError("The text AI queue is full.")

            self._waiters.append(ticket)
            position = len(self._waiters)
            logger.info(
                "Text AI queue wait ticket=%d kind=%s position=%d pending=%d",
                ticket,
                request_kind,
                position,
                len(self._waiters),
            )
            wait_deadline = queued_at + min(self._wait_timeout, self._total_timeout)

            try:
                while self._active or not self._waiters or self._waiters[0] != ticket:
                    remaining = wait_deadline - perf_counter()
                    if remaining <= 0:
                        raise TimeoutError
                    await asyncio.wait_for(self._condition.wait(), timeout=remaining)
            except TimeoutError:
                self._remove_waiter(ticket)
                self._condition.notify_all()
                logger.warning(
                    "Text AI queue wait timeout ticket=%d kind=%s waitedMs=%d",
                    ticket,
                    request_kind,
                    int((perf_counter() - queued_at) * 1000),
                )
                raise TextAIQueueWaitTimeoutError(
                    "The text AI queue wait time expired."
                ) from None
            except asyncio.CancelledError:
                self._remove_waiter(ticket)
                self._condition.notify_all()
                logger.warning(
                    "Text AI queue wait cancelled ticket=%d kind=%s waitedMs=%d",
                    ticket,
                    request_kind,
                    int((perf_counter() - queued_at) * 1000),
                )
                raise

            self._waiters.popleft()
            self._active = True
            return ticket

    def _remove_waiter(self, ticket: int) -> None:
        try:
            self._waiters.remove(ticket)
        except ValueError:
            pass


@lru_cache
def get_text_ai_queue() -> TextAIQueue:
    settings = get_settings()
    return TextAIQueue(
        max_pending=settings.text_ai_queue_max_pending,
        wait_timeout=settings.text_ai_queue_wait_timeout,
        total_timeout=settings.text_ai_total_timeout,
    )
