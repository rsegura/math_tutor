# Testing Workflow

All tests run inside Docker. Never run pytest on the host.

```bash
make test                                  # whole suite
make test ARGS="tests/test_environment.py -x" # existing subset, stop at first failure
make test ARGS="-k 'rule_engine' -vv"      # by keyword, verbose
make test-live-llm                         # opt-in real-provider smokes (keys + network)
docker compose --profile dev run --rm tooling uv run pytest <anything>   # canonical form
```

The pytest configuration registers `live_llm` and applies `-m "not live_llm"` by default, including for the canonical Docker command. `make test-live-llm` explicitly selects that marker and requires provider credentials plus network access. Two live smokes exist, each at the bottom of its adapter's own unit-test file and each skipped when its key is absent: `test_live_responses_smoke` (`OPENAI_API_KEY`) in `tests/unit/infrastructure/test_openai_responses_adapter.py`, and `test_live_gemini_chat_completions_smoke` (`GEMINI_API_KEY`) in `tests/unit/infrastructure/test_chat_completions_adapter.py`.

## TDD protocol (Red → Green → Refactor)

1. **Red** — write ONE failing test describing the next small behavior. Run it and show the failing output before writing any production code. If it doesn't fail, the test is wrong — fix the test first.
2. **Green** — write the minimum production code that makes that test pass. No extra features, no speculative generality. Run the test and show it passing.
3. **Refactor** — as a separate step, clean up while the suite stays green. Re-run the affected tests after refactoring.
4. Repeat. One behavior per cycle; commits should map to completed cycles.

Anti-patterns that will get a change rejected:

- Writing implementation and tests in the same step ("test-after").
- Kitchen-sink tests asserting many behaviors at once — the one exception is a phase Definition-of-Done gate in `tests/integration/`, which asserts a whole scripted conversation's outcome deliberately and whose every clause is also covered granularly by a unit test.
- Mocking pure functions or domain objects.
- Hardcoding a return value to pass and never adding the follow-up test that forces the real implementation.
- Asserting exact assistant wording — assert behavior (which field was asked about, which tool was called, what was rejected), not text.

## Test taxonomy

The locations below are the target layout and are created with their delivery phase. After Phases 0–2, `tests/unit/` (domain, application, harness, infrastructure, dependency and import-boundary guards), `tests/contract/` (pinned LiveKit SDK surface, worker, compose topology, token endpoint, static page) and `tests/integration/` (the scripted `FakeModelAdapter` harness conversation gate) are populated; `tests/e2e/` arrives with its phase. Phase 3 adds `tests/integration/test_voice_boundary.py`, the voice-boundary gate: it drives `ScreeningAgent`'s real `on_user_turn_completed`/`llm_node` over a `build_screening_runtime` composition to pin turn correlation, supersession, §14 interruption case (b), transcript integrity under a barge-in, and idempotent replay. Phase 4 adds `tests/integration/test_persistence_wal.py`, the §14 WAL battery (cold start before any sidecar exists, sidecar recreation across a writer restart, a query-only reader against a live writer, and the bounded busy timeout), and `tests/support/persistence.py`, which builds every temporary database under `tmp_path`. `tests/test_environment.py` and `tests/test_development_harness.py` remain at the tests root as repository guardrails.

| Layer | Target location | Phase / rules |
|---|---|---|
| Unit | `tests/unit/` | Phases 1–2 onward. No network, no LLM, no LiveKit. Covers rules engine, protocol loader, state machine, field updates, tool authorization, projections. |
| Integration | `tests/integration/` | Phases 2–6. Harness driven by `FakeModelAdapter` (scripted responses). Covers tool pipeline, repair loop, stale-generation rejection, idempotency. |
| Contract | `tests/contract/` | Phase 0 onward. Pinned-version expectations against LiveKit SDK surfaces (e.g. dispatch metadata serialization, `llm_node` signature). |
| E2E | `tests/e2e/` | Phase 7. Controlled fake STT/LLM/TTS tests for interruption, cancellation, transcript, and lifecycle boundaries. |
| Live-LLM (optional) | marker `live_llm` | Real provider calls; excluded by default and run explicitly with `make test-live-llm`. Requires keys in `.env` and network access. |
| Evals | `evals/` | Phase 7. Offline scenario runner with behavioral assertions, never exact text. |

Mock policy: fake only what crosses the process boundary — model providers (`FakeModelAdapter`), STT/TTS plugins, clock where needed. Everything inside `src/domain/` runs real in every test.

Async tests: `pytest-asyncio` is configured with `asyncio_mode = "auto"` — write `async def test_...` directly, no decorator needed.
