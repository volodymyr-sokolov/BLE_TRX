<#
  Build the ble_trx firmware with the nRF Connect SDK v3.1.1.

  Usage (from anywhere):
    .\firmware\tools\build.ps1                 # Adafruit Feather nRF52840 Express
    .\firmware\tools\build.ps1 -Board nicenano # nice!nano v2 (LED on P0.15)
    .\firmware\tools\build.ps1 -Pristine       # clean rebuild
    .\firmware\tools\build.ps1 -Release        # build BOTH boards and refresh firmware_bin\

  Output: firmware\build_<board>\firmware\zephyr\zephyr.hex  (linked at 0x26000)
  -Release also copies each image to firmware_bin\ble_trx_<board>.{hex,uf2}
  and rewrites firmware_bin\SHA256SUMS.txt.
#>
param(
  [ValidateSet("feather", "nicenano")][string]$Board = "feather",
  [switch]$Pristine,
  [switch]$Release
)

. "$PSScriptRoot\ncs_env.ps1"
$fw   = Split-Path -Parent $PSScriptRoot
$repo = Split-Path -Parent $fw
$p    = if ($Pristine) { "always" } else { "auto" }
$boards = if ($Release) { @("feather", "nicenano") } else { @($Board) }

Push-Location $fw
try {
  foreach ($b in $boards) {
    $extra = @()
    if ($b -eq "nicenano") {
      $extra = @("--", "-DEXTRA_DTC_OVERLAY_FILE=boards/nice_nano_v2.overlay")
    }
    west build -b adafruit_feather_nrf52840/nrf52840/uf2 -p $p -d "build_$b" . @extra
    if ($LASTEXITCODE -ne 0) { throw "build failed ($b)" }
    Write-Host "built firmware\build_$b\firmware\zephyr\zephyr.hex"
  }
} finally {
  Pop-Location
}

if ($Release) {
  $bin = Join-Path $repo "firmware_bin"
  New-Item -ItemType Directory -Force $bin | Out-Null
  foreach ($b in $boards) {
    foreach ($ext in "hex", "uf2") {
      Copy-Item (Join-Path $fw "build_$b\firmware\zephyr\zephyr.$ext") (Join-Path $bin "ble_trx_$b.$ext") -Force
    }
  }
  # sha256sum-compatible: "<hash>  <file>"
  $lines = Get-ChildItem $bin -File | Where-Object { $_.Extension -in ".hex", ".uf2" } | Sort-Object Name |
           ForEach-Object { "{0}  {1}" -f (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower(), $_.Name }
  [System.IO.File]::WriteAllText((Join-Path $bin "SHA256SUMS.txt"), (($lines -join "`n") + "`n"))
  Write-Host "refreshed firmware_bin\ (images + SHA256SUMS.txt)"
}
