COMPOSE := docker compose --profile dev
ARGS ?=

.PHONY: up down build agent test test-live-llm eval-math lock logs

up: ## Start LiveKit and the local static web service
	$(COMPOSE) up -d livekit web

down: ## Stop and remove local services
	$(COMPOSE) down

build: ## Build all project images from the committed lockfile
	$(COMPOSE) build tooling agent web

agent: ## Run the real tutoring voice worker in the foreground
	$(COMPOSE) run --rm agent python -m math_tutor.agent.worker start

test: ## Run pytest inside the network-independent tooling container
	$(COMPOSE) run --rm tooling uv run pytest $(ARGS)

test-live-llm: ## Run only opt-in real-provider checks
	@result_file=$$(mktemp); \
	$(COMPOSE) run --rm -e OPENROUTER_API_KEY -e OPENROUTER_MODEL tooling \
		uv run pytest -m live_llm -rA tests/live_llm/test_openrouter_responses_live.py $(ARGS) >"$$result_file" 2>&1; \
	status=$$?; cat "$$result_file"; \
	if [ "$$status" -ne 0 ]; then echo "FAIL: OpenRouter Responses tool smoke"; rm -f "$$result_file"; exit "$$status"; fi; \
	if grep -Eq 'SKIPPED \[' "$$result_file"; then echo "SKIP: OpenRouter credentials or model not configured"; else echo "PASS: OpenRouter Responses tool smoke"; fi; \
	rm -f "$$result_file"

eval-math: ## Run deterministic offline tutoring scenarios and safety gate
	$(COMPOSE) run --rm tooling uv run python -m evals.math_tutor.runner

lock: ## Regenerate uv.lock inside Docker; never edit it by hand
	docker build -q --target toolchain -t math-tutor-voice-poc-toolchain -f docker/Dockerfile.tooling .
	docker run --rm -v "$(PWD)":/app -w /app -e UV_PROJECT_ENVIRONMENT=/tmp/math-tutor-lockenv math-tutor-voice-poc-toolchain uv lock

logs: ## Follow local service logs
	$(COMPOSE) logs -f
