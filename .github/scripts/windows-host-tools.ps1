$ErrorActionPreference = "Stop"
$dest = Join-Path $env:RUNNER_TEMP "host-tools"
New-Item -ItemType Directory -Force -Path $dest | Out-Null
$ageZip = Join-Path $env:RUNNER_TEMP "age.zip"
if ($env:AGE_VERSION -eq "v1.3.1") {
    $ageSha256 = "c56e8ce22f7e80cb85ad946cc82d198767b056366201d3e1a2b93d865be38154"
} else {
    throw "no pinned age checksum for $env:AGE_VERSION/windows-amd64"
}
if ($env:SOPS_VERSION -eq "v3.13.3") {
    $sopsSha256 = "a4a9a398858fe8b2ef72d9686d930bf7c5cece9be74ad83ac3b53cfdd70e6b1c"
} else {
    throw "no pinned sops checksum for $env:SOPS_VERSION/windows-amd64"
}
curl.exe -fsSL -o $ageZip "https://github.com/FiloSottile/age/releases/download/$env:AGE_VERSION/age-$env:AGE_VERSION-windows-amd64.zip"
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $ageZip).Hash -ne $ageSha256) {
    throw "age checksum mismatch"
}
tar -xf $ageZip -C $env:RUNNER_TEMP
Get-ChildItem -Recurse (Join-Path $env:RUNNER_TEMP "age") -File |
    Where-Object { $_.Name -in @("age.exe", "age-keygen.exe") } |
    ForEach-Object { Copy-Item $_.FullName -Destination (Join-Path $dest $_.Name) }
$sopsPath = Join-Path $dest "sops.exe"
curl.exe -fsSL -o $sopsPath "https://github.com/getsops/sops/releases/download/$env:SOPS_VERSION/sops-$env:SOPS_VERSION.amd64.exe"
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $sopsPath).Hash -ne $sopsSha256) {
    throw "sops checksum mismatch"
}
Add-Content -Path $env:GITHUB_PATH -Value $dest
& (Join-Path $dest "age.exe") --version
& (Join-Path $dest "sops.exe") --version
