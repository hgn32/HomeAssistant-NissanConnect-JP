# NissanConnect JP: この統合が使う API

この統合が実際に呼ぶエンドポイントだけを列挙する。使っていない API、過去の調査経緯、
別バージョンのアプリの解析結果は載せない。

- ベース URL は `kamereon_const.py` の `SETTINGS_MAP['nissan']['JP']`。
  - `{BFF}` = `https://nc-app-bff-prod.apps.jp.kamereon.io/nc-app-bff/`
  - `{FrontAPI}` = `https://ngx-front-api-market-prod.apps.jp.kamereon.io/`
- `{carId}` は token-info が返す識別子（例 `E13-063484`）。`{uuid}` は車両 UUID。
  **details と features だけが uuid を使い、`X-VehicleIdType: UUID` を付ける。**
- JP の全リクエストに `Authorization: Bearer` と `X-Vehicle-Gateway`（token-info の
  gateway 値）が付く。**GraphQL だけは例外で、どちらのヘッダも付けない。**

## ログイン

| メソッド | パス | 用途 |
|---|---|---|
| POST | `{BFF}nissan/account/v1/login` | ID/パスワードでログイン。ヘッダ `X-App-Id: jp.co.nissan.nissanconnect.ncx`、`Content-Type: application/vnd.api+json`。body は `{"data":{"type":"token","attributes":{"username","password"}}}` |
| GET | `{BFF}nissan/account/v1/token-info` | `data.id`（userId）と車両一覧（vin / uuid / gateway）を取る |
| POST | `{BFF}nissan/account/v3/users/{userId}/cars/{carId}/user-initialize` | ユーザーと車のセッション紐付け。body は `{"data":{"type":"ncUserInitialization"}}` |
| GET | `{BFF}nissan/vehicle-info/v1/cars/{uuid}/details` | 車名・型式 |
| GET | `{BFF}nissan/config/v1/cars/{uuid}/features` | 機能可用性マップ |
| GET | `{BFF}nissan/account/v1/cars/{carId}/contract` | 契約プラン・契約終了日 |

## 定期取得（`fetch_all`、アプリのダッシュボードと同じ順序）

| メソッド | パス | 用途 |
|---|---|---|
| POST | `{BFF}alliance/car-adapter/v1/cars/{carId}/actions/wake-up-vehicle` | TCU を起こす |
| GET | `{BFF}nissan/vehicle-info/v1/cars/{carId}/location` | 位置 |
| GET | `{BFF}alliance/car-adapter/v1/cars/{carId}/cockpit` | 走行距離・燃料 |
| GET | `{BFF}alliance/car-adapter/v1/cars/{carId}/health-status` | 警告灯 |
| GET | `{BFF}nissan/remote-action/v1/cars/{carId}/lock-status` | 施錠状態 |
| GET | `{BFF}nissan/remote-action/v1/cars/{carId}/hvac-status` | エアコン状態 |
| GET | `{BFF}alliance/car-adapter/v2/cars/{carId}/res-state` | `remoteEngineStatus`（6=始動可 / 12=遠隔始動中） |
| GET | `{BFF}alliance/car-adapter/v1/cars/{carId}/trip-history` | 日次・月次の走行統計 |

## 遠隔操作

アプリ 3.5.0 は遠隔操作を GraphQL で送る（2026-09-21 に mitmproxy で実測、HA からの
実車始動も成功）。**旧来の REST（hvac-control）は始動には使わない。**

**POST `{FrontAPI}query`** / `Content-Type: application/json` / `User-Agent: Dart/3.9 (dart:io)`
/ `x-service-version: 3.5.0` / `production-variant: jp` / `time-zone: GMT` / `accept-language: ja`
/ `traceparent`（クライアント生成）。**X-Vehicle-Gateway と X-App-Id は付けない。**

```json
{"operationName":"ApplyProcedure",
 "variables":{"input":{
   "procedure":"ENGINE_START",
   "revision":"123",
   "userArgument":{"engineStartOption":{"option":"ONCE"}},
   "contextArgument":{"vehicleIdInput":{"vehicleId":"<uuid>"}},
   "signature":"dummy_signauture"}},
 "query":"mutation ApplyProcedure($input: ProcedureInput!) { apply(input: $input) { callbackKey __typename } __typename }"}
```

- `procedure`: `ENGINE_START`（`userArgument` あり）/ `LOCK`（なし）/ `ENGINE_STOP`（**未確認**）
- `signature` はアプリ自身がこの綴りのダミー固定。`revision` も固定値。
- `vehicleId` は **uuid**。carId ではない。
- クエリは `callbackKey` だけ要求する最小形でサーバが受理する（実測）。
- 応答の `data.apply.callbackKey` は base64。デコードすると
  `ENGINE_START:{"Tasks":[{"ID":"<actionId>",...}],"Revision":"123"}` で、
  **`Tasks[0].ID` が actionId**。

### 結果ポーリング

**GET `{BFF}alliance/action-status-polling/v1/cars/{carId}/actions/status?actionId=<actionId>`**

1 秒間隔。`data.attributes.status` が `CREATED → PENDING → COMPLETED / CANCELLED`。
`actionId` は必須で、省略すると 0399 が返る。直後は 404 になることがあるので継続する。
`clientId` は常に `test` になるが、アプリの成功操作でも同じ値で、失敗要因ではない。

### エンジン停止（旧経路・フォールバック用）

**POST `{BFF}alliance/car-adapter/v1/cars/{carId}/actions/engine-start`**
body `{"data":{"type":"EngineStart","attributes":{"action":"stop"}}}`

停止ボタンは GraphQL の `ENGINE_STOP` を先に試し、失敗したらこちらに落とす。
`ENGINE_STOP` という procedure 名は**未確認**（アプリに停止 UI が無くキャプチャできていない）。

## 使っていないもの

`kamereon.py` には EU 共通の API（充電・SRP・通知設定など）が多数あるが、JP からは
呼ばれない。EU の動作を壊さないために残してあるだけで、JP の仕様ではない。
