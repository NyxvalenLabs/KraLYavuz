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

& $python -m PyInstaller --noconfirm --clean KraLYavuzUpdater.spec
if ($LASTEXITCODE -ne 0) {
    throw "Updater build başarısız oldu (çıkış kodu: $LASTEXITCODE)."
}

$updaterSource = "dist\KraLYavuzUpdater.exe"
$updaterTarget = "dist\KraLYavuz\KraLYavuzUpdater.exe"
if (-not (Test-Path -LiteralPath $updaterSource)) {
    throw "KraLYavuzUpdater.exe üretilemedi."
}
Move-Item -LiteralPath $updaterSource -Destination $updaterTarget -Force

if (-not (Test-Path -LiteralPath "dist\KraLYavuz\KraLYavuz.exe")) {
    throw "KraLYavuz.exe dağıtım klasöründe bulunamadı."
}
if (-not (Test-Path -LiteralPath "dist\KraLYavuz\_internal")) {
    throw "KraLYavuz _internal klasörü bulunamadı."
}

Write-Host "Hazır uygulama: dist\KraLYavuz\KraLYavuz.exe"
Write-Host "Hazır updater: dist\KraLYavuz\KraLYavuzUpdater.exe"
