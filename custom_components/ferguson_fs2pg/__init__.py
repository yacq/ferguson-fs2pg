"""Ferguson FS2PG smart plug, controlled locally."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PORT, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    CONF_ALLOWED_SOURCES,
    CONF_DEVICES,
    CONF_NAMES,
    DOMAIN,
    SIGNAL_NEW_PLUG,
)
from .server import FergusonServer, Plug

PLATFORMS = [Platform.BUTTON, Platform.SENSOR, Platform.SWITCH, Platform.TEXT]

type FergusonConfigEntry = ConfigEntry[FergusonServer]


async def async_setup_entry(hass: HomeAssistant, entry: FergusonConfigEntry) -> bool:
    """Start the local server and set up platforms."""

    @callback
    def remember(plug: Plug) -> None:
        """Store the plug's ID and name so they survive a restart."""
        devices = list(entry.data.get(CONF_DEVICES, []))
        names = dict(entry.data.get(CONF_NAMES, {}))
        if plug.device_id not in devices:
            devices.append(plug.device_id)
        if plug.name:
            names[plug.device_id] = plug.name
        if devices != entry.data.get(CONF_DEVICES) or names != entry.data.get(
            CONF_NAMES, {}
        ):
            hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_DEVICES: devices, CONF_NAMES: names}
            )

    @callback
    def sync_device(plug: Plug) -> None:
        """Copy the plug's name and MAC into the device registry when learned."""
        remember(plug)
        registry = dr.async_get(hass)
        identifier = (DOMAIN, plug.device_id)
        device = next(
            (
                d
                for d in dr.async_entries_for_config_entry(registry, entry.entry_id)
                if identifier in d.identifiers
            ),
            None,
        )
        if device is None:  # entities not added yet; the next update retries
            return
        changes: dict = {}
        if plug.name and device.name != plug.name:
            changes["name"] = plug.name
        if plug.mac:
            connection = (dr.CONNECTION_NETWORK_MAC, dr.format_mac(plug.mac))
            if connection not in device.connections:
                changes["new_connections"] = {*device.connections, connection}
        if changes:
            registry.async_update_device(device.id, **changes)

    def track(plug: Plug) -> None:
        entry.async_on_unload(plug.add_listener(lambda: sync_device(plug)))

    @callback
    def on_new_plug(plug: Plug) -> None:
        remember(plug)
        track(plug)
        async_dispatcher_send(hass, f"{SIGNAL_NEW_PLUG}_{entry.entry_id}", plug)

    names = entry.data.get(CONF_NAMES, {})
    known = {i: names.get(i) for i in entry.data.get(CONF_DEVICES, [])}
    server = FergusonServer(
        entry.data[CONF_PORT],
        known,
        on_new_plug,
        allowed_sources=entry.options.get(CONF_ALLOWED_SOURCES, []),
    )
    for plug in server.plugs.values():
        track(plug)
    try:
        await server.async_start()
    except OSError as err:
        raise ConfigEntryNotReady(
            f"Cannot listen on port {entry.data[CONF_PORT]}: {err}"
        ) from err

    entry.runtime_data = server
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: FergusonConfigEntry) -> bool:
    """Stop the server and unload platforms."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_stop()
    return unloaded
