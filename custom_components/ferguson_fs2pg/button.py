"""Button platform for Ferguson FS2PG: reset the energy total."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
    async_dispatcher_send,
)
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import FergusonConfigEntry
from .const import SIGNAL_NEW_PLUG, SIGNAL_RESET_ENERGY
from .entity import FergusonEntity
from .server import Plug


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FergusonConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    @callback
    def add_plug(plug: Plug) -> None:
        async_add_entities([FergusonResetEnergyButton(plug)])

    async_add_entities(
        FergusonResetEnergyButton(p) for p in entry.runtime_data.plugs.values()
    )
    entry.async_on_unload(
        async_dispatcher_connect(
            hass, f"{SIGNAL_NEW_PLUG}_{entry.entry_id}", add_plug
        )
    )


class FergusonResetEnergyButton(FergusonEntity, ButtonEntity):
    """Start "Energy total" again from zero.

    Only the integration's total is reset; nothing is sent to the plug. The
    Energy dashboard treats the drop as a meter reset, so past statistics
    stay as they are.
    """

    _attr_translation_key = "reset_energy_total"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, plug: Plug) -> None:
        super().__init__(plug)
        self._attr_unique_id = f"{plug.device_id}_reset_energy_total"

    @property
    def available(self) -> bool:
        return True  # the total lives in Home Assistant, not in the plug

    async def async_press(self) -> None:
        async_dispatcher_send(
            self.hass, f"{SIGNAL_RESET_ENERGY}_{self._plug.device_id}"
        )
