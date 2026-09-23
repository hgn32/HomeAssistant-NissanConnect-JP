import pytest
from unittest.mock import AsyncMock, MagicMock
from homeassistant.helpers import entity_registry as er
from custom_components.nissan_connect.const import DOMAIN, DATA_VEHICLES, DATA_COORDINATOR_POLL, DATA_COORDINATOR_FETCH, DATA_COORDINATOR_STATISTICS
from custom_components.nissan_connect.kamereon.kamereon_const import Feature, HVACAction
from custom_components.nissan_connect.kamereon.kamereon_jp_const import EngineCycleTime

from custom_components.nissan_connect.button import (
    async_setup_entry,
    ForceUpdateButton,
    HornLightsButtons,
    ChargeControlButtons,
    DoorLockButton,
    EngineStartButton,
    EngineStartLongButton,
    EngineStopButton,
)


@pytest.fixture
def mock_hass():
    hass = MagicMock()
    hass.data = {
        DOMAIN: {
            'test_account': {
                DATA_VEHICLES: {
                    'vehicle_1': MagicMock(features=[Feature.HORN_AND_LIGHTS, Feature.CHARGING_START])
                },
                DATA_COORDINATOR_POLL: MagicMock(),
                DATA_COORDINATOR_FETCH: MagicMock(),
                DATA_COORDINATOR_STATISTICS: MagicMock(),
            }
        }
    }
    return hass


@pytest.fixture
def mock_config():
    return MagicMock(data={'email': 'test_account'})


@pytest.fixture
def mock_async_add_entities():
    return AsyncMock()


@pytest.mark.asyncio
async def test_async_setup_entry(mock_hass, mock_config, mock_async_add_entities):
    await async_setup_entry(mock_hass, mock_config, mock_async_add_entities)
    assert mock_async_add_entities.call_count == 1
    entities = mock_async_add_entities.call_args[0][0]
    assert len(entities) == 4
    assert isinstance(entities[0], ForceUpdateButton)
    assert isinstance(entities[1], HornLightsButtons)
    assert isinstance(entities[2], HornLightsButtons)
    assert isinstance(entities[3], ChargeControlButtons)


@pytest.mark.asyncio
async def test_force_update_button():
    coordinator = AsyncMock()
    vehicle = MagicMock()
    hass = AsyncMock()
    stats_coordinator = MagicMock()

    button = ForceUpdateButton(coordinator, vehicle, hass, stats_coordinator)

    await button.async_press()
    vehicle.refresh.assert_called_once()
    coordinator.async_refresh.assert_called_once()


def test_horn_lights_buttons():
    coordinator = MagicMock()
    vehicle = MagicMock()
    button = HornLightsButtons(
        coordinator, vehicle, "flash_lights", "mdi:car-light-high", "lights")

    button.press()
    vehicle.control_horn_lights.assert_called_once_with('start', "lights")


def test_charge_control_buttons():
    coordinator = MagicMock()
    vehicle = MagicMock()
    button = ChargeControlButtons(
        coordinator, vehicle, "charge_start", "mdi:play", "start")

    button.press()
    vehicle.control_charging.assert_called_once_with("start")


@pytest.mark.asyncio
async def test_async_setup_entry_with_door_lock_jp(mock_config, mock_async_add_entities):
    hass = MagicMock()
    vehicle = MagicMock(features=[Feature.APP_DOOR_LOCKING])
    vehicle.session.region = 'JP'
    hass.data = {
        DOMAIN: {
            'test_account': {
                DATA_VEHICLES: {'vehicle_1': vehicle},
                DATA_COORDINATOR_POLL: MagicMock(),
                DATA_COORDINATOR_FETCH: MagicMock(),
                DATA_COORDINATOR_STATISTICS: MagicMock(),
            }
        }
    }

    await async_setup_entry(hass, mock_config, mock_async_add_entities)
    entities = mock_async_add_entities.call_args[0][0]
    assert any(isinstance(e, DoorLockButton) for e in entities)


@pytest.mark.asyncio
async def test_async_setup_entry_skips_door_lock_eu(mock_config, mock_async_add_entities):
    hass = MagicMock()
    vehicle = MagicMock(features=[Feature.APP_DOOR_LOCKING])
    vehicle.session.region = 'EU'
    hass.data = {
        DOMAIN: {
            'test_account': {
                DATA_VEHICLES: {'vehicle_1': vehicle},
                DATA_COORDINATOR_POLL: MagicMock(),
                DATA_COORDINATOR_FETCH: MagicMock(),
                DATA_COORDINATOR_STATISTICS: MagicMock(),
            }
        }
    }

    await async_setup_entry(hass, mock_config, mock_async_add_entities)
    entities = mock_async_add_entities.call_args[0][0]
    assert not any(isinstance(e, DoorLockButton) for e in entities)


def test_door_lock_button():
    coordinator = MagicMock()
    vehicle = MagicMock()
    button = DoorLockButton(coordinator, vehicle)

    button.press()
    vehicle.lock.assert_called_once_with()


@pytest.mark.asyncio
async def test_async_setup_entry_with_engine_start(mock_config, mock_async_add_entities):
    hass = MagicMock()
    vehicle = MagicMock(features=[Feature.REMOTE_ENGINE_START])
    vehicle.session.region = 'JP'
    hass.data = {
        DOMAIN: {
            'test_account': {
                DATA_VEHICLES: {'vehicle_1': vehicle},
                DATA_COORDINATOR_POLL: MagicMock(),
                DATA_COORDINATOR_FETCH: MagicMock(),
                DATA_COORDINATOR_STATISTICS: MagicMock(),
            }
        }
    }

    await async_setup_entry(hass, mock_config, mock_async_add_entities)
    entities = mock_async_add_entities.call_args[0][0]
    assert any(isinstance(e, EngineStartButton) for e in entities)
    assert any(isinstance(e, EngineStartLongButton) for e in entities)
    assert any(isinstance(e, EngineStopButton) for e in entities)


def test_engine_start_button():
    coordinator = MagicMock()
    vehicle = MagicMock()
    button = EngineStartButton(coordinator, vehicle)

    button.press()
    vehicle.set_hvac_status.assert_called_once_with(
        HVACAction.START, cycle_time=EngineCycleTime.NORMAL)


def test_engine_start_long_button():
    coordinator = MagicMock()
    vehicle = MagicMock()
    button = EngineStartLongButton(coordinator, vehicle)

    button.press()
    vehicle.set_hvac_status.assert_called_once_with(
        HVACAction.START, cycle_time=EngineCycleTime.DOUBLE)


@pytest.mark.asyncio
async def test_async_setup_entry_no_extra_buttons_for_jp_engine_start(mock_config, mock_async_add_entities):
    """JP かつ REMOTE_ENGINE_START のときは始動系 (通常 / 20分) と停止ボタンのみが出る（調査用ボタンは無い）。"""
    hass = MagicMock()
    vehicle = MagicMock(features=[Feature.REMOTE_ENGINE_START])
    vehicle.session.region = 'JP'
    vehicle.double_start_available.return_value = True
    hass.data = {
        DOMAIN: {
            'test_account': {
                DATA_VEHICLES: {'vehicle_1': vehicle},
                DATA_COORDINATOR_POLL: MagicMock(),
                DATA_COORDINATOR_FETCH: MagicMock(),
                DATA_COORDINATOR_STATISTICS: MagicMock(),
            }
        }
    }

    await async_setup_entry(hass, mock_config, mock_async_add_entities)
    entities = mock_async_add_entities.call_args[0][0]
    assert any(isinstance(e, EngineStartButton) for e in entities)
    assert any(isinstance(e, EngineStartLongButton) for e in entities)
    assert any(isinstance(e, EngineStopButton) for e in entities)
    engine_related = [
        e for e in entities
        if isinstance(e, (EngineStartButton, EngineStartLongButton, EngineStopButton))
    ]
    assert len(engine_related) == 3


@pytest.mark.asyncio
async def test_async_setup_entry_skips_engine_stop_for_eu(mock_config, mock_async_add_entities):
    """EU は REMOTE_ENGINE_START があってもエンジン停止ボタンを出さない (JP 固有機能)。"""
    hass = MagicMock()
    vehicle = MagicMock(features=[Feature.REMOTE_ENGINE_START])
    vehicle.session.region = 'EU'
    vehicle.double_start_available.return_value = None
    hass.data = {
        DOMAIN: {
            'test_account': {
                DATA_VEHICLES: {'vehicle_1': vehicle},
                DATA_COORDINATOR_POLL: MagicMock(),
                DATA_COORDINATOR_FETCH: MagicMock(),
                DATA_COORDINATOR_STATISTICS: MagicMock(),
            }
        }
    }

    await async_setup_entry(hass, mock_config, mock_async_add_entities)
    entities = mock_async_add_entities.call_args[0][0]
    assert not any(isinstance(e, EngineStopButton) for e in entities)


def test_engine_stop_button_calls_stop_engine():
    coordinator = MagicMock()
    vehicle = MagicMock()
    button = EngineStopButton(coordinator, vehicle)

    button.press()
    vehicle.stop_engine.assert_called_once_with()
