"""Sensor platform for Ferguson FS2PG."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity

from . import FergusonConfigEntry
from .const import SIGNAL_NEW_PLUG
from .entity import FergusonEntity
from .server import (
    OID_CURRENT,
    OID_ENERGY,
    OID_POWER,
    OID_VOLTAGE,
    EnergyTotal,
    Plug,
)


@dataclass(frozen=True, kw_only=True)
class FergusonSensorDescription(SensorEntityDescription):
    oid: str


# Mapping confirmed with a real load on 2026-11-16: power and current agree
# with V * A, and energy agrees with the integrated power (see protocol.md).
SENSORS: tuple[FergusonSensorDescription, ...] = (
    FergusonSensorDescription(
        key="power",
        oid=OID_POWER,
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    FergusonSensorDescription(
        key="current",
        oid=OID_CURRENT,
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    FergusonSensorDescription(
        key="voltage",
        oid=OID_VOLTAGE,
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    # The plug's own counter: steps every 5 minutes and resets every 20,
    # so it has no state class (no long-term statistics). "Energy total"
    # below is the one for the Energy dashboard.
    FergusonSensorDescription(
        key="energy",
        oid=OID_ENERGY,
        translation_key="energy_counter",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=3,  # the plug reports whole Wh
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FergusonConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    @callback
    def add_plug(plug: Plug) -> None:
        async_add_entities(_entities(plug))

    async_add_entities(
        entity for plug in entry.runtime_data.plugs.values() for entity in _entities(plug)
    )
    entry.async_on_unload(
        async_dispatcher_connect(
            hass, f"{SIGNAL_NEW_PLUG}_{entry.entry_id}", add_plug
        )
    )


class FergusonSensor(FergusonEntity, SensorEntity):
    """One measurement reported by the plug."""

    entity_description: FergusonSensorDescription

    def __init__(self, plug: Plug, description: FergusonSensorDescription) -> None:
        super().__init__(plug)
        self.entity_description = description
        self._attr_unique_id = f"{plug.device_id}_{description.key}"

    @property
    def native_value(self) -> float | None:
        try:
            return float(self._plug.values[self.entity_description.oid])
        except (KeyError, TypeError, ValueError):
            return None


def _entities(plug: Plug) -> list[SensorEntity]:
    return [
        *(FergusonSensor(plug, description) for description in SENSORS),
        FergusonEnergyTotalSensor(plug),
    ]


@dataclass
class _EnergyTotalData(ExtraStoredData):
    total: float
    last: float | None

    def as_dict(self) -> dict[str, Any]:
        return {"total": self.total, "last": self.last}


class FergusonEnergyTotalSensor(FergusonEntity, SensorEntity, RestoreEntity):
    """Running total of the plug's resetting energy counter, kept across restarts."""

    _attr_translation_key = "energy_total"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 3

    def __init__(self, plug: Plug) -> None:
        super().__init__(plug)
        self._attr_unique_id = f"{plug.device_id}_energy_total"
        self._total = EnergyTotal()
        # Only period ends that arrive after the entity is set up are counted.
        self._period_seen = plug.period_energy[0] if plug.period_energy else 0

    @property
    def native_value(self) -> float:
        return self._total.total

    @property
    def extra_restore_state_data(self) -> _EnergyTotalData:
        return _EnergyTotalData(self._total.total, self._total.last)

    async def async_added_to_hass(self) -> None:
        stored = await self.async_get_last_extra_data()
        if stored is not None:
            data = stored.as_dict()
            try:
                total = float(data["total"])
                last = None if data.get("last") is None else float(data["last"])
            except (KeyError, TypeError, ValueError):
                pass
            else:
                self._total = EnergyTotal(total, last)
        self._feed()
        self.async_on_remove(self._plug.add_listener(self._handle_update))

    def _feed(self) -> None:
        if self._plug.energy is not None:
            self._total.update(self._plug.energy)
        period = self._plug.period_energy
        if period is not None and period[0] > self._period_seen:
            self._period_seen = period[0]
            self._total.close(period[1])

    @callback
    def _handle_update(self) -> None:
        self._feed()
        self.async_write_ha_state()
