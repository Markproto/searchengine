.PHONY: help up-apollo9 up-azure7 up-dev down-apollo9 down-azure7 down-dev logs-apollo9 logs-azure7 ps

help:
	@echo "Profoundd compose targets — pick the one matching THIS host."
	@echo ""
	@echo "  make up-apollo9    Start production stack (Apollo9 only)"
	@echo "  make up-azure7     Start mirror stack    (Azure7 only)"
	@echo "  make up-dev        Start local dev stack"
	@echo ""
	@echo "  make down-apollo9 / down-azure7 / down-dev"
	@echo "  make logs-apollo9 / logs-azure7"
	@echo "  make ps            Show all profoundd containers"
	@echo ""
	@echo "Each target wraps 'docker compose -f docker-compose.<host>.yml ...'"
	@echo "so you cannot accidentally run Apollo9's IP bindings on Azure7."

up-apollo9:
	sudo docker compose -f docker-compose.apollo9.yml up -d --build

up-azure7:
	sudo docker compose -f docker-compose.azure7.yml up -d --build

up-dev:
	docker compose -f docker-compose.dev.yml up -d --build

down-apollo9:
	sudo docker compose -f docker-compose.apollo9.yml down

down-azure7:
	sudo docker compose -f docker-compose.azure7.yml down

down-dev:
	docker compose -f docker-compose.dev.yml down

logs-apollo9:
	sudo docker compose -f docker-compose.apollo9.yml logs -f --tail=100

logs-azure7:
	sudo docker compose -f docker-compose.azure7.yml logs -f --tail=100

ps:
	sudo docker ps --filter name=profoundd --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
