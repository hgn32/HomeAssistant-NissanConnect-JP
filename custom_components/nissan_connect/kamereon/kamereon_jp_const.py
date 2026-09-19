"""JP (NissanConnect / MyNISSAN アプリ) 固有の定数。

値はすべて MyNISSAN 3.4.0 (arm64) の逆アセンブルから取っている。
"""
import enum


# JP アプリが自分を名乗るときの値。
# nissan/config/v1/cars/{vin}/features のクエリに付く
APP_VERSION = '3.4.0'
APP_OS = 'Android'
APP_OS_VERSION = '14'
APP_DEVICE_INFO = 'HomeAssistant'


class EngineCycleTime(enum.Enum):
    """JP: hvac-control の targetCycleTime。

    アプリの EngineAction.start / startAndKeepLonger に対応する。
    doubleStart は RemoteActionFeature.hvacOnRes20mins、つまり 2 サイクル (20 分) 運転。
    """
    NORMAL = 'normalStart'
    DOUBLE = 'doubleStart'


class RemoteEngineStatus(enum.Enum):
    """JP: res-state / hvac-status が返す remoteEngineStatus。

    値はアプリの engine_status_response.dart の分岐と同じ。
    """
    UNAVAILABLE = 'unavailable'
    REMOTE_START_NOT_ALLOWED = 'remoteStartNotAllowed'
    READY_FOR_REMOTE_START = 'readyForRemoteStart'
    MANUALLY_STARTED = 'manuallyStarted'
    REMOTELY_STARTED = 'remotelyStarted'
    REMOTE_START_NOT_SUPPORTED = 'remoteStartNotSupported'
    UNKNOWN = 'unknown'


REMOTE_ENGINE_STATUS_MAP = {
    '0': RemoteEngineStatus.UNAVAILABLE,
    '3': RemoteEngineStatus.REMOTE_START_NOT_ALLOWED,
    '6': RemoteEngineStatus.READY_FOR_REMOTE_START,
    '9': RemoteEngineStatus.MANUALLY_STARTED,
    '12': RemoteEngineStatus.REMOTELY_STARTED,
    '15': RemoteEngineStatus.REMOTE_START_NOT_SUPPORTED,
}


class RemoteEngineErrorStatus(enum.Enum):
    """JP: res-state が返す remoteEngineErrorStatus。"""
    NO_ERROR = 'noError'
    REMOTE_ENGINE_START_NOT_ACTIVATED = 'remoteEngineStartNotActivated'
    ERROR_DUE_TO_DURATION_BETWEEN_2_RES_CYCLES = 'errorDueToDurationBetween2ResCycles'
    TCU_ERROR = 'tcuError'
    UNKNOWN = 'unknown'


class RemoteActionStatus(enum.Enum):
    """JP: action-status-polling が返す status。"""
    PRISTINE = 'PRISTINE'
    CREATED = 'CREATED'
    PENDING = 'PENDING'
    PARTIAL = 'PARTIAL'
    REJECTED = 'REJECTED'
    CANCELLED = 'CANCELLED'
    COMPLETED = 'COMPLETED'
    SYNCHRONIZED = 'SYNCHRONIZED'


# 完了 / 失敗 / それ以外 (継続) の三分類
REMOTE_ACTION_SUCCESS = (RemoteActionStatus.COMPLETED, RemoteActionStatus.SYNCHRONIZED)
REMOTE_ACTION_FAILURE = (RemoteActionStatus.REJECTED, RemoteActionStatus.CANCELLED)

# アプリは POST の 1 秒後に最初のポーリングを行い、以後 1 秒間隔で繰り返す。
# タイムアウトはアプリでは CDN の app-config.json 由来で取得できなかったので
# こちらで決め打ちする
REMOTE_ACTION_POLL_INTERVAL = 1
REMOTE_ACTION_POLL_TIMEOUT = 60


# アプリには存在するが、実機のレスポンスをまだ確認していないエンドポイント。
# 読み取りだけ行い、中身をそのまま「(未確認)」センサーとして出す。
# 確認が取れたものから個別のセンサーに昇格させる。
#   (キー, ベースURL種別, パステンプレート)
#   ベースURL種別: 'user' = user_base_url, 'car' = car_adapter_base_url,
#                  'notif' = notifications_base_url
PROBE_ENDPOINTS = (
    # --- 通知・アラート設定 ---
    ('area_restrictions', 'user',
     'nissan/notification-setting/v1/cars/{vin}/area-restrictions'),
    ('speed_restrictions', 'user',
     'nissan/notification-setting/v1/cars/{vin}/speed-restrictions'),
    ('curfew_restrictions', 'user',
     'nissan/notification-setting/v2/cars/{vin}/curfew-restrictions'),
    ('notification_activate_status', 'user',
     'nissan/notification-setting/v1/users/{user}/cars/{vin}/activate-status'),
    ('notifications', 'notif',
     'v1/notifications/users/{user}/vehicles/{vin}'),
    ('gfc_restrictions', 'car',
     'v1/cars/{vin}/settings/gfc-restrictions'),
    # --- 乗る前エアコン ---
    ('hvac_settings', 'user',
     'nissan/remote-action/v1/cars/{vin}/hvac-settings'),
    ('hvac_schedule', 'car',
     'v2/cars/{vin}/actions/hvac-schedule'),
    # --- エコ / 運転スコア ---
    ('eco_daily_driving_score', 'car', 'v2/cars/{vin}/eco/daily-driving-score'),
    ('eco_columns', 'car', 'v1/cars/{vin}/eco/columns'),
    ('eco_top_local_ranking', 'car', 'v1/cars/{vin}/eco/top-local-ranking'),
    ('eco_local_ranking_history', 'car', 'v1/cars/{vin}/eco/local-ranking-history'),
    # --- 契約・車両情報 ---
    ('contract', 'user', 'nissan/account/v1/cars/{vin}/contract'),
    ('entitlements', 'user', 'nissan/account/v1/cars/{vin}/entitlements'),
    ('profile', 'user', 'nissan/account/v1/cars/{vin}/profile'),
    ('role_info', 'user', 'nissan/account/v1/cars/{vin}/role-info'),
    ('campaign_info', 'user',
     'nissan/account/v1/users/{user}/cars/{vin}/campaign-info'),
    ('vehicle_reminder_settings', 'car', 'v1/cars/{vin}/VehicleReminderSettings'),
    # --- お知らせ・入庫 ---
    ('announcement_list', 'user', 'nissan/account/v1/cars/{vin}/announcement/list'),
    ('announcement_unread', 'user',
     'nissan/account/v1/cars/{vin}/announcement/unread-categories'),
    ('nyuko_stream', 'user', 'nissan/account/v1/cars/{vin}/nyuko/stream'),
)

# HA の state は 255 文字まで
PROBE_STATE_MAX = 250
