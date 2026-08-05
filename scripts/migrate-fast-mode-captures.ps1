[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string]$Source,

    [Parameter(Mandatory = $false)]
    [string]$Destination
)

$ErrorActionPreference = 'Stop'

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if ([string]::IsNullOrWhiteSpace($Source)) {
    $Source = Join-Path $repositoryRoot 'captures\local\fast-mode'
}
if ([string]::IsNullOrWhiteSpace($Destination)) {
    $yahooRoot = Split-Path (Split-Path $repositoryRoot -Parent) -Parent
    $Destination = Join-Path $yahooRoot 'Captures\fast-mode'
}

$sourcePath = [System.IO.Path]::GetFullPath($Source)
$destinationPath = [System.IO.Path]::GetFullPath($Destination)
$repositoryPath = [System.IO.Path]::GetFullPath($repositoryRoot).TrimEnd('\')

if ($destinationPath.Equals($repositoryPath, [System.StringComparison]::OrdinalIgnoreCase) -or
    $destinationPath.StartsWith($repositoryPath + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Destination must be outside the synchronized repository: $destinationPath"
}

if (-not (Test-Path -LiteralPath $sourcePath -PathType Container)) {
    throw "Source capture directory does not exist: $sourcePath"
}

New-Item -ItemType Directory -Force -Path $destinationPath | Out-Null

$sourceFiles = @(Get-ChildItem -LiteralPath $sourcePath -File -Recurse -Force)
if ($sourceFiles.Count -eq 0) {
    Write-Host "No files found under: $sourcePath"
    exit 0
}

Write-Host "Copying $($sourceFiles.Count) files"
Write-Host "From: $sourcePath"
Write-Host "To  : $destinationPath"

foreach ($sourceFile in $sourceFiles) {
    $relativePath = $sourceFile.FullName.Substring($sourcePath.Length).TrimStart('\')
    $destinationFile = Join-Path $destinationPath $relativePath
    $destinationDirectory = Split-Path $destinationFile -Parent
    New-Item -ItemType Directory -Force -Path $destinationDirectory | Out-Null
    Copy-Item -LiteralPath $sourceFile.FullName -Destination $destinationFile -Force
}

$destinationFiles = @(Get-ChildItem -LiteralPath $destinationPath -File -Recurse -Force)
$verifiedCount = 0
$verifiedBytes = [int64]0

foreach ($sourceFile in $sourceFiles) {
    $relativePath = $sourceFile.FullName.Substring($sourcePath.Length).TrimStart('\')
    $destinationFile = Join-Path $destinationPath $relativePath
    if (-not (Test-Path -LiteralPath $destinationFile -PathType Leaf)) {
        throw "Verification failed; destination file is missing: $relativePath"
    }
    $destinationInfo = Get-Item -LiteralPath $destinationFile
    if ($sourceFile.Length -ne $destinationInfo.Length) {
        throw "Verification failed; file size differs: $relativePath"
    }
    $sourceHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $sourceFile.FullName).Hash
    $destinationHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $destinationFile).Hash
    if ($sourceHash -ne $destinationHash) {
        throw "Verification failed; SHA-256 differs: $relativePath"
    }
    $verifiedCount++
    $verifiedBytes += $sourceFile.Length
}

Write-Host ""
Write-Host "COPY AND VERIFY PASSED"
Write-Host "Verified files: $verifiedCount"
Write-Host "Verified bytes: $verifiedBytes"
Write-Host "Source retained: $sourcePath"
Write-Host "Destination   : $destinationPath"
Write-Host ""
Write-Host "Nothing was deleted. Remove the old source only after reviewing the external copy."
