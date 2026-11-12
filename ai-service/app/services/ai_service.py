import logging
from typing import Any

from app.core.config import Settings
from app.services.providers.base import BaseAIProvider, ProviderConfigurationError, ProviderError
from app.services.providers.ollama_provider import OllamaProvider
from app.services.providers.gemini_provider import GeminiProvider

logger = logging.getLogger(__name__)


class AIService:
    def __init__(
        self,
        provider: BaseAIProvider,
        fallback_provider: BaseAIProvider | None = None,
        *,
        provider_name: str | None = None,
        fallback_provider_name: str | None = None,
    ) -> None:
        self._provider = provider
        self._fallback_provider = fallback_provider
        self._provider_name = provider_name or type(provider).__name__.removesuffix("Provider").lower()
        self._fallback_provider_name = (
            fallback_provider_name
            or (type(fallback_provider).__name__.removesuffix("Provider").lower() if fallback_provider else None)
        )

    @property
    def has_fallback(self) -> bool:
        return self._fallback_provider is not None

    @classmethod
    def from_settings(cls, settings: Settings) -> "AIService":
        provider_name = settings.ai_provider.strip().lower()
        if provider_name == "ollama":
            provider = OllamaProvider(
                base_url=settings.ollama_base_url,
                model=settings.ollama_model,
                timeout=settings.ollama_request_timeout or settings.ai_request_timeout,
            )
            fallback = (
                GeminiProvider(
                    api_key=settings.gemini_api_key,
                    model=settings.gemini_model,
                    timeout=settings.gemini_request_timeout or settings.ai_request_timeout,
                )
                if settings.gemini_api_key.strip() and settings.gemini_model.strip()
                else None
            )
            return cls(
                provider,
                fallback,
                provider_name="ollama",
                fallback_provider_name="gemini" if fallback else None,
            )
        if provider_name == "gemini":
            provider = GeminiProvider(
                api_key=settings.gemini_api_key,
                model=settings.gemini_model,
                timeout=settings.gemini_request_timeout or settings.ai_request_timeout,
            )
            return cls(provider, provider_name="gemini")

        raise ProviderConfigurationError(
            f"AI provider '{settings.ai_provider}' is not supported."
        )

    async def generate(self, prompt: str, json_schema: dict[str, Any] | None = None) -> str:
        try:
            result = await self._provider.generate(prompt, json_schema)
        except ProviderError as error:
            if self._fallback_provider is None:
                raise
            logger.warning(
                "AI text provider failed provider=%s category=%s fallback=%s",
                self._provider_name,
                type(error).__name__,
                self._fallback_provider_name,
            )
            return await self.generate_fallback(prompt, json_schema)
        logger.info(
            "AI text provider used provider=%s fallback=false structured=%s",
            self._provider_name,
            json_schema is not None,
        )
        return result

    async def generate_fallback(
        self,
        prompt: str,
        json_schema: dict[str, Any] | None = None,
    ) -> str:
        """Generate directly with the secondary provider after validated output rejection."""
        if self._fallback_provider is None:
            raise ProviderConfigurationError("No secondary AI provider is configured.")
        try:
            result = await self._fallback_provider.generate(prompt, json_schema)
        except ProviderError as error:
            logger.warning(
                "AI text provider failed provider=%s category=%s fallback_exhausted=true",
                self._fallback_provider_name,
                type(error).__name__,
            )
            raise
        logger.info(
            "AI text provider used provider=%s fallback=true structured=%s",
            self._fallback_provider_name,
            json_schema is not None,
        )
        return result

    async def provider_available(self) -> bool:
        return await self._provider.is_available()
