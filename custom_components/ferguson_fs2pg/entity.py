"""Base entity for Ferguson FS2PG."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .server import Plug


class FergusonEntity(Entity):
    """Entity bound to one plug; state is pushed by the server."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, plug: Plug) -> None:
        self._plug = plug
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, plug.device_id)},
            # The name set in the vendor app, remembered across restarts;
            # __init__.sync_device updates it once the plug reports it.
            name=plug.name or f"Ferguson FS2PG {plug.device_id}",
            manufacturer="Ferguson",
            model="FS2PG",
        )

    @property
    def available(self) -> bool:
        return self._plug.available

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._plug.add_listener(self.async_write_ha_state))
