.PHONY: install run migrate freeze

install:
	python -m venv .venv && .venv/bin/pip install -r requirements.txt

run:
	source .venv/bin/activate && uvicorn server:app --reload --port 8080

migrate:
	source .venv/bin/activate && python migrate.py

freeze:
	.venv/bin/pip freeze > requirements.txt
