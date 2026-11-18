"""Text platform for Ferguson FS2PG: the name stored in the plug."""

from __future__ import annotations

from homeassistant.components.text import TextEntity, TextMode
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import FergusonConfigEntry
from .const import SIGNAL_NEW_PLUG
from .entity import FergusonEntity
from .server import NAME_MAX_BYTES, Plug


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FergusonConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    @callback
    def add_plug(plug: Plug) -> None:
        async_add_entities([FergusonNameText(plug)])

    async_add_entities(FergusonNameText(p) for p in entry.runtime_data.plugs.values())
    entry.async_on_unload(
        async_dispatcher_connect(
            hass, f"{SIGNAL_NEW_PLUG}_{entry.entry_id}", add_plug
        )
    )


class FergusonNameText(FergusonEntity, TextEntity):
    """The plug's own name, as shown in the vendor app.

    Renaming the device in Home Assistant does not change it; this does.
    """

    _attr_translation_key = "plug_name"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = TextMode.TEXT
    _attr_native_min = 1
    # Characters; the byte limit (UTF-8) is checked in async_set_value.
    _attr_native_max = NAME_MAX_BYTES

    def __init__(self, plug: Plug) -> None:
        super().__init__(plug)
        self._attr_unique_id = f"{plug.device_id}_name"

    @property
    def native_value(self) -> str | None:
        return self._plug.name

    async def async_set_value(self, value: str) -> None:
        try:
            await self._plug.async_set_name(value)
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        except OSError as err:
            raise HomeAssistantError(f"Plug is not reachable: {err}") from err
