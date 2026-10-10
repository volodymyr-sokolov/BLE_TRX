cmake_minimum_required(VERSION 3.20.0)
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(ble_trx)

target_sources(app PRIVATE
  src/main.c
  src/hostif.c
  src/adv.c
  src/rx.c
  src/cmd.c
  src/store.c
)
