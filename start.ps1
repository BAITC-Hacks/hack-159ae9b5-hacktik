[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8000,
    [string]$ListenAddress = '127.0.0.1',
    [switch]$PrepareOnly
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

function Test-UsablePython {
    param([string]$Executable)
    if (-not $Executable -or -not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
        return $false
    }
    try {
        & $Executable -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>$null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

$venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-UsablePython $venvPython)) {
    if (Test-Path -LiteralPath (Join-Path $PSScriptRoot '.venv')) {
        throw 'Existing .venv is unusable or older than Python 3.11. Rename that folder, then run start.ps1 again.'
    }
    $basePython = $null
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) {
        try {
            $candidate = & $launcher.Source -3 -c 'import sys; print(sys.executable)' 2>$null
            if ($LASTEXITCODE -eq 0 -and (Test-UsablePython ([string]$candidate))) {
                $basePython = [string]$candidate
            }
        } catch {
            # A Windows launcher may exist without a registered interpreter.
        }
    }
    if (-not $basePython) {
        foreach ($name in @('python.exe', 'python3.exe')) {
            $command = Get-Command $name -ErrorAction SilentlyContinue
            if ($command -and $command.Source -notlike '*\WindowsApps\*' -and (Test-UsablePython $command.Source)) {
                $basePython = $command.Source
                break
            }
        }
    }
    if (-not $basePython) {
        $profileDirectory = [Environment]::GetFolderPath('UserProfile')
        $codexPython = Join-Path $profileDirectory '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
        if (Test-UsablePython $codexPython) {
            $basePython = $codexPython
        }
    }
    if (-not $basePython) {
        throw 'Install Python 3.11 or newer from python.org, enable its launcher, then run start.ps1 again.'
    }
    Write-Host 'Creating a local Python environment...'
    & $basePython -m venv (Join-Path $PSScriptRoot '.venv')
    if ($LASTEXITCODE -ne 0 -or -not (Test-UsablePython $venvPython)) {
        throw 'Could not create .venv. Install the standard Python 3.11+ distribution and retry.'
    }
}

$requirements = Join-Path $PSScriptRoot 'requirements.txt'
$fingerprint = (Get-FileHash -LiteralPath $requirements -Algorithm SHA256).Hash
$stampPath = Join-Path $PSScriptRoot '.venv\.ekt-requirements-sha256'
$installedFingerprint = if (Test-Path -LiteralPath $stampPath) { (Get-Content -LiteralPath $stampPath -Raw).Trim() } else { '' }
$dependenciesReady = $false
try {
    & $venvPython -c 'import fastapi, uvicorn, openai, pydantic, httpx, python_multipart, openpyxl, dotenv' 2>$null
    $dependenciesReady = ($LASTEXITCODE -eq 0)
} catch {
    $dependenciesReady = $false
}
if ($installedFingerprint -ne $fingerprint -or -not $dependenciesReady) {
    Write-Host 'Installing dependencies into .venv (internet required on first run)...'
    & $venvPython -m pip install -r $requirements
    if ($LASTEXITCODE -ne 0) {
        throw 'Dependency installation failed. Check the internet connection and run start.ps1 again.'
    }
    Set-Content -LiteralPath $stampPath -Value $fingerprint -Encoding ASCII
}

$environmentPath = Join-Path $PSScriptRoot '.env'
if (-not (Test-Path -LiteralPath $environmentPath)) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot '.env.example') -Destination $environmentPath
    Write-Host 'Created .env. Set OPENAI_API_KEY there for conversational AI; GEOAPIFY_API_KEY enables address search.'
}
if ($PrepareOnly) {
    Write-Host 'Project environment is ready.'
    exit 0
}
Write-Host "EKT Space: http://127.0.0.1:$Port"
Write-Host "HTTP API:  http://127.0.0.1:$Port/docs"
Write-Host 'Press Ctrl+C to stop.'
& $venvPython -m uvicorn app.main:app --host $ListenAddress --port $Port
exit $LASTEXITCODE
