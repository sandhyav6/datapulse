# Windows equivalent of the Makefile:  .\make.ps1 <target> [-File path] [-Dataset name]
param(
    [Parameter(Position = 0)][string]$Target = "help",
    [string]$File = "sample_data/dirty_small.csv",
    [string]$Dataset = "sales",
    [string]$Stack = "serverless-etl-dev",
    [string]$AwsProfile = "etl-dev",
    [string]$Region = "ap-south-1",
    [string]$Email = "sandhya.avm@gmail.com"
)
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Py = Join-Path $Root ".venv\Scripts\python.exe"
$AwsArgs = @("--stack", $Stack, "--profile", $AwsProfile, "--region", $Region)

function In([string]$dir, [scriptblock]$block) {
    Push-Location (Join-Path $Root $dir)
    try { & $block } finally { Pop-Location }
}

switch ($Target) {
    "setup" {
        python -m venv (Join-Path $Root ".venv")
        & $Py -m pip install --upgrade pip
        & $Py -m pip install -r (Join-Path $Root "backend\requirements-dev.txt")
        In frontend { npm ci }
    }
    "test"          { In backend { & $Py -m pytest --cov --cov-report=term-missing }; In frontend { npm test } }
    "test-backend"  { In backend { & $Py -m pytest --cov --cov-report=term-missing } }
    "test-frontend" { In frontend { npm test } }
    "lint" {
        In backend { & $Py -m ruff check .; & (Join-Path $Root ".venv\Scripts\cfn-lint.exe") template.yaml }
        In frontend { npm run lint; npm run typecheck }
    }
    "build"         { In backend { sam build }; In frontend { npm run build } }
    "deploy" {
        Write-Host "Run these yourself (they change AWS):"
        Write-Host "  cd backend; sam build; sam deploy --parameter-overrides Stage=dev EnableAnalytics=true AlertEmail=$Email"
        Write-Host "  .\make.ps1 deploy-frontend"
    }
    "deploy-frontend" { In backend { & $Py scripts/deploy_frontend.py @AwsArgs } }
    "dev-api"       { In backend { & $Py scripts/local_demo.py } }
    "dev-web"       { In frontend { npm run dev } }
    "ingest"        { In backend { & $Py scripts/ingest_now.py @AwsArgs } }
    "upload"        { In backend { & $Py scripts/upload.py $File --dataset $Dataset @AwsArgs } }
    "benchmark"     { In backend { & $Py scripts/run_benchmark.py @AwsArgs } }
    "teardown"      { In backend { & $Py scripts/teardown.py @AwsArgs } }
    "clean" {
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue `
            (Join-Path $Root "backend\.aws-sam"), (Join-Path $Root "backend\.pytest_cache"), `
            (Join-Path $Root "backend\.ruff_cache"), (Join-Path $Root "backend\.coverage"), `
            (Join-Path $Root "frontend\dist")
    }
    default {
        Write-Host "Targets: setup test test-backend test-frontend lint build deploy deploy-frontend"
        Write-Host "         dev-api dev-web ingest upload benchmark teardown clean"
    }
}
