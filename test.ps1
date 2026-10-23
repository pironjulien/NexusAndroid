# Full offline check, no phone needed: lint, types, unit tests. Exit code 0 when everything passes.
# Works from any copy of the repository: each checkout gets its own test environment under ~\.nexus\nexus-android
# (outside the sources, never the environment of the installed server); nothing is written to the source tree.
# Real phone test: install.ps1 -Live. -Full: also the real scenarios (tests/scenarios.py, graded by the phone's state;
# phone reachable and unlocked; every changed setting is restored).
# -Python 3.12 (or 3.13...): run everything under that Python version, in its own test environment (CI matrix).
# Python and locked dependencies must already be installed or cached; see README, Development, for preparation.
param([string]$Python = '', [switch]$Full)
$ErrorActionPreference = 'Stop'
$nodeExe = Get-Command node -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source
if (-not $nodeExe) { throw 'Node.js must already be installed for Pyright; test.ps1 never downloads it (README, Development).' }
$state = Join-Path $HOME '.nexus\nexus-android'
$key = [BitConverter]::ToString([Security.Cryptography.SHA256]::HashData(
        [Text.Encoding]::UTF8.GetBytes($PSScriptRoot.ToLowerInvariant()))).Replace('-', '').Substring(0, 8)
$env:UV_PROJECT_ENVIRONMENT = Join-Path $state ("test-venv-$key" + $(if ($Python) { "-py$Python" } else { '' }))
if ($Python) { $env:UV_PYTHON = $Python }  # else .python-version
$env:UV_LINK_MODE = 'copy'
$env:UV_OFFLINE = '1'
$env:UV_PYTHON_DOWNLOADS = 'never'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONPYCACHEPREFIX = Join-Path $state 'cache\pycache'
$env:PYRIGHT_PYTHON_CACHE_DIR = Join-Path $state 'cache\pyright'
$pythonExe = Join-Path $env:UV_PROJECT_ENVIRONMENT 'Scripts\python.exe'
Push-Location $PSScriptRoot
try {
    if (-not (Test-Path $pythonExe)) {
        # System Python first: the one uv installs under %AppData% is virtualized by the Claude desktop app.
        $systemPython = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python314\python.exe'
        if ($Python) { uv venv --quiet --python $Python $env:UV_PROJECT_ENVIRONMENT }
        elseif (Test-Path $systemPython) { uv venv --quiet --python $systemPython $env:UV_PROJECT_ENVIRONMENT }
        else { uv venv --quiet $env:UV_PROJECT_ENVIRONMENT }  # .python-version, else any Python allowed by pyproject
        if ($LASTEXITCODE) { throw 'Could not create the test environment offline. Prepare Python locally first (README, Development); test.ps1 never downloads it.' }
    }
    uv sync --frozen --quiet
    if ($LASTEXITCODE) { throw 'Offline dependency sync failed. Prepare the locked packages and build dependencies in the uv cache first (README, Development); test.ps1 never downloads them.' }
    & $pythonExe -m ruff check src tests
    if ($LASTEXITCODE) { throw 'ruff reported errors.' }
    # Run the wheel's bundled Pyright with the existing Node: its Python wrapper can fetch updates or install Node.
    $pyright = & $pythonExe -c 'from importlib.resources import files; print(files("pyright").joinpath("dist", "index.js"))'
    if ($LASTEXITCODE -or -not (Test-Path -LiteralPath $pyright)) { throw 'Bundled Pyright is missing. Prepare the locked dependencies first (README, Development).' }
    & $nodeExe $pyright --project $PSScriptRoot --pythonpath $pythonExe
    if ($LASTEXITCODE) { throw 'pyright reported type errors.' }
    & $pythonExe -m pytest -q
    if ($LASTEXITCODE) { throw 'Unit tests failed.' }
    if ($Full) {
        & $pythonExe tests/scenarios.py
        if ($LASTEXITCODE) { throw 'Real scenarios failed (or phone locked).' }
    }
} finally { Pop-Location }
exit 0
