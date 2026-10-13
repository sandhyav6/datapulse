# Windows equivalent of the Makefile:  .\make.ps1 <target> [-File path] [-Dataset name]
param(
    [Parameter(Position = 0)][string]$Target = "help",
    [string]$File = "sample_data/dirty_small.csv",
    [string]$Dataset = "sales",
    [string]$Stack = "serverless-etl-dev",
    [string]$AwsProfile = "etl-dev",
    [string]$Region = "ap-south-1"
)
$ErrorActionPreference = "Stop"
$Py = ".venv\Scripts\python.exe"

switch ($Target) {
    "venv" {
        python -m venv .venv
        & $Py -m pip install --upgrade pip
        & $Py -m pip install -r requirements-dev.txt
    }
    "test"     { & $Py -m pytest --cov --cov-report=term-missing }
    "lint"     { & $Py -m ruff check .; & $Py -m cfnlint template.yaml }
    "build"    { sam build }
    "validate" { sam validate --lint }
    "deploy" {
        Write-Host "Run this yourself (it changes AWS):"
        Write-Host "  sam build; sam deploy --profile $AwsProfile"
    }
    "upload"   { & $Py scripts/upload.py $File --dataset $Dataset --stack $Stack --profile $AwsProfile --region $Region }
    "samples" {
        & $Py scripts/generate_sample.py --rows 100 --bad-ratio 0 --seed 1 --out sample_data/clean_small.csv
        & $Py scripts/generate_sample.py --rows 1000 --bad-ratio 0.05 --seed 42 --out sample_data/dirty_small.csv
        & $Py scripts/generate_sample.py --poison --out sample_data/poison.csv
    }
    "demo"          { & $Py scripts/local_demo.py }
    "dashboard-env" { & $Py scripts/make_dashboard_env.py --stack $Stack --profile $AwsProfile --region $Region }
    "dashboard"     { & $Py -m streamlit run dashboard/app.py }
    "benchmark"     { & $Py scripts/run_benchmark.py --stack $Stack --profile $AwsProfile --region $Region }
    "teardown"      { & $Py scripts/teardown.py --stack $Stack --profile $AwsProfile --region $Region }
    "clean" {
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue .aws-sam, .pytest_cache, .ruff_cache, .coverage, htmlcov
        Get-ChildItem -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force
    }
    default { Write-Host "Targets: venv test lint build validate deploy upload samples demo dashboard-env dashboard benchmark teardown clean" }
}
