"""Provider-neutral model boundary."""
from typing import Awaitable, Protocol
from math_tutor.harness.context import HarnessContext
class ModelAdapter(Protocol):
    def complete(self, *, prompt: str, context: HarnessContext, repair: bool, validation_error: str | None = None) -> object | Awaitable[object]: ...


class ProviderFailure(RuntimeError):
    """Closed provider failure contract; implementations must not retain causes."""
    code = "provider-failure"
    def __init__(self) -> None: super().__init__(self.code)

class ProviderTimeout(ProviderFailure): code = "provider-timeout"
class ProviderRateLimited(ProviderFailure): code = "provider-rate-limited"
class ProviderUpstreamUnavailable(ProviderFailure): code = "provider-upstream-unavailable"
class ProviderInvalidResponse(ProviderFailure): code = "provider-invalid-response"
