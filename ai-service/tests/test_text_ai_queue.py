import asyncio
from unittest.mock import patch

import httpx
import pytest

from app.main import app
from app.schemas.agent import AgentAskRequest
from app.services.agent_service import AgentService
from app.services.text_ai_queue import (
    TextAIProcessingTimeoutError,
    TextAIQueue,
    TextAIQueueFullError,
    TextAIQueueWaitTimeoutError,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def wait_for_pending(queue: TextAIQueue, expected: int) -> None:
    for _ in range(100):
        if queue.pending_count == expected:
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"Expected {expected} pending requests, got {queue.pending_count}")


@pytest.mark.anyio
async def test_five_concurrent_requests_run_fifo_with_single_active_operation():
    queue = TextAIQueue(max_pending=4, wait_timeout=1, total_timeout=2)
    release_first = asyncio.Event()
    first_started = asyncio.Event()
    starts: list[int] = []
    finishes: list[int] = []
    active = 0
    max_active = 0

    async def operation(index: int) -> str:
        nonlocal active, max_active
        starts.append(index)
        active += 1
        max_active = max(max_active, active)
        if index == 0:
            first_started.set()
            await release_first.wait()
        else:
            await asyncio.sleep(0.01)
        active -= 1
        finishes.append(index)
        return f"response-{index}"

    tasks = [asyncio.create_task(queue.execute(lambda: operation(0), "agent"))]
    await first_started.wait()
    for index in range(1, 5):
        tasks.append(asyncio.create_task(queue.execute(lambda index=index: operation(index), "agent")))
        await wait_for_pending(queue, index)

    release_first.set()
    results = await asyncio.gather(*tasks)

    assert results == [f"response-{index}" for index in range(5)]
    assert starts == [0, 1, 2, 3, 4]
    assert finishes == [0, 1, 2, 3, 4]
    assert max_active == 1
    assert queue.active is False
    assert queue.pending_count == 0


@pytest.mark.anyio
async def test_failure_is_isolated_and_next_requests_recover_in_fifo_order(caplog):
    queue = TextAIQueue(max_pending=2, wait_timeout=1, total_timeout=2)
    release_first = asyncio.Event()
    first_started = asyncio.Event()
    starts: list[int] = []

    async def operation(index: int) -> str:
        starts.append(index)
        if index == 0:
            first_started.set()
            await release_first.wait()
            raise RuntimeError("private request contents")
        return f"isolated-{index}"

    tasks = [asyncio.create_task(queue.execute(lambda: operation(0), "diagram"))]
    await first_started.wait()
    for index in (1, 2):
        tasks.append(asyncio.create_task(queue.execute(lambda index=index: operation(index), "diagram")))
        await wait_for_pending(queue, index)
    release_first.set()

    results = await asyncio.gather(*tasks, return_exceptions=True)
    assert isinstance(results[0], RuntimeError)
    assert results[1:] == ["isolated-1", "isolated-2"]
    assert starts == [0, 1, 2]
    assert "private request contents" not in caplog.text
    assert await queue.execute(lambda: operation(3), "diagram") == "isolated-3"


@pytest.mark.anyio
async def test_repair_and_fallback_phases_keep_the_same_queue_turn():
    queue = TextAIQueue(max_pending=2, wait_timeout=1, total_timeout=2)
    events: list[str] = []
    first_phase = asyncio.Event()
    continue_workflow = asyncio.Event()

    async def complete_workflow() -> str:
        events.append("request-0-primary")
        first_phase.set()
        await continue_workflow.wait()
        events.append("request-0-repair")
        await asyncio.sleep(0)
        events.append("request-0-fallback")
        return "request-0"

    async def single_phase(index: int) -> str:
        events.append(f"request-{index}-primary")
        return f"request-{index}"

    tasks = [asyncio.create_task(queue.execute(complete_workflow, "agent"))]
    await first_phase.wait()
    for index in (1, 2):
        tasks.append(asyncio.create_task(
            queue.execute(lambda index=index: single_phase(index), "agent")
        ))
        await wait_for_pending(queue, index)
    continue_workflow.set()

    assert await asyncio.gather(*tasks) == ["request-0", "request-1", "request-2"]
    assert events == [
        "request-0-primary",
        "request-0-repair",
        "request-0-fallback",
        "request-1-primary",
        "request-2-primary",
    ]


@pytest.mark.anyio
async def test_full_queue_and_wait_timeout_are_controlled_and_do_not_deadlock():
    queue = TextAIQueue(max_pending=1, wait_timeout=0.05, total_timeout=1)
    release_active = asyncio.Event()
    active_started = asyncio.Event()

    async def blocked() -> str:
        active_started.set()
        await release_active.wait()
        return "active"

    active_task = asyncio.create_task(queue.execute(blocked, "runtime"))
    await active_started.wait()
    waiting_task = asyncio.create_task(
        queue.execute(lambda: asyncio.sleep(0, result="waiting"), "runtime")
    )
    await wait_for_pending(queue, 1)

    with pytest.raises(TextAIQueueFullError):
        await queue.execute(lambda: asyncio.sleep(0), "runtime")
    with pytest.raises(TextAIQueueWaitTimeoutError):
        await waiting_task

    release_active.set()
    assert await active_task == "active"
    assert await queue.execute(lambda: asyncio.sleep(0, result="recovered"), "runtime") == "recovered"


@pytest.mark.anyio
async def test_total_timeout_releases_the_queue_for_the_next_request():
    queue = TextAIQueue(max_pending=1, wait_timeout=0.5, total_timeout=0.05)

    with pytest.raises(TextAIProcessingTimeoutError):
        await queue.execute(lambda: asyncio.sleep(1), "generate")

    assert await queue.execute(lambda: asyncio.sleep(0, result="next"), "generate") == "next"


def deterministic_request() -> AgentAskRequest:
    return AgentAskRequest.model_validate({
        "message": "Relaciona Alumno y Materia de muchos a muchos",
        "context": {
            "projectId": 1,
            "projectName": "Universidad",
            "entities": [
                {"id": "alumno", "name": "Alumno", "attributes": []},
                {"id": "materia", "name": "Materia", "attributes": []},
            ],
            "relationships": [],
            "recentEvents": [],
        },
    })


def test_deterministic_planner_is_identified_without_text_ai():
    service = AgentService(object())
    assert service.requires_text_ai(deterministic_request()) is False


@pytest.mark.anyio
async def test_deterministic_agent_route_bypasses_shared_queue():
    with patch("app.api.routes.agent.get_text_ai_queue") as get_queue:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post("/api/agent/ask", json=deterministic_request().model_dump(mode="json"))

    assert response.status_code == 200, response.text
    assert response.json()["outcome"] == "PROPOSAL"
    get_queue.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("error", "status", "detail"),
    [
        (
            TextAIQueueFullError("private"),
            429,
            "La cola de IA esta llena. Intenta nuevamente.",
        ),
        (
            TextAIQueueWaitTimeoutError("private"),
            504,
            "La espera en la cola de IA excedio el tiempo permitido.",
        ),
        (
            TextAIProcessingTimeoutError("private"),
            504,
            "La solicitud de IA excedio el tiempo total permitido.",
        ),
    ],
)
async def test_queue_errors_have_controlled_http_responses(error, status, detail):
    class FailingQueue:
        async def execute(self, _operation, _request_kind):
            raise error

    with patch("app.api.routes.ai.get_text_ai_queue", return_value=FailingQueue()):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post("/api/ai/generate", json={"prompt": "hola"})

    assert response.status_code == status
    assert response.json() == {"detail": detail}
    if isinstance(error, TextAIQueueFullError):
        assert response.headers["Retry-After"] == "5"
