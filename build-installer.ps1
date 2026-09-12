param(
    [string]$IsccPath = $env:SCLEANER_ISCC
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonExecutable = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExecutable)) {
    throw 'Create .venv and install requirements-build.txt first. See README.md.'
}
foreach ($requiredFile in @('dist\SCleaner\SCleaner.exe', 'dist\SCleaner\SCleaner-source.zip', 'artifacts\SCleaner.ico')) {
    if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot $requiredFile))) {
        throw 'Build and package the application first: .\build.ps1 -PortableOnly'
    }
}
if (-not $IsccPath) {
    $compilerCommand = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($compilerCommand) { $IsccPath = $compilerCommand.Source }
}
if (-not $IsccPath) {
    $compilerCandidates = @(
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
        (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
        (Join-Path $PSScriptRoot 'build\tools\inno\ISCC.exe')
    )
    $IsccPath = $compilerCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if (-not $IsccPath -or -not (Test-Path -LiteralPath $IsccPath)) {
    throw 'Inno Setup 6.3+ is required. Install it or pass -IsccPath to ISCC.exe. See README.md.'
}
$metadataJson = & $pythonExecutable -c 'import json, scleaner; print(json.dumps([scleaner.__version__, scleaner.__copyright_holder__]))'
if ($LASTEXITCODE -ne 0) { throw 'Cannot read application metadata.' }
$metadata = $metadataJson | ConvertFrom-Json
$releaseVersion = $metadata[0]
$builtVersion = (Get-Item -LiteralPath 'dist\SCleaner\SCleaner.exe').VersionInfo.ProductVersion
if ($builtVersion -ne $releaseVersion) { throw 'Executable version differs from sources. Run build.ps1 again.' }
& $IsccPath "/DAppVersion=$releaseVersion" "/DAppPublisher=$($metadata[1])" 'installer\SCleaner.iss'
if ($LASTEXITCODE -ne 0) { throw 'Installer compilation failed.' }
$setupFile = Join-Path $PSScriptRoot "dist\SCleaner-$releaseVersion-win64-Setup.exe"
$digest = (Get-FileHash -LiteralPath $setupFile -Algorithm SHA256).Hash.ToLowerInvariant()
$checksumFile = [IO.Path]::ChangeExtension($setupFile, '.sha256')
[IO.File]::WriteAllText($checksumFile, "$digest  $([IO.Path]::GetFileName($setupFile))`n", [Text.Encoding]::ASCII)
Write-Output "Installer: $setupFile"
