import logging
import math

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    UnitOfTemperature
)
from homeassistant.core import callback
from homeassistant.const import PERCENTAGE, UnitOfLength, UnitOfTime, UnitOfPower, UnitOfPressure, UnitOfVolume, EntityCategory
from homeassistant.components.sensor import SensorStateClass
from .base import KamereonEntity
from .kamereon import ChargingSpeed, Feature
from .const import DOMAIN, DATA_VEHICLES, DATA_COORDINATOR_FETCH, DATA_COORDINATOR_STATISTICS
_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config, async_add_entities):
    """Set up the Kamereon sensors."""
    account_id = config.data['email']

    data = hass.data[DOMAIN][account_id][DATA_VEHICLES]
    coordinator = hass.data[DOMAIN][account_id][DATA_COORDINATOR_FETCH]
    coordinator_stats = hass.data[DOMAIN][account_id][DATA_COORDINATOR_STATISTICS]

    entities = []

    imperial_distance = config.data.get("imperial_distance", False)

    for vehicle in data:
        if Feature.BATTERY_STATUS in data[vehicle].features or data[vehicle].range_hvac_on is not None:
            entities.append(RangeSensor(coordinator, data[vehicle], True, imperial_distance))
        # JP の battery-status は lastUpdateTime を返さないので、
        # 常に unknown になるセンサーを作らない
        if data[vehicle].battery_status_last_updated is not None:
            entities.append(TimestampSensor(coordinator, data[vehicle], 'battery_status_last_updated', 'last_updated', 'mdi:clock-time-eleven-outline'))
        # アプリがバッテリ残量を扱わない車は作らない
        # (値自体は battery-status から返ってくることがある)
        battery_available = data[vehicle].app_available('batteryLevel')
        if battery_available is not False and data[vehicle].battery_level is not None:
            entities.append(BatteryLevelSensor(coordinator, data[vehicle]))
        if data[vehicle].charge_time_required_to_full[ChargingSpeed.NORMAL] is not None:
            entities += [ChargeTimeRequiredSensor(coordinator, data[vehicle], ChargingSpeed.NORMAL),
                         ChargeTimeRequiredSensor(coordinator, data[vehicle], ChargingSpeed.FAST)]
        if data[vehicle].charge_time_required_to_full[ChargingSpeed.ADAPTIVE] is not None:
            entities.append(ChargeTimeRequiredSensor(coordinator, data[vehicle], ChargingSpeed.ADAPTIVE))
        if data[vehicle].range_hvac_off is not None:
            entities.append(RangeSensor(coordinator, data[vehicle], False, imperial_distance))
        if data[vehicle].internal_temperature is not None:
            entities.append(InternalTemperatureSensor(coordinator, data[vehicle]))
        if data[vehicle].external_temperature is not None:
            entities.append(ExternalTemperatureSensor(coordinator, data[vehicle]))
        if Feature.DRIVING_JOURNEY_HISTORY in data[vehicle].features:
            entities += [
                StatisticSensor(coordinator_stats, data[vehicle], 'daily', lambda x: x.total_distance, 'daily_distance', 'mdi:map-marker-distance', SensorDeviceClass.DISTANCE, UnitOfLength.KILOMETERS, 0, imperial_distance),
                StatisticSensor(coordinator_stats, data[vehicle], 'daily', lambda x: x.trip_count, 'daily_trips', 'mdi:hiking', None, None, 0),
                StatisticSensor(coordinator_stats, data[vehicle], 'monthly', lambda x: x.total_distance, 'monthly_distance', 'mdi:map-marker-distance', SensorDeviceClass.DISTANCE, UnitOfLength.KILOMETERS, 0, imperial_distance),
                StatisticSensor(coordinator_stats, data[vehicle], 'monthly', lambda x: x.trip_count, 'monthly_trips', 'mdi:hiking',  None, None, 0),
            ]
            if Feature.BATTERY_STATUS in data[vehicle].features:
                entities += [
                    StatisticSensor(coordinator_stats, data[vehicle], 'daily', lambda x: x.total_distance / x.consumed_electricity, 'daily_efficiency', 'mdi:ev-station', SensorDeviceClass.DISTANCE, UnitOfLength.KILOMETERS, 2, imperial_distance),
                    StatisticSensor(coordinator_stats, data[vehicle], 'monthly', lambda x: x.total_distance / x.consumed_electricity, 'monthly_efficiency', 'mdi:ev-station', SensorDeviceClass.DISTANCE, UnitOfLength.KILOMETERS, 2, imperial_distance),
                ]

        entities.append(OdometerSensor(coordinator, data[vehicle], imperial_distance))

        if data[vehicle].remote_engine_status is not None:
            entities.append(GenericAttributeSensor(coordinator, data[vehicle], 'remote_engine_status', 'remote_engine_status', 'mdi:engine'))
        if data[vehicle].remote_engine_status_text is not None:
            entities.append(GenericAttributeSensor(coordinator, data[vehicle], 'remote_engine_status_text', 'remote_engine_status_text', 'mdi:engine-outline'))
        if data[vehicle].remote_engine_error_status is not None:
            entities.append(GenericAttributeSensor(coordinator, data[vehicle], 'remote_engine_error_status', 'remote_engine_error_status', 'mdi:engine-off-outline', entity_category=EntityCategory.DIAGNOSTIC))
        if data[vehicle].engine_cycle_remaining_time is not None:
            entities.append(GenericAttributeSensor(coordinator, data[vehicle], 'engine_cycle_remaining_time', 'engine_cycle_remaining_time', 'mdi:timer-outline', state_class=SensorStateClass.MEASUREMENT))
        if data[vehicle].last_remote_action_status is not None:
            entities.append(GenericAttributeSensor(coordinator, data[vehicle], 'last_remote_action_status', 'last_remote_action_status', 'mdi:cellphone-check', entity_category=EntityCategory.DIAGNOSTIC))
        # この車が TPMS の値を返さない場合は全輪 0 になる。
        # 0 だけのセンサーを並べても意味がないので作らない
        if any(data[vehicle].tyre_pressure.values()):
            for key in sorted(data[vehicle].tyre_pressure):
                # pressure レスポンスは輪ごとに <wheel>Pressure と <wheel>Status を返す
                if key.lower().endswith('status'):
                    entities.append(TyreStatusSensor(coordinator, data[vehicle], key))
                else:
                    entities.append(TyrePressureSensor(coordinator, data[vehicle], key))
        if getattr(data[vehicle], 'subscription_name', None) is not None:
            entities.append(GenericAttributeSensor(coordinator, data[vehicle], 'subscription_name', 'subscription_name', 'mdi:file-document-outline', entity_category=EntityCategory.DIAGNOSTIC))
        if getattr(data[vehicle], 'subscription_end_date', None) is not None:
            entities.append(DateSensor(coordinator, data[vehicle], 'subscription_end_date', 'subscription_end_date', 'mdi:calendar-end'))
        for key in sorted(getattr(data[vehicle], 'probe_data', {})):
            entities.append(ProbeSensor(coordinator, data[vehicle], key))
        if data[vehicle].session.region == 'JP':
            entities.append(RemoteActionLogSensor(coordinator, data[vehicle]))
            entities.append(AppFeatureMapSensor(coordinator, data[vehicle]))
        if data[vehicle].battery_temperature is not None:
            entities.append(GenericAttributeSensor(coordinator, data[vehicle], 'battery_temperature', 'battery_temperature', 'mdi:thermometer-alert'))
        if data[vehicle].battery_bar_level is not None:
            entities.append(GenericAttributeSensor(coordinator, data[vehicle], 'battery_bar_level', 'battery_bar_level', 'mdi:battery-heart-variant', state_class=SensorStateClass.MEASUREMENT))
        if data[vehicle].instantaneous_power is not None:
            entities.append(GenericAttributeSensor(coordinator, data[vehicle], 'instantaneous_power', 'instantaneous_power', 'mdi:flash', device_class=SensorDeviceClass.POWER, unit=UnitOfPower.KILO_WATT, state_class=SensorStateClass.MEASUREMENT))
        if data[vehicle].eco_score is not None:
            entities.append(GenericAttributeSensor(coordinator, data[vehicle], 'eco_score', 'eco_score', 'mdi:leaf'))
        if data[vehicle].fuel_autonomy is not None:
            entities.append(GenericAttributeSensor(coordinator, data[vehicle], 'fuel_autonomy', 'fuel_autonomy', 'mdi:gas-station'))
        if data[vehicle].fuel_consumption is not None:
            entities.append(GenericAttributeSensor(coordinator, data[vehicle], 'fuel_consumption', 'fuel_consumption', 'mdi:gas-station'))
        if data[vehicle].fuel_economy is not None:
            entities.append(GenericAttributeSensor(coordinator, data[vehicle], 'fuel_economy', 'fuel_economy', 'mdi:gas-station'))
        if data[vehicle].fuel_level is not None:
            entities.append(GenericAttributeSensor(coordinator, data[vehicle], 'fuel_level', 'fuel_level', 'mdi:gas-station', state_class=SensorStateClass.MEASUREMENT))
        if data[vehicle].fuel_quantity is not None:
            entities.append(GenericAttributeSensor(coordinator, data[vehicle], 'fuel_quantity', 'fuel_quantity', 'mdi:gas-station', device_class=SensorDeviceClass.VOLUME, unit=UnitOfVolume.LITERS))
        if data[vehicle].mileage is not None:
            entities.append(GenericAttributeSensor(coordinator, data[vehicle], 'mileage', 'mileage', 'mdi:counter'))
        if data[vehicle].next_target_temperature is not None:
            entities.append(GenericAttributeSensor(coordinator, data[vehicle], 'next_target_temperature', 'next_target_temperature', 'mdi:thermometer', device_class=SensorDeviceClass.TEMPERATURE, unit=UnitOfTemperature.CELSIUS))
        if data[vehicle].location_last_updated is not None:
            entities.append(TimestampSensor(coordinator, data[vehicle], 'location_last_updated', 'location_last_updated', 'mdi:clock-time-eleven-outline'))
        if data[vehicle].lock_status_last_updated is not None:
            entities.append(TimestampSensor(coordinator, data[vehicle], 'lock_status_last_updated', 'lock_status_last_updated', 'mdi:clock-time-eleven-outline'))
        if data[vehicle].next_hvac_start_date is not None:
            entities.append(TimestampSensor(coordinator, data[vehicle], 'next_hvac_start_date', 'next_hvac_start_date', 'mdi:clock-time-eleven-outline'))
        if data[vehicle].phase is not None:
            entities.append(GenericAttributeSensor(coordinator, data[vehicle], 'phase', 'phase', 'mdi:information-outline', entity_category=EntityCategory.DIAGNOSTIC))
        if data[vehicle].privacy_mode is not None:
            entities.append(GenericAttributeSensor(coordinator, data[vehicle], 'privacy_mode', 'privacy_mode', 'mdi:incognito', entity_category=EntityCategory.DIAGNOSTIC))

    async_add_entities(entities, update_before_add=True)


class BatteryLevelSensor(KamereonEntity, SensorEntity):
    _attr_translation_key = "battery_level"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, vehicle):
        KamereonEntity.__init__(self, coordinator, vehicle)
        
    @property
    def state(self):
        """Return the state."""
        return self.vehicle.battery_level

    @property
    def icon(self):
        """Icon of the sensor. Round up to the nearest 10% icon."""
        nearest = math.ceil((self.state or 0) / 10.0) * 10
        if nearest == 0:
            return "mdi:battery-outline"
        elif nearest == 100:
            return "mdi:battery"
        else:
            return "mdi:battery-" + str(nearest)

    @property
    def device_state_attributes(self):
        """Return device specific state attributes."""
        a = KamereonEntity.device_state_attributes.fget(self)
        a.update({
            'battery_capacity': self.vehicle.battery_capacity,
            'battery_level': self.vehicle.battery_level,
        })


class InternalTemperatureSensor(KamereonEntity, SensorEntity):
    _attr_translation_key = "internal_temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    @property
    def native_value(self):
        """Return the state."""
        return self.vehicle.internal_temperature

    @property
    def icon(self):
        """Icon of the sensor."""
        return "mdi:thermometer"

    @property
    def device_state_attributes(self):
        """Return device specific state attributes."""
        a = KamereonEntity.device_state_attributes.fget(self)
        a.update({
            'battery_capacity': self.vehicle.battery_capacity,
            'battery_bar_level': self.vehicle.battery_bar_level,
        })
        return a


class ExternalTemperatureSensor(KamereonEntity, SensorEntity):
    _attr_translation_key = "external_temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    @property
    def native_value(self):
        """Return the state."""
        return self.vehicle.external_temperature

    @property
    def icon(self):
        """Icon of the sensor."""
        return "mdi:thermometer"

    @property
    def device_state_attributes(self):
        """Return device specific state attributes."""
        a = KamereonEntity.device_state_attributes.fget(self)
        a.update({
            'battery_capacity': self.vehicle.battery_capacity,
            'battery_bar_level': self.vehicle.battery_bar_level,
        })
        return a


class RangeSensor(KamereonEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.DISTANCE
    _attr_native_unit_of_measurement = UnitOfLength.KILOMETERS

    def __init__(self, coordinator, vehicle, hvac, imperial_distance):
        if imperial_distance:
            self._attr_suggested_unit_of_measurement = UnitOfLength.MILES

        self._attr_translation_key = "range_ac_on" if hvac else "range_ac_off"
        KamereonEntity.__init__(self, coordinator, vehicle)
        self.hvac = hvac

    @property
    def native_value(self):
        """Return the state."""
        val = getattr(self.vehicle, 'range_hvac_{}'.format(
            'on' if self.hvac else 'off'))
        return val

    @property
    def icon(self):
        """Icon of the sensor."""
        return "mdi:map-marker-distance"


class OdometerSensor(KamereonEntity, SensorEntity):
    _attr_translation_key = "odometer"
    _attr_device_class = SensorDeviceClass.DISTANCE
    _attr_native_unit_of_measurement = UnitOfLength.KILOMETERS
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, coordinator, vehicle, imperial_distance):
        if imperial_distance:
            self._attr_suggested_unit_of_measurement = UnitOfLength.MILES

        self._state = None

        KamereonEntity.__init__(self, coordinator, vehicle)

    @callback
    def _handle_coordinator_update(self) -> None:
        new_state = getattr(self.vehicle, "total_mileage")

        # This sometimes goes backwards? So only accept a positive odometer delta
        if new_state is not None and new_state > (self._state or 0):           
            self._state = new_state
            self.async_write_ha_state()

    @property
    def native_value(self):
        """Return the state."""
        return self._state

    @property
    def icon(self):
        """Icon of the sensor."""
        return "mdi:counter"


class StatisticSensor(KamereonEntity, SensorEntity):
    def __init__(self, coordinator, vehicle, key, func, translation_key, icon, device_class, unit, precision, imperial_distance=False):
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = unit
        self._attr_suggested_display_precision = precision
        if imperial_distance:
            self._attr_suggested_unit_of_measurement = UnitOfLength.MILES
        self._attr_translation_key = translation_key
        self._icon = icon
        self._key = key
        self._lambda = func
        self._state = None
        self._attributes = {}
        KamereonEntity.__init__(self, coordinator, vehicle)

    @property
    def native_value(self):
        """Return the state."""
        return self._state

    @property
    def extra_state_attributes(self):
        """Attributes of the sensor."""
        return self._attributes

    @callback
    def _handle_coordinator_update(self) -> None:
        if self.coordinator.data is None or self.vehicle.vin not in self.coordinator.data:
            return

        summary = self.coordinator.data[self.vehicle.vin][self._key]

        # No summaries yet, return 0
        if len(summary) == 0:
            self._state = 0
            self.async_write_ha_state()
            return

        # For statistic sensors, default to 0 on error
        try:
            self._state = self._lambda(summary[0])
        except:
            self._state = 0

        self.async_write_ha_state()

    @property
    def icon(self):
        """Icon of the sensor."""
        return self._icon


class ChargeTimeRequiredSensor(KamereonEntity, SensorEntity):
    _attr_translation_key = "charge_time"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES

    CHARGING_SPEED_NAME = {
        ChargingSpeed.FASTEST: '50kw',
        ChargingSpeed.FAST: '6kw',
        ChargingSpeed.NORMAL: '3kw',
        ChargingSpeed.SLOW: '1kw',
        ChargingSpeed.ADAPTIVE: 'adaptive'
    }

    def __init__(self, coordinator, vehicle, charging_speed):
        self.charging_speed = charging_speed
        self._attr_translation_key = "charge_time_" + self.CHARGING_SPEED_NAME[charging_speed]
        KamereonEntity.__init__(self, coordinator, vehicle)

    @property
    def native_value(self):
        """Return the state."""
        return self.vehicle.charge_time_required_to_full[self.charging_speed]

    @property
    def icon(self):
        """Icon of the sensor."""
        return "mdi:battery-clock"


class GenericAttributeSensor(KamereonEntity, SensorEntity):
    """vehicle の属性をそのまま公開する汎用センサー。単位・意味が未確定の値もひとまず生の数値として見えるようにする。"""

    def __init__(self, coordinator, vehicle, attribute, translation_key, icon,
                 device_class=None, unit=None, state_class=None, entity_category=None):
        self._attribute = attribute
        self._attr_translation_key = translation_key
        self._icon = icon
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = unit
        self._attr_state_class = state_class
        self._attr_entity_category = entity_category
        KamereonEntity.__init__(self, coordinator, vehicle)

    @property
    def native_value(self):
        """Return the state."""
        return getattr(self.vehicle, self._attribute)

    @property
    def icon(self):
        """Icon of the sensor."""
        return self._icon


class TyrePressureSensor(KamereonEntity, SensorEntity):
    """JP: /nissan/vehicle-info/v1/cars/{vin}/pressure が返す各輪の空気圧。"""

    _attr_device_class = SensorDeviceClass.PRESSURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPressure.KPA
    _attr_icon = "mdi:car-tire-alert"

    def __init__(self, coordinator, vehicle, key):
        self._key = key
        self._attr_translation_key = 'tyre_pressure'
        self._attr_translation_placeholders = {'tyre': key}
        KamereonEntity.__init__(self, coordinator, vehicle)

    @property
    def unique_id(self):
        return "{}-tyre-pressure-{}".format(super().unique_id, self._key)

    @property
    def native_value(self):
        return self.vehicle.tyre_pressure.get(self._key)


class TyreStatusSensor(KamereonEntity, SensorEntity):
    """JP: 同じ pressure レスポンスの輪ごとの状態。圧力値ではないので単位は付けない。"""

    _attr_icon = "mdi:car-tire-alert"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, vehicle, key):
        self._key = key
        self._attr_translation_key = 'tyre_status'
        self._attr_translation_placeholders = {'tyre': key}
        KamereonEntity.__init__(self, coordinator, vehicle)

    @property
    def unique_id(self):
        return "{}-tyre-status-{}".format(super().unique_id, self._key)

    @property
    def native_value(self):
        return self.vehicle.tyre_pressure.get(self._key)


class DateSensor(KamereonEntity, SensorEntity):
    """date をそのまま公開するセンサー。TimestampSensor は日時用なので分けている。"""

    _attr_device_class = SensorDeviceClass.DATE

    def __init__(self, coordinator, vehicle, attribute, translation_key, icon):
        self._attribute = attribute
        self._attr_translation_key = translation_key
        self._icon = icon
        KamereonEntity.__init__(self, coordinator, vehicle)

    @property
    def icon(self):
        return self._icon

    @property
    def native_value(self):
        return getattr(self.vehicle, self._attribute, None)


class ProbeSensor(KamereonEntity, SensorEntity):
    """JP: まだレスポンスを確認していないエンドポイントの生の中身。

    state には payload を JSON にして 250 文字まで、属性には全体を入れる。
    中身が分かったものから個別のセンサーに置き換える。
    """

    _attr_icon = "mdi:help-network-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, vehicle, key):
        self._key = key
        self._attr_translation_key = 'probe'
        self._attr_translation_placeholders = {'probe': key}
        KamereonEntity.__init__(self, coordinator, vehicle)

    @property
    def unique_id(self):
        return "{}-probe-{}".format(super().unique_id, self._key)

    @property
    def native_value(self):
        return self.vehicle.probe_summary(self.vehicle.probe_data.get(self._key))

    @property
    def extra_state_attributes(self):
        return self.vehicle.probe_attributes(self.vehicle.probe_data.get(self._key))


class RemoteActionLogSensor(KamereonEntity, SensorEntity):
    """JP: 直近の遠隔操作のリクエスト / レスポンス / ポーリングをそのまま持つ。

    ログレベルに関係なく残る。state は「操作 → 結果」、属性に全体
    (Authorization は伏せ、recorder の上限に収まるよう切り詰める)。
    """

    _attr_translation_key = 'remote_action_log'
    _attr_icon = 'mdi:text-box-search-outline'
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self):
        trace = self.vehicle.last_remote_action_trace()
        if not trace:
            return None
        return '{} -> {}'.format(trace.get('action'), trace.get('result'))

    @property
    def extra_state_attributes(self):
        trace = self.vehicle.last_remote_action_trace()
        if not trace:
            return {}
        attributes = self.vehicle.probe_attributes(self.vehicle._redact(trace))
        attributes['history'] = len(self.vehicle.remote_action_log)
        return attributes


class AppFeatureMapSensor(KamereonEntity, SensorEntity):
    """JP: アプリの機能可用性マップ (features) の生の中身。ボタンの出し分け根拠の確認用。"""

    _attr_translation_key = 'app_feature_map'
    _attr_icon = 'mdi:feature-search-outline'
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self):
        config = self.vehicle.app_config or {}
        return len(config)

    @property
    def extra_state_attributes(self):
        config = self.vehicle.app_config or {}
        return self.vehicle.probe_attributes({'payload': self.vehicle._redact(config)})


class TimestampSensor(KamereonEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator, vehicle, attribute, translation_key, icon):
        self._attr_translation_key = translation_key
        self._icon = icon
        KamereonEntity.__init__(self, coordinator, vehicle)
        self.attribute = attribute

    @property
    def icon(self):
        """Icon of the sensor."""
        return self._icon

    @property
    def state(self):
        """Return the state."""
        val = getattr(self.vehicle, self.attribute)
        if val is None:
            return None
        return val.isoformat()
