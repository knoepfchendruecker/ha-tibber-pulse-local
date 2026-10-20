import logging
from datetime import timedelta

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import StateType
from homeassistant.util import dt as dt_util, slugify

from . import TibberLocalDataUpdateCoordinator, TibberLocalEntity
from .const import (
    DOMAIN,
    CONF_OBIS_CODES,
    SENSOR_TYPES,
    DEFAULT_OBIS_CODES,
    OBIS_DATA_KEY,
    METRICS_KEY,
)

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities):
    coordinator = hass.data[DOMAIN][config_entry.entry_id]
    entities = []
    obis_values = coordinator.data.get(OBIS_DATA_KEY, {}) if coordinator.data else {}
    metrics_values = coordinator.data.get(METRICS_KEY, {}) if coordinator.data else {}

    available_sensors = list(obis_values.keys()) if obis_values else None
    if available_sensors:
        _LOGGER.info(f"available obis codes found: {available_sensors}")
    else:
        available_sensors = config_entry.data.get(CONF_OBIS_CODES, [])
        _LOGGER.warning(f"no sensors found @ bridge [we check, if we have stored obis codes in our config_entry: {available_sensors}]")

    if available_sensors is None or len(available_sensors) == 0:
        _LOGGER.warning(f"could not detect available obis codes using just 'import total' and 'power current' as default!")
        # ok looks like, that we do not have any information about available sensors - so we just use two simple
        # obis codes 'import total' and 'power current'
        available_sensors = DEFAULT_OBIS_CODES

    for description in SENSOR_TYPES:
        if description.tag is None:
            _LOGGER.warning(f"no tag found for sensor description key: {description.key} - please create a issue on github!")
            continue

        should_add = False
        if description.tag.section == METRICS_KEY:
            should_add = bool(metrics_values)
        elif description.tag.section == OBIS_DATA_KEY:
            keys_to_check = [description.tag.key]
            if description.tag.aliases:
                keys_to_check.extend(description.tag.aliases)
            should_add = any(sensor_key in available_sensors for sensor_key in keys_to_check)

        if should_add:
            entities.append(TibberLocalSensor(coordinator, description))

    async_add_entities(entities)


class TibberLocalSensor(TibberLocalEntity, SensorEntity):
    def __init__(
            self,
            coordinator: TibberLocalDataUpdateCoordinator,
            description: SensorEntityDescription
    ):
        """Initialize a singular value sensor."""
        super().__init__(coordinator=coordinator, description=description)

        key = self.entity_description.key.lower()
        self.entity_id = f"{Platform.SENSOR}.{slugify(self.coordinator._config_entry.title)}_{key}".lower()

        # we use the "key" also as our internal translation-key - and EXTREMELY important we have
        self._attr_translation_key = key

        if hasattr(description, 'suggested_display_precision') and description.suggested_display_precision is not None:
            self._attr_suggested_display_precision = description.suggested_display_precision
        else:
            self._attr_suggested_display_precision = 2

        # 'SensorDeviceClass.UPTIME' bookkeeping (for the dedicated 'node_uptime' sensor):
        # remember the counter value we derived the boot-time from, so it is only
        # re-derived when the counter actually changes
        self._uptime_ms_last: float | None = None
        self._uptime_dt = None

    @property
    def native_value(self) -> StateType:
        if self.coordinator.data is not None:
            value = self.coordinator.get_sensor_value(self.entity_description.tag)

            # 'SensorDeviceClass.UPTIME' expects the boot-time as a tz-aware datetime - but the
            # bridge reports the node uptime as a raw millisecond counter -> convert it
            if self.entity_description.device_class == SensorDeviceClass.UPTIME and value is not None:
                try:
                    ms_count = float(value)
                except (TypeError, ValueError):
                    _LOGGER.warning(f"could not convert {self.entity_description.key} value '{value}' into a boot-time datetime!")
                    self._uptime_ms_last = None
                    self._uptime_dt = None
                    return None

                # the bridge can report the same uptime counter across consecutive metrics
                # updates (measured: the value changes only every ~30 min in steady operation,
                # never per message) -> only re-derive the boot-time when the counter itself
                # changes, otherwise the state would drift forward by the metrics-update
                # interval on every update
                if ms_count != self._uptime_ms_last or self._uptime_dt is None:
                    self._uptime_ms_last = ms_count
                    self._uptime_dt = dt_util.utcnow() - timedelta(milliseconds=ms_count)
                return self._uptime_dt

            return value
        return None

    @property
    def available(self):
        super_val = super().available
        if super_val:
            if self.entity_description.tag.section == OBIS_DATA_KEY and len(self.coordinator.data.get(OBIS_DATA_KEY, {})) == 0:
                return False

            if self.entity_description.tag.section == METRICS_KEY and len(self.coordinator.data.get(METRICS_KEY, {})) == 0:
                return False

        return super_val