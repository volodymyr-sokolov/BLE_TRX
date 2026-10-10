<#
  Flash ble_trx onto an nRF52840 over the Adafruit nRF52 serial DFU bootloader.

  These boards expose DFU only over CDC (no UF2 mass-storage volume, no SWD
  probe), so the path is: zephyr.hex -> Nordic legacy DFU package ->
  adafruit-nrfutil over the bootloader's serial port.

  Works on a board running ble_trx (it accepts a `dfu` command), on one running
  any other firmware that honours the 1200-baud touch (Arduino, CircuitPython,
  ...), and on a board already sitting in its bootloader.

  Which image:  -Prebuilt feather|nicenano  the compiled image in firmware_bin\
                -Hex <file>                 any .hex
                (neither)                   your own build, firmware\<Build>\

  Usage:
    .\firmware\tools\flash.ps1 -Port COM5 -Prebuilt feather      # no SDK needed
    .\firmware\tools\flash.ps1 -Port COM5 -Prebuilt nicenano
    .\firmware\tools\flash.ps1 -Port COM5                        # firmware\build_feather
    .\firmware\tools\flash.ps1 -Port COM5 -Build build_nicenano
    .\firmware\tools\flash.ps1 -Port COM5 -Hex firmware_bin\ble_trx_feather.hex
#>
param(
  [Parameter(Mandatory=$true)][string]$Port,
  [string]$Build = "build_feather",
  [ValidateSet("", "feather", "nicenano")][string]$Prebuilt = "",
  [string]$Hex = "",
  [string]$Python = "python"
)

# "python" after ncs_env.ps1 is the NCS toolchain's own interpreter, which has
# no adafruit-nrfutil; use the first python on PATH outside the toolchain.
if ($Python -eq "python") {
  $cand = Get-Command python -All -ErrorAction SilentlyContinue |
          Where-Object { $_.Source -notmatch '\\ncs\\toolchains\\' } | Select-Object -First 1
  if ($cand) { $Python = $cand.Source }
}

$root = Split-Path -Parent $PSScriptRoot
$hexPath = if ($Hex) { (Resolve-Path $Hex).Path }
           elseif ($Prebuilt) { Join-Path (Split-Path -Parent $root) "firmware_bin\ble_trx_$Prebuilt.hex" }
           else { Join-Path $root "$Build\firmware\zephyr\zephyr.hex" }
$pkg = Join-Path ([System.IO.Path]::GetTempPath()) "ble_trx_dfu_$PID.zip"

if (-not (Test-Path $hexPath)) { throw "no firmware at $hexPath -- run firmware\tools\build.ps1 (or use -Prebuilt)" }

# The NCS toolchain leaves PYTHONPATH pointed at its bundled 3.12 site-packages,
# which makes a newer system Python load 3.12 extension modules and die on import.
$savedPP = $env:PYTHONPATH
$env:PYTHONPATH = ""

Write-Host "packaging $hexPath"
& $Python -m nordicsemi dfu genpkg --dev-type 0x0052 --application $hexPath $pkg | Out-Null
if (-not (Test-Path $pkg)) { throw "genpkg failed (pip install adafruit-nrfutil)" }

# Our firmware takes a `dfu` command; other applications and the bootloader
# itself take the 1200-baud touch.  Try both, in that order.
$before = [System.IO.Ports.SerialPort]::GetPortNames()
Write-Host "resetting $Port into the bootloader"
try {
  $p = New-Object System.IO.Ports.SerialPort($Port, 115200)
  $p.ReadTimeout = 300
  $p.DtrEnable = $true
  $p.Open()
  # The leading newline flushes any half line left in the firmware's input.
  $p.Write("`r`ndfu`r`n")
  Start-Sleep -Milliseconds 400
  $p.Close()
} catch { Write-Host "  (dfu command not accepted: $($_.Exception.Message))" }

# The bootloader enumerates on a different port number than the application,
# and how long that takes varies from board to board (2-10 s seen), so poll
# for it rather than sleeping a fixed time.
function Wait-NewPort([int]$Seconds) {
  $deadline = (Get-Date).AddSeconds($Seconds)
  while ((Get-Date) -lt $deadline) {
    $new = @([System.IO.Ports.SerialPort]::GetPortNames() | Where-Object { $before -notcontains $_ })
    if ($new.Count -gt 0) { Start-Sleep -Milliseconds 800; return $new[0] }
    Start-Sleep -Milliseconds 200
  }
  return $null
}

$dfuPort = Wait-NewPort 4
if (-not $dfuPort -and ([System.IO.Ports.SerialPort]::GetPortNames() -contains $Port)) {
  try {
    $p = New-Object System.IO.Ports.SerialPort($Port, 1200)
    $p.DtrEnable = $false
    $p.Open(); Start-Sleep -Milliseconds 200; $p.Close()
  } catch { Write-Host "  (1200-baud touch refused: $($_.Exception.Message))" }
}
if (-not $dfuPort) { $dfuPort = Wait-NewPort 12 }
if (-not $dfuPort) {
  # Nothing new: the port given is the bootloader itself (or nothing worked).
  if ([System.IO.Ports.SerialPort]::GetPortNames() -contains $Port) { $dfuPort = $Port }
  else { throw "no DFU port appeared; double-tap RESET on the board and rerun with -Port <bootloader port>" }
}
Write-Host "DFU port: $dfuPort"

# adafruit-nrfutil can report "Failed to upgrade target" and still exit 0,
# so judge success by its output as well as by the exit code.
$out = & $Python -m nordicsemi dfu serial --package $pkg -p $dfuPort -b 115200 --singlebank --touch 0 2>&1 | Out-String
Write-Host $out
$rc = $LASTEXITCODE
if ($rc -eq 0 -and $out -notmatch "Device programmed") { $rc = 1 }
$env:PYTHONPATH = $savedPP
Remove-Item $pkg -ErrorAction SilentlyContinue
if ($rc -ne 0) { throw "DFU failed with exit code $rc" }
Write-Host "flashed $hexPath via $dfuPort"
