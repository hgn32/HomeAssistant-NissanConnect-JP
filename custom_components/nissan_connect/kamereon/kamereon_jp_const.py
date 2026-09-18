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
