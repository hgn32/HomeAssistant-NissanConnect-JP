import pytest
from unittest.mock import AsyncMock, MagicMock
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import PERCENTAGE, UnitOfTemperature, UnitOfLength, UnitOfTime
from custom_components.nissan_connect.base import KamereonEntity
from custom_components.nissan_connect.kamereon import ChargingSpeed, Feature

from custom_components.nissan_connect.sensor import (
    BatteryLevelSensor,
    InternalTemperatureSensor,
    ExternalTemperatureSensor,
    RangeSensor,
    OdometerSensor,
    StatisticSensor,
    ChargeTimeRequiredSensor,
    TimestampSensor,
    GenericAttributeSensor,
    RemoteEngineStatusSensor,
    async_setup_entry
)

@pytest.fixture
def mock_hass():
    hass = MagicMock()
    hass.data = {
        'nissan_connect': {
            'test_account': {
                'vehicles': {
                    'test_vehicle': MagicMock(
                        battery_level=80,
                        internal_temperature=22.5,
                        external_temperature=15.0,
                        range_hvac_on=100,
                        range_hvac_off=120,
                        total_mileage=5000,
                        charge_time_required_to_full={ChargingSpeed.NORMAL: 60, ChargingSpeed.FAST: 30, ChargingSpeed.ADAPTIVE: None},
                        features=[Feature.BATTERY_STATUS, Feature.DRIVING_JOURNEY_HISTORY]
                    )
                },
                'coordinator_fetch': AsyncMock(),
                'coordinator_statistics': AsyncMock()
            }
        }
    }
    return hass

@pytest.fixture
def mock_config():
    return MagicMock(data={'email': 'test_account', 'imperial_distance': False})

@pytest.fixture
def mock_async_add_entities():
    return AsyncMock()

@pytest.mark.asyncio
async def test_async_setup_entry(mock_hass, mock_config, mock_async_add_entities):
    await async_setup_entry(mock_hass, mock_config, mock_async_add_entities)
    assert mock_async_add_entities.call_count == 1
    entities = mock_async_add_entities.call_args[0][0]
    assert len(entities) > 0

@pytest.mark.asyncio
async def test_async_setup_entry_creates_no_probe_sensors(mock_hass, mock_config, mock_async_add_entities):
    """probe_data の中身に関わらず (未確認) センサーは作らない (該当のセンサークラスはユーザー指示で削除済み)。"""
    vehicle = mock_hass.data['nissan_connect']['test_account']['vehicles']['test_vehicle']
    vehicle.probe_data = {
        'contract': {'payload': {'dummy': 'value'}},
        'entitlements': {'payload': {'dummy': 'value'}},
        'curfew_restrictions': {'payload': {'dummy': 'value'}},
    }

    await async_setup_entry(mock_hass, mock_config, mock_async_add_entities)

    entities = mock_async_add_entities.call_args[0][0]
    assert not any(getattr(entity, '_attr_translation_key', None) == 'probe' for entity in entities)


@pytest.mark.asyncio
async def test_async_setup_entry_jp_creates_no_remote_action_log_sensor(mock_hass, mock_config, mock_async_add_entities):
    """JP でも直近の遠隔操作を表示するセンサーは作らない (ユーザー指示で削除)。"""
    vehicle = mock_hass.data['nissan_connect']['test_account']['vehicles']['test_vehicle']
    vehicle.session.region = 'JP'
    vehicle.probe_data = {}
    vehicle.app_config = {}

    await async_setup_entry(mock_hass, mock_config, mock_async_add_entities)

    entities = mock_async_add_entities.call_args[0][0]
    assert not any(getattr(entity, '_attr_translation_key', None) == 'remote_action_log'
                   for entity in entities)


@pytest.mark.asyncio
async def test_async_setup_entry_no_app_feature_map_sensor(mock_hass, mock_config, mock_async_add_entities):
    """app_feature_map センサーはユーザー指定で削除済み。AppFeatureMapSensor という型のエンティティは作られない。"""
    import custom_components.nissan_connect.sensor as sensor_module

    vehicle = mock_hass.data['nissan_connect']['test_account']['vehicles']['test_vehicle']
    vehicle.session.region = 'JP'
    vehicle.probe_data = {}
    vehicle.app_config = {}

    await async_setup_entry(mock_hass, mock_config, mock_async_add_entities)

    entities = mock_async_add_entities.call_args[0][0]
    assert all(type(entity).__name__ != 'AppFeatureMapSensor' for entity in entities)
    assert not hasattr(sensor_module, 'AppFeatureMapSensor')


@pytest.mark.asyncio
async def test_async_setup_entry_fuel_autonomy_has_distance_unit(mock_hass, mock_config, mock_async_add_entities):
    """fuel_autonomy センサーは距離センサーとして km 単位・SensorDeviceClass.DISTANCE を持つ。"""
    await async_setup_entry(mock_hass, mock_config, mock_async_add_entities)

    entities = mock_async_add_entities.call_args[0][0]
    fuel_autonomy_sensors = [
        entity for entity in entities
        if isinstance(entity, GenericAttributeSensor) and entity._attribute == 'fuel_autonomy'
    ]
    assert len(fuel_autonomy_sensors) == 1
    sensor = fuel_autonomy_sensors[0]
    assert sensor._attr_native_unit_of_measurement == UnitOfLength.KILOMETERS
    assert sensor._attr_device_class == SensorDeviceClass.DISTANCE


def test_battery_level_sensor(mock_hass):
    vehicle = mock_hass.data['nissan_connect']['test_account']['vehicles']['test_vehicle']
    coordinator = mock_hass.data['nissan_connect']['test_account']['coordinator_fetch']
    sensor = BatteryLevelSensor(coordinator, vehicle)
    assert sensor.state == 80

def test_internal_temperature_sensor(mock_hass):
    vehicle = mock_hass.data['nissan_connect']['test_account']['vehicles']['test_vehicle']
    coordinator = mock_hass.data['nissan_connect']['test_account']['coordinator_fetch']
    sensor = InternalTemperatureSensor(coordinator, vehicle)
    assert sensor.native_value == 22.5

def test_external_temperature_sensor(mock_hass):
    vehicle = mock_hass.data['nissan_connect']['test_account']['vehicles']['test_vehicle']
    coordinator = mock_hass.data['nissan_connect']['test_account']['coordinator_fetch']
    sensor = ExternalTemperatureSensor(coordinator, vehicle)
    assert sensor.native_value == 15.0

def test_range_sensor(mock_hass):
    vehicle = mock_hass.data['nissan_connect']['test_account']['vehicles']['test_vehicle']
    coordinator = mock_hass.data['nissan_connect']['test_account']['coordinator_fetch']
    sensor = RangeSensor(coordinator, vehicle, True, False)
    assert sensor.native_value == 100

def test_odometer_sensor(mock_hass):
    vehicle = mock_hass.data['nissan_connect']['test_account']['vehicles']['test_vehicle']
    coordinator = mock_hass.data['nissan_connect']['test_account']['coordinator_fetch']
    sensor = OdometerSensor(coordinator, vehicle, False)
    sensor.async_write_ha_state = MagicMock()
    sensor._handle_coordinator_update()
    assert sensor.native_value == 5000

def test_charge_time_required_sensor(mock_hass):
    vehicle = mock_hass.data['nissan_connect']['test_account']['vehicles']['test_vehicle']
    coordinator = mock_hass.data['nissan_connect']['test_account']['coordinator_fetch']
    sensor = ChargeTimeRequiredSensor(coordinator, vehicle, ChargingSpeed.NORMAL)
    assert sensor.native_value == 60

def test_timestamp_sensor(mock_hass):
    vehicle = mock_hass.data['nissan_connect']['test_account']['vehicles']['test_vehicle']
    coordinator = mock_hass.data['nissan_connect']['test_account']['coordinator_fetch']
    sensor = TimestampSensor(coordinator, vehicle, 'battery_status_last_updated', 'last_updated', 'mdi:clock-time-eleven-outline')

def test_generic_attribute_sensor(mock_hass):
    vehicle = mock_hass.data['nissan_connect']['test_account']['vehicles']['test_vehicle']
    vehicle.battery_temperature = 3
    coordinator = mock_hass.data['nissan_connect']['test_account']['coordinator_fetch']
    sensor = GenericAttributeSensor(coordinator, vehicle, 'battery_temperature', 'battery_temperature', 'mdi:thermometer-alert')
    assert sensor.native_value == 3
    assert sensor.icon == 'mdi:thermometer-alert'


def test_remote_engine_status_sensor(mock_hass):
    """native_value は意味のある文字列、extra_state_attributes['raw'] は生値であること。"""
    vehicle = mock_hass.data['nissan_connect']['test_account']['vehicles']['test_vehicle']
    vehicle.remote_engine_status = 6
    vehicle.remote_engine_status_text = 'readyForRemoteStart'
    coordinator = mock_hass.data['nissan_connect']['test_account']['coordinator_fetch']

    sensor = RemoteEngineStatusSensor(coordinator, vehicle)

    assert sensor.native_value == 'readyForRemoteStart'
    assert sensor.extra_state_attributes == {'raw': 6}
    assert sensor.icon == 'mdi:engine'
    assert sensor._attr_device_class == SensorDeviceClass.ENUM
    assert 'readyForRemoteStart' in sensor._attr_options


@pytest.mark.asyncio
async def test_async_setup_entry_remote_engine_status_single_sensor(mock_hass, mock_config, mock_async_add_entities):
    """remote_engine_status / remote_engine_status_text は 1 つのセンサーに統合されていること。"""
    vehicle = mock_hass.data['nissan_connect']['test_account']['vehicles']['test_vehicle']
    vehicle.remote_engine_status = 6
    vehicle.remote_engine_status_text = 'readyForRemoteStart'

    await async_setup_entry(mock_hass, mock_config, mock_async_add_entities)

    entities = mock_async_add_entities.call_args[0][0]
    remote_engine_sensors = [entity for entity in entities if isinstance(entity, RemoteEngineStatusSensor)]
    assert len(remote_engine_sensors) == 1
    assert not any(
        isinstance(entity, GenericAttributeSensor) and entity._attribute == 'remote_engine_status'
        for entity in entities
    )
    assert not any(
        isinstance(entity, GenericAttributeSensor) and entity._attribute == 'remote_engine_status_text'
        for entity in entities
    )
