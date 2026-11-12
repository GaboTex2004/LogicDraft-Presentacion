import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.schemas.agent import AgentAskRequest
from app.schemas.diagram import DiagramContext
from app.core.config import Settings
from app.services.agent_service import AgentService
from app.services.ai_service import AIService
from app.services.diagram_ai_service import DiagramAIService, DiagramIncompleteResponseError
from app.services.providers.base import (
    ProviderConnectionError,
    ProviderModelNotFoundError,
    ProviderTimeoutError,
)
from app.skills.diagram.semantic_safety import SemanticSafetyError


@pytest.fixture
def anyio_backend():
    return "asyncio"


class FakeProvider:
    def __init__(self, *responses):
        self.generate = AsyncMock(side_effect=responses)
        self.is_available = AsyncMock(return_value=True)


def test_settings_build_independent_timeouts_and_require_gemini_credentials_for_fallback():
    configured = AIService.from_settings(Settings(
        ai_provider="ollama",
        gemini_api_key="x",
        ollama_request_timeout=17,
        gemini_request_timeout=9,
    ))
    assert configured.has_fallback is True
    assert configured._provider._timeout == 17
    assert configured._fallback_provider._timeout == 9

    without_secret = AIService.from_settings(Settings(
        ai_provider="ollama",
        gemini_api_key="",
    ))
    assert without_secret.has_fallback is False


@pytest.mark.anyio
@pytest.mark.parametrize(
    "failure",
    [
        ProviderConnectionError("private endpoint"),
        ProviderTimeoutError("private timeout"),
        ProviderModelNotFoundError("private model"),
    ],
)
async def test_technical_primary_failures_use_gemini_without_leaking_details(failure, caplog):
    primary = FakeProvider(failure)
    secondary = FakeProvider('{"operations":[]}')
    service = AIService(
        primary,
        secondary,
        provider_name="ollama",
        fallback_provider_name="gemini",
    )

    assert await service.generate("bounded prompt", {"type": "object"}) == '{"operations":[]}'
    primary.generate.assert_awaited_once()
    secondary.generate.assert_awaited_once()
    assert "provider=ollama" in caplog.text
    assert "fallback=gemini" in caplog.text
    assert "private" not in caplog.text


def compound_request(message: str) -> AgentAskRequest:
    return AgentAskRequest.model_validate({
        "message": message,
        "context": {
            "projectId": 14,
            "projectName": "Fallback fixture",
            "entities": [],
            "relationships": [],
            "recentEvents": [],
            "uml": {"version": 2, "nodes": [], "edges": []},
        },
    })


@pytest.mark.anyio
async def test_agent_falls_back_after_invalid_and_incomplete_json_for_full_compound_batch():
    fixture = json.loads(
        (Path(__file__).resolve().parents[2] / "backend/src/test/resources/ai/compound-uml.json")
        .read_text(encoding="utf-8")
    )
    message = fixture["message"]
    complete = fixture["structure"]
    primary = FakeProvider("{invalid", json.dumps({"classes": complete["classes"][:3], "relationships": []}))
    secondary = FakeProvider(json.dumps(complete))
    service = AIService(primary, secondary, provider_name="ollama", fallback_provider_name="gemini")

    result = await AgentService(service).ask(compound_request(message))

    assert result.outcome == "PROPOSAL", result.answer
    assert [operation.type for operation in result.operations] == ["ADD_ENTITY"] * 4 + ["ADD_RELATIONSHIP"] * 3
    assert sum(len(operation.entity.attributes) for operation in result.operations[:4]) == 12
    assert sum(attribute.primaryKey for operation in result.operations[:4] for attribute in operation.entity.attributes) == 1
    primary.generate.assert_awaited()
    assert primary.generate.await_count == 2
    secondary.generate.assert_awaited_once()


@pytest.mark.anyio
async def test_semantic_safety_rejection_never_calls_fallback():
    unsafe = json.dumps({"operations": [{"type": "DELETE_ENTITY", "entityName": "Cliente"}]})
    primary = FakeProvider(unsafe)
    secondary = FakeProvider('{"operations":[]}')
    service = AIService(primary, secondary, provider_name="ollama", fallback_provider_name="gemini")
    diagram = DiagramContext.model_validate({
        "entities": [{"name": "Cliente", "attributes": []}],
        "relationships": [],
    })

    with pytest.raises(SemanticSafetyError):
        await DiagramAIService(service).interpret("No elimines Cliente", diagram)

    secondary.generate.assert_not_awaited()


@pytest.mark.anyio
async def test_ambiguous_conversion_is_rejected_before_either_provider():
    primary = FakeProvider('{"operations":[]}')
    secondary = FakeProvider('{"operations":[]}')
    service = AIService(primary, secondary, provider_name="ollama", fallback_provider_name="gemini")
    diagram = DiagramContext.model_validate({
        "entities": [
            {"name": "Alumno", "attributes": []},
            {"name": "Materia", "attributes": []},
        ],
        "relationships": [
            {
                "id": "first",
                "sourceEntity": "Alumno",
                "targetEntity": "Materia",
                "sourceCardinality": "ZERO_MANY",
                "targetCardinality": "ZERO_MANY",
            },
            {
                "id": "second",
                "sourceEntity": "Alumno",
                "targetEntity": "Materia",
                "sourceCardinality": "ZERO_MANY",
                "targetCardinality": "ZERO_MANY",
            },
        ],
    })

    with pytest.raises(DiagramIncompleteResponseError, match="unica relacion N:M"):
        await DiagramAIService(service).interpret(
            "Convierte la relacion N:M entre Alumno y Materia en una entidad asociativa Inscripcion",
            diagram,
        )

    primary.generate.assert_not_awaited()
    secondary.generate.assert_not_awaited()
