$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

if (-not (Test-Path "assets\kralyavuz_icon.ico")) {
    throw "assets\kralyavuz_icon.ico bulunamadı."
}

$venvPython = Join-Path (Get-Location) ".venv\Scripts\python.exe"
$python = if (Test-Path -LiteralPath $venvPython) { $venvPython } else { "py" }

& $python -c "import PyInstaller, requests"
if ($LASTEXITCODE -ne 0) {
    throw "Build bağımlılıkları eksik. Önce requirements-build.txt dosyasını kurun."
}

& $python -m PyInstaller --noconfirm --clean KraLYavuz.spec
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build başarısız oldu (çıkış kodu: $LASTEXITCODE)."
}

Write-Host "Hazır uygulama: dist\KraLYavuz\KraLYavuz.exe"
