"""Local TCP server that stands in for the Ferguson/Treesat cloud.

The FS2PG plug opens a TCP connection to app.ferguson.com.pl:23778 and speaks
a simple unencrypted protocol. Every frame is:

    1 byte type | 4 bytes payload length (big endian) | payload

This module has no Home Assistant imports so it can be tested on its own.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timedelta, timezone
import ipaddress
import json
import logging
import random
import struct
import time

_LOGGER = logging.getLogger(__name__)

FRAME_LOGIN = 0x20
FRAME_LOGIN_ACK = 0x28
FRAME_NOTIFY = 0x40
FRAME_NOTIFY_ACK = 0x48
FRAME_REQUEST = 0x70
FRAME_REQUEST_ACK = 0x78
FRAME_DATA = 0x80
FRAME_DATA_ACK = 0x88
FRAME_TIME = 0xC0
FRAME_TIME_ACK = 0xC8
FRAME_PING = 0xD0
FRAME_PONG = 0xD8

# Values observed in traffic from the original cloud.
SENDER_ID = bytes.fromhex("00001c4e")
TEID = "7246"
SID = "homeassistant"
CLOUD_SID = "treesat.io"  # the cloud's own SID, used for the time-zone set
CID_SET = 30011
CID_GET = 30021

# Sent by the cloud about 5 s after every login, before anything else; the
# integration sends the same frame (seen in both recorded sessions).
TIMEZONE_SET = {"2.1.1.2.1.7": "+0"}
# Longest name the plug accepted in a recorded rename (and echoed back).
NAME_MAX_BYTES = 118

OID_PREFIX = "2.1.1.4."
OID_RELAY = "2.1.1.4.1"
OID_POWER = "2.1.1.4.2"
OID_CURRENT = "2.1.1.4.3"
OID_VOLTAGE = "2.1.1.4.4"
OID_ENERGY = "2.1.1.4.7"
OID_NAME = "2.1.1.4.253"  # the name set in the vendor app

NAME_OIDS = (OID_NAME,)  # queried once per connection, as the app does
STATE_OIDS = (OID_RELAY, "2.1.1.4.5")
MEASUREMENT_OIDS = (OID_POWER, OID_CURRENT, OID_VOLTAGE)
# Energy is asked for exactly as the app did ([4.2, 4.7], seen; the app sent
# it about once a second, the integration once a minute).
ENERGY_OIDS = (OID_POWER, OID_ENERGY)
# A reply to a get reports the energy counter 10 times higher than telemetry
# does ("1.53" vs "0.153" at 153 Wh; constant from 11 Wh up, seen). Telemetry
# matches the integrated power, so it is kWh and a reply is divided by 10.
ENERGY_REPLY_SCALE = 10
# An "electricity" notification this soon after login is not a period end.
ELECTRICITY_SETTLE = 30

READ_TIMEOUT = 90  # the plug pings roughly every 20 s
POLL_DELAY = 5  # seconds after login before the first request (the cloud: ~5 s)
POLL_INTERVAL = 60
MAX_FRAME = 8192


def parse_sources(text: str | Iterable[str]) -> tuple[str, ...]:
    """Parse allowed client addresses: IPs or CIDR networks.

    Accepts a comma/whitespace separated string or an iterable of strings and
    returns normalized network strings. Raises ValueError on a bad entry.
    """
    items = text.replace(",", " ").split() if isinstance(text, str) else text
    networks = []
    for item in items:
        try:
            networks.append(str(ipaddress.ip_network(item.strip(), strict=False)))
        except ValueError as err:
            raise ValueError(f"Invalid address or network: {item!r}") from err
    return tuple(dict.fromkeys(networks))


def _frame(frame_type: int, payload: bytes = b"") -> bytes:
    return bytes([frame_type]) + len(payload).to_bytes(4, "big") + payload


def _time_payload() -> bytes:
    """Build the time reply the way the cloud does: UTC+8 with zone 800."""
    now = datetime.now(timezone.utc) + timedelta(hours=8)
    weekday = (now.weekday() + 1) % 7 + 1  # Sunday = 1
    return struct.pack(
        ">BHBBBBBBH",
        0x0D,
        now.year,
        now.month,
        now.day,
        weekday,
        now.hour,
        now.minute,
        now.second,
        800,
    )


JSON_OFFSET = 10  # JSON starts at byte 10 of both 0x80 and 0x40 payloads


def _json_length(frame_type: int, payload: bytes) -> int | None:
    """Return the JSON length declared in a 0x80 or 0x40 payload.

    0x80: device(4) seq(2) 00 aa len(2, big endian) JSON[\\r\\n] ff
    0x40: device(4) 01 01 0e 00 90 len(1) JSON
    """
    if len(payload) < JSON_OFFSET:
        return None
    if frame_type == FRAME_DATA:
        return int.from_bytes(payload[8:10], "big")
    if frame_type == FRAME_NOTIFY:
        # [guess] one length byte; a JSON longer than 255 bytes was never seen
        return payload[9]
    return None


def _extract_json(frame_type: int, payload: bytes) -> dict | None:
    """Decode the JSON of a 0x80 or 0x40 payload using its length field.

    Searching for the first "{" is not enough: a length byte can itself be
    0x7b ("{") when the JSON is 123 bytes long.
    """
    length = _json_length(frame_type, payload)
    if length is None:
        return None
    data = payload[JSON_OFFSET : JSON_OFFSET + length]
    if len(data) < length:
        # Never seen: declared length longer than the frame. Fall back to the
        # outermost braces after the header.
        _LOGGER.debug("JSON length %d exceeds frame: %s", length, payload.hex())
        data = payload[JSON_OFFSET:]
        data = data[: data.rfind(b"}") + 1]
    try:
        obj = json.loads(data.decode("utf-8", "replace"))
    except ValueError:
        _LOGGER.debug("Unparsable JSON payload: %s", payload.hex())
        return None
    return obj if isinstance(obj, dict) else None


class EnergyTotal:
    """Turn the plug's resetting energy counter into a running total (kWh).

    The counter covers a 20-minute period: it steps every 5 minutes, and at
    the end of the period the plug reports the period's final value in an
    "electricity" notification and resets the counter to zero (see
    docs/protocol.md). The last 5 minutes of a period only show up in that
    notification, so it is fed in with `close`.

    `update`: a rise adds the difference; a drop to at most half of the last
    value is taken as a reset and adds the new value. A smaller drop is
    ignored as noise. The first value only sets the starting point. `total`
    and `last` are what has to be stored to continue after a restart.
    """

    RESET_FRACTION = 0.5

    def __init__(self, total: float = 0.0, last: float | None = None) -> None:
        self.total = total
        self.last = last
        # Last value before a reset seen by `update`, until the notification
        # that closes that period arrives (the two may come in either order).
        self._before_reset: float | None = None

    def _add(self, added: float) -> bool:
        if added <= 0:
            return False
        self.total = round(self.total + added, 4)
        return True

    def update(self, counter: float) -> bool:
        """Feed a counter value; return True if the total changed."""
        last = self.last
        if last is None:
            self.last = counter
            return False
        if counter >= last:
            added = counter - last
            if counter > 0:
                self._before_reset = None  # the new period is under way
        elif counter <= last * self.RESET_FRACTION:
            added = counter
            self._before_reset = last
        else:
            return False
        self.last = counter
        return self._add(added)

    def close(self, final: float) -> bool:
        """Feed the final value of a period; return True if the total changed."""
        if self._before_reset is not None:
            # The reset came first: the period's counted part ended there.
            counted, self._before_reset = self._before_reset, None
        elif self.last is None:
            # Nothing known about this period: start counting from the next one.
            self.last = 0.0
            return False
        else:
            counted, self.last = self.last, 0.0
        return self._add(final - counted)


class Plug:
    """State of a single FS2PG plug."""

    def __init__(self, device_id: str, name: str | None = None) -> None:
        self.device_id = device_id
        self.values: dict[str, object] = {}
        if name:
            self.values[OID_NAME] = name
        self.mac: str | None = None  # from 0x40 notifications, e.g. F0FE6B000001
        # Final energy of the last closed 20-minute period (kWh), numbered so
        # a listener can tell a new one from one it has already used.
        self.period_energy: tuple[int, float] | None = None
        self._period_count = 0
        self._connected_at = 0.0
        self.available = False
        self._writer: asyncio.StreamWriter | None = None
        self._listeners: set[Callable[[], None]] = set()

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)

    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()

    @property
    def is_on(self) -> bool | None:
        value = self.values.get(OID_RELAY)
        return value if isinstance(value, bool) else None

    @property
    def energy(self) -> float | None:
        """The plug's energy counter in kWh (resets; see docs/protocol.md)."""
        value = self.values.get(OID_ENERGY)
        return value if isinstance(value, float) else None

    @property
    def name(self) -> str | None:
        value = self.values.get(OID_NAME)
        if not isinstance(value, str):
            return None
        return value.strip() or None

    async def async_set_relay(self, on: bool) -> None:
        await self._send(CID_SET, {OID_RELAY: bool(on)})

    async def async_set_name(self, name: str) -> None:
        """Rename the plug, as the vendor app does.

        The plug echoes the new name in its reply, which updates `name`.
        Raises ValueError for an empty name or one longer than ever observed.
        """
        name = name.strip()
        if not name:
            raise ValueError("The name must not be empty")
        if len(name.encode()) > NAME_MAX_BYTES:
            raise ValueError(f"The name must not exceed {NAME_MAX_BYTES} bytes")
        await self._send(CID_SET, {OID_NAME: name})

    async def async_set_timezone(self) -> None:
        await self._send(CID_SET, TIMEZONE_SET, sid=CLOUD_SID)

    async def async_query(self, oids: Iterable[str]) -> None:
        await self._send(CID_GET, {"OID": list(oids)})

    async def _send(self, cid: int, payload: dict, sid: str = SID) -> None:
        writer = self._writer
        if writer is None:
            raise ConnectionError(f"Plug {self.device_id} is not connected")
        body = json.dumps(
            {
                "SN": random.randint(100000, 999999),
                "CID": cid,
                "SID": sid,
                "TEID": TEID,
                "PL": payload,
            },
            separators=(",", ":"),
            ensure_ascii=False,  # the app sends names as raw UTF-8, not \uXXXX
        ).encode()
        header = SENDER_ID + random.getrandbits(16).to_bytes(2, "big") + b"\x00"
        writer.write(_frame(FRAME_REQUEST, header + body))
        await writer.drain()

    def _period_end(self, value: object) -> bool:
        """Handle an "electricity" notification (end of a 20-minute period)."""
        try:
            energy = round(float(value), 4)
        except (TypeError, ValueError):
            return False
        # The plug also sends one right after connecting, which does not end
        # a period (seen once, with 0.000); what it reports is [unknown], so
        # it is not counted.
        if time.monotonic() - self._connected_at < ELECTRICITY_SETTLE:
            _LOGGER.debug("%s: electricity %s right after connecting, not counted",
                          self.device_id, energy)
            return False
        self._period_count += 1
        self.period_energy = (self._period_count, energy)
        return True

    def _update(self, obj: dict) -> None:
        changed = False
        payload = obj.get("PL")
        # Unsolicited telemetry has "SN": null; replies echo our request number.
        is_reply = obj.get("SN") is not None
        if isinstance(payload, dict):
            for key, value in payload.items():
                if key == OID_ENERGY:
                    try:
                        value = float(value) / (ENERGY_REPLY_SCALE if is_reply else 1)
                    except (TypeError, ValueError):
                        continue
                    value = round(value, 4)
                if key.startswith(OID_PREFIX):
                    self.values[key] = value
                    changed = True
        message = obj.get("message")
        if isinstance(message, dict) and isinstance(message.get("onoff"), bool):
            self.values[OID_RELAY] = message["onoff"]
            changed = True
        if isinstance(message, dict) and "electricity" in message:
            changed |= self._period_end(message["electricity"])
        mac = obj.get("plugMac")
        if isinstance(mac, str) and len(mac) == 12 and mac != self.mac:
            try:
                bytes.fromhex(mac)
            except ValueError:
                pass
            else:
                self.mac = mac.upper()
                changed = True
        if changed:
            self._notify()


class FergusonServer:
    """Accepts connections from plugs and keeps their state."""

    def __init__(
        self,
        port: int,
        known: Iterable[str] | Mapping[str, str | None] = (),
        on_new_plug: Callable[[Plug], None] | None = None,
        allowed_sources: Iterable[str] = (),
    ) -> None:
        """Create the server.

        known: device IDs seen before, or a mapping of device ID to name.
        allowed_sources: IPs or networks allowed to connect; empty allows all.
        """
        self._port = port
        self._on_new_plug = on_new_plug
        self._allowed = [ipaddress.ip_network(n) for n in parse_sources(allowed_sources)]
        self._rejected: set[str] = set()
        self._server: asyncio.Server | None = None
        self._tasks: set[asyncio.Task] = set()
        names = known if isinstance(known, Mapping) else dict.fromkeys(known)
        self.plugs: dict[str, Plug] = {i: Plug(i, n) for i, n in names.items()}

    def _is_allowed(self, host: str) -> bool:
        if not self._allowed:
            return True
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return False
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        return any(address in network for network in self._allowed)

    async def async_start(self) -> None:
        self._server = await asyncio.start_server(self._accept, "0.0.0.0", self._port)

    async def async_stop(self) -> None:
        if self._server is not None:
            self._server.close()
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._server = None

    async def _accept(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername")
        host = peer[0] if isinstance(peer, tuple) and peer else ""
        if not self._is_allowed(host):
            if host not in self._rejected:  # warn once per address
                self._rejected.add(host)
                _LOGGER.warning(
                    "Rejected connection from %s: not in the allowed sources. "
                    "With a masquerade rule the plug connects from the router's "
                    "address",
                    host,
                )
            writer.close()
            return
        task = asyncio.current_task()
        self._tasks.add(task)
        try:
            await self._handle(reader, writer)
        finally:
            self._tasks.discard(task)

    def _attach(self, device_id: str, writer: asyncio.StreamWriter) -> Plug:
        plug = self.plugs.get(device_id)
        is_new = plug is None
        if plug is None:
            plug = self.plugs[device_id] = Plug(device_id)
        if plug._writer is not None and plug._writer is not writer:
            plug._writer.close()
        plug._writer = writer
        plug._connected_at = time.monotonic()
        plug.available = True
        if is_new and self._on_new_plug is not None:
            self._on_new_plug(plug)
        plug._notify()
        return plug

    async def _poll(self, plug: Plug) -> None:
        """Query the plug until the connection ends.

        A write error ends polling quietly; the connection handler notices the
        broken connection and marks the plug unavailable.
        """
        try:
            await asyncio.sleep(POLL_DELAY)
            await plug.async_set_timezone()
            await asyncio.sleep(1)
            await plug.async_query(NAME_OIDS)
            await asyncio.sleep(1)
            while True:
                await plug.async_query(STATE_OIDS)
                await asyncio.sleep(1)
                await plug.async_query(MEASUREMENT_OIDS)
                await asyncio.sleep(1)
                await plug.async_query(ENERGY_OIDS)
                await asyncio.sleep(POLL_INTERVAL)
        except (ConnectionError, OSError) as err:
            _LOGGER.debug("Polling %s stopped: %s", plug.device_id, err)

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername")
        _LOGGER.debug("Connection from %s", peer)
        plug: Plug | None = None
        poll: asyncio.Task | None = None

        def attach(device_id: str) -> Plug:
            nonlocal plug, poll
            plug = self._attach(device_id, writer)
            if poll is None:
                poll = asyncio.create_task(self._poll(plug))
            return plug

        try:
            while True:
                header = await asyncio.wait_for(reader.readexactly(5), READ_TIMEOUT)
                frame_type = header[0]
                length = int.from_bytes(header[1:5], "big")
                if length > MAX_FRAME:
                    _LOGGER.warning("Oversized frame from %s, closing", peer)
                    break
                payload = (
                    await asyncio.wait_for(reader.readexactly(length), READ_TIMEOUT)
                    if length
                    else b""
                )

                if frame_type == FRAME_LOGIN:
                    if length < 5:
                        break
                    attach(payload[1:5].hex())
                    writer.write(_frame(FRAME_LOGIN_ACK, b"\x00\x00"))
                    _LOGGER.info("Plug %s connected from %s", plug.device_id, peer)
                elif frame_type == FRAME_TIME:
                    writer.write(_frame(FRAME_TIME_ACK, _time_payload()))
                elif frame_type == FRAME_PING:
                    writer.write(_frame(FRAME_PONG))
                elif frame_type in (FRAME_DATA, FRAME_NOTIFY):
                    if length < 7:
                        continue
                    ack = (
                        FRAME_DATA_ACK
                        if frame_type == FRAME_DATA
                        else FRAME_NOTIFY_ACK
                    )
                    writer.write(_frame(ack, payload[:7]))
                    current = plug or attach(payload[:4].hex())
                    obj = _extract_json(frame_type, payload)
                    _LOGGER.debug("%s -> %s", current.device_id, obj)
                    if obj is not None:
                        current._update(obj)
                elif frame_type == FRAME_REQUEST_ACK:
                    pass
                else:
                    _LOGGER.debug(
                        "Unknown frame 0x%02x from %s: %s",
                        frame_type,
                        peer,
                        payload.hex(),
                    )
                await writer.drain()
        except (asyncio.IncompleteReadError, TimeoutError, ConnectionError, OSError):
            pass
        finally:
            if poll is not None:
                poll.cancel()
            if plug is not None and plug._writer is writer:
                plug._writer = None
                plug.available = False
                plug._notify()
                _LOGGER.info("Plug %s disconnected", plug.device_id)
            writer.close()
