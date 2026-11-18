"""Switch platform for Ferguson FS2PG."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import FergusonConfigEntry
from .const import SIGNAL_NEW_PLUG
from .entity import FergusonEntity
from .server import Plug


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FergusonConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    @callback
    def add_plug(plug: Plug) -> None:
        async_add_entities([FergusonSwitch(plug)])

    async_add_entities(FergusonSwitch(p) for p in entry.runtime_data.plugs.values())
    entry.async_on_unload(
        async_dispatcher_connect(
            hass, f"{SIGNAL_NEW_PLUG}_{entry.entry_id}", add_plug
        )
    )


class FergusonSwitch(FergusonEntity, SwitchEntity):
    """The plug relay."""

    _attr_name = None
    _attr_device_class = SwitchDeviceClass.OUTLET

    def __init__(self, plug: Plug) -> None:
        super().__init__(plug)
        self._attr_unique_id = f"{plug.device_id}_relay"

    @property
    def is_on(self) -> bool | None:
        return self._plug.is_on

    async def _set(self, on: bool) -> None:
        try:
            await self._plug.async_set_relay(on)
        except OSError as err:
            raise HomeAssistantError(f"Plug is not reachable: {err}") from err

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)
