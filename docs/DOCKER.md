# Docker development and verification

Docker Compose is the supported execution environment. Do not run Python,
pytest, or uv directly on the host. Commands below run from the repository
root.

## Services and profiles

| Service | Purpose | Profile | Network exposure |
|---|---|---|---|
| `livekit` | Local LiveKit server | default | `127.0.0.1:7880`, `:7881`, UDP `50000-50019` |
| `web` | Learner and therapist HTTP surfaces | `dev` | `127.0.0.1:8080` |
| `agent` | Live voice worker | `dev` | Shares the `livekit` network namespace |
| `tooling` | Tests, evals, lock generation helpers | `dev`, `test` | No provider service dependency |

The checked-in LiveKit key and secret are local-development credentials only.
They are not deployment credentials.

## Routine commands

```bash
make up                         # start livekit and web in the background
make agent                      # run the voice worker in the foreground
make down                       # stop and remove the Compose stack
make test                       # offline default pytest suite
make test ARGS="path -k name"   # focused pytest invocation
make eval-math                  # deterministic fake-model acceptance gate
make test-live-llm              # opt-in provider checks; keys/network required
make build                      # rebuild tooling, agent, and web images
make lock                       # regenerate uv.lock in a pinned Docker toolchain
docker compose --profile dev config  # validate and render Compose configuration
```

Use `docker compose --profile dev run --rm tooling <command>` for another
one-off command. Edit `pyproject.toml`, then use `make lock && make build` when
dependencies change; never edit `uv.lock` manually.

`make up` does not start the agent. Run `make agent` separately after LiveKit is
available. A live voice run also needs configured STT, LLM, and TTS provider
values and credentials in `.env`. The offline `make test` and `make eval-math`
commands need neither provider credentials nor network access.

## Runtime configuration

Compose passes the following provider settings to `agent`: `STT_PROVIDER`,
`STT_MODEL`, `STT_API_KEY`, `LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY`,
`LLM_BASE_URL`, `LLM_FIRST_RESPONSE_SECONDS`, `LLM_TOTAL_SECONDS`,
`TTS_PROVIDER`, `TTS_MODEL`, `TTS_VOICE_ID`, and `TTS_API_KEY`.

Audio evidence is disabled by default. The agent accepts
`AUDIO_EVIDENCE_ENABLED`, retention days, context seconds, hard-cap seconds,
directory, and sweep interval. The web service receives the enabled flag,
retention days, and directory. Product policy still requires active scoped
consent before capture; setting an environment variable is not consent.

The web therapist API is enabled in local Compose and requires a non-empty,
high-entropy `THERAPIST_API_TOKEN` before any meaningful shared or supervised
environment. The default empty value is suitable only for configuration and
automated checks, not a supervised voice gate.

Only variables wired in `docker-compose.yml` affect these services.
`LLM_TEMPERATURE` and `STORE_TRANSCRIPT` are present in `.env.example` but are
not currently passed by Compose; do not rely on them as runtime controls.

## Verification boundary

The canonical automated gate is:

```bash
make test
make eval-math
docker compose --profile dev config
```

Passing it proves the checked-in offline behavior and a valid Compose model.
It does not prove provider connectivity, speech latency, interruption quality,
audio capture behavior on a real call, therapist usability, consent readiness,
or safeguarding readiness. The current evidence and outstanding human gates
are recorded in [math-tutor-poc-verification.md](math-tutor-poc-verification.md).
