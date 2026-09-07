# Provider Abstraction Design

## Goal

Make the tutoring runtime independent of a particular LLM vendor while adding
first-class OpenRouter support. OpenAI and OpenRouter must be selectable by
configuration without changing the pedagogical harness, deterministic domain
logic, or worker lifecycle. The design must leave a stable extension point for
a future Gemini adapter without claiming Gemini support today.

## Boundaries

`math_tutor.harness.model.ModelAdapter` remains the provider-neutral port. The
harness only supplies its bounded prompt/context and consumes canonical tool
proposals or conversation replies. Vendor SDK objects, request formats,
authentication, base URLs, and response parsing stay in infrastructure-facing
agent provider modules.

A lazy provider factory selects a concrete adapter from validated
configuration:

```text
PedagogicalHarness -> ModelAdapter <- ModelProviderFactory
                                      |- OpenAI Responses
                                      |- OpenRouter Responses
                                      `- future Gemini adapter
```

OpenAI and OpenRouter may share a reusable OpenResponses transport/parser, but
they remain explicit provider profiles. OpenRouter defaults to
`https://openrouter.ai/api/v1`; OpenAI uses the SDK default endpoint. Arbitrary
`openai-compatible` endpoints are not accepted as verified providers.

Provider construction is lazy on first model use, not merely delayed until the
engine constructor. `BoundedConversationEngine.__init__` stores the factory but
does not invoke it. It does construct the provider-free
`PedagogicalToolRegistry`, preserving the shared authorization and mutation
fence. Each decision evaluates durable terminal state, caps, and a stop request
first. A stop executes an `END_SESSION` proposal with reason `stop-requested`
through that registry without constructing a model. Only a turn that will call
`model.complete()` obtains and caches the adapter/harness—assembled with the
existing registry—through an async initialization lock. Later turns reuse the
same instance, and concurrent first use cannot create two clients. A direct
`model=` override remains available for offline tests. This prevents
network-client creation without creating a mutation shortcut.

## Configuration

Common settings remain `LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY`, bounded
response deadlines, bounded `LLM_MAX_OUTPUT_TOKENS` (default 256, accepted
range 64–1024), and optional `LLM_BASE_URL`. Supported provider values are
`openai` and `openrouter`. OpenRouter uses its official endpoint by default;
the exact URL policy is:

- `openai` plus an empty base URL uses the SDK default;
- `openai` plus any non-empty base URL is rejected;
- `openrouter` plus an empty base URL resolves to
  `https://openrouter.ai/api/v1`;
- `openrouter` accepts only that canonical HTTPS URL, optionally with one
  trailing slash, and rejects credentials, query, fragment, port, subdomain,
  HTTP, or another path.

Proxy/custom endpoint support is outside this change. The resolved base URL is
stored in settings and environment is never reread by the factory. API-key
fields are excluded from representations and provider errors are sanitized.
The unused `LLM_TEMPERATURE` setting is removed from examples rather than
presented as operational.

The existing canonical model result and repair loop remain unchanged. Provider
errors must not expose API keys or raw sensitive child context.

`ModelAdapter.complete()` explicitly returns either an object or an awaitable
object so existing deterministic fakes remain synchronous. Production
adapters are asynchronous and are used only through `run_async()`. The
synchronous harness path rejects an awaitable adapter result with an explicit
contract error instead of leaking an un-awaited coroutine. It checks declared
async callables before invocation and closes any unexpected coroutine object
before raising, so rejection cannot emit an un-awaited-coroutine warning.

## TTS voice default

`TTS_VOICE_ID` becomes optional. When `TTS_PROVIDER=elevenlabs` and the value is
empty, the ElevenLabs adapter omits the `voice_id` argument so the pinned
LiveKit plugin applies its default. A non-empty value is forwarded explicitly.
For `TTS_PROVIDER=openai`, a non-empty explicit voice remains required; it does
not inherit the ElevenLabs defaulting rule. An empty setting is normalized to
`None`, and factories omit kwargs rather than passing `voice_id=None`.

## Verification

- Contract tests prove supported/unsupported provider configuration.
- Adapter tests prove OpenAI and OpenRouter endpoint selection and identical
  canonical parsing/tool behavior.
- Representative OpenRouter response fixtures cover completed tool calls and
  text plus failed/incomplete/error, multiple-call, and mixed-output failures.
- Factory tests prove the harness receives only a `ModelAdapter`.
- Lifecycle tests prove terminal sessions never invoke the lazy model factory.
- First-use tests cover immediate stop (zero creations), first normal turn (one
  creation), reuse on later turns, and concurrent first-use initialization.
- Stop tests also prove durable termination, equivalent terminal decisions,
  and execution through the shared registry/fence.
- Sync/async conformance tests prove production cannot enter the synchronous
  harness path accidentally.
- Voice-provider tests prove ElevenLabs construction omits an empty voice ID.
- Existing offline suite/eval remains green.
- An opt-in OpenRouter `live_llm` smoke uses Responses API and a real function
  call for the configured model. It reports PASS, SKIP/no credentials, or FAIL
  distinctly; a skip is never reported as a live pass.

## Documentation

Update `.env.example`, README, Docker documentation, and verification notes to
show OpenRouter as supported, Gemini as a future adapter, and ElevenLabs voice
selection as optional. Do not describe `LLM_BASE_URL` or Gemini as active in a
way the implementation does not support.

OpenRouter documentation must require a model that supports both Responses and
tool calling. Construction tests do not prove that the default ElevenLabs
voice is enabled for every account; that remains a live-provider check.
