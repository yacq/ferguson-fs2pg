"""Protocol tests for server.py. No Home Assistant needed.

    python3 -m unittest discover -s tests -v      (or: pytest tests)

The frames come from sanitized recordings of a real plug talking to the
vendor cloud: tests/fixtures/session.jsonl (a whole session) and
tests/fixtures/rename.jsonl (renames from the app, the time-zone set after
login, and an "electricity" notification).
"""

import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent

# Load server.py directly so the package __init__ (which imports Home
# Assistant) is not executed.
_spec = importlib.util.spec_from_file_location(
    "fs2pg_server", ROOT / "custom_components" / "ferguson_fs2pg" / "server.py"
)
server = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = server
_spec.loader.exec_module(server)

def _load(name: str) -> list[dict]:
    path = ROOT / "tests" / "fixtures" / name
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


FRAMES = _load("session.jsonl")
RENAME_FRAMES = _load("rename.jsonl")


def recorded(
    sender: str, frame_type: int, contains: bytes = b"", frames: list[dict] = FRAMES
) -> bytes:
    for item in frames:
        payload = bytes.fromhex(item["payload"])
        if (
            item["from"] == sender
            and int(item["type"], 16) == frame_type
            and contains in payload
        ):
            return payload
    raise LookupError((sender, frame_type, contains))


def with_json(frame_type: int, payload: bytes, old: bytes, new: bytes) -> bytes:
    """Replace `old` with `new` in a recorded frame's JSON, fixing its length."""
    length = server._json_length(frame_type, payload)
    end = server.JSON_OFFSET + length
    body = payload[server.JSON_OFFSET : end].replace(old, new, 1)
    if frame_type == 0x80:
        return payload[:8] + len(body).to_bytes(2, "big") + body + payload[end:]
    return payload[:9] + bytes([len(body)]) + body + payload[end:]


class ExtractJsonTest(unittest.TestCase):
    def test_every_recorded_frame_decodes(self):
        count = 0
        for item in FRAMES:
            frame_type = int(item["type"], 16)
            if item["from"] == "plug" and frame_type in (0x40, 0x80):
                payload = bytes.fromhex(item["payload"])
                self.assertIsInstance(server._extract_json(frame_type, payload), dict)
                count += 1
        self.assertGreater(count, 50)

    def test_length_byte_equal_to_open_brace(self):
        # A 123-byte JSON makes the length byte 0x7b == "{", which would
        # confuse a search for the first brace.
        telemetry = recorded("plug", 0x80, b"2.1.1.4.4")
        frame = with_json(0x80, telemetry, b'"0.000"', b'"0.00"')
        frame = with_json(0x80, frame, b'"0.00"', b'"0"')
        self.assertEqual(frame[8:10], b"\x00\x7b")
        obj = server._extract_json(0x80, frame)
        self.assertEqual(obj["PL"]["2.1.1.4.4"], "236.03")

        notify = recorded("plug", 0x40, b'"onoff":true')
        sid = b"some.user@example.com"
        length = server._json_length(0x40, notify)
        frame = with_json(0x40, notify, sid, sid + b"x" * (123 - length))
        self.assertEqual(frame[9], 0x7B)
        self.assertIs(server._extract_json(0x40, frame)["message"]["onoff"], True)


class EnergyTotalTest(unittest.TestCase):
    def test_owner_log_sequence(self):
        # The counter as seen on 2026-11-17 (kWh): 5-minute steps, then a
        # drop to zero at a full hour, then a new run.
        total = server.EnergyTotal()
        changes = [total.update(v) for v in (0.0, 0.0, 0.071, 0.071, 0.153, 0.0, 0.0, 0.05)]
        self.assertEqual(changes, [False, False, True, False, True, False, False, True])
        self.assertAlmostEqual(total.total, 0.203)
        self.assertEqual(total.last, 0.05)

    def test_first_value_only_sets_the_start(self):
        total = server.EnergyTotal()
        self.assertFalse(total.update(0.153))
        self.assertEqual(total.total, 0.0)
        self.assertTrue(total.update(0.2))
        self.assertAlmostEqual(total.total, 0.047)

    def test_reset_to_a_nonzero_value_counts_it(self):
        total = server.EnergyTotal(total=1.0, last=0.153)
        self.assertTrue(total.update(0.02))  # reset, then 20 Wh since
        self.assertAlmostEqual(total.total, 1.02)

    def test_small_drop_is_noise(self):
        total = server.EnergyTotal(total=1.0, last=0.153)
        self.assertFalse(total.update(0.150))
        self.assertEqual((total.total, total.last), (1.0, 0.153))
        self.assertTrue(total.update(0.160))
        self.assertAlmostEqual(total.total, 1.007)

    # The owner's log of 2026-11-17, 13:19:36-14:14:27 (kWh): counter steps,
    # the "electricity" value at each period end, then the counter's reset.
    PERIODS = [0.0, 0.074, 0.132, ("close", 0.177), 0.0,
               0.035, 0.062, 0.087, ("close", 0.118), 0.0,
               0.021, 0.05, 0.084]

    def feed(self, total, items):
        for item in items:
            if isinstance(item, tuple):
                total.close(item[1])
            else:
                total.update(item)

    def test_period_end_adds_the_last_five_minutes(self):
        total = server.EnergyTotal()
        self.feed(total, self.PERIODS)
        # 0.177 + 0.118 + 0.084; the integrated power gave 0.172 + 0.116 + 0.082.
        self.assertAlmostEqual(total.total, 0.379)
        self.assertEqual(total.last, 0.084)

    def test_period_end_after_the_reset(self):
        # Same log with each reset arriving before its "electricity" message.
        items = [0.0, 0.074, 0.132, 0.0, ("close", 0.177),
                 0.035, 0.062, 0.087, 0.0, ("close", 0.118),
                 0.021, 0.05, 0.084]
        total = server.EnergyTotal()
        self.feed(total, items)
        self.assertAlmostEqual(total.total, 0.379)

    def test_period_end_without_history_starts_counting(self):
        total = server.EnergyTotal()
        self.assertFalse(total.close(0.138))
        self.assertTrue(total.update(0.021))
        self.assertAlmostEqual(total.total, 0.021)

    def test_continues_after_restart(self):
        # Restored state; the plug kept counting while Home Assistant was down.
        total = server.EnergyTotal(total=2.5, last=0.071)
        self.assertTrue(total.update(0.153))
        self.assertAlmostEqual(total.total, 2.582)


class AllowedSourcesTest(unittest.IsolatedAsyncioTestCase):
    def test_parse_sources(self):
        self.assertEqual(
            server.parse_sources("192.0.2.10, 198.51.100.7/24\n192.0.2.10"),
            ("192.0.2.10/32", "198.51.100.0/24"),
        )
        self.assertEqual(server.parse_sources(""), ())
        with self.assertRaises(ValueError):
            server.parse_sources("192.0.2.300")

    async def connect_and_login(self, allowed):
        srv = server.FergusonServer(0, allowed_sources=allowed)
        await srv.async_start()
        port = srv._server.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(server._frame(0x20, recorded("plug", 0x20)))
        try:
            header = await asyncio.wait_for(reader.read(5), 2)
        except ConnectionResetError:
            header = b""  # closed with the login still unread
        finally:
            writer.close()
            await srv.async_stop()
        return header, srv

    async def test_rejects_address_outside_the_list(self):
        with self.assertLogs(server._LOGGER, "WARNING"):
            header, srv = await self.connect_and_login(["192.0.2.0/24"])
        self.assertEqual(header, b"")  # closed without a login ack
        self.assertEqual(srv.plugs, {})

    async def test_accepts_address_in_the_list(self):
        header, srv = await self.connect_and_login(["127.0.0.0/8"])
        self.assertEqual(header[:1], b"\x28")
        self.assertIn("a1b2c3d4", srv.plugs)

    def test_known_names_seed_plugs(self):
        srv = server.FergusonServer(0, {"a1b2c3d4": "Ładowarka tel", "00000001": None})
        self.assertEqual(srv.plugs["a1b2c3d4"].name, "Ładowarka tel")
        self.assertIsNone(srv.plugs["00000001"].name)


class ServerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.new_plugs = []
        self.server = server.FergusonServer(0, on_new_plug=self.new_plugs.append)
        await self.server.async_start()
        port = self.server._server.sockets[0].getsockname()[1]
        self.reader, self.writer = await asyncio.open_connection("127.0.0.1", port)

    async def asyncTearDown(self):
        self.writer.close()
        await self.server.async_stop()

    def send(self, frame_type: int, payload: bytes = b"") -> None:
        self.writer.write(server._frame(frame_type, payload))

    async def read(self):
        header = await asyncio.wait_for(self.reader.readexactly(5), 6)
        length = int.from_bytes(header[1:], "big")
        return header[0], (await self.reader.readexactly(length) if length else b"")

    async def login(self):
        self.send(0x20, recorded("plug", 0x20))
        self.assertEqual(await self.read(), (0x28, b"\x00\x00"))
        return self.new_plugs[0]

    async def test_login_time_ping(self):
        plug = await self.login()
        self.assertEqual(plug.device_id, "a1b2c3d4")
        self.assertTrue(plug.available)

        self.send(0xC0, b"\x0c")
        frame_type, payload = await self.read()
        self.assertEqual(frame_type, 0xC8)
        self.assertEqual(len(payload), len(recorded("cloud", 0xC8)))
        self.assertEqual(payload[0], 0x0D)
        self.assertEqual(payload[-2:], b"\x03\x20")

        self.send(0xD0)
        self.assertEqual(await self.read(), (0xD8, b""))

    async def test_telemetry_is_acked_and_parsed(self):
        plug = await self.login()
        telemetry = recorded("plug", 0x80, b"2.1.1.4.4")
        # the real plug sometimes glues two frames into one TCP segment
        self.writer.write(
            server._frame(0x78, bytes(7)) + server._frame(0x80, telemetry)
        )
        self.assertEqual(await self.read(), (0x88, telemetry[:7]))
        await asyncio.sleep(0.05)
        self.assertEqual(plug.values[server.OID_VOLTAGE], "236.03")

    async def test_set_relay_matches_cloud_format(self):
        plug = await self.login()
        await plug.async_set_relay(False)
        frame_type, payload = await self.read()
        cloud = recorded("cloud", 0x70, b'"CID":30011,')
        self.assertEqual(frame_type, 0x70)
        self.assertEqual(payload[:4], cloud[:4])
        self.assertEqual(payload[6], 0)
        body = json.loads(payload[7:])
        self.assertEqual(list(body), list(json.loads(cloud[7:])))
        self.assertEqual(body["CID"], 30011)
        self.assertEqual(body["PL"], {"2.1.1.4.1": False})

        reply = recorded("plug", 0x80, b'"2.1.1.4.1":false},"TEID":"999"')
        self.send(0x80, reply)
        self.assertEqual(await self.read(), (0x88, reply[:7]))
        await asyncio.sleep(0.05)
        self.assertIs(plug.is_on, False)

    async def test_notify_updates_state(self):
        plug = await self.login()
        notify = recorded("plug", 0x40, b'"onoff":true')
        self.send(0x40, notify)
        self.assertEqual(await self.read(), (0x48, notify[:7]))
        await asyncio.sleep(0.05)
        self.assertIs(plug.is_on, True)

    async def test_polls_after_login(self):
        await self.login()
        bodies = []
        for _ in range(5):
            frame_type, payload = await asyncio.wait_for(self.read(), 8)
            self.assertEqual(frame_type, 0x70)
            bodies.append(json.loads(payload[7:]))

        # First the time-zone set, byte for byte as the cloud sent it
        # (apart from the random SN).
        zone = recorded("cloud", 0x70, b'"2.1.1.2.1.7"', RENAME_FRAMES)
        expected = json.loads(zone[7:])
        self.assertEqual(list(bodies[0]), list(expected))
        self.assertEqual({**bodies[0], "SN": 0}, {**expected, "SN": 0})

        # Then the name, exactly as the app asked for it.
        requests = [b["PL"] for b in bodies[1:]]
        self.assertTrue(all(b["CID"] == 30021 for b in bodies[1:]))
        cloud = recorded("cloud", 0x70, b'"OID":["2.1.1.4.253"]')
        self.assertEqual(requests[0], json.loads(cloud[7:])["PL"])
        polled = [oid for pl in requests[1:3] for oid in pl["OID"]]
        self.assertIn(server.OID_RELAY, polled)
        self.assertIn(server.OID_POWER, polled)
        self.assertNotIn(server.OID_ENERGY, polled)

        # Then energy, with the same request the app made.
        app = recorded("cloud", 0x70, b'"OID":["2.1.1.4.2","2.1.1.4.7"]')
        self.assertEqual(requests[3], json.loads(app[7:])["PL"])

    async def test_name_and_mac(self):
        plug = await self.login()
        self.assertIsNone(plug.name)
        self.assertIsNone(plug.mac)
        name_reply = recorded("plug", 0x80, b'"2.1.1.4.253"')
        notify = recorded("plug", 0x40, b'"plugMac"')
        self.send(0x80, name_reply)
        self.send(0x40, notify)
        self.assertEqual((await self.read())[0], 0x88)
        self.assertEqual((await self.read())[0], 0x48)
        await asyncio.sleep(0.05)
        self.assertEqual(plug.name, "Ładowarka tel")
        self.assertEqual(plug.mac, "F0FE6B000001")

    async def test_energy_reply_is_scaled(self):
        # Real values: at 153 Wh telemetry said "0.153" and a get reply
        # "1.53" (2026-11-17); at 11 Wh "0.011" and "0.11" (2026-11-16).
        plug = await self.login()
        telemetry = recorded("plug", 0x80, b'"2.1.1.4.7":"0.000"')
        telemetry = with_json(0x80, telemetry, b'"2.1.1.4.7":"0.000"', b'"2.1.1.4.7":"0.011"')
        reply = recorded("plug", 0x80, b'"2.1.1.4.7":"0.00"}')
        reply = with_json(0x80, reply, b'"2.1.1.4.7":"0.00"', b'"2.1.1.4.7":"1.53"')
        reply = with_json(0x80, reply, b'"2.1.1.4.2":"0.00"', b'"2.1.1.4.2":"930.10"')

        self.send(0x80, telemetry)
        self.assertEqual((await self.read())[0], 0x88)
        await asyncio.sleep(0.05)
        self.assertEqual(plug.energy, 0.011)

        self.send(0x80, reply)
        self.assertEqual((await self.read())[0], 0x88)
        await asyncio.sleep(0.05)
        self.assertEqual(plug.energy, 0.153)
        self.assertEqual(plug.values[server.OID_POWER], "930.10")

    async def test_disconnect_marks_unavailable(self):
        plug = await self.login()
        self.writer.close()
        await asyncio.sleep(0.1)
        self.assertFalse(plug.available)
        with self.assertRaises(ConnectionError):
            await plug.async_set_relay(True)

    async def test_rename_matches_app_format(self):
        plug = await self.login()
        name = "Zażòłć gęsta jaźni"
        await plug.async_set_name(name)
        frame_type, payload = await self.read()
        app = recorded("cloud", 0x70, '"2.1.1.4.253":"Zażòłć'.encode(), RENAME_FRAMES)
        self.assertEqual(frame_type, 0x70)
        self.assertEqual(payload[:4], app[:4])
        self.assertEqual(payload[6], 0)
        body = json.loads(payload[7:])
        self.assertEqual(list(body), list(json.loads(app[7:])))
        self.assertEqual(body["CID"], 30011)
        self.assertEqual(body["PL"], json.loads(app[7:])["PL"])
        # raw UTF-8 like the app, not \u escapes
        self.assertIn(f'"2.1.1.4.253":"{name}"'.encode(), payload)
        self.assertNotIn(b"\\u", payload)

    async def test_rename_reply_updates_name(self):
        plug = await self.login()
        reply = recorded("plug", 0x80, b'"FERGUSON Plug Subwoofer"', RENAME_FRAMES)
        self.send(0x80, reply)
        self.assertEqual(await self.read(), (0x88, reply[:7]))
        await asyncio.sleep(0.05)
        self.assertEqual(plug.name, "FERGUSON Plug Subwoofer")

    async def test_rename_limits(self):
        plug = await self.login()
        for bad in ("", "   ", "x" * 119, "ż" * 60):  # "ż" is 2 bytes: 120
            with self.assertRaises(ValueError):
                await plug.async_set_name(bad)
        # The longest name the plug accepted in the recording is 118 bytes.
        longest = recorded("cloud", 0x70, b'"2.1.1.4.253":"!aaaa', RENAME_FRAMES)
        name = json.loads(longest[7:])["PL"]["2.1.1.4.253"]
        self.assertEqual(len(name.encode()), server.NAME_MAX_BYTES)
        await plug.async_set_name(name)
        frame_type, payload = await self.read()
        self.assertEqual(json.loads(payload[7:])["PL"], {"2.1.1.4.253": name})

    async def test_electricity_notification(self):
        plug = await self.login()
        notify = recorded("plug", 0x40, b'"electricity"', RENAME_FRAMES)
        self.send(0x40, notify)
        self.assertEqual(await self.read(), (0x48, notify[:7]))
        await asyncio.sleep(0.05)
        self.assertEqual(plug.mac, "F0FE6B000001")
        self.assertIsNone(plug.is_on)  # carries no relay state
        self.assertIsNone(plug.period_energy)  # right after connecting

    async def test_electricity_notification_ends_a_period(self):
        plug = await self.login()
        plug._connected_at -= server.ELECTRICITY_SETTLE
        notify = recorded("plug", 0x40, b'"electricity"', RENAME_FRAMES)
        body = json.loads(notify[10:])
        body["message"]["electricity"] = "0.177"
        new = json.dumps(body, separators=(",", ":")).encode()
        self.send(0x40, notify[:9] + bytes([len(new)]) + new)
        await self.read()
        await asyncio.sleep(0.05)
        self.assertEqual(plug.period_energy, (1, 0.177))

    async def test_poll_ends_quietly_when_not_connected(self):
        plug = server.Plug("a1b2c3d4")  # no writer: every query fails
        delay, server.POLL_DELAY = server.POLL_DELAY, 0
        try:
            # Returns instead of raising, so no "Task exception was never
            # retrieved" ends up in the Home Assistant log.
            await asyncio.wait_for(self.server._poll(plug), 1)
        finally:
            server.POLL_DELAY = delay

    async def test_whole_recorded_session_replays(self):
        plug = await self.login()
        count = 0
        for item in FRAMES:
            frame_type = int(item["type"], 16)
            if item["from"] == "plug" and frame_type in (0x40, 0x80, 0xC0, 0xD0):
                self.send(frame_type, bytes.fromhex(item["payload"]))
                count += 1
        await self.writer.drain()
        await asyncio.sleep(0.3)
        self.assertGreater(count, 50)
        self.assertTrue(plug.available)
        self.assertIsInstance(plug.is_on, bool)
        self.assertIn(server.OID_VOLTAGE, plug.values)


if __name__ == "__main__":
    unittest.main()
