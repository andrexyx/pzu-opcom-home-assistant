"""Native sensors for PZU OPCOM."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import (
    DOMAIN,
    ENTITY_ICONS,
    ENTITY_NAMES,
    PzuRuntime,
    SOURCE_URL,
)


async def async_setup_entry(
    hass: HomeAssistant,
    _entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up PZU OPCOM sensors."""
    runtime: PzuRuntime = hass.data[DOMAIN]

    registry = er.async_get(hass)
    for desired_entity_id in ENTITY_NAMES:
        unique_id = desired_entity_id.split(".", 1)[1]
        current_entity_id = registry.async_get_entity_id(
            "sensor", DOMAIN, unique_id
        )
        if current_entity_id and current_entity_id != desired_entity_id:
            registry.async_update_entity(
                current_entity_id, new_entity_id=desired_entity_id
            )

    entities = [
        PzuSensor(runtime, entity_id)
        for entity_id in ENTITY_NAMES
    ]
    runtime.entities.extend(entities)
    async_add_entities(entities)


class PzuSensor(SensorEntity):
    """A sensor backed by the shared PZU OPCOM runtime."""

    _attr_has_entity_name = False

    def __init__(self, runtime: PzuRuntime, entity_id: str) -> None:
        self.runtime = runtime
        self.entity_key = entity_id
        object_id = entity_id.split(".", 1)[1]
        self._attr_unique_id = object_id
        self._attr_suggested_object_id = object_id
        self._attr_name = ENTITY_NAMES[entity_id]
        self._attr_icon = ENTITY_ICONS[entity_id]

    @property
    def device_info(self) -> dict[str, Any]:
        """Return the shared PZU OPCOM device."""
        return {
            "identifiers": {(DOMAIN, DOMAIN)},
            "name": "PZU OPCOM",
            "manufacturer": "OPCOM",
            "configuration_url": SOURCE_URL,
        }

    @property
    def native_value(self) -> Any:
        """Return the latest value fetched from OPCOM."""
        return self.runtime.values.get(self.entity_key)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return runtime metadata for this sensor."""
        return self.runtime.attributes.get(self.entity_key)

    async def async_added_to_hass(self) -> None:
        """Refresh the entity when it is first added."""
        await super().async_added_to_hass()
        self.async_write_ha_state()
