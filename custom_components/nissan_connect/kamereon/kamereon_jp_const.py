"""JP (NissanConnect / MyNISSAN アプリ) 固有の定数。

値はすべて MyNISSAN 3.4.0 (arm64) の逆アセンブルから取っている。
"""
import enum


# JP アプリが自分を名乗るときの値。
# nissan/config/v1/cars/{uuid}/features のクエリに付く（実装は uuid で通らなければ vin も試す）
APP_VERSION = '3.4.0'
APP_OS = 'Android'
APP_OS_VERSION = '14'
APP_DEVICE_INFO = 'HomeAssistant'

# アプリの UserAgentHolderImpl.resolve (0x105b938) が組み立てる形
# "NCX" + "/" + version + " (" + [platform, platformVersion, modelName, locale].join("; ") + ")"
# (docs/jp_api.md「ログイン」)。modelName は実機の機種名だが、ここでは
# 自己申告として APP_DEVICE_INFO を入れる。
APP_USER_AGENT = 'NCX/{}'.format(APP_VERSION) + ' ({}; {}; {}; ja_JP)'.format(APP_OS, APP_OS_VERSION, APP_DEVICE_INFO)


class EngineCycleTime(enum.Enum):
    """JP: hvac-control の targetCycleTime。

    アプリの EngineAction.start / startAndKeepLonger に対応する。
    アプリの表記は normalStart が「10分間作動」、doubleStart が「20分間作動」
    (RemoteActionFeature.hvacOnRes20mins、「連続起動は最長 20 分までです」)。
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


# 完了 / 失敗 / 結果不明 (終端だが成否不明) / それ以外 (継続) の四分類
REMOTE_ACTION_SUCCESS = (RemoteActionStatus.COMPLETED, RemoteActionStatus.SYNCHRONIZED)
REMOTE_ACTION_FAILURE = (RemoteActionStatus.REJECTED,)

# CANCELLED は終端ステータス (アプリも終端扱い。docs/jp_api.md「結果ポーリング」)。
# ただし CANCELLED が「実車で実行されなかった」ことを意味するかは実車で未確認
# (成功例がまだ 1 件も無い)。判定に使えるかどうか分かるまでは失敗扱いにせず、
# ポーリングは打ち切るが結果不明として呼び出し側に status をそのまま返す。
# 実車の結果が出たら、失敗扱いに戻すかどうかを決め直す。
REMOTE_ACTION_INDETERMINATE = (RemoteActionStatus.CANCELLED,)

# アプリは POST の 1 秒後に最初のポーリングを行う (RegisterRemoteActionUseCase が
# Duration 1 秒で遅延タスクを登録する)。2 回目以降の間隔はバイナリから取れていないので
# 1 秒のままにしてある。
REMOTE_ACTION_POLL_INTERVAL = 1

# 打ち切り秒数は RemoteActionTimeoutResolverImpl (0x16d45a8) がゲートウェイで決める:
#   NGDC            → 300 秒
#   AVN / MOCK      → engineDoubleStart (20分始動) なら 400 秒、それ以外は 200 秒
#   Other           → アプリでは "Unsupported gateway" エラー。こちらは 200 秒に倒す
REMOTE_ACTION_TIMEOUT_NGDC = 300
REMOTE_ACTION_TIMEOUT_DOUBLE_START = 400
REMOTE_ACTION_TIMEOUT_DEFAULT = 200
GATEWAY_NGDC = 'NGDC'

# 遠隔操作のリクエスト/レスポンス/ポーリングをそのまま残しておく件数と、
# HA の設定ディレクトリに追記する JSON Lines のファイル名
REMOTE_ACTION_LOG_MAX = 20
REMOTE_ACTION_LOG_FILE = 'nissan_connect_remote_actions.jsonl'

# HA ホストにシェルが無く、MCP のファイル読み取りは統合ディレクトリ配下しか許可されて
# いないため、遠隔操作ログは HA の設定ディレクトリに加えてこのディレクトリ (統合パッケージ
# 自身の下) にも複製する。開発者専用リポジトリでの運用 (このリポジトリは配布物ではない)。
REMOTE_ACTION_LOG_LOCAL_DIR = 'tmp'

# 20分始動 (doubleStart) をアプリが出す条件:
#   features の remoteEngineStart.operationTimeSetting
#   (EngineStartConfig.isOperationTimeSettingAvailable 0x114f514 → 確認ダイアログの選択肢)
FEATURE_OPERATION_TIME_SETTING = ('remoteEngineStart', 'operationTimeSetting')


# アプリには存在するが、実機のレスポンスをまだ確認していないエンドポイント。
# 読み取りだけ行い、中身をそのまま「(未確認)」センサーとして出す。
# 確認が取れたものから個別のセンサーに昇格させる。
#   (キー, ベースURL種別, パステンプレート, クエリ)
#   ベースURL種別: 'user' = user_base_url, 'car' = car_adapter_base_url,
#                  'notif' = notifications_base_url
#   クエリの値は {today} / {month} で当日・当月に置き換わる
PROBE_ENDPOINTS = (
    ('contract', 'user', 'nissan/account/v1/cars/{vin}/contract', None),
    # --- この車両の遠隔操作の記録。actionId を付けないと何が返るかを見る。
    #     アプリ発の操作が一緒に返るなら clientId を突き合わせられる ---
)

# 取得は続けるが (未確認) センサーには出さないプローブ。
# contract → 契約プラン/終了日 の元データ (_promote_probe_values)。
# 正規のセンサーで値が見えるので、生の応答を別に表示する意味はない。
PROBE_INTERNAL_KEYS = frozenset({'contract'})

# プローブの結果に混ざる個人情報。センサーの属性にもログにも出したくないので伏せる。
# contract が氏名・電話番号・会員IDを返してくるのを実機で確認済み。
PROBE_REDACT_KEYS = frozenset({
    'userName', 'userName1', 'userName2', 'phoneNum', 'phoneNumber', 'tel',
    'ncId', 'nuId', 'ropId', 'mailAddress', 'email', 'address', 'zipCode',
    'vin', 'uuid', 'registrationNumber',
})
PROBE_REDACTED = '***'

# JWT の claims のうち診断に出してよいもの。個人・車両を特定する値 (sub, email, name, vin, uuid,
# ropId, userId など) は含めない。ここに無いキーは値を出さずキー名だけ残す。
# ログイン応答 (nissan/account/v1/login) の access_token / id_token がどのクライアント向けに
# 発行されたかを、車を動かさずに確認するための診断用 (実測で clientId "test" が遠隔操作の記録に
# 付くことの切り分け)。
TOKEN_CLAIMS_ALLOWLIST = ('iss', 'aud', 'azp', 'client_id', 'clientId', 'scope', 'scp', 'realm',
                          'token_type', 'typ', 'grant_type', 'auth_level', 'authLevel', 'acr', 'amr',
                          'exp', 'iat', 'nbf', 'auditTrackingId', 'tokenName', 'subname', 'cnf')

# アプリはログイン直後 (token-info → details → features の後) にこれを POST する
# (GarageUseCaseImpl.login 0x118f130 → UserInitializerImpl 0xcc4590 → UserInitializeApi 0xcc47ac)。
# HA はこれを呼んでおらず、遠隔操作が CANCELLED/Failed になる原因の候補。
USER_INITIALIZE_TYPE = 'ncUserInitialization'


# ------------------------------------------------------------------
# front-api-market の GraphQL ApplyProcedure (アプリ 3.5.0 の遠隔操作経路)
# 根拠: docs/jp_api.md「遠隔操作」(mitmproxy 実測、2026-09-20)。
# アプリ 3.5.0 は remote-action(hvac-control) / v2/.../lock ではなく、front-api-market の
# GraphQL ミューテーション ApplyProcedure でエンジン始動・施錠を送っている。
# ------------------------------------------------------------------

# front_api_base_url からの相対パス
FRONT_API_QUERY_PATH = 'query'

# アプリ (Flutter/Dart) の HTTP クライアントが送る User-Agent。実測値
GRAPHQL_USER_AGENT = 'Dart/3.9 (dart:io)'

# ApplyProcedure に付くヘッダ (実測、非機微のみ)。X-App-Id / X-Vehicle-Gateway は付かない
GRAPHQL_HEADERS = {
    'content-type': 'application/json',
    'user-agent': GRAPHQL_USER_AGENT,
    'x-service-version': '3.5.0',
    'production-variant': 'jp',
    'time-zone': 'GMT',
    'accept': '*/*',
    'accept-language': 'ja',
    'accept-encoding': 'gzip',
}

# ApplyProcedure の procedure 種別。ENGINE_START / LOCK は実測済み。
# JP アプリに遠隔解錠の UI/API が無いため UNLOCK は未実装 (docs/jp_api.md「遠隔操作」参照)
PROCEDURE_ENGINE_START = 'ENGINE_START'
PROCEDURE_LOCK = 'LOCK'

# エンジン停止の procedure 名。未確認: ENGINE_START からの類推。アプリに停止 UI が無く
# mitmproxy でキャプチャできていない (docs/jp_api.md「遠隔操作」)
PROCEDURE_ENGINE_STOP = 'ENGINE_STOP'  # 未確認

# エンジン停止 (legacy) の car-adapter エンドポイントに送るボディ。
# 根拠: docs/jp_api.md「エンジン停止（旧経路・フォールバック用）」。
# POST {car_adapter_base_url}v1/cars/{vin}/actions/engine-start
# ボディ {"data": {"type": "EngineStart", "attributes": {"action": "stop"}}}
ENGINE_STOP_LEGACY_TYPE = 'EngineStart'
ENGINE_STOP_LEGACY_ACTION = 'stop'

# stop_engine が受け付ける方式。graphql = 3.5.0 相当 (ENGINE_STOP、未確認)、
# legacy = 3.4.0 解析で確定した car-adapter engine-start action=stop
ENGINE_STOP_METHOD_LEGACY = 'legacy'
ENGINE_STOP_METHOD_DEFAULT = 'graphql'

# revision / signature はアプリ自身が固定で送る値。"dummy_signauture" は typo に見えるが
# アプリのバイナリに同じ綴りで埋め込まれている値であり、こちらの誤記ではない
PROCEDURE_REVISION = '123'
PROCEDURE_SIGNATURE = 'dummy_signauture'

# ENGINE_START の userArgument。ONCE = 1 サイクル (アプリの「ワンショット始動」)
ENGINE_START_USER_ARGUMENT = {'engineStartOption': {'option': 'ONCE'}}

# callbackKey だけ取れればよいので、最小形のミューテーションを使う
# (全文は applyprocedure_query.graphql を参照。フォールバック用の参考)
APPLY_PROCEDURE_QUERY = (
    'mutation ApplyProcedure($input: ProcedureInput!) '
    '{ apply(input: $input) { callbackKey __typename } __typename }'
)
