from math_tutor.application.session_runtime import SessionRuntime


def test_starting_a_new_generation_cancels_the_previous_one():
    runtime = SessionRuntime()
    first = runtime.start_generation("session-1")

    second = runtime.start_generation("session-1")

    assert first.cancelled
    assert not second.cancelled
    assert runtime.active_generation("session-1") == second


def test_mutation_cancels_and_clears_active_generation():
    runtime = SessionRuntime()
    generation = runtime.start_generation("session-1")

    runtime.cancel_generation("session-1")

    assert generation.cancelled
    assert runtime.active_generation("session-1") is None


def test_targeted_cancel_never_pops_a_newer_generation():
    runtime = SessionRuntime()
    older = runtime.start_generation("session-1")
    newer = runtime.start_generation("session-1")

    assert runtime.cancel_generation_if_active("session-1", older.generation_id) is False
    assert runtime.active_generation("session-1") is newer
    assert runtime.cancel_generation_if_active("session-1", newer.generation_id) is True
    assert runtime.active_generation("session-1") is None
