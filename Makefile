.PHONY: install up down seed load api test lint eval eval-baseline benchmark

-include .env
export

install:
	pip install -r requirements-dev.txt

up:
	docker compose up -d db

down:
	docker compose down

seed:            ## small synthetic dataset for development
	python scripts/seed_sample.py --rows 200000

load:            ## full 2023 NYC TLC yellow + green trip data
	python scripts/load_tlc_data.py --year 2023 --types yellow green

api:
	uvicorn sqlagent.api.main:app --reload --port 8000

test:
	pytest

lint:
	ruff check .

benchmark:       ## regenerate eval/benchmark.jsonl
	python eval/build_benchmark.py

eval:            ## full agent: reviewer on, up to 3 attempts
	python -m eval.run_eval

eval-baseline:   ## ablation: single-shot writer, no reviewer
	python -m eval.run_eval --no-reviewer
