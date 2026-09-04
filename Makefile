COMPOSE := docker compose --profile dev
ARGS ?=

.PHONY: up down build agent test test-live-llm lock logs

up: ## Start LiveKit and the local static web service
	$(COMPOSE) up -d livekit web

down: ## Stop and remove local services
	$(COMPOSE) down

build: ## Build all project images from the committed lockfile
	$(COMPOSE) build tooling agent web

agent: ## Run the agent scaffold in the foreground
	$(COMPOSE) run --rm agent

test: ## Run pytest inside the network-independent tooling container
	$(COMPOSE) run --rm tooling uv run pytest $(ARGS)

test-live-llm: ## Run only opt-in real-provider checks
	$(COMPOSE) run --rm tooling sh -c 'uv run pytest -m live_llm $(ARGS); status=$$?; if [ "$$status" -eq 5 ]; then echo "SKIP: no live_llm tests are implemented yet"; exit 0; fi; exit "$$status"'

lock: ## Regenerate uv.lock inside Docker; never edit it by hand
	docker build -q --target toolchain -t math-tutor-voice-poc-toolchain -f docker/Dockerfile.tooling .
	docker run --rm -v "$(PWD)":/app -w /app -e UV_PROJECT_ENVIRONMENT=/tmp/math-tutor-lockenv math-tutor-voice-poc-toolchain uv lock

logs: ## Follow local service logs
	$(COMPOSE) logs -f
