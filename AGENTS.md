# Math Tutor Voice PoC

Spanish-speaking primary mathematics voice tutor built with LiveKit,
STT/LLM/TTS, FastAPI, and durable local state. The product explores whether a
probabilistic model can support a bounded educational workflow whose
mathematical decisions remain deterministic and reviewable.

Authoritative documents:

- `DESIGN.md` — approved product and architecture design.
- `IMPLEMENTATION_PLAN.md` — staged implementation plan.
- `TESTING_REFERENCE.md` — test workflow and taxonomy.
- `docs/DOCKER.md` — supported container workflows and runtime configuration.
- `docs/math-tutor-poc-verification.md` — current verification evidence and
  gates that still block a supervised learner trial.

## Stack and commands

Python 3.12, uv, LiveKit Agents, FastAPI, YAML, and SQLite. Run Python, pytest,
and uv only inside Docker; the host environment is not part of the supported
toolchain.

```bash
make up
make down
make agent
make test
make test ARGS="tests/test_environment.py -v"
make eval-math
make test-live-llm
make build
make lock
docker compose --profile dev config
```

Before `make up`, set `THERAPIST_API_TOKEN` in `.env` to a non-placeholder
server secret of at least 24 characters and at least 8 distinct characters.
Compose enables the therapist API for `web`; startup fails closed when that
token is missing or weak. `docker compose --profile dev config` only renders
and validates Compose and does not prove that the web application can start.

Use `docker compose --profile dev run --rm tooling <command>` for one-off
container commands. Add dependencies to `pyproject.toml`, then run
`make lock && make build`. Never edit `uv.lock` by hand and never use floating
container or dependency versions.

Automated tests and offline evals do not authorize a learner trial. Check the
verification record before any supervised use: professional tabletop review,
the supervised voice gate, consent, safeguarding, retention approval, and
specialist sign-off are separate requirements.

## Architecture non-negotiables

The product flow is:

```text
voice -> LLM -> pedagogical harness -> educational domain
      -> durable state -> therapist review
```

1. Dependency direction points inward: infrastructure may depend on
   application and domain; domain code never imports vendor SDKs.
2. The LLM proposes interpretations and pedagogical actions. It never mutates
   educational state or decides whether an answer is mathematically correct.
3. Deterministic code verifies mathematics, permissions, progression rules,
   attempt limits, and stop conditions before any state-changing action is
   executed or spoken.
4. Material learner-profile changes remain provisional until deterministic
   policy permits them or a therapist consolidates them.
5. Learner-facing language is descriptive and educational. The system must not
   issue diagnoses, assign medical labels, compare a child with peers, or
   promise improvement.
6. Full-session audio must never be retained. Selective evidence clips are
   default-off and require explicit, active consent scoped to the learner and
   session. When enabled, clips must be short and justified, use a configured
   retention period of 1–30 days, expire automatically, support explicit
   deletion, and become ineligible for new capture immediately on revocation.
7. The educational domain and pedagogical harness must be testable offline,
   without network access, voice providers, or a real model.

## TDD is mandatory

Use Red -> Green -> Refactor in small steps. Write one failing behavioral test
and run it before adding production code. Implement only enough to pass, then
refactor with the affected suite green. Mock external process boundaries only;
never mock pure domain functions. Real-provider checks use the `live_llm`
marker and remain opt-in.
