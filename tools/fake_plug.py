#!/usr/bin/env python3
"""Simulated FS2PG plug for testing the integration without hardware.

Connects to the integration's server, logs in and behaves like the real plug
as far as the protocol is understood (see docs/protocol.md).

    python3 tools/fake_plug.py --host 127.0.0.1 --port 23778 --toggle-every 15
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json

DEVICE_ID = bytes.fromhex("a1b2c3d4")
KEY = b"0123456789abcdef"
MAC = "F0FE6B000001"


def frame(frame_type: int, payload: bytes = b"") -> bytes:
    return bytes([frame_type]) + len(payload).to_bytes(4, "big") + payload


class FakePlug:
    def __init__(self, writer: asyncio.StreamWriter) -> None:
        self.writer = writer
        self.seq = 0
        self.relay = False
        self.values = {
            "2.1.1.4.1": False,
            "2.1.1.4.2": "0.00",
            "2.1.1.4.3": "0.00",
            "2.1.1.4.4": "235.10",
            "2.1.1.4.5": True,
            "2.1.1.4.7": "0.000",
            "2.1.1.4.253": "Fake plug",
        }
        self.wh = 0  # energy used in the current period, whole Wh
        self.shown = 0  # what the counter shows: updated only every quarter period
        self.button_sn = 5000  # real plug used SN 5002 for a button press

    def energy(self, reply: bool) -> str:
        # Like the real plug: telemetry is kWh, a get reply is 10 times higher.
        wh = self.shown
        return f"{wh / 100:.2f}" if reply else f"{wh / 1000:.3f}"

    def send_data(self, obj: dict, crlf: bool = True) -> None:
        self.seq += 1
        body = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode()
        if crlf:
            body += b"\r\n"
        payload = (
            DEVICE_ID
            + self.seq.to_bytes(2, "big")
            + b"\x00\xaa"
            + len(body).to_bytes(2, "big")
            + body
            + b"\xff"
        )
        self.writer.write(frame(0x80, payload))

    def send_set_reply(self, pl: dict, sn: int) -> None:
        self.send_data(
            {"PL": pl, "TEID": "999", "RC": 1, "SN": sn, "CID": 30012, "SID": ""}
        )

    def send_notify(self, sid: str) -> None:
        body = json.dumps(
            {
                "message": {
                    "onoff": self.values["2.1.1.4.1"],
                    # the real plug reports UTC here
                    "time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                    "SID": sid,
                },
                "plugMac": MAC,
            },
            separators=(",", ":"),
        ).encode()
        payload = DEVICE_ID + bytes.fromhex("01010e0090") + bytes([len(body)]) + body
        self.writer.write(frame(0x40, payload))

    def step(self) -> None:
        """Every quarter period the counter catches up (the real plug: 5 min)."""
        self.shown = self.wh

    def end_period(self) -> None:
        """Report the period's energy, then reset (the real plug: every 20 min)."""
        body = json.dumps(
            {
                "message": {
                    "electricity": f"{self.wh / 1000:.3f}",
                    "time": datetime.now(timezone.utc).strftime("%Y-%m-%d 23:59:59"),
                },
                "plugMac": MAC,
            },
            separators=(",", ":"),
        ).encode()
        payload = DEVICE_ID + bytes.fromhex("01010e0090") + bytes([len(body)]) + body
        self.writer.write(frame(0x40, payload))
        print("period end:", self.wh, "Wh")
        self.wh = self.shown = 0
        self.telemetry()

    def set_relay(self, on: bool) -> None:
        self.values["2.1.1.4.1"] = on
        self.values["2.1.1.4.2"] = "40.00" if on else "0.00"
        self.values["2.1.1.4.3"] = "0.17" if on else "0.00"
        print("relay ->", "ON" if on else "OFF")

    def handle_request(self, payload: bytes) -> None:
        self.writer.write(frame(0x78, payload[:7]))
        req = json.loads(payload[7:].decode())
        print("request:", req)
        if req["CID"] == 30011:
            for oid, value in req["PL"].items():
                if oid == "2.1.1.4.1":
                    self.set_relay(bool(value))
                else:
                    self.values[oid] = value
            self.send_set_reply(req["PL"], req.get("SN"))
            if "2.1.1.4.1" in req["PL"]:  # renames and zone sets send no 0x40
                self.send_notify(req.get("SID", ""))
        elif req["CID"] == 30021:
            values = {o: self.values.get(o) for o in req["PL"]["OID"]}
            if "2.1.1.4.7" in values:
                values["2.1.1.4.7"] = self.energy(reply=True)
            self.send_data(
                {
                    "PL": values,
                    "TEID": req.get("TEID"),
                    "RC": 1,
                    "SN": req.get("SN"),
                    "CID": 30022,
                    "SID": "",
                }
            )

    def press_button(self) -> None:
        """What the real plug sent on a button press (2026-11-16)."""
        self.set_relay(not self.values["2.1.1.4.1"])
        self.button_sn += 1
        self.send_set_reply({"2.1.1.4.1": self.values["2.1.1.4.1"]}, self.button_sn)
        self.send_notify("Manually")

    def telemetry(self) -> None:
        if self.values["2.1.1.4.1"]:
            self.wh += 1
        self.values["2.1.1.4.7"] = self.energy(reply=False)
        oids = ("2.1.1.4.2", "2.1.1.4.3", "2.1.1.4.4", "2.1.1.4.7")
        self.send_data(
            {
                "PL": {o: self.values[o] for o in oids},
                "RC": 1,
                "SN": None,
                "CID": 30022,
                "SID": None,
            },
            crlf=False,
        )


async def periodic(interval: float, func) -> None:
    while True:
        await asyncio.sleep(interval)
        func()


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=23778)
    parser.add_argument(
        "--toggle-every",
        type=float,
        default=0,
        help="simulate a press of the physical button every N seconds",
    )
    parser.add_argument(
        "--period",
        type=float,
        default=1200,
        help="energy counter period in seconds (the real plug: 1200)",
    )
    args = parser.parse_args()

    reader, writer = await asyncio.open_connection(args.host, args.port)
    plug = FakePlug(writer)
    login = b"\x02" + DEVICE_ID + len(KEY).to_bytes(2, "big") + KEY + b"\x00\x00\x3c"
    writer.write(frame(0x20, login))
    writer.write(frame(0xC0, b"\x0c"))

    button = plug.press_button

    tasks = [
        asyncio.create_task(periodic(20, lambda: writer.write(frame(0xD0)))),
        asyncio.create_task(periodic(30, plug.telemetry)),
    ]

    async def periods() -> None:
        while True:
            for _ in range(3):
                await asyncio.sleep(args.period / 4)
                plug.step()
            await asyncio.sleep(args.period / 4)
            plug.end_period()

    tasks.append(asyncio.create_task(periods()))
    if args.toggle_every:
        tasks.append(asyncio.create_task(periodic(args.toggle_every, button)))

    try:
        while True:
            header = await reader.readexactly(5)
            length = int.from_bytes(header[1:], "big")
            payload = await reader.readexactly(length) if length else b""
            if header[0] == 0x70:
                plug.handle_request(payload)
            elif header[0] == 0x28:
                print("logged in")
            elif header[0] == 0xC8:
                print("time reply:", payload.hex())
            await writer.drain()
    except (asyncio.IncompleteReadError, ConnectionError):
        print("server closed the connection")
    finally:
        for task in tasks:
            task.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
