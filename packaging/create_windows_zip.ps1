param(
    [string]$OutputPath = "dist\KraLYavuz_Windows.zip"
)

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$distribution = "dist\KraLYavuz"
$requiredPaths = @(
    "$distribution\KraLYavuz.exe",
    "$distribution\KraLYavuzUpdater.exe",
    "$distribution\_internal"
)

foreach ($requiredPath in $requiredPaths) {
    if (-not (Test-Path -LiteralPath $requiredPath)) {
        throw "Windows ZIP oluşturulamadı; eksik: $requiredPath"
    }
}

if (Test-Path -LiteralPath $OutputPath) {
    Remove-Item -LiteralPath $OutputPath -Force
}

Compress-Archive -Path $distribution -DestinationPath $OutputPath -CompressionLevel Optimal

Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [System.IO.Compression.ZipFile]::OpenRead((Resolve-Path $OutputPath))
try {
    $entryNames = @($archive.Entries | ForEach-Object { $_.FullName.Replace("\", "/") })
    if ($entryNames -notcontains "KraLYavuz/KraLYavuz.exe") {
        throw "ZIP içinde KraLYavuz/KraLYavuz.exe yok."
    }
    if ($entryNames -notcontains "KraLYavuz/KraLYavuzUpdater.exe") {
        throw "ZIP içinde KraLYavuz/KraLYavuzUpdater.exe yok."
    }
    if (-not ($entryNames | Where-Object { $_ -like "KraLYavuz/_internal/*" })) {
        throw "ZIP içinde KraLYavuz/_internal içeriği yok."
    }
}
finally {
    $archive.Dispose()
}

Write-Host "GitHub Release asset hazır: $OutputPath"
