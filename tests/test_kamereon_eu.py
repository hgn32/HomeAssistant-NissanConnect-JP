"""EU (NissanConnect) 固有の挙動のテスト。"""
from unittest.mock import MagicMock

from custom_components.nissan_connect.kamereon.kamereon import Vehicle
from custom_components.nissan_connect.kamereon.kamereon_const import Feature


def _make_eu_vehicle():
    vehicle = Vehicle.__new__(Vehicle)
    vehicle.vin = 'VIN0000000000000'
    vehicle.user_id = 'user'
    vehicle.features = [Feature.APP_DOOR_LOCKING]

    session = MagicMock()
    session.region = 'EU'
    session.settings = {
        'car_adapter_base_url': 'https://car-adapter/',
    }
    vehicle._session = session
    type(vehicle).session = property(lambda self: self._session)
    return vehicle


def test_lock_unlock_url_has_no_stray_quote():
    """回帰テスト: lock-unlock の URL に余分な二重引用符が混入していた不具合の再発防止。

    修正前は '...actions/lock-unlock"' のように末尾に `"` が混入し、
    実際の日産 BFF に対する URL が不正になっていた。
    """
    vehicle = _make_eu_vehicle()
    vehicle._post = MagicMock(return_value=MagicMock(json=MagicMock(return_value={'data': {'id': 'action-1'}})))
    vehicle.poll_remote_action = MagicMock()

    vehicle.lock_unlock(srp='srp-token', action='lock')

    url = vehicle._post.call_args[0][0]
    assert url.endswith('actions/lock-unlock')
    assert '"' not in url
