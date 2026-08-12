$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPython = Join-Path $repoRoot ".build\venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $venvPython)) {
    python -m venv (Join-Path $repoRoot ".build\venv")
}

& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r (Join-Path $repoRoot "requirements-build.txt")
& $venvPython -m PyInstaller `
    --noconfirm `
    --clean `
    --distpath (Join-Path $repoRoot "portable") `
    --workpath (Join-Path $repoRoot ".build\pyinstaller") `
    (Join-Path $repoRoot "MusicOrganizer.spec")

$exe = Join-Path $repoRoot "portable\AAA音乐信息与封面一键整理.exe"
$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $exe).Hash
"$hash  AAA音乐信息与封面一键整理.exe" | Set-Content `
    -LiteralPath (Join-Path $repoRoot "portable\SHA256SUMS.txt") `
    -Encoding ASCII

Write-Host "构建完成：$exe"

