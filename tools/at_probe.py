#!/usr/bin/env python3
"""Read-only probe of the HF-LPT100F Wi-Fi module over UDP port 48899.

    python3 tools/at_probe.py PLUG_IP [AT+CMD ...]

Only query commands are sent by default. Do NOT send AT+RELD (factory reset)
or any command with "=" unless you know exactly what it does: it can
disconnect the plug from Wi-Fi or from the vendor app.
"""

import socket
import sys

DEFAULT = ["AT+VER", "AT+TMODE", "AT+NETP", "AT+SOCKB", "AT+UART", "AT+TCPLK", "AT+WMODE"]


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    ip, commands = sys.argv[1], sys.argv[2:] or DEFAULT
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(3)
    sock.sendto(b"HF-A11ASSISTHREAD", (ip, 48899))
    print(sock.recvfrom(1024)[0].decode(errors="replace"))
    sock.sendto(b"+ok", (ip, 48899))
    for command in commands:
        sock.sendto((command + "\r").encode(), (ip, 48899))
        print("==", command)
        try:
            while True:
                print(sock.recvfrom(4096)[0].decode(errors="replace"), end="")
        except socket.timeout:
            print()
    sock.sendto(b"AT+Q\r", (ip, 48899))


if __name__ == "__main__":
    main()
