# Hardware and network notes

## Inside the plug

- **MCU:** STM8S003F3P6 (8-bit STMicro). Drives the relay, button and energy
  metering; talks to the Wi-Fi module over UART (115200 8N1, reported by
  `AT+UART`). It cannot run Tasmota/ESPHome.
- **Wi-Fi module:** Hi-Flying HF-LPT100F (marking `hf-lpt100f-12`). It
  identifies itself as `HF-LPB100` and gets that hostname from DHCP.
  MAC prefix F0:FE:6B (Shanghai High-Flying Electronics Technology).
- The tested FS2PG has no USB port, although the protocol mentions
  `usbonoff`. The firmware is probably shared with models that have one. [guess]

Which chip builds the cloud protocol (MCU or module firmware) is unknown. The
module's own socket settings do not describe the cloud connection (see below),
which suggests custom module firmware built on the Hi-Flying SDK.

## Module AT interface (UDP 48899)

Handshake: send `HF-A11ASSISTHREAD`, the module replies `IP,MAC,MID`; send
`+ok`; then send commands terminated by `\r`; finish with `AT+Q\r`.
`tools/at_probe.py` does this.

Observed values:

| Command     | Reply                                   |
|-------------|-----------------------------------------|
| AT+VER      | `V1.0.07`                               |
| AT+TMODE    | `throughput`                            |
| AT+NETP     | `TCP,Server,8899,10.10.100.254`         |
| AT+SOCKB    | `NONE`                                  |
| AT+UART     | `115200,8,1,None,NFC`                   |
| AT+TCPLK    | `off`                                   |
| AT+WMODE    | `STA`                                   |
| AT+NTPSER   | `61.164.36.105`                         |
| AT+LOGSW    | `off`                                   |
| AT+LOGPORT  | `18626`                                 |
| AT+UDPLCPT  | `0,0`                                   |
| AT+UPURL    | `http://`                               |

`AT+H` returns the stock Hi-Flying command list, no vendor additions.

Despite `AT+NETP`, TCP port 8899 is **closed**. A full TCP scan shows only
port 80 open (stock Hi-Flying web panel, Web Ver 1.0.14). So the module's
transparent socket is not in use and there is no local control channel.

Dangerous commands (do not send unless you know exactly what they do): `AT+RELD`,
`AT+Z`, `AT+UPST`, `AT+UPWEB`, `AT+UPCFG`, `AT+UPAUTO`, and any setter
(`AT+...=`), especially `AT+NETP`, `AT+WMODE`, `AT+WSSSID`, `AT+WSKEY`.

`AT+UDPLCPT` looks like the local UDP ports of the module's two sockets;
`0,0` would mean no fixed port, consistent with the sockets being unused.
[guess]

`AT+UPURL` looks like the module's firmware update URL; the bare `http://`
means none is configured, so the module has no update source of its own.
[guess] Whether the cloud protocol can trigger an update is [unknown].

`AT+LOGSW` / `AT+LOGPORT` look like the module's debug log switch and the UDP
port it would send logs to. [guess] Logging was off. As a test it was turned on (`AT+LOGSW=on`,
accepted, and still `on` after a power cycle), but the module sent nothing
on the network: no datagrams reached a listener on UDP 18626 in another
subnet, and a sniffer on the router saw no packets from the plug on port
18626 at all, before or after the power cycle. [seen, 2026-11-16] The log
probably goes to the UART only, or needs settings we do not know. [guess]
Logging was then turned off again (`AT+LOGSW=off`, confirmed `off`).

## Network

- The plug gets its address, gateway and DNS server from DHCP and resolves
  the cloud's name through that DNS server. [seen]
- The vendor app: "Ferguson Home" (`io.treesat.smarthome`), backend by Treesat.
- Tested setup: a MikroTik router doing the dst-nat, Home Assistant in another
  subnet. The Home Assistant host also had an interface in the plug's subnet,
  so it answered the plug directly and the connection failed until a
  masquerade rule was added (README, "When Home Assistant can reach the plug
  directly"). The redirect rules are in the README.

Capturing traffic on a MikroTik (replace PLUG_IP):

    /tool sniffer set filter-ip-address=PLUG_IP/32 filter-interface=all \
        file-name=ferguson.pcap file-limit=10000 memory-limit=10000
    /tool sniffer start
    /tool sniffer stop

The sniffer records each packet once per interface it crosses;
`tools/decode_pcap.py` removes the duplicates. Captures contain the account
e-mail and the device key in clear text: do not commit them.

## Unrelated project

`github.com/damianniemiec/fs1pg` supports the older FS1PG plug, which uses a
different, LAN-based protocol. It was the starting point of the investigation
and is not expected to work with the FS2PG.
