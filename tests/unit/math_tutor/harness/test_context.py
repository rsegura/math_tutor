import pytest

from math_tutor.harness.context import (
    ActivityContext,
    ContextError,
    LearnerState,
    TurnEvidence,
    build_harness_context,
)


def test_context_contains_only_bounded_child_safe_data():
    context = build_harness_context(
        session_id="session-1",
        learner_id="learner-1",
        expected_session_version=3,
        expected_profile_version=2,
        generation_id="gen-4",
        authorised_objective_ids=("units-tens",),
        active_objective_ids=("units-tens",),
        activity=ActivityContext(
            activity_id="activity-1", template_id="template-1",
            objective_id="units-tens", difficulty=2, prompt_es="¿Cuántas unidades?",
            hint_ids=("hint-1",), hint_texts=("Cuenta las unidades.",), hints_used=0,
        ),
        learner_state=LearnerState(
            competency_states=(("units-tens", "exploring"),),
            presentation=("short-instructions",),
        ),
        recent_history=("uno", "dos", "tres"),
        current_turn=TurnEvidence("turn-9", "Hay cuatro", 0.91),
        max_history_turns=2,
    )

    assert context.recent_history == ("dos", "tres")
    assert "No diagnostiques" in context.child_safe_policy
    assert not hasattr(context, "diagnosis")


def test_context_rejects_diagnostic_or_unrelated_profile_fields():
    with pytest.raises(ContextError, match="learner state field"):
        LearnerState(
            competency_states=(("units-tens", "exploring"),),
            presentation=("diagnosis: lesión",),
        )


def test_context_rejects_active_objective_outside_authorised_plan():
    with pytest.raises(ContextError, match="active objective"):
        build_harness_context(
            session_id="session-1", learner_id="learner-1",
            expected_session_version=1, expected_profile_version=1, generation_id="gen-1",
            authorised_objective_ids=("units-tens",), active_objective_ids=("addition",),
            activity=ActivityContext("a", "t", "addition", 1, "Suma", (), (), 0),
            learner_state=LearnerState((("units-tens", "exploring"),), ("short",)),
            recent_history=(), current_turn=TurnEvidence("turn", "Dos", .9), max_history_turns=2,
        )
