"""Support for Kamereon cars."""
from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass
from homeassistant.const import EntityCategory, STATE_UNKNOWN

from .base import KamereonEntity
from .kamereon import ChargingStatus, PluggedStatus, LockStatus, Feature, HEALTH_LAMPS
from .const import DOMAIN, DATA_VEHICLES, DATA_COORDINATOR_FETCH

# 機能可用性マップのキー -> 翻訳キー / アイコン
HEALTH_LAMP_TRANSLATIONS = {
    'brakeWarning': 'warning_brake',
    'absWarning': 'warning_abs',
    'airbagWarning': 'warning_airbag',
    'lampRequest': 'warning_mil',
    'oilPressureWarning': 'warning_oil_pressure',
    'tyrePressureWarning': 'warning_tyre_pressure',
    'parkingLightWarning': 'warning_parking_light',
    'powerSteeringWarning': 'warning_power_steering',
    'batteryWarning': 'warning_battery',
    'powerLimitationAlert': 'warning_power_limitation',
}

HEALTH_LAMP_ICONS = {
    'brakeWarning': 'mdi:car-brake-alert',
    'absWarning': 'mdi:car-brake-abs',
    'airbagWarning': 'mdi:airbag',
    'lampRequest': 'mdi:engine',
    'oilPressureWarning': 'mdi:oil',
    'tyrePressureWarning': 'mdi:car-tire-alert',
    'parkingLightWarning': 'mdi:car-parking-lights',
    'powerSteeringWarning': 'mdi:steering',
    'batteryWarning': 'mdi:car-battery',
    'powerLimitationAlert': 'mdi:speedometer-slow',
}

async def async_setup_entry(hass, config, async_add_entities):
    """Set up the Kamereon sensors."""
    account_id = config.data['email']

    data = hass.data[DOMAIN][account_id][DATA_VEHICLES]
    coordinator = hass.data[DOMAIN][account_id][DATA_COORDINATOR_FETCH]

    entities = []

    for vehicle in data:
        car = data[vehicle]

        if Feature.BATTERY_STATUS in car.features:
            entities += [ChargingStatusEntity(coordinator, car),
                         PluggedStatusEntity(coordinator, car)]

        # アプリの機能可用性マップがあればそれを優先し、
        # 無ければ従来どおり services の feature で判定する
        lock_available = car.app_available('vehicleStatus', 'lockStatus')
        if lock_available is None:
            lock_available = Feature.LOCK_STATUS_CHECK in car.features
        if lock_available:
            entities.append(LockStatusEntity(coordinator, car))

        if car.app_available('vehicleStatus', 'generalDoorStatus'):
            entities.append(DoorsOpenEntity(coordinator, car))

        for lamp_key in car.available_health_lamps():
            entities.append(HealthLampEntity(coordinator, car, lamp_key))

        if car.fuel_low_warning is not None:
            entities.append(FuelLowWarningEntity(coordinator, car))

    async_add_entities(entities, update_before_add=True)


class ChargingStatusEntity(KamereonEntity, BinarySensorEntity):
    """Representation of charging status."""
    _attr_device_class = BinarySensorDeviceClass.BATTERY_CHARGING
    _attr_translation_key = "charging"

    @property
    def icon(self):
        """Return the icon."""
        return 'mdi:{}'.format('battery-charging' if self.is_on else 'battery-off')

    @property
    def is_on(self):
        """Return True if the binary sensor is on."""
        if self.vehicle.charging is None:
            return STATE_UNKNOWN
        return self.vehicle.charging is ChargingStatus.CHARGING

    @property
    def device_state_attributes(self):
        a = KamereonEntity.device_state_attributes.fget(self)
        a.update({
            'charging_speed': self.vehicle.charging_speed.value,
            'last_updated': self.vehicle.battery_status_last_updated,
        })
        return a


class PluggedStatusEntity(KamereonEntity, BinarySensorEntity):
    """Representation of plugged status."""
    _attr_device_class = BinarySensorDeviceClass.PLUG
    _attr_translation_key = "plugged"

    @property
    def icon(self):
        """Return the icon."""
        return 'mdi:{}'.format('power-plug' if self.is_on else 'power-plug-off')

    @property
    def is_on(self):
        """Return True if the binary sensor is on."""
        if self.vehicle.plugged_in is None:
            return STATE_UNKNOWN
        return self.vehicle.plugged_in is PluggedStatus.PLUGGED

    @property
    def device_state_attributes(self):
        a = KamereonEntity.device_state_attributes.fget(self)
        a.update({
            'plugged_in_time': self.vehicle.plugged_in_time,
            'unplugged_time': self.vehicle.unplugged_time,
            'last_updated': self.vehicle.battery_status_last_updated,
        })
        return a


class LockStatusEntity(KamereonEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.LOCK
    _attr_translation_key = "doors_locked"

    @property
    def icon(self):
        """Return the icon."""
        return 'mdi:car-door-lock' if self.vehicle.lock_status == LockStatus.LOCKED else 'mdi:car-door-lock-open'

    @property
    def is_on(self):
        return self.vehicle.lock_status == LockStatus.UNLOCKED

    @property
    def extra_state_attributes(self):
        """各ドアとハッチ、ボンネットの開閉状態。"""
        return {
            door.value: (status.value if status is not None else None)
            for door, status in self.vehicle.door_status.items()
        }


class DoorsOpenEntity(KamereonEntity, BinarySensorEntity):
    """どこか一つでも開いているか。"""
    _attr_device_class = BinarySensorDeviceClass.DOOR
    _attr_translation_key = "doors_open"

    @property
    def icon(self):
        """Return the icon."""
        return 'mdi:car-door' if self.is_on else 'mdi:car-side'

    @property
    def is_on(self):
        statuses = [s for s in self.vehicle.door_status.values() if s is not None]
        if not statuses:
            return None
        return any(status == LockStatus.OPEN for status in statuses)

    @property
    def extra_state_attributes(self):
        """各ドアとハッチ、ボンネットの開閉状態。"""
        return {
            door.value: (status.value if status is not None else None)
            for door, status in self.vehicle.door_status.items()
        }


class FuelLowWarningEntity(KamereonEntity, BinarySensorEntity):
    """燃料残量警告 (cockpit の fuelLowWarning)。"""
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_translation_key = "fuel_low_warning"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def icon(self):
        """Return the icon."""
        return 'mdi:gas-station-off' if self.is_on else 'mdi:gas-station'

    @property
    def is_on(self):
        """Return True if the binary sensor is on."""
        return self.vehicle.fuel_low_warning


class HealthLampEntity(KamereonEntity, BinarySensorEntity):
    """警告灯。health-status の値が 0 以外で点灯とする。"""
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, vehicle, lamp_key):
        self._lamp_key = lamp_key
        self._payload_key = HEALTH_LAMPS[lamp_key]
        self._attr_translation_key = HEALTH_LAMP_TRANSLATIONS[lamp_key]
        KamereonEntity.__init__(self, coordinator, vehicle)

    @property
    def icon(self):
        """Return the icon."""
        return HEALTH_LAMP_ICONS.get(self._lamp_key, 'mdi:alert')

    @property
    def is_on(self):
        value = self.vehicle.malfunction_lamps.get(self._payload_key)
        if value is None:
            return None
        return value != 0

    @property
    def extra_state_attributes(self):
        return {
            'raw_value': self.vehicle.malfunction_lamps.get(self._payload_key),
            'last_updated': self.vehicle.health_status_last_updated,
        }
