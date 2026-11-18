#!/usr/bin/env python3
"""Decode FS2PG protocol frames from a pcapng capture (no dependencies).

    python3 tools/decode_pcap.py capture.pcapng [--port 23778] [--pings] [--mask]

Handles Ethernet (optionally VLAN-tagged) and raw-IP link types, removes the
duplicate packets a MikroTik sniffer records on several interfaces, and
reassembles the TCP stream in arrival order (no retransmission handling).
"""

from __future__ import annotations

import argparse
import re
import socket
import struct


def packets(path: str):
    data = open(path, "rb").read()
    offset, link_types = 0, []
    while offset + 12 <= len(data):
        block_type, block_len = struct.unpack_from("<II", data, offset)
        body = data[offset + 8 : offset + block_len - 4]
        if block_type == 1:  # interface description
            link_types.append(struct.unpack_from("<H", body, 0)[0])
        elif block_type == 6:  # enhanced packet
            iface, ts_hi, ts_lo, cap_len, _ = struct.unpack_from("<IIIII", body, 0)
            yield link_types[iface], (ts_hi << 32) | ts_lo, body[20 : 20 + cap_len]
        offset += block_len


def tcp_segments(path: str, port: int):
    seen = set()
    for link_type, ts, raw in packets(path):
        if link_type == 1:
            ether_type, off = struct.unpack_from(">H", raw, 12)[0], 14
            while ether_type == 0x8100:
                ether_type = struct.unpack_from(">H", raw, off + 2)[0]
                off += 4
            if ether_type != 0x0800:
                continue
            ip = raw[off:]
        elif link_type == 101:
            ip = raw
        else:
            continue
        if ip[9] != 6:
            continue
        ihl = (ip[0] & 15) * 4
        total = struct.unpack_from(">H", ip, 2)[0]
        tcp = ip[ihl:total]
        sport, dport, seq, _, off_flags = struct.unpack_from(">HHIIH", tcp, 0)
        payload = tcp[(off_flags >> 12) * 4 :]
        if port not in (sport, dport) or not payload:
            continue
        key = (sport, dport, seq, len(payload))
        if key in seen:
            continue
        seen.add(key)
        yield ts, "cloud" if sport == port else "plug", payload


def frames(path: str, port: int):
    buffers = {"plug": b"", "cloud": b""}
    for ts, side, payload in tcp_segments(path, port):
        buffers[side] += payload
        buf = buffers[side]
        while len(buf) >= 5:
            length = int.from_bytes(buf[1:5], "big")
            if len(buf) < 5 + length:
                break
            yield ts, side, buf[0], buf[5 : 5 + length]
            buf = buf[5 + length :]
        buffers[side] = buf


# Where the JSON starts: after a 7-byte header in cloud requests, after a
# 10-byte header in plug data and notifications (see docs/protocol.md).
# Searching for "{" instead fails when a header byte happens to be 0x7b.
JSON_OFFSET = {0x70: 7, 0x80: 10, 0x40: 10}


def split(frame_type: int, payload: bytes) -> tuple[bytes, str, bytes]:
    """Return (header, JSON text, trailer) for frames that carry JSON."""
    offset = JSON_OFFSET.get(frame_type)
    if offset is None or len(payload) <= offset:
        return payload, "", b""
    if frame_type == 0x80:
        end = offset + int.from_bytes(payload[8:10], "big")
    elif frame_type == 0x40:
        end = offset + payload[9]
    else:
        end = len(payload)
    body = payload[offset:end].decode("utf-8", "replace").strip()
    return payload[:offset], body, payload[end:]


def mask(text: str) -> str:
    """Hide the account e-mail, the device key and MACs, for sharing."""
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "<EMAIL>", text)
    text = re.sub(r'("plugMac":")[0-9A-Fa-f]{12}"', r'\1<MAC>"', text)
    return text


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("pcap")
    parser.add_argument("--port", type=int, default=23778)
    parser.add_argument("--pings", action="store_true", help="also show ping/pong")
    parser.add_argument(
        "--mask",
        action="store_true",
        help="hide the e-mail, MACs and the login frame (device key) for sharing",
    )
    args = parser.parse_args()

    start = None
    for ts, side, frame_type, payload in frames(args.pcap, args.port):
        start = start if start is not None else ts
        if frame_type in (0xD0, 0xD8) and not args.pings:
            continue
        if frame_type == 0x20 and args.mask:
            text = "<login frame hidden: it carries the device key>"
        else:
            header, body, trailer = split(frame_type, payload)
            text = " ".join(x for x in (header.hex(), body, trailer.hex()) if x)
            if args.mask:
                text = mask(text)
        # timestamp resolution depends on the capture tool; MikroTik uses ns
        print(f"{(ts - start) / 1e9:8.2f} {side:5} {frame_type:02x} {text}")


if __name__ == "__main__":
    main()
