# Testing Workflow

All tests run inside Docker. Never run pytest on the host.

```bash
make test                                  # whole suite
make test ARGS="tests/test_environment.py -x" # existing subset, stop at first failure
make test ARGS="-k 'rule_engine' -vv"      # by keyword, verbose
make test-live-llm                         # opt-in real-provider smokes (keys + network)
docker compose --profile dev run --rm tooling uv run pytest <anything>   # canonical form
```

The pytest configuration registers `live_llm` and applies `-m "not live_llm"`
by default, including for the canonical Docker command. `make test-live-llm`
selects that marker and is ready for future opt-in provider smokes, but the
bootstrap scaffold does not contain any live-provider tests yet. Until one is
added, the target reports `SKIP` and maps only pytest's no-tests exit status to
success; collection errors and test failures keep their original non-zero
status.

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

The locations below are the target layout and are created with their delivery
phase. `tests/unit/` covers domain, application, harness, infrastructure,
dependency, and import-boundary guards. `tests/contract/` pins LiveKit SDK
surfaces, worker composition, Compose topology, token endpoints, and static
pages. `tests/integration/` drives the pedagogical harness through a scripted
`FakeModelAdapter`, while `tests/e2e/` is reserved for controlled fake
STT/LLM/TTS flows. Persistence tests build every temporary database under
`tmp_path`; repository guardrails remain at the tests root.

| Layer | Target location | Phase / rules |
|---|---|---|
| Unit | `tests/unit/` | Phases 1–2 onward. No network, no LLM, no LiveKit. Covers rules engine, protocol loader, state machine, field updates, tool authorization, projections. |
| Integration | `tests/integration/` | Phases 2–6. Harness driven by `FakeModelAdapter` (scripted responses). Covers tool pipeline, repair loop, stale-generation rejection, idempotency. |
| Contract | `tests/contract/` | Phase 0 onward. Pinned-version expectations against LiveKit SDK surfaces (e.g. dispatch metadata serialization, `llm_node` signature). |
| E2E | `tests/e2e/` | Phase 7. Controlled fake STT/LLM/TTS tests for interruption, cancellation, transcript, and lifecycle boundaries. |
| Live-LLM (optional) | marker `live_llm` | Real provider calls; excluded by default and run explicitly with `make test-live-llm`. Requires keys in `.env` and network access. |
| Evals | `evals/` | Phase 7. Offline scenario runner with behavioral assertions, never exact text. |

Mock policy: fake only what crosses the process boundary — model providers (`FakeModelAdapter`), STT/TTS plugins, clock where needed. Everything inside `src/math_tutor/domain/` runs real in every test.

Async tests: `pytest-asyncio` is configured with `asyncio_mode = "auto"` — write `async def test_...` directly, no decorator needed.
