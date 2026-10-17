param(
    [switch]$Clean,
    [switch]$Debug
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $root
try {
    if ($Clean -and (Test-Path build)) {
        Remove-Item -Recurse -Force build
    }

    $config = if ($Debug) { "Debug" } else { "Release" }
    $pybind11Dir = python -m pybind11 --cmakedir
    if (-not $pybind11Dir) {
        throw "pybind11 not found. Run: python -m pip install pybind11"
    }

    cmake -S . -B build -G "Visual Studio 17 2022" -A x64 -Dpybind11_DIR="$pybind11Dir"
    cmake --build build --config $config

    $pyd = Get-ChildItem -Path app -Filter "ncm_core*.pyd" -ErrorAction SilentlyContinue
    if (-not $pyd) {
        throw "Build finished but ncm_core*.pyd was not found in app/"
    }
    Write-Host "OK: $($pyd.FullName)" -ForegroundColor Green
    Write-Host "Run the app with: python run.py" -ForegroundColor Cyan
}
finally {
    Pop-Location
}
