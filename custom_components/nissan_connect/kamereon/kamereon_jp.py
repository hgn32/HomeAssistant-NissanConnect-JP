"""JP (NissanConnect / MyNISSAN アプリ) 固有の取得・遠隔操作。

アプリ MyNISSAN 3.4.0 の挙動に合わせてある。EU 側と共通の処理は
kamereon.py に残し、こちらは JP でしか使わないものだけを持つ。
"""
import datetime
import json
import logging
import time

from .kamereon_const import Feature, VEHICLES
from .kamereon_jp_const import (
    APP_DEVICE_INFO,
    APP_OS,
    APP_OS_VERSION,
    APP_VERSION,
    REMOTE_ACTION_FAILURE,
    REMOTE_ACTION_POLL_INTERVAL,
    REMOTE_ACTION_POLL_TIMEOUT,
    REMOTE_ACTION_SUCCESS,
    REMOTE_ENGINE_STATUS_MAP,
    RemoteActionStatus,
    RemoteEngineStatus,
)

_LOGGER = logging.getLogger(__name__)


class JPSessionMixin:
    """KamereonSession の JP 用部分。"""

    def _fetch_vehicles_jp(self):
        """JP の BFF は EU と別系統 (token-info + car details) を使う。"""
        # kamereon から呼ばれる側なので、循環 import を避けて遅延で取る
        from .kamereon import Vehicle, _registry

        resp = self.oauth.get(
            '{}nissan/account/v1/token-info'.format(self.settings['user_base_url'])
        )
        vehicles = []
        for vehicle_baseinfo in resp.json()['data']['attributes']['vehicles']:
            _LOGGER.debug("token-info vehicle keys: %s", sorted(vehicle_baseinfo))
            vehicle_data = self._fetch_vehicle_details_jp(vehicle_baseinfo)
            vehicle_data.setdefault('vin', vehicle_baseinfo['vin'])
            vehicle_data['services'] = vehicle_baseinfo.get('services', [])
            vehicle_data['appConfig'] = self._fetch_app_config(vehicle_baseinfo['vin'])
            vehicle = Vehicle(vehicle_data, self.user_id)
            vehicles.append(vehicle)
            _registry[VEHICLES][vehicle.vin] = vehicle
        return vehicles

    def _fetch_vehicle_details_jp(self, vehicle_baseinfo):
        """車両の詳細。アプリと同じ vehicle-info を先に試す。

        アプリは X-VehicleIdType: UUID と一緒に UUID をパスに渡すので、
        UUID が分かるならそれを、駄目なら VIN を、それでも駄目なら
        以前から動いていた config 系を使う。どれが通ったかはログに残す。
        """
        vin = vehicle_baseinfo['vin']
        uuid = vehicle_baseinfo.get('uuid') or vehicle_baseinfo.get('id')

        attempts = []
        if uuid:
            attempts.append((
                'vehicle-info (uuid)',
                '{}nissan/vehicle-info/v1/cars/{}/details'.format(
                    self.settings['user_base_url'], uuid),
                {'X-Vehicle-Gateway': vin, 'X-VehicleIdType': 'UUID'},
            ))
        attempts.append((
            'vehicle-info (vin)',
            '{}nissan/vehicle-info/v1/cars/{}/details'.format(
                self.settings['user_base_url'], vin),
            {'X-Vehicle-Gateway': vin},
        ))
        attempts.append((
            'config',
            '{}nissan/config/v1/cars/{}/details'.format(
                self.settings['user_base_url'], vin),
            {},
        ))

        problems = []
        for label, url, headers in attempts:
            try:
                body = self.oauth.get(url, headers=headers).json()
            except Exception as err:  # noqa: BLE001
                problems.append('{}: {}'.format(label, err))
                continue
            if 'errors' in body:
                problems.append('{}: {}'.format(label, body['errors']))
                continue
            _LOGGER.debug("Vehicle details came from %s", label)
            # vehicle-info は JSON:API ({"data": {"attributes": {...}}})、
            # config は素の dict を返す
            if 'data' in body:
                return dict(body['data'].get('attributes') or {})
            return dict(body)

        raise ValueError(' | '.join(problems))

    def _fetch_app_config(self, vin):
        """アプリ自身の機能可用性マップ。取れなければ空を返す。"""
        try:
            body = self.oauth.get(
                '{}nissan/config/v1/cars/{}/features'.format(
                    self.settings['user_base_url'], vin),
                headers={'X-Vehicle-Gateway': vin,
                         'X-VehicleIdType': 'UUID',
                         'X-App-Id': self.settings.get('app_id', 'jp.co.nissan.nissanconnect.ncx')},
                params={'app_ver': APP_VERSION,
                        'os': APP_OS,
                        'os_ver': APP_OS_VERSION,
                        'device_info': APP_DEVICE_INFO}
            ).json()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Could not fetch the app feature map: %s", err)
            return {}
        if 'errors' in body:
            _LOGGER.warning("Could not fetch the app feature map: %s", body['errors'])
            return {}
        return body.get('data', {}).get('attributes', {}) or {}


class JPVehicleMixin:
    """Vehicle の JP 用部分。"""

    def wake_up(self):
        """JP: アプリが前面に戻るたびに投げている車両の起こし込み。

        アプリは DashboardViewModel.didChangeAppLifecycleState(resumed) で
        これを呼んでから dashboard の取得を始める。対応していない車両では
        エラーを返すだけなので、失敗しても後続の取得は止めない。
        """
        if self.session.region != 'JP':
            return
        try:
            resp = self._post(
                '{}v1/cars/{}/actions/wake-up-vehicle'.format(
                    self.session.settings['car_adapter_base_url'], self.vin),
                data=json.dumps({'data': {'type': 'WakeUpVehicle'}}),
                headers={'Content-Type': 'application/vnd.api+json'}
            )
            body = resp.json()
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("wake-up-vehicle failed: %s", err)
            return
        if 'errors' in body:
            _LOGGER.debug("wake-up-vehicle rejected: %s", body['errors'])
            return
        return body

    def _set_remote_engine_status(self, raw):
        """remoteEngineStatus をアプリと同じ意味に落とす。"""
        self.remote_engine_status = raw
        status = REMOTE_ENGINE_STATUS_MAP.get(str(raw), RemoteEngineStatus.UNKNOWN)
        self.remote_engine_status_text = status.value
        _LOGGER.debug("Remote engine status: %s (%s)", raw, status.value)

    def fetch_engine_status(self):
        """JP: res-state。アプリはダッシュボードで hvac-status と一緒に叩く。"""
        if self.session.region != 'JP':
            return
        if Feature.REMOTE_ENGINE_START not in self.features:
            return

        try:
            resp = self._get(
                '{}v2/cars/{}/res-state'.format(
                    self.session.settings['car_adapter_base_url'], self.vin),
                headers={'Content-Type': 'application/vnd.api+json'}
            )
            body = resp.json()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("res-state failed: %s", err)
            return
        if 'errors' in body:
            _LOGGER.warning("res-state: %s", body['errors'])
            return
        engine_data = body['data']['attributes']
        if 'remoteEngineStatus' in engine_data:
            self._set_remote_engine_status(engine_data['remoteEngineStatus'])
        if 'remoteEngineErrorStatus' in engine_data:
            self.remote_engine_error_status = engine_data['remoteEngineErrorStatus']
        if 'cycleRemainingTime' in engine_data:
            self.engine_cycle_remaining_time = engine_data['cycleRemainingTime']

    def fetch_tyre_pressure(self):
        """JP: タイヤ空気圧。"""
        if self.session.region != 'JP':
            return

        try:
            resp = self._get(
                '{}nissan/vehicle-info/v1/cars/{}/pressure'.format(
                    self.session.settings['user_base_url'], self.vin),
                headers={'Content-Type': 'application/vnd.api+json'}
            )
            body = resp.json()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("pressure failed: %s", err)
            return
        if 'errors' in body:
            _LOGGER.warning("pressure: %s", body['errors'])
            return
        pressure_data = body['data']['attributes']
        self.tyre_pressure = {
            key: value for key, value in pressure_data.items()
            if key != 'lastUpdateTime'
        }
        if 'lastUpdateTime' in pressure_data:
            self.tyre_pressure_last_updated = datetime.datetime.fromisoformat(
                pressure_data['lastUpdateTime'].replace('Z', '+00:00'))

    def fetch_remote_action_status(self, action_id):
        """JP: 遠隔操作の結果を一度だけ問い合わせる。"""
        resp = self._get(
            '{}alliance/action-status-polling/v1/cars/{}/actions/status'.format(
                self.session.settings['user_base_url'], self.vin),
            params={'actionId': action_id},
            headers={'Content-Type': 'application/vnd.api+json'}
        )
        body = resp.json()
        if 'errors' in body:
            raise ValueError(body['errors'])
        attributes = body['data']['attributes']
        raw = attributes.get('status')
        try:
            status = RemoteActionStatus(raw)
        except ValueError:
            raise ValueError('Unknown action status: {}'.format(raw))
        error = attributes.get('error') or {}
        return status, error.get('code')

    def poll_remote_action(self, action_id,
                           timeout=REMOTE_ACTION_POLL_TIMEOUT,
                           interval=REMOTE_ACTION_POLL_INTERVAL):
        """JP: アプリと同じく POST の interval 秒後から結果を取りに行く。

        成功 (COMPLETED / SYNCHRONIZED) なら status を返す。失敗
        (REJECTED / CANCELLED) なら ValueError。timeout まで決着しなければ
        最後に見た status をそのまま返す。
        """
        if self.session.region != 'JP' or not action_id:
            return None

        status = None
        deadline = time.monotonic() + timeout
        while True:
            time.sleep(interval)
            try:
                status, error_code = self.fetch_remote_action_status(action_id)
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("action status poll failed: %s", err)
                if time.monotonic() >= deadline:
                    return status
                continue
            _LOGGER.debug("action %s status=%s error=%s", action_id, status.value, error_code)
            self.last_remote_action = action_id
            self.last_remote_action_status = status.value
            if status in REMOTE_ACTION_SUCCESS:
                return status
            if status in REMOTE_ACTION_FAILURE:
                raise ValueError('Remote action {} {} (error code {})'.format(
                    action_id, status.value, error_code))
            if time.monotonic() >= deadline:
                _LOGGER.warning("action %s did not settle within %ss (last status %s)",
                                action_id, timeout, status.value)
                return status

    @staticmethod
    def _action_id(body):
        """JSON:API のレスポンスから actionId を取り出す。"""
        if not isinstance(body, dict):
            return None
        return (body.get('data') or {}).get('id')
