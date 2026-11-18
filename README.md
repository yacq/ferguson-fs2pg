# Ferguson FS2PG (local) for Home Assistant

Controls the Ferguson Smart Wi-Fi Plug FS2PG without the vendor cloud.

The plug only ever talks to the vendor cloud (`app.ferguson.com.pl:23778`,
plain unencrypted TCP). This integration runs a small TCP server inside Home
Assistant that stands in for that cloud, and your router redirects the plug's
connection to it. Nothing is changed on the plug itself.

Tested with a real plug: on/off, the physical button, voltage, power,
current and energy, over days of continuous use. See
[docs/roadmap.md](docs/roadmap.md) for what is verified and what is open.

## Entities

- Switch: the relay.
- Sensors: voltage, power, current, and "Energy total" for the Energy
  dashboard. The plug's own energy counter covers 20-minute periods: it
  changes every 5 minutes, and at the end of each period the plug reports the
  period's energy and resets the counter. The integration sums the periods
  into a total that survives restarts. The raw counter is a diagnostic
  sensor, "Energy counter (plug)".
- Button (configuration): "Reset energy total" starts the total again from
  zero, for example when a different appliance is plugged in. Nothing is
  sent to the plug. Past statistics stay; the Energy dashboard treats the
  drop as a meter reset.
- Text (configuration): the name stored in the plug (at first, the one
  given in the Ferguson Home app). Changing it renames the plug itself, up to 118 bytes
  (UTF-8); the plug keeps the name across power cycles. The Ferguson Home
  app keeps showing the name stored in the vendor cloud, which this does not
  change. Renaming the device in Home Assistant does not touch the plug.

The device takes the plug's name and MAC address once the plug reports them.

All four measurements are confirmed with a real load. "Energy total" starts
at zero when the integration is first set up and grows in 5-minute steps,
as the plug's counter does. For a smoother curve, an Integral (Riemann)
helper on the power sensor is an alternative.

## Installation

Requires Home Assistant 2026.3 or newer.

1. HACS → Custom repositories → `https://github.com/yacq/ferguson-fs2pg`,
   type: Integration.
2. Install "Ferguson FS2PG (local)" and restart Home Assistant.
3. Settings → Devices & services → Add integration → "Ferguson FS2PG".
   Keep the default port 23778.

The integration is a hub: one entry serves all plugs, and each plug appears
as a device on its own once its traffic is redirected (see below). There is
nothing to add per plug.

Without HACS: download
[ferguson_fs2pg.zip](https://github.com/yacq/ferguson-fs2pg/releases/latest/download/ferguson_fs2pg.zip)
from the latest release, unpack it into
`config/custom_components/ferguson_fs2pg/` and restart Home Assistant.

## Router redirect (MikroTik example)

Replace `PLUG_IP` with the plug's address and `HA_IP` with the address of
Home Assistant. Give the plug a static DHCP lease so the rule keeps matching.

    /ip firewall nat add chain=dstnat src-address=PLUG_IP protocol=tcp \
        dst-port=23778 action=dst-nat to-addresses=HA_IP to-ports=23778 \
        comment="Ferguson FS2PG -> Home Assistant"

The firewall must allow plug → `HA_IP:23778`. After adding the rule, unplug
the plug and plug it back in (NAT only affects new connections). The device
appears in Home Assistant after its first connection.

### When Home Assistant can reach the plug directly

If the Home Assistant host has any network interface in the plug's subnet
(the same subnet, or a second interface), it answers the plug directly instead
of through the router. The plug then gets replies from the wrong address and
the connection never opens: the NAT counters grow, but no device appears.

Add a masquerade rule so Home Assistant sees the router as the client and
replies through it:

    /ip firewall nat add chain=srcnat src-address=PLUG_IP \
        dst-address=HA_IP protocol=tcp dst-port=23778 \
        action=masquerade place-before=0 \
        comment="Ferguson FS2PG -> HA: force return path via router"

Then remove the stale connections and power-cycle the plug:

    /ip firewall connection remove [find src-address~"PLUG_IP:" dst-address~":23778"]

Home Assistant will log the router's address as the plug's peer; this is
expected, the plug is identified by its login.

Any router that can do destination NAT works the same way.

## Allowed sources (optional)

By default the server accepts a plug from any address. To restrict it:
Settings → Devices & services → Ferguson FS2PG → Configure, and enter the
allowed IP addresses or networks, comma separated. With the masquerade rule
above, the plug connects from the router's address, so enter that one. A
refused connection is logged once per address as a warning.

## Going back to the vendor app

Disable the NAT rule and power-cycle the plug.

## Limitations

- While the redirect is active, the Ferguson Home app cannot see the plug.
- Schedules and countdown timers from the app are not supported; use Home
  Assistant automations.
- Home Assistant in Docker without `network_mode: host` needs port 23778
  published.
- The server does not check the device key. Use allowed sources and do not
  expose the port outside your network.

## Calibration

The plug's metering is not calibrated to a reference; one unit read about 5%
high. Every unit may differ, so the integration reports the raw values and
leaves correction to Home Assistant.

1. Check against a trusted meter with a resistive load (kettle, heater,
   incandescent bulb). With motors or power supplies, meters can disagree
   because of the power factor; the plug reports real power (W), not VA.
2. Correct power with the built-in `compensation` integration (one set of
   data points per plug):

       compensation:
         fs2pg_power_corrected:
           source: sensor.ferguson_fs2pg_DEVICE_ID_power
           unit_of_measurement: W
           data_points:
             - [0, 0]
             - [1050, 1000]

3. The plug computes energy from its own power reading, so energy is off by
   the same factor. Scale it with a template sensor and keep it a
   `total_increasing` energy sensor if you use it in the Energy dashboard:

       template:
         - sensor:
             - name: FS2PG energy corrected
               unit_of_measurement: kWh
               device_class: energy
               state_class: total_increasing
               state: "{{ states('sensor.ferguson_fs2pg_DEVICE_ID_energy_total') | float(0) / 1.05 }}"
               availability: "{{ has_value('sensor.ferguson_fs2pg_DEVICE_ID_energy_total') }}"

## Debug logging

    logger:
      logs:
        custom_components.ferguson_fs2pg: debug

## Development

    python3 -m unittest discover -s tests -v            # protocol tests, stdlib only
    python3 tools/fake_plug.py --port 23778 --toggle-every 15

- [docs/protocol.md](docs/protocol.md): reverse-engineered wire protocol.
- [docs/hardware.md](docs/hardware.md): what is inside the plug.
- [docs/roadmap.md](docs/roadmap.md): status, open questions, ideas.
- `tests/fixtures/`: sanitized recordings of real sessions with the vendor
  cloud, replayed by the tests.
- `tools/`: a simulated plug (`fake_plug.py`), a capture decoder
  (`decode_pcap.py`), a read-only probe of the Wi-Fi module
  (`at_probe.py`), and the icon generator (`make_icon.py`).

Releasing: bump `version` in `manifest.json`, commit, then push an annotated
tag `vX.Y.Z` whose message is the release notes. The Release workflow checks
the version, runs the tests and publishes the GitHub release that HACS shows,
with `ferguson_fs2pg.zip` (the integration folder) attached; HACS installs
from that zip.

Not affiliated with Ferguson or Treesat.

## License

Apache License 2.0, see [LICENSE](LICENSE).
