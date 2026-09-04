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
