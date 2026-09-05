from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from math_tutor.agent import worker
from math_tutor.infrastructure.dispatch import DispatchMetadataError


@pytest.mark.asyncio
async def test_bad_dispatch_fails_before_connect_and_provider_creation(monkeypatch):
    ctx=SimpleNamespace(job=SimpleNamespace(metadata="{}",room=SimpleNamespace(name="bad")),connect=AsyncMock())
    monkeypatch.setattr(worker,"create_voice_providers",lambda value: pytest.fail("providers started"))
    with pytest.raises(DispatchMetadataError):
        await worker.entrypoint(ctx)
    ctx.connect.assert_not_awaited()

