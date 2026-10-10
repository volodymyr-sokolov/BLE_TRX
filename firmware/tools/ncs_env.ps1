# Sets up the nRF Connect SDK v3.1.1 build environment (toolchain c1a76fddb2).
$TC   = "C:\ncs\toolchains\c1a76fddb2"
$NCS  = "C:\ncs\v3.1.1"
$paths = @(
  $TC,
  "$TC\mingw64\bin", "$TC\bin", "$TC\opt\bin", "$TC\opt\bin\Scripts",
  "$TC\opt\nanopb\generator-bin", "$TC\nrfutil\bin",
  "$TC\opt\zephyr-sdk\arm-zephyr-eabi\bin", "$TC\opt\zephyr-sdk\riscv64-zephyr-elf\bin"
)
$env:PATH = ($paths -join ";") + ";" + $env:PATH
$env:PYTHONPATH = "$TC\opt\bin;$TC\opt\bin\Lib;$TC\opt\bin\Lib\site-packages"
$env:NRFUTIL_HOME = "$TC\nrfutil\home"
$env:ZEPHYR_TOOLCHAIN_VARIANT = "zephyr"
$env:ZEPHYR_SDK_INSTALL_DIR = "$TC\opt\zephyr-sdk"
$env:ZEPHYR_BASE = "$NCS\zephyr"
