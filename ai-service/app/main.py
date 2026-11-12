from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.routes import agent, ai, health, diagram, runtime, audio, image
from app.core.config import get_settings
from app.services.text_ai_queue import (
    TextAIProcessingTimeoutError,
    TextAIQueueFullError,
    TextAIQueueWaitTimeoutError,
)

app = FastAPI(title="Diagram AI Service", version="0.1.0")


@app.exception_handler(TextAIQueueFullError)
async def text_ai_queue_full(_request: Request, _error: TextAIQueueFullError) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"detail": "La cola de IA esta llena. Intenta nuevamente."},
        headers={"Retry-After": "5"},
    )


@app.exception_handler(TextAIQueueWaitTimeoutError)
async def text_ai_queue_wait_timeout(
    _request: Request,
    _error: TextAIQueueWaitTimeoutError,
) -> JSONResponse:
    return JSONResponse(
        status_code=504,
        content={"detail": "La espera en la cola de IA excedio el tiempo permitido."},
    )


@app.exception_handler(TextAIProcessingTimeoutError)
async def text_ai_processing_timeout(
    _request: Request,
    _error: TextAIProcessingTimeoutError,
) -> JSONResponse:
    return JSONResponse(
        status_code=504,
        content={"detail": "La solicitud de IA excedio el tiempo total permitido."},
    )

app.include_router(health.router, prefix="/api")
app.include_router(ai.router, prefix="/api")
app.include_router(diagram.router, prefix="/api")
app.include_router(agent.router, prefix="/api")
app.include_router(runtime.router, prefix="/api")
app.include_router(audio.router, prefix="/api")
app.include_router(image.router, prefix="/api")

if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.ai_service_host,
        port=settings.ai_service_port,
        reload=True,
    )
