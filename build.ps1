param(
    [switch]$PortableOnly,
    [string]$IsccPath = $env:SCLEANER_ISCC
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonExecutable = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExecutable)) {
    throw 'Create .venv and install requirements-build.txt first. See README.md.'
}
& $pythonExecutable tools\make_icon.py
if ($LASTEXITCODE -ne 0) { throw 'Icon generation failed.' }
& $pythonExecutable -m unittest discover -s tests
if ($LASTEXITCODE -ne 0) { throw 'Tests failed.' }
$buildTarget = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'dist\SCleaner'))
$expectedRoot = [IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\') + '\'
if (-not $buildTarget.StartsWith($expectedRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Build destination must stay inside the project.'
}
& $pythonExecutable -m PyInstaller --noconfirm SCleaner.spec
if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
& $pythonExecutable tools\verify_executable.py
if ($LASTEXITCODE -ne 0) { throw 'Packaged executable verification failed.' }
& $pythonExecutable tools\package_release.py
if ($LASTEXITCODE -ne 0) { throw 'Packaging failed.' }
if (-not $PortableOnly) {
    & (Join-Path $PSScriptRoot 'build-installer.ps1') -IsccPath $IsccPath
}
Write-Output 'Ready: dist\SCleaner\SCleaner.exe'
