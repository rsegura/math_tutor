"""One-call OpenRouter Responses API smoke; deliberately excluded by default."""

import os
from types import SimpleNamespace

import pytest


@pytest.mark.live_llm
@pytest.mark.asyncio
async def test_openrouter_responses_returns_canonical_tool_call() -> None:
    """Exercise a real user-defined tool and the production canonical parser."""
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    model = os.getenv("OPENROUTER_MODEL", "").strip()
    if not api_key or not model:
        pytest.skip("OPENROUTER_API_KEY and OPENROUTER_MODEL are required")

    # Imports remain local so collection and offline skips never construct a client.
    from openai import AsyncOpenAI

    from math_tutor.agent.providers.model import OpenResponsesAdapter
    from math_tutor.domain.templates import ExpectedAnswerKind

    context = SimpleNamespace(
        current_turn=SimpleNamespace(
            turn_id="openrouter-smoke-turn", transcript="necesito una pista", stt_confidence=1.0
        ),
        activity=SimpleNamespace(
            activity_id="openrouter-smoke-activity",
            template_id="place-value-1",
            objective_id="units-tens",
            difficulty=1,
            prompt_es="¿Cuántas decenas y unidades hay en 24?",
            expected_answer_kind=ExpectedAnswerKind.INTEGER_PAIR,
            expected_answer_fields=("tens", "units"),
            hints_used=0,
            attempts_used=0,
        ),
        active_objective_ids=("units-tens",),
        authorised_objective_ids=("units-tens",),
        adaptations=("short-instructions",),
        duration_minutes=10,
        max_activities=4,
        activities_used=1,
    )

    client = AsyncOpenAI(
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
        max_retries=0,
        timeout=20.0,
    )
    adapter = OpenResponsesAdapter(client=client, model=model, first_response_seconds=15)
    try:
        result = await adapter.complete(
            prompt=(
                "Return exactly one function call to give_hint with empty arguments. "
                "Do not return a message."
            ),
            context=context,
            repair=False,
        )
    finally:
        await adapter.aclose()

    assert result == {"type": "tool", "name": "give_hint", "arguments": {}}
