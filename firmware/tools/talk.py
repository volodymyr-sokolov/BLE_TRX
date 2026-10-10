"""Quick one-shot serial exchange with a board, for bring-up and debugging.

Usage: python firmware/tools/talk.py COM7 [seconds] [command ...]
  e.g. python firmware/tools/talk.py COM7 2 info "rx info"

Sends each command, then prints everything the board says for `seconds`.
ble_tx.py --cmd and --shell do the same through the ble_tx tool.
"""
import sys, time, serial

port = sys.argv[1]
secs = float(sys.argv[2]) if len(sys.argv) > 2 else 3.0
cmds = sys.argv[3:]

s = serial.Serial(port, 115200, timeout=0.2)
s.dtr = True
time.sleep(0.4)
s.reset_input_buffer()
s.write(b"\r\n")                  # flush a half-typed line in the firmware
for c in cmds:
    s.write((c + "\n").encode())
    time.sleep(0.15)
t0 = time.time()
buf = b""
while time.time() - t0 < secs:
    d = s.read(8192)
    if d:
        buf += d
s.close()
sys.stdout.write(buf.decode("utf-8", "replace"))
