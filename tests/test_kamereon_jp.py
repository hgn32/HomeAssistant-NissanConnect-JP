"""JP (NissanConnect / MyNISSAN アプリ) 固有の挙動のテスト。"""
import base64
import json
from unittest.mock import MagicMock, patch

import pytest

from custom_components.nissan_connect.kamereon.kamereon import NCISession, Vehicle
from custom_components.nissan_connect.kamereon.kamereon_const import Feature, HVACAction
from custom_components.nissan_connect.kamereon.kamereon_jp import decode_jwt_claims, redact_claims
from custom_components.nissan_connect.kamereon.kamereon_jp_const import (
    APP_USER_AGENT,
    ENGINE_START_USER_ARGUMENT,
    GRAPHQL_USER_AGENT,
    PROCEDURE_ENGINE_START,
    PROCEDURE_ENGINE_STOP,
    PROCEDURE_LOCK,
    PROCEDURE_REVISION,
    PROCEDURE_SIGNATURE,
    EngineCycleTime,
    RemoteActionStatus,
    RemoteEngineStatus,
)

# ダミー値 (実車の UUID ではない)
DUMMY_UUID = '00000000-0000-0000-0000-000000000000'
DUMMY_TASK_ID = '00000000-0000-0000-0000-000000000001'


def _make_jwt(payload):
    """署名検証はしないテスト用の JWT。ヘッダ・署名部はダミー。"""
    def _segment(data):
        raw = json.dumps(data).encode('utf-8')
        return base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii')

    header = _segment({'alg': 'none', 'typ': 'JWT'})
    body = _segment(payload)
    return '{}.{}.dummy-signature'.format(header, body)


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
    # front-api-market の ApplyProcedure は vehicleId に carId (VIN) ではなく UUID を使う
    vehicle.uuid = DUMMY_UUID

    session = MagicMock()
    session.region = 'JP'
    session.settings = {
        'user_base_url': 'https://bff/nc-app-bff/',
        'car_adapter_base_url': 'https://bff/nc-app-bff/alliance/car-adapter/',
        'front_api_base_url': 'https://front-api/',
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


def test_engine_start_uses_graphql_apply_procedure():
    """アプリ3.5.0と同じく、既定の始動ボタンはGraphQL ApplyProcedure(ENGINE_START)で送る。

    force_target_temperature/gateway_override/action_label を指定しない通常呼び出しが
    対象。
    """
    vehicle = _make_vehicle([Feature.REMOTE_ENGINE_START])
    vehicle.gateway = 'AVN'
    vehicle.apply_procedure = MagicMock(return_value={'data': {'apply': {'callbackKey': None}}})

    vehicle.set_hvac_status(HVACAction.START, 21, cycle_time=EngineCycleTime.DOUBLE)

    vehicle.apply_procedure.assert_called_once_with(
        PROCEDURE_ENGINE_START,
        user_argument=ENGINE_START_USER_ARGUMENT,
        wait=True,
        double_start=True,
    )


def test_gateway_header_falls_back_to_vin():
    vehicle = _make_vehicle()
    assert vehicle.gateway_header() == 'VIN0000000000000'
    vehicle.gateway = 'NGDC'
    assert vehicle.gateway_header() == 'NGDC'


def test_request_adds_gateway_header():
    """_request は全リクエストに token-info の gateway を付ける。"""
    vehicle = _make_vehicle()
    vehicle.gateway = 'AVN'
    vehicle.session.oauth.get = MagicMock(return_value=_response({}))

    vehicle._get('https://bff/x', headers={'Content-Type': 'application/vnd.api+json'})

    headers = vehicle.session.oauth.get.call_args[1]['headers']
    assert headers['X-Vehicle-Gateway'] == 'AVN'


@pytest.mark.parametrize('gateway,double,expected', [
    ('NGDC', False, 300),
    ('NGDC', True, 300),
    ('AVN', True, 400),
    ('AVN', False, 200),
    (None, False, 200),
])
def test_remote_action_timeout_matches_app(gateway, double, expected):
    vehicle = _make_vehicle()
    vehicle.gateway = gateway
    assert vehicle.remote_action_timeout(double_start=double) == expected


def test_remote_action_is_traced(monkeypatch):
    """POST / レスポンス / 各ポーリングがそのまま記録され、sink に渡る。

    STOP を使う (JP の START は apply_procedure に差し替わったため、
    execute_remote_action の汎用トレース挙動は STOP 経由で確認する)。
    """
    monkeypatch.setattr(
        'custom_components.nissan_connect.kamereon.kamereon_jp.time.sleep', lambda _: None)
    vehicle = _make_vehicle([Feature.REMOTE_ENGINE_START])
    vehicle.gateway = 'AVN'
    vehicle._post = MagicMock(return_value=_response({'data': {'id': 'action-9'}}))
    vehicle._get = MagicMock(side_effect=[
        # POST の前に取る res-state
        _response({'data': {'attributes': {'remoteEngineStatus': '6'}}}),
        _response({'data': {'attributes': {'status': 'PENDING'}}}),
        _response({'data': {'attributes': {'status': 'COMPLETED', 'error': {'code': 0}}}}),
        # ポーリング終了後に取る res-state
        _response({'data': {'attributes': {'remoteEngineStatus': '12'}}}),
    ])
    sink = MagicMock()
    vehicle.action_log_sink = sink

    vehicle.set_hvac_status(HVACAction.STOP)

    trace = vehicle.last_remote_action_trace()
    assert trace['action'] == 'hvac_stop'
    assert trace['request']['url'].endswith('/hvac-control')
    assert trace['request']['headers']['X-Vehicle-Gateway'] == 'AVN'
    assert 'targetCycleTime' not in trace['request']['body']['data']['attributes']
    assert trace['response']['body'] == {'data': {'id': 'action-9'}}
    assert [p['status'] for p in trace['polls']] == ['PENDING', 'COMPLETED']
    assert trace['res_state_before'] == {'remoteEngineStatus': '6'}
    assert trace['res_state_after'] == {'remoteEngineStatus': '12'}
    assert trace['result'] == 'COMPLETED'
    assert trace['error'] is None
    sink.assert_called_once_with(trace)


def test_remote_action_rejected_is_traced(monkeypatch):
    """REJECTED は従来どおり失敗として扱われ、ValueError で終端する。"""
    monkeypatch.setattr(
        'custom_components.nissan_connect.kamereon.kamereon_jp.time.sleep', lambda _: None)
    vehicle = _make_vehicle([Feature.REMOTE_ENGINE_START])
    vehicle._post = MagicMock(return_value=_response({'data': {'id': 'action-9'}}))
    vehicle._get = MagicMock(return_value=_response(
        {'data': {'attributes': {'status': 'REJECTED', 'error': {'code': 'Failed'}}}}))

    with pytest.raises(ValueError, match='REJECTED'):
        vehicle.set_hvac_status(HVACAction.STOP)

    trace = vehicle.last_remote_action_trace()
    assert trace['result'] == 'failed'
    assert 'REJECTED' in trace['error']
    assert trace['polls'][0]['error_code'] == 'Failed'
    assert trace['polls'][0]['body']['data']['attributes']['status'] == 'REJECTED'


def test_remote_action_cancelled_is_indeterminate_not_failure(monkeypatch):
    """CANCELLED は実車では成功していることがある (3.4 の実車記録) ため、

    例外を投げずにポーリングを打ち切り、status をそのまま結果として記録する。
    修正前のコードは CANCELLED を REMOTE_ACTION_FAILURE として扱い ValueError を
    送出していたため、このテストは修正前では失敗し、修正後に成功する。
    """
    monkeypatch.setattr(
        'custom_components.nissan_connect.kamereon.kamereon_jp.time.sleep', lambda _: None)
    vehicle = _make_vehicle([Feature.REMOTE_ENGINE_START])
    vehicle._post = MagicMock(return_value=_response({'data': {'id': 'action-9'}}))
    vehicle._get = MagicMock(return_value=_response(
        {'data': {'attributes': {'status': 'CANCELLED', 'error': {'code': 'Failed'}}}}))

    # 例外が飛ばないこと
    vehicle.set_hvac_status(HVACAction.STOP)

    trace = vehicle.last_remote_action_trace()
    assert trace['result'] == 'CANCELLED'
    assert trace['error'] is None
    assert trace['polls'][0]['error_code'] == 'Failed'
    assert trace['polls'][0]['body']['data']['attributes']['status'] == 'CANCELLED'
    # ポーリングがそこで止まり、余計な fetch は起きていないこと
    # (res-state 前後の 2 回 + ポーリング 1 回 = 計 3 回の _get 呼び出し)
    assert vehicle._get.call_count == 3
    # last_remote_action_status にも CANCELLED が反映されること
    assert vehicle.last_remote_action_status == 'CANCELLED'


def test_lock_matches_app_payload():
    """アプリ3.5.0と同じく、施錠はGraphQL ApplyProcedure(LOCK)で送る。"""
    vehicle = _make_vehicle([Feature.APP_DOOR_LOCKING])
    vehicle.gateway = 'AVN'
    vehicle.apply_procedure = MagicMock(return_value={'data': {'apply': {'callbackKey': None}}})

    vehicle.lock()

    vehicle.apply_procedure.assert_called_once_with(PROCEDURE_LOCK, wait=True)


def test_apply_procedure_sends_graphql_mutation_without_gateway_header():
    """front-api-market へ ApplyProcedure(GraphQL) を送り、

    X-Vehicle-Gateway / X-App-Id を付けないこと (docs/jp_api.md「遠隔操作」実測)。
    """
    vehicle = _make_vehicle([Feature.REMOTE_ENGINE_START])
    vehicle._post = MagicMock(return_value=_response(
        {'data': {'apply': {'callbackKey': None}}}))
    vehicle.poll_remote_action = MagicMock()

    vehicle.apply_procedure(PROCEDURE_ENGINE_START, ENGINE_START_USER_ARGUMENT, wait=False)

    url, = vehicle._post.call_args[0]
    assert url == 'https://front-api/query'
    body = json.loads(vehicle._post.call_args[1]['data'])
    assert body['operationName'] == 'ApplyProcedure'
    assert 'apply(input' in body['query']
    input_data = body['variables']['input']
    assert input_data['procedure'] == 'ENGINE_START'
    assert input_data['revision'] == PROCEDURE_REVISION
    assert input_data['signature'] == PROCEDURE_SIGNATURE
    assert input_data['userArgument'] == ENGINE_START_USER_ARGUMENT
    # carId (VIN) ではなく車両 UUID を使う
    assert input_data['contextArgument'] == {'vehicleIdInput': {'vehicleId': DUMMY_UUID}}

    headers = vehicle._post.call_args[1]['headers']
    assert headers['user-agent'] == GRAPHQL_USER_AGENT
    assert headers['content-type'] == 'application/json'
    assert headers['x-service-version'] == '3.5.0'
    assert headers['production-variant'] == 'jp'
    assert 'traceparent' in headers
    # X-Vehicle-Gateway は None を渡して _request 側の自動付与を抑止する (送信前に除去される)
    assert headers.get('X-Vehicle-Gateway') is None
    assert 'X-App-Id' not in headers
    # wait=False なのでポーリングは行わない
    vehicle.poll_remote_action.assert_not_called()


def test_apply_procedure_request_omits_gateway_header_on_the_wire():
    """実際の送信経路 (_request) でも X-Vehicle-Gateway が付かないことを確認する。

    JP の _request は既定で X-Vehicle-Gateway を setdefault するが、apply_procedure が
    値 None を渡すことで送信前に除去される。session.oauth.post に渡る実ヘッダで検証する。
    """
    vehicle = _make_vehicle([Feature.REMOTE_ENGINE_START])
    vehicle.gateway = 'AVN'
    vehicle.poll_remote_action = MagicMock()
    vehicle.session.oauth.post = MagicMock(return_value=_response(
        {'data': {'apply': {'callbackKey': None}}}))

    vehicle.apply_procedure(PROCEDURE_ENGINE_START, ENGINE_START_USER_ARGUMENT, wait=False)

    sent_headers = vehicle.session.oauth.post.call_args[1]['headers']
    assert 'X-Vehicle-Gateway' not in sent_headers
    assert sent_headers['user-agent'] == GRAPHQL_USER_AGENT


def test_request_still_adds_gateway_header_for_normal_jp_calls():
    """通常の JP リクエストでは従来どおり X-Vehicle-Gateway が付く (回帰防止)。"""
    vehicle = _make_vehicle([Feature.REMOTE_ENGINE_START])
    vehicle.gateway_header = MagicMock(return_value='AVN')
    vehicle.session.oauth.get = MagicMock(return_value=_response({}))

    vehicle._get('https://bff/nc-app-bff/something')

    sent_headers = vehicle.session.oauth.get.call_args[1]['headers']
    assert sent_headers['X-Vehicle-Gateway'] == 'AVN'


def test_apply_procedure_lock_has_no_user_argument():
    """LOCK は userArgument を送らない (実測)。"""
    vehicle = _make_vehicle([Feature.APP_DOOR_LOCKING])
    vehicle._post = MagicMock(return_value=_response(
        {'data': {'apply': {'callbackKey': None}}}))
    vehicle.poll_remote_action = MagicMock()

    vehicle.apply_procedure(PROCEDURE_LOCK, wait=False)

    body = json.loads(vehicle._post.call_args[1]['data'])
    assert 'userArgument' not in body['variables']['input']
    assert body['variables']['input']['procedure'] == 'LOCK'


def test_decode_callback_action_id_extracts_task_id():
    """callbackKey (base64) をデコードし、Tasks[0].ID を actionId として取り出す。"""
    payload = 'ENGINE_START:' + json.dumps({
        'Tasks': [{
            'DetailCode': 'engineStart',
            'ID': DUMMY_TASK_ID,
            'Status': 0,
            'VehicleID': DUMMY_UUID,
        }],
        'Revision': '123',
    })
    callback_key = base64.b64encode(payload.encode('utf-8')).decode('ascii')

    vehicle = _make_vehicle()

    assert vehicle._decode_callback_action_id(callback_key) == DUMMY_TASK_ID


@pytest.mark.parametrize('callback_key', [None, '', 'not-valid-base64!!'])
def test_decode_callback_action_id_returns_none_for_missing_or_malformed(callback_key):
    vehicle = _make_vehicle()
    assert vehicle._decode_callback_action_id(callback_key) is None


def test_authorization_is_redacted_in_trace():
    vehicle = _make_vehicle()
    trace = vehicle._trace_start('x', 'POST', 'https://bff/x',
                                 {'Authorization': 'Bearer secret', 'X-Vehicle-Gateway': 'AVN'}, {})
    assert trace['request']['headers'] == {'Authorization': '***', 'X-Vehicle-Gateway': 'AVN'}


def test_remote_action_log_is_capped():
    vehicle = _make_vehicle()
    for i in range(30):
        vehicle._trace_start('x{}'.format(i), 'POST', 'https://bff/x', {}, {})
    assert len(vehicle.remote_action_log) == 20
    assert vehicle.remote_action_log[-1]['action'] == 'x29'


@pytest.mark.parametrize('config,expected', [
    ({}, None),
    ({'remoteEngineStart': {'operationTimeSetting': True}}, True),
    ({'remoteEngineStart': {'operationTimeSetting': False}}, False),
    ({'remoteEngineStart': {'operationTimeSetting': {'available': True}}}, True),
    ({'remoteEngineStart': {'available': True}}, None),
])
def test_double_start_available_follows_features(config, expected):
    vehicle = _make_vehicle()
    vehicle.app_config = config
    assert vehicle.double_start_available() is expected


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
        'custom_components.nissan_connect.kamereon.kamereon_jp.time.sleep', lambda _: None)
    vehicle = _make_vehicle()
    vehicle.fetch_remote_action_status = MagicMock(side_effect=[
        (RemoteActionStatus.PENDING, None),
        (RemoteActionStatus.COMPLETED, None),
    ])

    assert vehicle.poll_remote_action('action-1') is RemoteActionStatus.COMPLETED
    assert vehicle.last_remote_action_status == 'COMPLETED'


def test_poll_remote_action_rejected(monkeypatch):
    monkeypatch.setattr(
        'custom_components.nissan_connect.kamereon.kamereon_jp.time.sleep', lambda _: None)
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


def test_wake_up_swallows_errors():
    vehicle = _make_vehicle()
    vehicle._post = MagicMock(return_value=_response(
        {'errors': [{'detail': 'This feature is not supported at the current time.'}]}))

    assert vehicle.wake_up() is None
    url, = vehicle._post.call_args[0]
    assert url.endswith('v1/cars/VIN0000000000000/actions/wake-up-vehicle')
    assert json.loads(vehicle._post.call_args[1]['data']) == {
        'data': {'type': 'WakeUpVehicle'}}


def test_fetch_probes_collects_payloads_and_errors(monkeypatch):
    from custom_components.nissan_connect.kamereon import kamereon_jp_const as jpc
    monkeypatch.setattr(jpc, 'PROBE_ENDPOINTS', (
        ('good', 'user', 'nissan/account/v1/cars/{vin}/contract', None),
        ('bad', 'car', 'v1/cars/{vin}/settings/gfc-restrictions', None),
    ), raising=False)
    from custom_components.nissan_connect.kamereon import kamereon_jp
    monkeypatch.setattr(kamereon_jp, 'PROBE_ENDPOINTS', jpc.PROBE_ENDPOINTS)

    vehicle = _make_vehicle()
    vehicle.session.settings['notifications_base_url'] = 'https://bff/nc-app-bff/alliance/notifications/'
    vehicle.user_id = 'user'
    vehicle.uuid = 'UUID-1'
    vehicle.probe_data = {}

    def fake_get(url, headers=None, params=None):
        if url.endswith('/contract'):
            return _response({'data': {'attributes': {'expiry': '2030-01-01'}}})
        return _response({'errors': [{'code': '0145', 'detail': 'nope'}]})

    vehicle._get = MagicMock(side_effect=fake_get)
    vehicle.fetch_probes()

    assert vehicle.probe_data['good']['payload'] == {'expiry': '2030-01-01'}
    assert vehicle.probe_data['bad']['error'] == '0145'


def test_fetch_probes_runs_once(monkeypatch):
    vehicle = _make_vehicle()
    vehicle.probe_data = {'already': {'payload': 1}}
    vehicle._get = MagicMock()
    vehicle.fetch_probes()
    vehicle._get.assert_not_called()


def test_probe_endpoints_no_longer_include_removed_keys():
    """ユーザー指定の削除対象はすべて PROBE_ENDPOINTS から削除済み。

    contract は _promote_probe_values (契約プラン/終了日) の元データなので取得は残す
    (PROBE_INTERNAL_KEYS で (未確認) センサー化だけを止める)。entitlements
    (遠隔施錠/エアコン利用可否) と curfew_restrictions (時間帯アラート) は、対応する
    二値センサーごとユーザー指定で削除済みのため、これらも取得しない。
    残るのは contract の 1 件のみ (エリア通知機能そのものを削除したため
    area_restrictions も削除済み。HA 側の zone / device_tracker で実現する方針になった)。

    hvac_status_raw / campaign_info は別途ユーザー指定で削除済み
    (対応する (未確認) センサーが不要と判断されたため)。
    hvac_settings / vehicle_reminder_settings / profile / role_info /
    gfc_restrictions も別途ユーザー指定で削除済み。
    """
    from custom_components.nissan_connect.kamereon.kamereon_jp_const import (
        PROBE_ENDPOINTS,
        PROBE_INTERNAL_KEYS,
    )

    removed_keys = {
        'announcement_list', 'announcement_unread', 'eco_columns',
        'eco_top_local_ranking', 'eco_local_ranking_history',
        'eco_daily_driving_score', 'notification_activate_status',
        'notifications', 'nyuko_stream', 'speed_restrictions', 'token_info',
        'hvac_status_raw', 'campaign_info',
        'hvac_settings', 'vehicle_reminder_settings', 'profile', 'role_info',
        'gfc_restrictions', 'area_restrictions',
        'entitlements', 'curfew_restrictions',
        # 読み取り API ではなかったもの (エラーだから消したのではない):
        #   hvac_schedule     -> APK には postHvacSchedule しかなく POST 専用
        #   action_status_all -> ?actionId= が必須で一覧取得の口ではない
        'hvac_schedule', 'action_status_all',
    }
    kept_internal_keys = {'contract'}

    endpoint_keys = {entry[0] for entry in PROBE_ENDPOINTS}

    assert endpoint_keys.isdisjoint(removed_keys)
    assert kept_internal_keys <= endpoint_keys
    assert endpoint_keys == kept_internal_keys
    assert PROBE_INTERNAL_KEYS == kept_internal_keys
    assert PROBE_INTERNAL_KEYS <= endpoint_keys


def test_probe_redacts_personal_fields():
    vehicle = _make_vehicle()
    payload = {'userInfo': {'userName': 'somebody', 'phoneNum': '000',
                            'ncId': 'x', 'contractStartDate': None},
               'subscriptionInfo': [{'subscriptionName': 'plan', 'vin': 'VIN1'}]}
    redacted = vehicle._redact(payload)
    assert redacted['userInfo']['userName'] == '***'
    assert redacted['userInfo']['phoneNum'] == '***'
    assert redacted['userInfo']['ncId'] == '***'
    # 空や None は伏せても意味がないのでそのまま
    assert redacted['userInfo']['contractStartDate'] is None
    assert redacted['subscriptionInfo'][0]['vin'] == '***'
    assert redacted['subscriptionInfo'][0]['subscriptionName'] == 'plan'


def test_probe_variants_cover_gateway_and_uuid():
    vehicle = _make_vehicle()
    vehicle.uuid = 'UUID-1'
    vehicle.gateway = 'AVN'
    labels = [v[0] for v in vehicle._probe_variants()]
    assert labels == ['vin', 'vin+gw', 'uuid', 'uuid+gw']
    # gateway / uuid が無ければ VIN だけ
    plain = _make_vehicle()
    plain.uuid = None
    plain.gateway = None
    assert [v[0] for v in plain._probe_variants()] == ['vin']


def test_promote_probe_values():
    vehicle = _make_vehicle()
    vehicle.probe_data = {
        'contract': {'payload': {'subscriptionInfo': [
            {'subscriptionName': 'docomo', 'subscriptionEndDate': '',
             'subscriptionTypeCode': '0020'},
            {'subscriptionName': 'plan', 'subscriptionEndDate': '20270630',
             'subscriptionTypeCode': '0000'},
        ]}},
    }
    vehicle._promote_probe_values()
    assert vehicle.subscription_name == 'plan'
    assert vehicle.subscription_end_date == __import__('datetime').date(2027, 6, 30)


def test_decode_jwt_claims_valid_jwt():
    token = _make_jwt({'aud': 'ncb_jp_client', 'client_id': 'test', 'exp': 123})
    claims = decode_jwt_claims(token)
    assert claims == {'aud': 'ncb_jp_client', 'client_id': 'test', 'exp': 123}


def test_decode_jwt_claims_rejects_non_jwt_strings():
    assert decode_jwt_claims('not-a-jwt') is None
    assert decode_jwt_claims('') is None
    assert decode_jwt_claims(None) is None
    # 3 分割はできるが base64 として壊れている
    assert decode_jwt_claims('a.!!!.c') is None


def test_redact_claims_keeps_allowlist_only():
    claims = {
        'aud': 'ncb_jp_client',
        'client_id': 'test',
        'exp': 123,
        'sub': 'user-secret-id',
        'email': 'someone@example.com',
        'cnf': {'jkt': 'thumbprint'},
    }
    redacted = redact_claims(claims)
    assert redacted['aud'] == 'ncb_jp_client'
    assert redacted['client_id'] == 'test'
    assert redacted['exp'] == 123
    # allowlist に無いキー、および入れ子構造の値は伏せるが、キー名は残す
    assert redacted['sub'] == '***'
    assert redacted['email'] == '***'
    assert redacted['cnf'] == '***'
    assert set(redacted) == set(claims)


def _bff_login_response(access_token, id_token):
    resp = MagicMock()
    resp.json.return_value = {
        'data': {
            'attributes': {
                'access_token': access_token,
                'id_token': id_token,
                'refresh_token': 'refresh-secret-value',
                'scope': 'openid profile',
                'token_type': 'Bearer',
                'expires_in': 3600,
            }
        }
    }
    return resp


def test_login_jp_bff_records_redacted_token_claims():
    """BFF ログイン応答の JWT claims を、車を動かさずに診断用へ残す。

    access_token / id_token 本体・sub 等の個人情報は保存せず、allowlist の
    キー (aud/client_id など) だけを self.token_claims に残す。既存の戻り値
    (access_token 文字列) は変わらない。
    """
    access_token = _make_jwt({'aud': 'ncb_jp_client', 'client_id': 'test',
                              'sub': 'user-secret-id', 'exp': 123})
    id_token = _make_jwt({'aud': 'ncb_jp_client', 'sub': 'user-secret-id',
                          'email': 'someone@example.com'})

    session = NCISession('JP')
    session.session = MagicMock()
    session.session.post.return_value = _bff_login_response(access_token, id_token)

    token = session._login_jp_bff('user@example.com', 'password123')

    # アプリと同じ Content-Type (application/vnd.api+json) で送っていること。
    # requests の json= だと application/json になり別クライアント扱いされる
    # 疑いがあるため、data=json.dumps(...) + 明示的ヘッダで送る
    # (docs/jp_api.md「ログイン」)。
    _, post_kwargs = session.session.post.call_args
    assert post_kwargs['headers']['Content-Type'] == 'application/vnd.api+json'
    assert 'json' not in post_kwargs
    assert isinstance(post_kwargs['data'], str)
    assert json.loads(post_kwargs['data']) == {
        'data': {
            'type': 'token',
            'attributes': {
                'username': 'user@example.com',
                'password': 'password123',
            }
        }
    }

    assert token == access_token
    assert session.token_claims['access_token']['aud'] == 'ncb_jp_client'
    assert session.token_claims['access_token']['client_id'] == 'test'
    assert session.token_claims['access_token']['sub'] == '***'
    assert session.token_claims['id_token']['aud'] == 'ncb_jp_client'
    assert session.token_claims['id_token']['sub'] == '***'
    assert session.token_claims['id_token']['email'] == '***'
    assert session.token_claims['scope'] == 'openid profile'
    assert session.token_claims['token_type'] == 'Bearer'
    assert session.token_claims['expires_in'] == 3600
    # 個人情報・トークン本体はどこにも残さない
    dumped = json.dumps(session.token_claims)
    assert 'user-secret-id' not in dumped
    assert 'someone@example.com' not in dumped
    assert 'refresh-secret-value' not in dumped


def test_login_jp_bff_without_jwt_tokens_leaves_claims_none():
    """access_token が JWT でない (KAuth 経由など想定外の形) 場合も落ちない。"""
    session = NCISession('JP')
    session.session = MagicMock()
    session.session.post.return_value = _bff_login_response('opaque-token', None)

    token = session._login_jp_bff('user@example.com', 'password123')

    assert token == 'opaque-token'
    assert session.token_claims['access_token'] is None
    assert session.token_claims['id_token'] is None


def test_login_jp_sets_app_user_agent_on_session_and_oauth():
    """JP の /login とその後の全要求にアプリと同じ User-Agent を付ける。

    アプリの UserAgentInterceptor は /login を含む全要求に NCX/... を付けており
    (docs/jp_api.md「ログイン」)、/login の UA で OAuth
    クライアント (test/prod) が決まる疑いがあるため、ログイン用セッションと
    ログイン後の _oauth セッションの両方に APP_USER_AGENT を設定する。
    """
    session = NCISession('JP')
    session._login_jp_bff = MagicMock(return_value='access-token-value')

    login_session = MagicMock()
    login_session.headers = {}
    oauth_session = MagicMock()
    oauth_session.headers = {}

    with patch(
        'custom_components.nissan_connect.kamereon.kamereon.requests.session',
        side_effect=[login_session, oauth_session],
    ):
        session._login_jp('user@example.com', 'password123')

    assert session.session is login_session
    assert session.session.headers['User-Agent'] == APP_USER_AGENT
    assert session._oauth is oauth_session
    assert session._oauth.headers['User-Agent'] == APP_USER_AGENT
    assert session._oauth.headers['Authorization'] == 'Bearer access-token-value'


def _make_session_jp():
    """user-initialize テスト用の JP セッション。oauth はモック、ネットワークは叩かない。"""
    session = NCISession('JP')
    session._oauth = MagicMock()
    session._user_id = 'user-1'
    return session


def _token_info_response(user_id, vehicles, rop_id=None):
    """token-info 応答のダミー。user_id は data.id、rop_id は data.attributes.ropId。

    user-initialize の {userId} には user_id (data.id) を使う。ropId ではない
    (ropId を入れると 401 0101 が返ることが 2026-09-20 に実測で確認された)。
    """
    return _response({
        'data': {
            'id': user_id,
            'attributes': {'ropId': rop_id, 'vehicles': vehicles},
        }
    })


def test_fetch_vehicles_jp_calls_user_initialize_after_details_and_features():
    """token-info -> details -> features の後に user-initialize が POST されること。

    アプリ (GarageUseCaseImpl.login) はこの順で呼ぶ。HA はこれまで user-initialize を
    一度も呼んでおらず、遠隔操作が CANCELLED/Failed になる原因の候補だった。
    {userId} には token-info の data.id を使う (ropId は別属性で 401 0101 の原因)。
    """
    session = _make_session_jp()
    session.oauth.get.return_value = _token_info_response(
        '000000', [{'vin': 'VIN0000000000000', 'gateway': 'AVN', 'services': []}],
        rop_id='rop-999')

    call_order = []

    def fake_details(info):
        call_order.append('details')
        return {'vin': info['vin']}

    def fake_features(info):
        call_order.append('features')
        return {}

    def fake_post(url, data=None, headers=None):
        call_order.append('user-initialize')
        return _response({'data': {'type': 'ncUserInitialization'}}, status_code=200)

    session._fetch_vehicle_details_jp = MagicMock(side_effect=fake_details)
    session._fetch_app_config = MagicMock(side_effect=fake_features)
    session.oauth.post = MagicMock(side_effect=fake_post)

    vehicles = session._fetch_vehicles_jp()

    assert call_order == ['details', 'features', 'user-initialize']
    assert len(vehicles) == 1
    vehicle = vehicles[0]

    url, = session.oauth.post.call_args[0]
    assert url.endswith('nissan/account/v3/users/000000/cars/VIN0000000000000/user-initialize')
    body = json.loads(session.oauth.post.call_args[1]['data'])
    assert body == {'data': {'type': 'ncUserInitialization'}}
    headers = session.oauth.post.call_args[1]['headers']
    assert headers['X-Vehicle-Gateway'] == 'AVN'
    assert headers['Content-Type'] == 'application/vnd.api+json'
    assert vehicle.user_initialize_result == {
        'status_code': 200, 'body': {'data': {'type': 'ncUserInitialization'}}}


def test_fetch_vehicles_jp_survives_user_initialize_exception():
    """user-initialize が例外を投げても、ログイン (車両の取得) 自体は止めない。"""
    session = _make_session_jp()
    session.oauth.get.return_value = _token_info_response(
        '000000', [{'vin': 'VIN0000000000000', 'gateway': 'AVN', 'services': []}])
    session._fetch_vehicle_details_jp = MagicMock(return_value={'vin': 'VIN0000000000000'})
    session._fetch_app_config = MagicMock(return_value={})
    session.oauth.post = MagicMock(side_effect=RuntimeError('boom'))

    vehicles = session._fetch_vehicles_jp()

    assert len(vehicles) == 1
    assert vehicles[0].user_initialize_result == {'error': 'boom'}


def test_fetch_vehicles_jp_survives_user_initialize_error_response():
    """user-initialize が JSON:API のエラー応答でも、車両は返り error が残る。"""
    session = _make_session_jp()
    session.oauth.get.return_value = _token_info_response(
        '000000', [{'vin': 'VIN0000000000000', 'gateway': 'AVN', 'services': []}])
    session._fetch_vehicle_details_jp = MagicMock(return_value={'vin': 'VIN0000000000000'})
    session._fetch_app_config = MagicMock(return_value={})
    session.oauth.post = MagicMock(return_value=_response(
        {'errors': [{'code': '0101', 'detail': 'The token is invalid or missing.'}]},
        status_code=400))

    vehicles = session._fetch_vehicles_jp()

    assert len(vehicles) == 1
    result = vehicles[0].user_initialize_result
    assert result['status_code'] == 400
    assert result['error'] == [{'code': '0101', 'detail': 'The token is invalid or missing.'}]


def test_fetch_vehicles_jp_skips_user_initialize_without_user_id():
    """token-info に data.id が無いときは user-initialize を投げず skipped を残す。"""
    session = _make_session_jp()
    session.oauth.get.return_value = _token_info_response(
        None, [{'vin': 'VIN0000000000000', 'gateway': 'AVN', 'services': []}])
    session._fetch_vehicle_details_jp = MagicMock(return_value={'vin': 'VIN0000000000000'})
    session._fetch_app_config = MagicMock(return_value={})
    session.oauth.post = MagicMock()

    vehicles = session._fetch_vehicles_jp()

    session.oauth.post.assert_not_called()
    assert vehicles[0].user_initialize_result == {'skipped': 'no token-info user id'}


def test_fetch_vehicles_jp_does_not_fall_back_to_rop_id():
    """data.id が無く ropId だけあっても、ropId を {userId} の代わりに使わない。

    ropId を user-initialize の {userId} に入れると 401 0101 (token is invalid or
    missing) が返ることが実車で確認された (2026-09-20)。ropId をフォールバックに
    使わないことを固定する回帰テスト。
    """
    session = _make_session_jp()
    session.oauth.get.return_value = _token_info_response(
        None, [{'vin': 'VIN0000000000000', 'gateway': 'AVN', 'services': []}],
        rop_id='rop-999')
    session._fetch_vehicle_details_jp = MagicMock(return_value={'vin': 'VIN0000000000000'})
    session._fetch_app_config = MagicMock(return_value={})
    session.oauth.post = MagicMock()

    vehicles = session._fetch_vehicles_jp()

    session.oauth.post.assert_not_called()
    assert vehicles[0].user_initialize_result == {'skipped': 'no token-info user id'}


def test_user_initialize_redacts_personal_fields_in_body():
    """応答 body に VIN/uuid/ropId 相当が含まれていたら伏せる (PROBE_REDACT_KEYS)。"""
    session = _make_session_jp()
    session.oauth.post = MagicMock(return_value=_response(
        {'data': {'attributes': {'vin': 'VIN0000000000000', 'ropId': '000000'}}}, status_code=200))

    result = session._initialize_user_jp('000000', 'VIN0000000000000', 'AVN')

    assert result['body']['data']['attributes']['vin'] == '***'
    assert result['body']['data']['attributes']['ropId'] == '***'


def test_fetch_probes_merges_token_claims_into_token_info(monkeypatch):
    """token_info プローブの属性から、ログイン時の token_claims が読めること。"""
    from custom_components.nissan_connect.kamereon import kamereon_jp_const as jpc
    monkeypatch.setattr(jpc, 'PROBE_ENDPOINTS', (
        ('token_info', 'user', 'nissan/account/v1/token-info', None),
    ), raising=False)
    from custom_components.nissan_connect.kamereon import kamereon_jp
    monkeypatch.setattr(kamereon_jp, 'PROBE_ENDPOINTS', jpc.PROBE_ENDPOINTS)

    vehicle = _make_vehicle()
    vehicle.session.settings['notifications_base_url'] = 'https://bff/nc-app-bff/alliance/notifications/'
    vehicle.probe_data = {}
    vehicle.session.token_claims = {
        'access_token': {'aud': 'ncb_jp_client', 'client_id': 'test'},
        'id_token': None,
        'scope': 'openid profile',
        'token_type': 'Bearer',
        'expires_in': 3600,
    }
    vehicle._get = MagicMock(return_value=_response(
        {'data': {'attributes': {'vehicles': []}}}))

    vehicle.fetch_probes()

    result = vehicle.probe_data['token_info']
    assert result['token_claims']['access_token']['client_id'] == 'test'
    assert result['token_claims']['access_token']['aud'] == 'ncb_jp_client'


def test_fetch_probes_merges_user_initialize_into_token_info(monkeypatch):
    """user-initialize の結果 (ログイン直後の紐付け POST) が (未確認) token_info センサーの属性から読めること。"""
    from custom_components.nissan_connect.kamereon import kamereon_jp_const as jpc
    monkeypatch.setattr(jpc, 'PROBE_ENDPOINTS', (
        ('token_info', 'user', 'nissan/account/v1/token-info', None),
    ), raising=False)
    from custom_components.nissan_connect.kamereon import kamereon_jp
    monkeypatch.setattr(kamereon_jp, 'PROBE_ENDPOINTS', jpc.PROBE_ENDPOINTS)

    vehicle = _make_vehicle()
    vehicle.session.settings['notifications_base_url'] = 'https://bff/nc-app-bff/alliance/notifications/'
    vehicle.session.token_claims = None
    vehicle.probe_data = {}
    vehicle.user_initialize_result = {'status_code': 200, 'body': {'data': {'type': 'ncUserInitialization'}}}
    vehicle._get = MagicMock(return_value=_response(
        {'data': {'attributes': {'vehicles': []}}}))

    vehicle.fetch_probes()

    result = vehicle.probe_data['token_info']
    assert result['user_initialize']['status_code'] == 200
    assert result['user_initialize']['body'] == {'data': {'type': 'ncUserInitialization'}}


# ------------------------------------------------------------------
# stop_engine (engine_stop サービスの実体)
# ------------------------------------------------------------------

def test_stop_engine_calls_apply_procedure():
    """stop_engine は ApplyProcedure(ENGINE_STOP) を送る (2026-09-23 実車確認済み)。"""
    vehicle = _make_vehicle([Feature.REMOTE_ENGINE_START])
    vehicle.apply_procedure = MagicMock()
    vehicle.last_remote_action_trace = MagicMock(return_value=None)

    result = vehicle.stop_engine(wait=False)

    vehicle.apply_procedure.assert_called_once_with(PROCEDURE_ENGINE_STOP, wait=False)
    assert result == {'result': 'no-trace'}


def test_stop_engine_summary_has_no_secrets():
    """戻り値の要約には method/result/statuses だけが入り、トークン・VIN・UUID の

    生の request/response は含めない。
    """
    vehicle = _make_vehicle([Feature.REMOTE_ENGINE_START])
    vehicle.apply_procedure = MagicMock()
    dummy_trace = {
        'action': 'ENGINE_STOP',
        'started': '2026-01-01T00:00:00.000+00:00',
        'request': {
            'method': 'POST',
            'url': 'https://front-api/query',
            'headers': {'Authorization': '***'},
            'body': {'variables': {'input': {'contextArgument': {
                'vehicleIdInput': {'vehicleId': DUMMY_UUID}}}}},
        },
        'response': {'status_code': 200, 'body': {'secret': 'token-value'}},
        'polls': [
            {'at': 't1', 'status': 'PENDING', 'error_code': None, 'body': {}, 'exception': None},
            {'at': 't2', 'status': 'COMPLETED', 'error_code': None, 'body': {}, 'exception': None},
        ],
        'result': 'COMPLETED',
        'error': None,
        'finished': '2026-01-01T00:00:05.000+00:00',
        'res_state_before': {'remoteEngineStatus': '12'},
        'res_state_after': {'remoteEngineStatus': '6'},
    }
    vehicle.last_remote_action_trace = MagicMock(return_value=dummy_trace)

    result = vehicle.stop_engine(wait=True)

    assert result == {
        'action': 'ENGINE_STOP',
        'result': 'COMPLETED',
        'error': None,
        'statuses': ['PENDING', 'COMPLETED'],
        'res_state_before': {'remoteEngineStatus': '12'},
        'res_state_after': {'remoteEngineStatus': '6'},
    }
    # 生の request/response (トークン・UUID を含みうる) は含まれない
    assert 'request' not in result
    assert 'response' not in result
    serialized = json.dumps(result)
    assert DUMMY_UUID not in serialized
    assert 'token-value' not in serialized
    assert vehicle.vin not in serialized
