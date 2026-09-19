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
    PROBE_ATTR_MAX,
    PROBE_ENDPOINTS,
    PROBE_REDACT_KEYS,
    PROBE_REDACTED,
    PROBE_STATE_MAX,
    FEATURE_OPERATION_TIME_SETTING,
    GATEWAY_NGDC,
    REMOTE_ACTION_FAILURE,
    REMOTE_ACTION_LOG_MAX,
    REMOTE_ACTION_POLL_INTERVAL,
    REMOTE_ACTION_SUCCESS,
    REMOTE_ACTION_TIMEOUT_DEFAULT,
    REMOTE_ACTION_TIMEOUT_DOUBLE_START,
    REMOTE_ACTION_TIMEOUT_NGDC,
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
            vehicle_data['appConfig'] = self._fetch_app_config(vehicle_baseinfo)
            vehicle = Vehicle(vehicle_data, self.user_id)
            # details / features は UUID でしか通らない。token-info の値を持たせておく
            vehicle.uuid = vehicle_baseinfo.get('uuid')
            vehicle.gateway = vehicle_baseinfo.get('gateway')
            vehicle.probe_data = {}
            vehicle.remote_action_log = []
            vehicle.action_log_sink = None
            vehicle.subscription_name = None
            vehicle.subscription_end_date = None
            vehicle.remote_lock_entitled = None
            vehicle.remote_hvac_entitled = None
            vehicle.curfew_enabled = None
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

    def _fetch_app_config(self, vehicle_baseinfo):
        """アプリ自身の機能可用性マップ。取れなければ空を返す。

        details と同じく、アプリは UUID をパスに渡す。VIN だと BFF が
        0101 (The token is invalid or missing.) を返す。
        """
        vin = vehicle_baseinfo['vin']
        uuid = vehicle_baseinfo.get('uuid')
        app_id = self.settings.get('app_id', 'jp.co.nissan.nissanconnect.ncx')
        params = {'app_ver': APP_VERSION,
                  'os': APP_OS,
                  'os_ver': APP_OS_VERSION,
                  'device_info': APP_DEVICE_INFO}

        attempts = []
        if uuid:
            attempts.append(('uuid', uuid, {'X-Vehicle-Gateway': vin,
                                            'X-VehicleIdType': 'UUID',
                                            'X-App-Id': app_id}))
        attempts.append(('vin', vin, {'X-Vehicle-Gateway': vin,
                                      'X-App-Id': app_id}))

        problems = []
        for label, car_id, headers in attempts:
            try:
                body = self.oauth.get(
                    '{}nissan/config/v1/cars/{}/features'.format(
                        self.settings['user_base_url'], car_id),
                    headers=headers, params=params
                ).json()
            except Exception as err:  # noqa: BLE001
                problems.append('{}: {}'.format(label, err))
                continue
            if 'errors' in body:
                problems.append('{}: {}'.format(label, body['errors']))
                continue
            _LOGGER.debug("App feature map came from %s", label)
            return body.get('data', {}).get('attributes', {}) or {}

        _LOGGER.warning("Could not fetch the app feature map: %s", ' | '.join(problems))
        return {}


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

    def fetch_jp_extras(self):
        """fetch_all から呼ばれる JP 専用の取得のまとめ。

        JP 側に取得を足すときはここに追加する (kamereon.py は触らない)。
        """
        if self.session.region != 'JP':
            return
        self.fetch_engine_status()
        self.fetch_tyre_pressure()
        self.fetch_probes()

    def fetch_probes(self):
        """未確認エンドポイントを読み取って中身をそのまま持っておく。

        アプリにはあるがレスポンスを実機で見ていないものを、まとめて GET する。
        毎サイクル叩くと無駄なので、値を持っていない最初の 1 回だけ実行する
        (統合をリロードすればやり直す)。
        """
        if self.session.region != 'JP':
            return
        if getattr(self, 'probe_data', None):
            return
        self.probe_data = {}

        bases = {
            'user': self.session.settings['user_base_url'],
            'car': self.session.settings['car_adapter_base_url'],
            'notif': self.session.settings['notifications_base_url'],
        }
        today = datetime.date.today()
        ids = {'vin': self.vin,
               'user': self.user_id,
               'today': today.isoformat(),
               'month': today.strftime('%Y%m')}

        for key, base, template, params in PROBE_ENDPOINTS:
            query = {name: str(value).format(**ids)
                     for name, value in (params or {}).items()}
            result = None
            for label, car_id, headers in self._probe_variants():
                url = bases[base] + template.format(**dict(ids, vin=car_id))
                result = self._probe(key, url, label, headers, query)
                if 'error' not in result:
                    break
                # id やゲートウェイを変えても直らない種類のエラーなら諦める
                if not self._probe_retryable(result['error']):
                    break
            self.probe_data[key] = result
        self._promote_probe_values()

    def _probe_variants(self):
        """車両の指定方法の候補。最初のものが今まで動いている組み合わせ。

        アプリの X-Vehicle-Gateway は VIN ではなく VehicleGateway の名前
        ("AVN" / "NGDC" / "MOCK") で、token-info の gateway がその値。
        既定は今まで通り VIN にしておき、弾かれたときだけ他を試す。
        """
        vin = self.vin
        uuid = getattr(self, 'uuid', None)
        gateway = getattr(self, 'gateway', None)

        variants = [('vin', vin, {'X-Vehicle-Gateway': vin})]
        if gateway and gateway != vin:
            variants.append(('vin+gw', vin, {'X-Vehicle-Gateway': gateway}))
        if uuid and uuid != vin:
            variants.append(('uuid', uuid, {'X-Vehicle-Gateway': vin,
                                            'X-VehicleIdType': 'UUID'}))
            if gateway and gateway != vin:
                variants.append(('uuid+gw', uuid, {'X-Vehicle-Gateway': gateway,
                                                   'X-VehicleIdType': 'UUID'}))
        return variants

    @staticmethod
    def _probe_retryable(code):
        """車両の指定方法を変えれば通るかもしれないエラーかどうか。"""
        # 0101 トークン不正 / 0319 アクセス拒否 / 0604 対象が見つからない
        return code in ('0101', '0319', '0604')

    def _probe(self, key, url, label=None, headers=None, params=None):
        """1 エンドポイントを GET して、結果を辞書で返す。例外は出さない。"""
        request_headers = {'Content-Type': 'application/vnd.api+json'}
        request_headers.update(headers or {})
        try:
            resp = self._get(url, headers=request_headers, params=params or None)
            body = resp.json()
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("probe %s (%s) failed: %s", key, label, err)
            return {'error': 'request', 'detail': str(err), 'variant': label}
        if 'errors' in body:
            first = (body['errors'] or [{}])[0]
            _LOGGER.debug("probe %s (%s) rejected: %s", key, label, body['errors'])
            return {'error': first.get('code', 'unknown'),
                    'detail': first.get('detail', ''),
                    'variant': label}
        payload = body.get('data', body)
        if isinstance(payload, dict) and 'attributes' in payload:
            payload = payload['attributes']
        payload = self._redact(payload)
        _LOGGER.debug("probe %s (%s): %s", key, label, payload)
        return {'payload': payload, 'variant': label}

    @classmethod
    def _redact(cls, value):
        """個人情報になりうるキーの値を伏せる。"""
        if isinstance(value, dict):
            return {k: (PROBE_REDACTED if k in PROBE_REDACT_KEYS and v not in (None, '')
                        else cls._redact(v))
                    for k, v in value.items()}
        if isinstance(value, list):
            return [cls._redact(v) for v in value]
        return value

    def _promote_probe_values(self):
        """確認が取れた項目を普通の属性に移す。"""
        contract = (self.probe_data.get('contract') or {}).get('payload') or {}
        for subscription in contract.get('subscriptionInfo') or []:
            # 本契約 (0000) を見る。docomo in Car Connect (0020) は別枠
            if subscription.get('subscriptionTypeCode') != '0000':
                continue
            self.subscription_name = subscription.get('subscriptionName')
            self.subscription_end_date = self._parse_compact_date(
                subscription.get('subscriptionEndDate'))
            break

        entitlements = (self.probe_data.get('entitlements') or {}).get('payload') or {}
        lock = entitlements.get('doorLockUnlock') or {}
        hvac = entitlements.get('hvacStart') or {}
        if 'remoteLock' in lock:
            self.remote_lock_entitled = bool(lock['remoteLock'])
        if 'remoteHvac' in hvac:
            self.remote_hvac_entitled = bool(hvac['remoteHvac'])

        curfew = (self.probe_data.get('curfew_restrictions') or {}).get('payload') or {}
        restrictions = curfew.get('curfewRestrictions')
        if isinstance(restrictions, list):
            self.curfew_enabled = any(
                r.get('enable') in (True, 'Enabled') for r in restrictions)

    @staticmethod
    def _parse_compact_date(value):
        """YYYYMMDD を date にする。空や書式違いは None。"""
        if not value or len(value) != 8 or not value.isdigit():
            return None
        try:
            return datetime.date(int(value[:4]), int(value[4:6]), int(value[6:]))
        except ValueError:
            return None

    @staticmethod
    def probe_summary(result):
        """(未確認) センサーの state にする短い文字列。"""
        if not result:
            return None
        if 'error' in result:
            return 'error {}'.format(result['error'])
        try:
            text = json.dumps(result.get('payload'), ensure_ascii=False)
        except Exception:  # noqa: BLE001
            text = str(result.get('payload'))
        if len(text) > PROBE_STATE_MAX:
            text = text[:PROBE_STATE_MAX - 1] + '\u2026'
        return text

    @staticmethod
    def _json_size(value):
        """HA が属性を保存するときと同じ、UTF-8 の JSON のバイト数。"""
        try:
            return len(json.dumps(value, ensure_ascii=False).encode('utf-8'))
        except Exception:  # noqa: BLE001
            return PROBE_ATTR_MAX + 1

    @classmethod
    def probe_attributes(cls, result):
        """(未確認) センサーの属性。recorder の上限に収まるよう切り詰める。

        notifications は 500 件以上返してきて 16384 バイトを超え、recorder に
        属性ごと捨てられる。大きいものは JSON 文字列にしてから切るが、文字列に
        入れ直すと " のエスケープで膨らむので、収まるまで半分にして測り直す。
        """
        if not result:
            return {}
        if cls._json_size(result) <= PROBE_ATTR_MAX:
            return result

        raw = json.dumps(result, ensure_ascii=False).encode('utf-8')
        variant = result.get('variant') if isinstance(result, dict) else None
        limit = PROBE_ATTR_MAX
        while limit >= 64:
            trimmed = {
                'variant': variant,
                'truncated': True,
                'payload_bytes': len(raw),
                'payload_json': raw[:limit].decode('utf-8', 'ignore'),
            }
            if cls._json_size(trimmed) <= PROBE_ATTR_MAX:
                return trimmed
            limit //= 2
        return {'variant': variant, 'truncated': True, 'payload_bytes': len(raw)}

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
        _LOGGER.debug("res-state attributes: %s", engine_data)
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
        _LOGGER.debug("pressure attributes: %s", pressure_data)
        self.tyre_pressure = {
            key: value for key, value in pressure_data.items()
            if key != 'lastUpdateTime'
        }
        if 'lastUpdateTime' in pressure_data:
            self.tyre_pressure_last_updated = datetime.datetime.fromisoformat(
                pressure_data['lastUpdateTime'].replace('Z', '+00:00'))

    # ------------------------------------------------------------------
    # ヘッダ / タイムアウト (アプリと同じ決め方)
    # ------------------------------------------------------------------

    def gateway_header(self):
        """X-Vehicle-Gateway に入れる値。

        アプリは token-info の gateway を VehicleGateway に変換し、toName (0xbdaf64) で
        "AVN" / "NGDC" / "MOCK" / その他はその名前 に戻して全リクエストに付ける。
        つまり token-info の gateway 文字列そのまま。無ければ VIN に倒す。
        """
        return getattr(self, 'gateway', None) or self.vin

    def remote_action_timeout(self, double_start=False):
        """結果ポーリングの打ち切り秒数 (RemoteActionTimeoutResolverImpl 0x16d45a8)。"""
        if (getattr(self, 'gateway', None) or '') == GATEWAY_NGDC:
            return REMOTE_ACTION_TIMEOUT_NGDC
        if double_start:
            return REMOTE_ACTION_TIMEOUT_DOUBLE_START
        return REMOTE_ACTION_TIMEOUT_DEFAULT

    def app_flag(self, *path):
        """features の真偽値。bool ならそのまま、{available: bool} ならその値、無ければ None。"""
        if not getattr(self, 'app_config', None):
            return None
        node = self.app_config
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return None
            node = node[key]
        if isinstance(node, bool):
            return node
        if isinstance(node, dict):
            value = node.get('available')
            return bool(value) if value is not None else None
        return None

    def double_start_available(self):
        """20分始動をアプリが選択肢として出す条件と同じ。features が無ければ None。"""
        return self.app_flag(*FEATURE_OPERATION_TIME_SETTING)

    # ------------------------------------------------------------------
    # 遠隔操作のログ (リクエスト / レスポンス / ポーリングをそのまま残す)
    # ------------------------------------------------------------------

    @staticmethod
    def _now():
        return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds')

    @staticmethod
    def _loggable_headers(headers):
        """Authorization だけ伏せる。それ以外はアプリとの照合に必要なのでそのまま。"""
        result = {}
        for key, value in (headers or {}).items():
            result[key] = '***' if key.lower() == 'authorization' else value
        return result

    def _trace_start(self, name, method, url, headers=None, body=None):
        trace = {
            'action': name,
            'started': self._now(),
            'request': {
                'method': method,
                'url': url,
                'headers': self._loggable_headers(headers),
                'body': body,
            },
            'response': None,
            'polls': [],
            'result': None,
            'error': None,
            'finished': None,
            'res_state_before': None,
            'res_state_after': None,
        }
        log = getattr(self, 'remote_action_log', None)
        if log is None:
            log = self.remote_action_log = []
        log.append(trace)
        del log[:-REMOTE_ACTION_LOG_MAX]
        return trace

    @staticmethod
    def _trace_response(trace, resp, body):
        trace['response'] = {
            'status_code': getattr(resp, 'status_code', None),
            'body': body,
            'at': JPVehicleMixin._now(),
        }

    @staticmethod
    def _trace_poll(trace, body, status=None, error_code=None, exception=None):
        trace['polls'].append({
            'at': JPVehicleMixin._now(),
            'status': status.value if status is not None else None,
            'error_code': error_code,
            'body': body,
            'exception': str(exception) if exception else None,
        })

    def _trace_finish(self, trace, result, error=None):
        if 'res_state_before' in trace and trace.get('res_state_after') is None:
            trace['res_state_after'] = self.res_state_snapshot()
        trace['result'] = result
        trace['error'] = str(error) if error else None
        trace['finished'] = self._now()
        _LOGGER.info("remote action %s -> %s (%d polls)%s",
                     trace['action'], result, len(trace['polls']),
                     ' error: {}'.format(error) if error else '')
        sink = getattr(self, 'action_log_sink', None)
        if sink is not None:
            try:
                sink(trace)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("remote action log sink failed: %s", err)

    def res_state_snapshot(self):
        """res-state をそのまま読む。遠隔操作の前後の車両状態を記録するため。

        アプリもダッシュボードで同じ GET をしている。失敗しても操作は止めない。
        """
        if self.session.region != 'JP':
            return None
        try:
            resp = self._get(
                '{}v2/cars/{}/res-state'.format(
                    self.session.settings['car_adapter_base_url'], self.vin),
                headers={'Content-Type': 'application/vnd.api+json'}
            )
            body = resp.json()
        except Exception as err:  # noqa: BLE001
            return {'error': str(err)}
        if 'errors' in body:
            return {'errors': body['errors']}
        return body.get('data', {}).get('attributes')

    def execute_remote_action(self, name, url, body, headers=None, wait=True,
                              double_start=False):
        """JP: 遠隔操作の POST → 結果ポーリング。全部をログに残す。

        アプリの順序と同じで、POST の前に他の呼び出しはしない。
        ヘッダは _request が X-Vehicle-Gateway を足す。

        ポーリングの CANCELLED はこの車両では実際に実行された操作にも返るので、
        判定材料として res-state を POST の前後に取って記録だけしておく
        (送信内容は変えない。読み取りだけ)。
        """
        request_headers = {'Content-Type': 'application/vnd.api+json'}
        request_headers.update(headers or {})
        request_headers.setdefault('X-Vehicle-Gateway', self.gateway_header())
        trace = self._trace_start(name, 'POST', url, request_headers, body)
        trace['res_state_before'] = self.res_state_snapshot()
        try:
            resp = self._post(url, data=json.dumps(body), headers=request_headers)
            try:
                payload = resp.json()
            except ValueError:
                payload = {'raw': getattr(resp, 'text', None)}
            self._trace_response(trace, resp, payload)
            if 'errors' in payload:
                raise ValueError(payload['errors'])
            action_id = self._action_id(payload)
            if wait:
                status = self.poll_remote_action(
                    action_id, timeout=self.remote_action_timeout(double_start), trace=trace)
                self._trace_finish(trace, status.value if status else 'no-action-id')
            else:
                self._trace_finish(trace, 'posted')
            return payload
        except Exception as err:
            self._trace_finish(trace, 'failed', err)
            raise

    # ------------------------------------------------------------------
    # 結果ポーリング (alliance/action-status-polling)
    # ------------------------------------------------------------------

    def fetch_remote_action_status(self, action_id, trace=None):
        """JP: 遠隔操作の結果を一度だけ問い合わせる。"""
        resp = self._get(
            '{}alliance/action-status-polling/v1/cars/{}/actions/status'.format(
                self.session.settings['user_base_url'], self.vin),
            params={'actionId': action_id},
            headers={'Content-Type': 'application/vnd.api+json'}
        )
        body = resp.json()
        if 'errors' in body:
            if trace is not None:
                self._trace_poll(trace, body, exception=body['errors'])
            raise ValueError(body['errors'])
        attributes = body['data']['attributes']
        raw = attributes.get('status')
        try:
            status = RemoteActionStatus(raw)
        except ValueError:
            if trace is not None:
                self._trace_poll(trace, body, exception='unknown status {}'.format(raw))
            raise ValueError('Unknown action status: {}'.format(raw))
        error = attributes.get('error') or {}
        if trace is not None:
            self._trace_poll(trace, body, status, error.get('code'))
        return status, error.get('code')

    def poll_remote_action(self, action_id, timeout=None,
                           interval=REMOTE_ACTION_POLL_INTERVAL, trace=None):
        """JP: アプリと同じく POST の interval 秒後から結果を取りに行く。

        成功 (COMPLETED / SYNCHRONIZED) なら status を返す。失敗
        (REJECTED / CANCELLED) なら ValueError。timeout まで決着しなければ
        最後に見た status をそのまま返す。timeout 省略時はゲートウェイで決める。
        """
        if self.session.region != 'JP' or not action_id:
            return None
        if timeout is None:
            timeout = self.remote_action_timeout()

        status = None
        deadline = time.monotonic() + timeout
        while True:
            time.sleep(interval)
            try:
                status, error_code = self.fetch_remote_action_status(action_id, trace=trace)
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("action status poll failed: %s", err)
                if trace is not None and not (trace['polls'] and trace['polls'][-1].get('exception')):
                    self._trace_poll(trace, None, exception=err)
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

    def last_remote_action_trace(self):
        log = getattr(self, 'remote_action_log', None) or []
        return log[-1] if log else None
