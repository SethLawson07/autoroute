# Qualification locale : la même chose que .github/workflows/qualif.yml, avant un push.
#
#   .\scripts\qualify.ps1          # tests, paquet, installation propre, commandes
#   .\scripts\qualify.ps1 -Live    # + vraies API (utilise les clés de .env, coûte quelques centimes)
#
# Prérequis : le venv du projet activé, et uv (https://docs.astral.sh/uv/).
param([switch]$Live)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

function Step($title) { Write-Host "`n=== $title" -ForegroundColor Cyan }
function Check($what) { if ($LASTEXITCODE -ne 0) { Write-Host "ÉCHEC : $what" -ForegroundColor Red; exit 1 } }

Step "1. Tests rapides"
pytest -q
Check "pytest"

Step "2. Construction du paquet"
if (Test-Path dist) { Remove-Item dist -Recurse -Force }
uv build
Check "uv build"
uvx twine check --strict dist/*
Check "twine check"

Step "3. Installation du wheel dans un environnement neuf"
$tmp = Join-Path ([IO.Path]::GetTempPath()) "autoroute-qualif-$(Get-Random)"
uv venv $tmp -q
Check "uv venv"
$wheel = (Get-ChildItem dist\*.whl).FullName
$env:VIRTUAL_ENV = $tmp
uv pip install $wheel -q
$code = $LASTEXITCODE
Remove-Item Env:VIRTUAL_ENV
if ($code -ne 0) { Write-Host "ÉCHEC : installation du wheel" -ForegroundColor Red; exit 1 }

Step "4. Commandes, depuis un dossier vide"
$autoroute = Join-Path $tmp "Scripts\autoroute.exe"
$smoke = Join-Path $tmp "smoke"
New-Item -ItemType Directory $smoke | Out-Null
Push-Location $smoke
try {
    & $autoroute --help | Out-Null
    Check "autoroute --help"
    & $autoroute init
    Check "autoroute init"
    if (-not (Test-Path .autoroute/config.yaml) -or -not (Test-Path .env.example)) {
        Write-Host "ÉCHEC : templates absents du paquet" -ForegroundColor Red; exit 1
    }
    # PowerShell 5.1 : avec « Stop », stderr redirigé par 2>&1 deviendrait une erreur fatale.
    $ErrorActionPreference = "Continue"
    $out = & $autoroute models 2>&1 | Out-String
    $ErrorActionPreference = "Stop"
    if ($out -match "Traceback") { Write-Host "ÉCHEC : trace Python`n$out" -ForegroundColor Red; exit 1 }
    Write-Host "autoroute models : sortie propre"
} finally {
    Pop-Location
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
}

if ($Live) {
    Step "5. Vraies API"
    pytest -m live -v -s
    Check "tests live"
} else {
    Write-Host "`n(Vraies API non testées : relance avec -Live)" -ForegroundColor Yellow
}

Write-Host "`nQualification OK" -ForegroundColor Green
