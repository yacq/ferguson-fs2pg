# Status and roadmap

## Verified

- Protocol: frame format and all six frame pairs, against two recorded
  sessions with the vendor cloud (unit tests replay them).
- Home Assistant 2026.3 (the minimum) and 2026.11, with `tools/fake_plug.py`:
  config flow and single-instance abort, entities created on first
  connection, state from polls and notifications, switch, entities
  unavailable on disconnect and restored after a restart, device name and MAC
  in the device registry, options flow for allowed sources, energy total
  across period ends and restarts, no deprecation warnings.
- Real plug (November 2026), installed from HACS:
  - connects through dst-nat (plus masquerade, see README); the plug accepts
    `SID` = `homeassistant` and `TEID` = `7246`;
  - relay on/off from Home Assistant, the physical button reflected at once;
  - power, current, voltage and energy mapping confirmed under load;
  - the connection stayed up for more than a day;
  - "Energy total" over a day matched a Shelly plug connected in series; the
    small difference matched the difference in measured power;
  - rename: the plug keeps a name set from Home Assistant across a power
    cycle; the vendor app shows the cloud's name instead, and the cloud does
    not write it back to the plug.

## To verify on real hardware

1. Reconnect after a Home Assistant restart: how long until the plug is back.
2. What the "electricity" notification sent right after connecting reports
   (ignored for now): the last period's value again, or a period that ended
   while the plug was offline? Test: restart Home Assistant mid-period, and
   once with the downtime spanning a period end, with debug logging on.

Enable debug logging while testing:

    logger:
      logs:
        custom_components.ferguson_fs2pg: debug

## Open questions

- Meaning of 2.1.1.4.5. The tested plug has no USB port, so it and
  `usbonoff` probably belong to other models sharing the firmware.
- How persistently the plug reconnects when the server stays down for long.
- Whether the device key is tied to the account or to the hardware.

## Ideas

- Diagnostics download (redacting the key) and a "last seen" sensor.
- Options flow for the poll interval.
- Alternative to dst-nat: a static DNS entry for `app.ferguson.com.pl`.
  Simpler, but it also breaks the vendor app for every device on that DNS.
