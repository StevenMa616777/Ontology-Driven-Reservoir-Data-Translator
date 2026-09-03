"""Model-provider abstraction for structured semantic generation."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, TypeVar

from pydantic import BaseModel


ResponseModel = TypeVar("ResponseModel", bound=BaseModel)


class SemanticProviderError(RuntimeError):
    """Safe, provider-neutral failure surfaced by hosted model adapters."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class SemanticModelProvider(ABC):
    """Provider-neutral async structured-output interface.

    Concrete OpenAI, DeepSeek, or local implementations belong outside the
    semantic agent.  They must honor ``response_model`` rather than returning
    prose intended for downstream parsing.
    """

    @property
    def provider_name(self) -> str:
        return type(self).__name__

    @abstractmethod
    async def structured_generate(
        self,
        prompt: str,
        response_model: type[ResponseModel],
    ) -> ResponseModel | dict[str, Any]:
        """Generate data conforming to the supplied Pydantic model."""

    def record_contract_failure(self, code: str, message: str) -> None:
        """Optionally annotate the latest provider call rejected by the agent."""
