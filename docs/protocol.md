# FS2PG cloud protocol

Reverse-engineered from one capture (164 s, 228 frames) of a real plug talking
to the vendor cloud on 2026-11-16. Firmware: Wi-Fi module `V1.0.07`.
A sanitized copy of that session is in `tests/fixtures/session.jsonl`: the
account e-mail, device key, MAC, device ID and device name are replaced with
placeholders of the same byte length (`some.user@example.com`,
`0123456789abcdef`, `F0FE6B000001`, `a1b2c3d4`, `Ładowarka tel`), so length
fields stay valid. Device IDs and names in this document are those
placeholders.

Confidence markers: **[seen]** observed in the capture, **[guess]** inferred,
**[unknown]** not understood.

## Transport

- The plug resolves `app.ferguson.com.pl` (A record, at the time
  188.68.249.43) through the DHCP-provided DNS server and opens one TCP
  connection to port **23778**. [seen]
- No TLS, no server authentication. [seen]
- The plug is always the TCP client. It reconnects after power-up; reconnect
  behaviour after a dropped connection has not been measured. [unknown]
- Source port was 49153 in every observed connection. [seen]
- The plug also sends NTP requests to 61.164.36.105, which never answers. [seen]
  The module has this address configured as a literal IP (`AT+NTPSER`), so
  there is no DNS name behind it. The address has no reverse DNS; APNIC lists
  the range as Shanghai Jiao Tong University on China Telecom Zhejiang. It is
  probably a default inherited from the Hi-Flying firmware. [guess]

## Framing

    +--------+---------------------+-----------+
    | type 1 | length 4 (big end.) | payload   |
    +--------+---------------------+-----------+

Several frames may arrive in one TCP segment, and one frame may be split. [seen]

| Type | Sender | Meaning                     | Acknowledged by |
|------|--------|-----------------------------|-----------------|
| 0x20 | plug   | login                       | 0x28            |
| 0xC0 | plug   | time request                | 0xC8            |
| 0xD0 | plug   | ping                        | 0xD8            |
| 0x70 | cloud  | request (JSON)              | 0x78            |
| 0x80 | plug   | reply or telemetry (JSON)   | 0x88            |
| 0x40 | plug   | state-change notification   | 0x48            |

Pattern: an acknowledgement type is the request type + 8.

## Login: 0x20 / 0x28

    20 0000001a  02  a1b2c3d4  0010  <16 ASCII chars>  00 00 3c

| Bytes        | Meaning                                              |
|--------------|------------------------------------------------------|
| `02`         | [unknown] constant (protocol version?)               |
| `a1b2c3d4`   | device ID, 4 bytes. Used in every later plug frame. [seen] |
| `0010`       | key length (16) [guess]                              |
| 16 chars     | device key, lowercase hex characters [seen]          |
| `00 00 3c`   | [unknown]; 0x3c = 60 may be a keep-alive in seconds [guess] |

Reply: `28 00000002 0000` [seen]. Presumably a status code [guess]. The
integration does not check the key.

## Time: 0xC0 / 0xC8

Request payload: `0c`. Sent twice right after login. [seen]

Reply payload (11 bytes), example `0d 07ea 0b 10 02 14 08 0e 0320`:

| Bytes  | Meaning                                         |
|--------|-------------------------------------------------|
| `0d`   | [unknown] constant                              |
| `07ea` | year 2026                                       |
| `0b`   | month 11                                        |
| `10`   | day 16                                          |
| `02`   | weekday, Sunday = 1 (the 16th was a Monday) [guess] |
| `14 08 0e` | 20:08:14, which is UTC + 8 h [seen]         |
| `0320` | 800, i.e. time zone +08:00 [guess]              |

The cloud sends Beijing time with zone 800, then sets the plug's own zone with
OID `2.1.1.2.1.7` (see below). The integration sends UTC+8 / 800 the same way.

Oddity: timestamps the plug later puts in 0x40 frames ran about 45 s ahead of
the time the cloud had sent. [unknown]

With the integration (UTC+8 / 800 sent), the 0x40 `time` was UTC, 17 to
25 s ahead of Home Assistant's clock. [seen, debug logs]

## Ping: 0xD0 / 0xD8

Empty payload both ways. The plug pings every 20 s. [seen] The integration
drops the connection after 90 s of silence.

## Cloud request: 0x70 / 0x78

    70 <len>  00001c4e  <id 2 bytes>  00  <JSON, no terminator>

- `00001c4e` = 7246 decimal, the same number as the JSON `TEID`. Probably a
  tenant/terminal ID of the sender. [guess] Whether the plug validates it is
  [unknown]; the integration reuses 7246.
- The 2-byte id looks random and was once reused for two frames. [seen]
- The plug acknowledges with `78 00000007` + the same 7 header bytes. [seen]

JSON, compact, keys in this order [seen]:

    {"SN":957799,"CID":30011,"SID":"<sender>","TEID":"7246ferguson","PL":{...}}

| Key  | Meaning                                                        |
|------|----------------------------------------------------------------|
| SN   | request number (6 digits observed), echoed in the reply        |
| CID  | command: 30011 = set, 30021 = get                              |
| SID  | who sent it: the app user's e-mail, or `treesat.io` for the cloud itself |
| TEID | `7246ferguson` from the app, `7246` from the cloud             |
| PL   | set: `{oid: value, ...}`; get: `{"OID": [oid, ...]}`           |

The plug accepted both TEID forms. The integration sends `SID` =
`homeassistant`, `TEID` = `7246`, and the real plug obeys relay sets sent
that way. [seen, 2026-11-16, in Home Assistant]

## Plug data: 0x80 / 0x88

    80 <len>  a1b2c3d4  <seq 2>  00  aa  <json len 2>  <JSON>[\r\n]  ff

- `seq` increments by one per 0x80 frame, starting at 1 after login. [seen]
- `json len` counts the JSON plus the optional `\r\n`. [seen]
- Replies to requests end with `\r\n`; unsolicited telemetry does not. [seen]
- `ff` trailer is constant. [seen]
- The JSON starts at payload byte 10 in both 0x80 and 0x40 frames, and the
  length fields matched the JSON in all 56 recorded frames. [seen] Parse by
  the length field: when the JSON is 123 bytes long the length byte is 0x7b,
  the same as `{`.
- Ack: `88 00000007` + first 7 payload bytes (device ID, seq, 00). [seen]

Reply to a set (CID 30012). Note TEID is always `"999"` here:

    {"PL":{"2.1.1.4.1":false},"TEID":"999","RC":1,"SN":957799,"CID":30012,"SID":""}

Reply to a get (CID 30022), TEID echoed from the request:

    {"PL":{"2.1.1.4.1":true,"2.1.1.4.5":true},"TEID":"7246ferguson","RC":1,"SN":205357,"CID":30022,"SID":""}

Unsolicited telemetry (CID 30022, `SN` and `SID` null):

    {"PL":{"2.1.1.4.2":"0.00","2.1.1.4.3":"0.00","2.1.1.4.4":"236.03","2.1.1.4.7":"0.000"},"RC":1,"SN":null,"CID":30022,"SID":null}

Telemetry arrived at irregular intervals (31 s, 4 s, 88 s) with no load, and
every 8 s while a load was changing. [seen] It may be sent when a value
changes. [guess] Every telemetry frame carried all four measurements (4.2,
4.3, 4.4, 4.7). [seen] `RC` was always 1; presumably "OK". [guess]

### Energy counter (2.1.1.4.7)

The counter holds the energy of the current 20-minute period, in kWh.
[seen, 2026-11-17, debug log with a load of about 920 W switched on and off]

- Telemetry reports it with three decimals (`"0.153"`); a reply to a get
  reports the same counter 10 times higher with two decimals (`"1.53"`).
  The factor was 10 at every value seen (11, 71 and 153 Wh). [seen] The
  integration divides a get reply by 10.
- It changes every 5 minutes, three times per period; telemetry and replies
  show the same steps, so polling more often does not make it more current.
  [seen] Each step matched the load.
- At the end of each period the plug sends an "electricity" notification
  (0x40, see below) with the period's final energy, and right after it a
  telemetry frame with the counter at `"0.000"`. The notification came about
  90 ms before the telemetry. [seen] Period ends in the log, by Home
  Assistant's clock: 12:59:36, 13:19:36, 13:39:36, 13:59:36; the plug's clock
  ran about 25 s ahead, so they are probably :00, :20 and :40 by the plug's
  clock. [guess]

The energy of a period's last 5 minutes appears only in the notification:

| Period        | Counter steps        | "electricity" | Integrated power |
|---------------|----------------------|---------------|------------------|
| 12:59 - 13:19 | (no load)            | 0.000         | 0.000            |
| 13:19 - 13:39 | 0.074, 0.132         | 0.177         | 0.172            |
| 13:39 - 13:59 | 0.035, 0.062, 0.087  | 0.118         | 0.116            |

"Energy total" adds the counter's rises and each period's final value; it
accepts the notification and the reset in either order.

## Physical button

A press produced two frames at once [seen, 2026-11-16, debug log]:

- a 0x80 frame shaped like a reply to a set, with the plug's own `SN`
  (5002) since there was no request:
  `{"PL":{"2.1.1.4.1":false},"TEID":"999","RC":1,"SN":5002,"CID":30012,"SID":""}`
- a 0x40 notification with `"SID":"Manually"`.

Home Assistant showed the change immediately.

## Notification: 0x40 / 0x48

Sent by the plug after each relay change, whether triggered from the app
(`SID` = the sender's SID) or by the physical button (`SID` = `Manually`).
[seen]

    40 <len>  a1b2c3d4  01 01 0e  00 90  <json len 1>  <JSON>

    {"message":{"onoff":false,"time":"2026-11-16 12:10:1","SID":"<user>"},"plugMac":"F0FE6B000001"}

- `01 01 0e 00 90` was constant. [unknown]
- The JSON length is one byte. JSONs observed were 109 to 111 bytes; what the
  plug does with a JSON over 255 bytes (long `SID`) is [unknown].
- `time` is not zero-padded. [seen]
- Ack: `48 00000007` + first 7 payload bytes. The cloud once sent the same ack
  twice. [seen]

This looks like the source of push notifications in the vendor app. [guess]

An "electricity" message ends every 20-minute energy period, with that
period's energy in kWh (see "Energy counter" above). [seen]

    {"message":{"electricity":"0.177","time":"2026-11-17 23:59:59"},"plugMac":"F0FE6B000001"}

Its `time` was always the end of the current day, not the end of the period.
[seen] The plug also sends one 2 s after connecting; the only one recorded
came shortly after a power cycle, with `0.000`. What it reports otherwise is
[unknown], so the integration ignores an "electricity" message received
within 30 s of login.

## OIDs

| OID            | Type            | Meaning                                    | Confidence |
|----------------|-----------------|--------------------------------------------|------------|
| 2.1.1.4.1      | bool            | relay on/off (get and set)                 | seen       |
| 2.1.1.4.2      | string "0.00"   | power, W                                   | seen       |
| 2.1.1.4.3      | string "0.00"   | current, A                                 | seen       |
| 2.1.1.4.4      | string "236.03" | mains voltage, V                           | seen       |
| 2.1.1.4.5      | bool, was true  | unknown; maybe a USB switch on other models | unknown   |
| 2.1.1.4.7      | string "0.000"  | energy of the 20-min period, kWh (get ×10) | seen       |
| 2.1.1.4.253    | string          | device name (UTF-8), get and set (rename)  | seen       |
| 2.1.1.2.1.7    | string "+0"     | time zone (meaning guessed), set after login | seen     |
| 2.1.1.2.2      | list, was []    | schedules                                  | guess      |
| 2.1.1.2.3      | list of 2       | countdown timers: `{"leftTime":0,"PL":{"onoff":false}}` and the same with `usbonoff` | seen (meaning guessed) |

All measurements were zero in the recorded sessions. A debug log with a real
load (about 44 W, then up to 1754 W) confirmed the mapping: power and current
agree with V × A (1754 W at 228 V and 7.83 A), and energy agrees with the
integrated power. Current and
power do not always update in the same telemetry frame. [seen, 2026-11-16]
`usbonoff` and 2.1.1.4.5 may belong to models with a switchable USB port:
the tested FS2PG has no USB port, so the firmware is probably shared with
such models. [guess]

## Session start as performed by the cloud

1. plug: 0x20 login → cloud: 0x28
2. plug: 0xC0 → cloud: 0xC8 (twice)
3. about 5 s later, cloud: set `{"2.1.1.2.1.7":"+0"}` with SID `treesat.io`
4. cloud: get `["2.1.1.2.2"]`
5. afterwards: pings, telemetry, and whatever the app requests. When the app
   opens the device screen it gets 2.1.1.2.3, 2.1.1.4.253, [4.1, 4.5] and
   [4.2, 4.7] in separate requests, repeated about once per second.

Steps 3 and 4 were the same in a second capture (2026-11-16, renames):
the zone set came 5 s after the session start, again with SID `treesat.io`,
TEID `7246`, and the plug replied with a normal set reply (CID 30012,
TEID "999"). [seen]

The integration does step 3 the same way, 5 s after login, then
gets [4.253] (the name) once, as the app does, then polls [4.1, 4.5] and
[4.2, 4.3, 4.4] and the app's own [4.2, 4.7] every 60 s. It skips step 4
(schedules are not used). Energy (4.7) from a get reply is divided by 10
(see above), so the counter is known a few seconds after connecting.

## Rename

Seen three times in the second capture [seen]: the app sends a set,

    {"SN":952313,"CID":30011,"SID":"<user>","TEID":"7246ferguson","PL":{"2.1.1.4.253":"Zażòłć gęsta jaźni"}}

and the plug echoes the new name in a normal set reply with `RC` 1:

    {"PL":{"2.1.1.4.253":"Zażòłć gęsta jaźni"},"TEID":"999","RC":1,"SN":952313,"CID":30012,"SID":""}

- Non-ASCII characters are sent as raw UTF-8, not as `\uXXXX` escapes.
  [seen] The integration sends all JSON that way (`ensure_ascii=False`).
- The longest name accepted and echoed was 118 bytes (118 ASCII characters);
  a name with Polish letters was 18 characters, 24 bytes. The integration
  limits names to 118 bytes, the largest value seen. Whether the plug
  accepts more, and what the app's own limit is, are [unknown].
- No 0x40 notification follows a rename (nor the time-zone set): 0x40 came
  only after relay changes. [seen]
- The plug stores the name: after a rename from Home Assistant and a power
  cycle it reported the new name. [seen, 2026-11-18]
- The vendor app shows the name kept in the cloud, never the plug's: after
  a rename from Home Assistant it still showed the old one. Back on the
  cloud, the cloud did not set the name on the plug again. [seen, same test]
