.PHONY: install ingest test lint api ui eval notebook docker-build docker-up

install:            ## create .venv and install the package with dev + notebook extras
	python3 -m venv .venv && .venv/bin/pip install -e ".[dev,notebook]"

ingest:             ## parse, chunk and index the regulation
	.venv/bin/aiact-qa ingest

test:
	.venv/bin/python -m pytest

lint:
	.venv/bin/ruff check src app tests scripts

api:                ## REST API on http://127.0.0.1:8000 (docs at /docs)
	.venv/bin/aiact-qa serve

ui:                 ## Streamlit chat on http://127.0.0.1:8501
	.venv/bin/aiact-qa ui

eval:               ## golden-set evaluation with the configured provider
	.venv/bin/aiact-qa eval

notebook:           ## re-execute the walkthrough notebook in place
	.venv/bin/jupyter nbconvert --to notebook --execute --inplace notebooks/AI_Act_QA_Prototype.ipynb

docker-build:
	docker compose build

docker-up:
	docker compose up -d
