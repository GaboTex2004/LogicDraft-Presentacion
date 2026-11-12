from fastapi import APIRouter, HTTPException
from app.core.config import get_settings
from app.schemas.agent import AgentAskRequest, AgentAskResponse
from app.services.agent_service import AgentService
from app.skills.diagram.semantic_safety import SemanticSafetyError
from app.services.ai_service import AIService
from app.services.text_ai_queue import get_text_ai_queue
from app.services.providers.base import (
    ProviderConfigurationError, ProviderConnectionError, ProviderModelNotFoundError,
    ProviderResponseError, ProviderTimeoutError,
)

router = APIRouter(prefix="/agent", tags=["agent"])


@router.post("/ask", response_model=AgentAskResponse)
async def ask(request: AgentAskRequest) -> AgentAskResponse:
    try:
        service = AgentService(AIService.from_settings(get_settings()))
        if not service.requires_text_ai(request):
            return await service.ask(request)
        return await get_text_ai_queue().execute(
            lambda: service.ask(request),
            "agent",
        )
    except (ProviderConfigurationError, ProviderConnectionError, ProviderModelNotFoundError):
        raise HTTPException(503, "El proveedor de IA no esta disponible.") from None
    except ProviderTimeoutError:
        raise HTTPException(504, "El proveedor de IA excedio el tiempo de espera.") from None
    except SemanticSafetyError as error:
        raise HTTPException(422, {"code": "AGENT_SEMANTIC_REJECTION", "message": str(error)}) from None
    except ProviderResponseError:
        raise HTTPException(502, "El proveedor no devolvio una respuesta valida.") from None
