.PHONY: test up down run

PYTHON ?= python

test:
	$(PYTHON) -m pytest -q

up:
	docker compose up --build

down:
	docker compose down

run:
	$(PYTHON) -m uvicorn app.api.main:app --host 0.0.0.0 --port 8000
