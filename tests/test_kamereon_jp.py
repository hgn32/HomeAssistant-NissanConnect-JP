"""JP (NissanConnect / MyNISSAN アプリ) 固有の挙動のテスト。"""
import json
from unittest.mock import MagicMock

import pytest

from custom_components.nissan_connect.kamereon.kamereon import Vehicle
from custom_components.nissan_connect.kamereon.kamereon_const import (
    EngineCycleTime,
    Feature,
    HVACAction,
    RemoteActionStatus,
    RemoteEngineStatus,
)


def _make_vehicle(features=None):
    vehicle = Vehicle.__new__(Vehicle)
    vehicle.vin = 'VIN0000000000000'
    vehicle.user_id = 'user'
    vehicle.features = features or []
    vehicle.app_config = {}
    vehicle.remote_engine_status = None
    vehicle.remote_engine_status_text = None
    vehicle.remote_engine_error_status = None
    vehicle.engine_cycle_remaining_time = None
    vehicle.last_remote_action = None
    vehicle.last_remote_action_status = None
    vehicle.tyre_pressure = {}
    vehicle.tyre_pressure_last_updated = None

    session = MagicMock()
    session.region = 'JP'
    session.settings = {
        'user_base_url': 'https://bff/nc-app-bff/',
        'car_adapter_base_url': 'https://bff/nc-app-bff/alliance/car-adapter/',
    }
    vehicle._session = session
    type(vehicle).session = property(lambda self: self._session)
    return vehicle


def _response(payload, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = payload
    return resp


@pytest.mark.parametrize('raw,expected', [
    ('0', RemoteEngineStatus.UNAVAILABLE),
    ('3', RemoteEngineStatus.REMOTE_START_NOT_ALLOWED),
    ('6', RemoteEngineStatus.READY_FOR_REMOTE_START),
    ('9', RemoteEngineStatus.MANUALLY_STARTED),
    ('12', RemoteEngineStatus.REMOTELY_STARTED),
    ('15', RemoteEngineStatus.REMOTE_START_NOT_SUPPORTED),
    (12, RemoteEngineStatus.REMOTELY_STARTED),
    ('99', RemoteEngineStatus.UNKNOWN),
])
def test_remote_engine_status_decoding(raw, expected):
    vehicle = _make_vehicle()
    vehicle._set_remote_engine_status(raw)
    assert vehicle.remote_engine_status == raw
    assert vehicle.remote_engine_status_text == expected.value


def test_hvac_control_matches_app_payload():
    """アプリと同じ targetCycleTime / hvacAccessorySetting を送る。"""
    vehicle = _make_vehicle([Feature.REMOTE_ENGINE_START])
    vehicle._post = MagicMock(return_value=_response({'data': {'id': 'action-1'}}))
    vehicle.poll_remote_action = MagicMock()

    vehicle.set_hvac_status(HVACAction.START, 21, cycle_time=EngineCycleTime.DOUBLE)

    url, = vehicle._post.call_args[0]
    assert url.endswith('nissan/remote-action/v1/cars/VIN0000000000000/hvac-control')
    body = json.loads(vehicle._post.call_args[1]['data'])
    attributes = body['data']['attributes']
    assert body['data']['type'] == 'HvacControl'
    assert attributes['action'] == 'start'
    assert attributes['targetCycleTime'] == 'doubleStart'
    assert attributes['hvacAccessorySetting'] == {'hvacFunctionRequest': 1}
    vehicle.poll_remote_action.assert_called_once_with('action-1')


def test_hvac_control_stop_has_no_cycle_time():
    vehicle = _make_vehicle([Feature.REMOTE_ENGINE_START])
    vehicle._post = MagicMock(return_value=_response({'data': {'id': 'action-2'}}))
    vehicle.poll_remote_action = MagicMock()

    vehicle.set_hvac_status(HVACAction.STOP)

    attributes = json.loads(vehicle._post.call_args[1]['data'])['data']['attributes']
    assert 'targetCycleTime' not in attributes
    assert 'hvacAccessorySetting' not in attributes


def test_fetch_remote_action_status():
    vehicle = _make_vehicle()
    vehicle._get = MagicMock(return_value=_response(
        {'data': {'attributes': {'status': 'COMPLETED', 'error': {'code': 0}}}}))

    status, code = vehicle.fetch_remote_action_status('action-1')

    assert status is RemoteActionStatus.COMPLETED
    assert code == 0
    url, = vehicle._get.call_args[0]
    assert url.endswith(
        'alliance/action-status-polling/v1/cars/VIN0000000000000/actions/status')
    assert vehicle._get.call_args[1]['params'] == {'actionId': 'action-1'}


def test_poll_remote_action_success(monkeypatch):
    monkeypatch.setattr(
        'custom_components.nissan_connect.kamereon.kamereon.time.sleep', lambda _: None)
    vehicle = _make_vehicle()
    vehicle.fetch_remote_action_status = MagicMock(side_effect=[
        (RemoteActionStatus.PENDING, None),
        (RemoteActionStatus.COMPLETED, None),
    ])

    assert vehicle.poll_remote_action('action-1') is RemoteActionStatus.COMPLETED
    assert vehicle.last_remote_action_status == 'COMPLETED'


def test_poll_remote_action_rejected(monkeypatch):
    monkeypatch.setattr(
        'custom_components.nissan_connect.kamereon.kamereon.time.sleep', lambda _: None)
    vehicle = _make_vehicle()
    vehicle.fetch_remote_action_status = MagicMock(
        return_value=(RemoteActionStatus.REJECTED, 42))

    with pytest.raises(ValueError, match='REJECTED'):
        vehicle.poll_remote_action('action-1')


def test_fetch_engine_status_reads_res_state():
    vehicle = _make_vehicle([Feature.REMOTE_ENGINE_START])
    vehicle._get = MagicMock(return_value=_response({'data': {'attributes': {
        'remoteEngineStatus': '12',
        'remoteEngineErrorStatus': 'noError',
        'cycleRemainingTime': 540,
    }}}))

    vehicle.fetch_engine_status()

    url, = vehicle._get.call_args[0]
    assert url.endswith('v2/cars/VIN0000000000000/res-state')
    assert vehicle.remote_engine_status_text == 'remotelyStarted'
    assert vehicle.remote_engine_error_status == 'noError'
    assert vehicle.engine_cycle_remaining_time == 540


def test_fetch_tyre_pressure():
    vehicle = _make_vehicle()
    vehicle._get = MagicMock(return_value=_response({'data': {'attributes': {
        'frontLeft': 240,
        'frontRight': 238,
        'lastUpdateTime': '2026-09-18T00:00:00Z',
    }}}))

    vehicle.fetch_tyre_pressure()

    url, = vehicle._get.call_args[0]
    assert url.endswith('nissan/vehicle-info/v1/cars/VIN0000000000000/pressure')
    assert vehicle.tyre_pressure == {'frontLeft': 240, 'frontRight': 238}
    assert vehicle.tyre_pressure_last_updated is not None


def test_wake_up_swallows_errors():
    vehicle = _make_vehicle()
    vehicle._post = MagicMock(return_value=_response(
        {'errors': [{'detail': 'This feature is not supported at the current time.'}]}))

    assert vehicle.wake_up() is None
    url, = vehicle._post.call_args[0]
    assert url.endswith('v1/cars/VIN0000000000000/actions/wake-up-vehicle')
    assert json.loads(vehicle._post.call_args[1]['data']) == {
        'data': {'type': 'WakeUpVehicle'}}
