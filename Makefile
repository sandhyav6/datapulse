# Run inside WSL2/Linux/macOS. On native Windows use .\make.ps1 <target> instead.
PYTHON ?= python3
VENV   := .venv
BIN    := $(VENV)/bin
STACK  ?= serverless-etl-dev
PROFILE ?= etl-dev
REGION ?= ap-south-1
FILE   ?= sample_data/dirty_small.csv
DATASET ?= sales

.PHONY: venv test lint build validate deploy upload ingest samples demo dashboard-env dashboard benchmark teardown clean

venv:
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install --upgrade pip
	$(BIN)/pip install -r requirements-dev.txt

test:
	$(BIN)/pytest --cov --cov-report=term-missing

lint:
	$(BIN)/ruff check .
	$(BIN)/cfn-lint template.yaml

build:
	sam build

validate:
	sam validate --lint

deploy:
	@echo "Run this yourself (it changes AWS):"
	@echo "  sam build && sam deploy --profile $(PROFILE)"

upload:
	$(BIN)/python scripts/upload.py $(FILE) --dataset $(DATASET) --stack $(STACK) --profile $(PROFILE) --region $(REGION)

ingest:
	$(BIN)/python scripts/ingest_now.py --stack $(STACK) --profile $(PROFILE) --region $(REGION)

samples:
	$(BIN)/python scripts/generate_sample.py --rows 100 --bad-ratio 0 --seed 1 --out sample_data/clean_small.csv
	$(BIN)/python scripts/generate_sample.py --rows 1000 --bad-ratio 0.05 --seed 42 --out sample_data/dirty_small.csv
	$(BIN)/python scripts/generate_sample.py --poison --out sample_data/poison.csv

demo:
	$(BIN)/python scripts/local_demo.py

dashboard-env:
	$(BIN)/python scripts/make_dashboard_env.py --stack $(STACK) --profile $(PROFILE) --region $(REGION)

dashboard:
	$(BIN)/streamlit run dashboard/app.py

benchmark:
	$(BIN)/python scripts/run_benchmark.py --stack $(STACK) --profile $(PROFILE) --region $(REGION)

teardown:
	$(BIN)/python scripts/teardown.py --stack $(STACK) --profile $(PROFILE) --region $(REGION)

clean:
	rm -rf .aws-sam .pytest_cache .ruff_cache .coverage htmlcov
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
