# Compiled firmware (ble_trx v3)

Ready-to-flash images of the [ble_trx firmware](../firmware/README.md).
You don't need the nRF Connect SDK to use them.

| File | Board | Format | Use with |
|---|---|---|---|
| `ble_trx_feather.hex` | Adafruit Feather nRF52840 Express, and other nRF52840 boards with the Adafruit bootloader | Intel HEX | serial DFU (`flash.ps1`, `adafruit-nrfutil`) |
| `ble_trx_feather.uf2` | same | UF2 | copy onto the board's UF2 drive, if its bootloader provides one |
| `ble_trx_nicenano.hex` | nice!nano v2 (status LED on P0.15) | Intel HEX | serial DFU |
| `ble_trx_nicenano.uf2` | same | UF2 | UF2 drive |
| `SHA256SUMS.txt` | | | checksums of the four images |

The two variants differ only in the status-LED pin. Both images:

- link at **0x26000**, behind the Adafruit nRF52 bootloader. **They are not for
  boards without that bootloader**: flashed at 0x0 with a probe, they would
  overwrite the MBR.
- were built with nRF Connect SDK v3.1.1 for
  `adafruit_feather_nrf52840/nrf52840/uf2`;
- report firmware version `ver 3` (`info` → `{"ev":"mode",…,"fw":"ble_trx","ver":3}`).

The Feather image is the one used for the
[hardware test report](../docs/TEST_REPORT.md) (14/14 passed). A fresh pristine
build of the same sources is byte-identical. The nice!nano image builds
cleanly but has not yet been run on a nice!nano.

## Flash

```powershell
pip install adafruit-nrfutil pyserial

# from the repository root
.\firmware\tools\flash.ps1 -Port COM7 -Prebuilt feather
.\firmware\tools\flash.ps1 -Port COM7 -Prebuilt nicenano
```

If the board is not yet running ble_trx and does not react to the
1200-baud touch, double-tap its RESET button. Then pass the new
(bootloader) COM port as `-Port`.

Without PowerShell (Windows, Linux or macOS):

1. Put the board in its bootloader. Send `dfu` to a board already running
   ble_trx (e.g. `python ble_tx/ble_tx.py --port COM7 --dfu`), or double-tap RESET.
2. Note the port it reappears on.
3. Run:

```sh
adafruit-nrfutil dfu genpkg --dev-type 0x0052 --application firmware_bin/ble_trx_feather.hex ble_trx.zip
adafruit-nrfutil dfu serial --package ble_trx.zip -p COM12 -b 115200 --singlebank --touch 0
#                                                  ^ bootloader port: /dev/ttyACM0 on Linux, /dev/cu.usbmodem* on macOS
```

Or, on boards that mount a UF2 drive after a RESET double-tap: copy the
`.uf2` file onto that drive.

After flashing, the board enumerates as USB **1915:520F**. Check it with:

```powershell
python ble_tx/ble_tx.py --list
python ble_tx/ble_tx.py --port COM7 --info
```

## Verify the download

```sh
sha256sum -c SHA256SUMS.txt                                         # Linux / macOS / Git Bash
```

```powershell
Get-FileHash firmware_bin\ble_trx_feather.hex -Algorithm SHA256      # Windows: compare with SHA256SUMS.txt
```

## Rebuilding this folder

```powershell
.\firmware\tools\build.ps1 -Release            # builds both boards, refreshes these files and SHA256SUMS.txt
.\firmware\tools\build.ps1 -Release -Pristine  # the same from a clean build directory
```
