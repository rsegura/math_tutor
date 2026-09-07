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
make up                         # start livekit and web; requires therapist token below
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

Before `make up`, set `THERAPIST_API_TOKEN` in `.env` to a high-entropy,
non-placeholder server secret with at least 24 characters and at least 8
distinct characters. For example, generate it with an approved secret manager
and keep it server-side; do not copy the placeholder from `.env.example`.
Compose sets `THERAPIST_API_ENABLED=true`, and `WebSettings` deliberately aborts
web startup when the token is absent, shorter than 24 characters, has fewer
than 8 distinct characters, or matches a recognized placeholder.

`make up` does not start the agent. Run `make agent` separately after LiveKit is
available. A live voice run also needs configured STT, LLM, and TTS provider
values and credentials in `.env`. The offline `make test` and `make eval-math`
commands need neither provider credentials nor network access.

For the pedagogical LLM, use one of these exact profiles:

```dotenv
# OpenAI: SDK default endpoint; LLM_BASE_URL must be empty
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o-mini-2024-07-18
LLM_API_KEY=your-openai-key
LLM_BASE_URL=

# OpenRouter: canonical endpoint only
LLM_PROVIDER=openrouter
LLM_MODEL=openai/gpt-4o-mini
LLM_API_KEY=your-openrouter-key
LLM_BASE_URL=https://openrouter.ai/api/v1
```

An OpenRouter model is usable only if it supports the Responses API and tool
calling. `LLM_BASE_URL` is active and fail-closed; it is not a generic proxy or
arbitrary OpenAI-compatible endpoint. Gemini is a future extension point and
is not implemented.

`TTS_VOICE_ID` is optional for ElevenLabs. When empty, the factory omits that
argument and the pinned LiveKit plugin chooses its default; construction alone
does not prove that an account is entitled to use that default. OpenAI TTS has
no such fallback and an explicit voice ID is required.

The opt-in OpenRouter smoke uses separate operator variables so it cannot
silently select another configured provider:

```bash
export OPENROUTER_API_KEY=your-openrouter-key
export OPENROUTER_MODEL=openai/gpt-4o-mini
make test-live-llm
```

It makes one Responses request with a real tool contract and reports `PASS`,
`SKIP` (missing credentials/model), or `FAIL`. A skip is not a provider pass.

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

The web therapist API is enabled in local Compose and requires the valid
`THERAPIST_API_TOKEN` described above for every web startup, including local
development. An empty or weak value is never a valid startup configuration.
`docker compose --profile dev config` can still succeed with an empty
interpolated value because it validates/renders Compose without importing the
FastAPI application; it is not a web startup check.

Only variables wired in `docker-compose.yml` affect these services.
`STORE_TRANSCRIPT` is present in `.env.example` but is not currently passed by
Compose; do not rely on it as a runtime control.

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
