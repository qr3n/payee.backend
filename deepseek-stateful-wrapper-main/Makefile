.PHONY: up down logs ps chat smoke

up: ## Build and start the local proxy
	docker compose up -d --build

down: ## Stop the proxy
	docker compose down

logs: ## Follow proxy logs
	docker compose logs -f deepseek-proxy

ps: ## Show proxy status
	docker compose ps

chat: ## Open an interactive stateful conversation
	python3 deepseek_chat.py --conversation-id "$${CONVERSATION_ID:-manual}"

smoke: ## Run a single connectivity check
	python3 deepseek_chat.py --conversation-id smoke "Ответь коротко: связь работает?"

