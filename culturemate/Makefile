.PHONY: up down db graph test lint

up:      ## 로컬 스택 기동
	docker compose up -d --build

down:
	docker compose down -v

db:      ## 스키마 재적용
	docker compose exec -T postgres psql -U culturemate -d culturemate < db/001_schema.sql

graph:   ## Mermaid 다이어그램 재생성
	python scripts/render_graph.py main      > docs/diagrams/00_main.mmd
	python scripts/render_graph.py archive   > docs/diagrams/01_archive.mmd
	python scripts/render_graph.py discovery > docs/diagrams/02_discovery.mmd
	python scripts/render_graph.py itinerary > docs/diagrams/03_itinerary.mmd
	python scripts/render_graph.py validation> docs/diagrams/04_validation.mmd

test:
	pytest -q

lint:
	ruff check app scripts
