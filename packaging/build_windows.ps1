$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

if (-not (Test-Path "assets\kralyavuz_icon.ico")) {
    throw "assets\kralyavuz_icon.ico bulunamadı."
}

py -m PyInstaller --noconfirm --clean KraLYavuz.spec

Write-Host "Hazır uygulama: dist\KraLYavuz\KraLYavuz.exe"
