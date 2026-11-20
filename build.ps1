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
# Work files go to TEMP so build\ holds only SCleaner.exe.
& $pythonExecutable -m PyInstaller --noconfirm --distpath build --workpath (Join-Path $env:TEMP 'SCleaner-pyinstaller') SCleaner.spec
if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
& $pythonExecutable tools\verify_executable.py
if ($LASTEXITCODE -ne 0) { throw 'Packaged executable verification failed.' }
Write-Output 'Ready: build\SCleaner.exe'
