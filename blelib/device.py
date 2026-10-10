"""Serial link to one nRF52840 running the ble_trx firmware.

The firmware speaks one command per line and answers with JSON lines:
``{"ok":...}`` or ``{"err":...}`` acknowledge a command, ``{"ev":...}`` are
events (``pkt`` for every received packet, ``info``/``rx``/... for queries).
A reader thread splits the stream so that a command's acknowledgement can be
awaited while packets keep flowing.
"""

from __future__ import annotations

import json
import queue
import threading
import time

import serial
import serial.tools.list_ports

USB_VID, USB_PID = 0x1915, 0x520F


class DeviceError(RuntimeError):
    pass


def find_ports() -> list[serial.tools.list_ports_common.ListPortInfo]:
    """Serial ports whose USB IDs are the ble_trx firmware's (1915:520F)."""
    return [p for p in serial.tools.list_ports.comports()
            if p.vid == USB_VID and p.pid == USB_PID]


class Device:
    def __init__(self, port: str, label: str | None = None, baud: int = 115200):
        self.port = port
        self.label = label or port
        self.serial_number = ""
        for p in serial.tools.list_ports.comports():
            if p.device.upper() == port.upper():
                self.serial_number = p.serial_number or ""
        try:
            self._s = serial.Serial(port, baud, timeout=0.05)
        except serial.SerialException as e:
            raise DeviceError(f"{port}: {e}") from e
        self._s.dtr = True               # the CDC endpoint stays quiet without DTR
        self.events: queue.Queue = queue.Queue(maxsize=100000)   # pkt + other ev lines
        self._acks: queue.Queue = queue.Queue()
        self.text: list[str] = []        # non-JSON lines (help text)
        self.dropped_host = 0
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._reader, name=f"rd-{port}", daemon=True)
        time.sleep(0.3)
        self._s.reset_input_buffer()
        self._t.start()
        # Flush any half-typed line left in the firmware's input buffer.
        self._s.write(b"\r\n")

    # ---- reading -------------------------------------------------------
    def _reader(self) -> None:
        buf = b""
        while not self._stop.is_set():
            try:
                chunk = self._s.read(65536)
            except serial.SerialException:
                break
            if not chunk:
                continue
            host_t = time.time()
            buf += chunk
            *lines, buf = buf.split(b"\n")
            for raw in lines:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                if not line.startswith("{"):
                    self.text.append(line)
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    self.text.append(line)
                    continue
                msg["_host_time"] = host_t
                if "ok" in msg or "err" in msg:
                    self._acks.put(msg)
                else:
                    try:
                        self.events.put_nowait(msg)
                    except queue.Full:
                        self.dropped_host += 1

    # ---- commands ------------------------------------------------------
    def send(self, line: str) -> None:
        self._s.write((line.strip() + "\r\n").encode())

    def command(self, line: str, timeout: float = 2.0, check: bool = True) -> dict:
        """Send one command and return its {"ok"...}/{"err"...} reply."""
        while not self._acks.empty():
            self._acks.get_nowait()
        self.send(line)
        try:
            ack = self._acks.get(timeout=timeout)
        except queue.Empty:
            raise DeviceError(f"{self.label}: no reply to {line!r} "
                              "(is the ble_trx firmware on this port?)") from None
        if check and "err" in ack:
            raise DeviceError(f"{self.label}: {line!r} -> {ack['err']}")
        return ack

    def query(self, line: str, settle: float = 0.4) -> list[dict]:
        """Send a query (info, rx info, raw) and collect the non-packet events."""
        self.drain_events()
        self.send(line)
        time.sleep(settle)
        return [e for e in self.drain_events() if e.get("ev") != "pkt"]

    def exchange(self, line: str, settle: float = 0.4) -> list[str]:
        """Send any line; return every reply (ack, events, text) as strings."""
        self.drain_events()
        while not self._acks.empty():
            self._acks.get_nowait()
        self.text.clear()
        self.send(line)
        time.sleep(settle)
        out = []
        while not self._acks.empty():
            out.append(self._acks.get_nowait())
        out += [e for e in self.drain_events() if e.get("ev") != "pkt"]
        res = [json.dumps({k: v for k, v in m.items() if k != "_host_time"}) for m in out]
        return res + list(self.text) if (res or self.text) else ["(no reply)"]

    def help_text(self, settle: float = 0.5) -> list[str]:
        self.text.clear()
        self.send("help")
        time.sleep(settle)
        return list(self.text)

    def drain_events(self) -> list[dict]:
        out = []
        while True:
            try:
                out.append(self.events.get_nowait())
            except queue.Empty:
                return out

    def info(self) -> dict:
        """The firmware's `info` dump merged into one dict keyed by event name."""
        merged: dict = {}
        for e in self.query("info"):
            merged[e.get("ev", "?")] = {k: v for k, v in e.items()
                                        if k not in ("ev", "_host_time")}
        if "mode" not in merged or merged["mode"].get("fw") != "ble_trx":
            raise DeviceError(f"{self.label}: not running the ble_trx firmware "
                              f"(info -> {list(merged) or 'nothing'})")
        return merged

    def close(self) -> None:
        self._stop.set()
        self._t.join(timeout=1)
        try:
            self._s.close()
        except serial.SerialException:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
