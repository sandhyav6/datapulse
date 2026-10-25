# Run inside WSL2/Linux/macOS. On native Windows use .\make.ps1 <target> instead.
PYTHON  ?= python3
VENV    := .venv
PY      := $(abspath $(VENV))/bin/python
STACK   ?= serverless-etl-dev
PROFILE ?= etl-dev
REGION  ?= ap-south-1
EMAIL   ?= sandhya.avm@gmail.com
AWSARGS := --stack $(STACK) --profile $(PROFILE) --region $(REGION)

.PHONY: setup test test-backend test-frontend lint build deploy deploy-frontend dev-api dev-web \
        ingest upload benchmark teardown clean

setup:
	$(PYTHON) -m venv $(VENV)
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r backend/requirements-dev.txt
	cd frontend && npm ci

test: test-backend test-frontend

test-backend:
	cd backend && $(PY) -m pytest --cov --cov-report=term-missing

test-frontend:
	cd frontend && npm test

lint:
	cd backend && $(PY) -m ruff check . && $(abspath $(VENV))/bin/cfn-lint template.yaml
	cd frontend && npm run lint && npm run typecheck

build:
	cd backend && sam build
	cd frontend && npm run build

deploy:
	@echo "Run these yourself (they change AWS):"
	@echo "  cd backend && sam build && sam deploy --parameter-overrides Stage=dev EnableAnalytics=true AlertEmail=$(EMAIL)"
	@echo "  make deploy-frontend"

deploy-frontend:
	cd backend && $(PY) scripts/deploy_frontend.py $(AWSARGS)

dev-api:
	cd backend && $(PY) scripts/local_demo.py

dev-web:
	cd frontend && npm run dev

ingest:
	cd backend && $(PY) scripts/ingest_now.py $(AWSARGS)

upload:
	cd backend && $(PY) scripts/upload.py $(FILE) --dataset $(or $(DATASET),sales) $(AWSARGS)

benchmark:
	cd backend && $(PY) scripts/run_benchmark.py $(AWSARGS)

teardown:
	cd backend && $(PY) scripts/teardown.py $(AWSARGS)

clean:
	rm -rf backend/.aws-sam backend/.pytest_cache backend/.ruff_cache backend/.coverage frontend/dist
	find . -name __pycache__ -type d -prune -not -path "./frontend/node_modules/*" -exec rm -rf {} +
