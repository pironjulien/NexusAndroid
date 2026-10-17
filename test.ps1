# Full offline check, no phone needed: lint, types, unit tests. Exit code 0 when everything passes.
# Works from any copy of the repository: each checkout gets its own test environment under ~\.nexus\nexus-android
# (outside the sources, never the environment of the installed server); nothing is written to the source tree.
# Real phone test: install.ps1 -Live.
$ErrorActionPreference = 'Stop'
$state = Join-Path $HOME '.nexus\nexus-android'
$key = [BitConverter]::ToString([Security.Cryptography.SHA256]::HashData(
        [Text.Encoding]::UTF8.GetBytes($PSScriptRoot.ToLowerInvariant()))).Replace('-', '').Substring(0, 8)
$env:UV_PROJECT_ENVIRONMENT = Join-Path $state "test-venv-$key"
$env:UV_LINK_MODE = 'copy'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONPYCACHEPREFIX = Join-Path $state 'cache\pycache'
$env:PYRIGHT_PYTHON_CACHE_DIR = Join-Path $state 'cache\pyright'
$pythonExe = Join-Path $env:UV_PROJECT_ENVIRONMENT 'Scripts\python.exe'
Push-Location $PSScriptRoot
try {
    if (-not (Test-Path $pythonExe)) {
        # System Python first: the one uv installs under %AppData% is virtualized by the Claude desktop app.
        $systemPython = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python314\python.exe'
        if (Test-Path $systemPython) { uv venv --quiet --python $systemPython $env:UV_PROJECT_ENVIRONMENT }
        else { uv venv --quiet --python 3.14 $env:UV_PROJECT_ENVIRONMENT }
        if ($LASTEXITCODE) { throw 'uv venv failed.' }
    }
    uv sync --frozen --quiet
    if ($LASTEXITCODE) { throw 'uv sync failed.' }
    uv run --frozen ruff check src tests
    if ($LASTEXITCODE) { throw 'ruff reported errors.' }
    # Types: what the agents' editors report (zero errors expected).
    uv run --frozen python -m pyright --project $PSScriptRoot --pythonpath $pythonExe
    if ($LASTEXITCODE) { throw 'pyright reported type errors.' }
    uv run --frozen pytest -q
    if ($LASTEXITCODE) { throw 'Unit tests failed.' }
} finally { Pop-Location }
exit 0
