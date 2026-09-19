# MyNISSAN (JP) アプリ 遠隔操作まわりの解析結果

対象: MyNISSAN 3.4.0 (Android, arm64) `libapp.so` の逆アセンブル
方法: blutter の出力 (pp.txt / objs.txt / asm) + capstone による直接逆アセンブル。
関数間の関係は **BL 命令の総当たりスキャン**、値は **オブジェクトプールの定数オペランド** で確認したものだけを「確認済み」とする。
文字列や型名が一致しているだけのものは「未確認」に分類する。

実車での API 実行は行っていない。実車で観測した事実は末尾にまとめる。

---

## 1. 結論（確認済み）

### 1.1 エンジン始動は hvac-control

ダッシュボードのエンジン始動ボタンからの呼び出し連鎖（BL 総当たりで得た唯一の経路）:

```
DashboardViewModel::_executeRemoteHvacStart        (0x1150f94)
 └─BL→ ExecuteHvacControlUseCaseImpl::start        (0x11511cc)
        │  0x1151278: EngineAction "startAndKeepLonger" を比較
        │            → HvacControl "startRes20Mins"(index1) / "start"(index0) を選ぶ
        └─BL→ ExecuteHvacControlUseCaseImpl::_execute (0x114de18)
              ├─BL→ HvacControlExecutorImpl::execute (0x114e0f0)
              │      0x114e1dc: "normalStart" / 0x114e204: "doubleStart" → targetCycleTime
              │      └─BL→ ApiExecutor.execute → HvacControlApi::postHvacControl (0x114ecec)
              │            POST https://nc-app-bff-prod.apps.jp.kamereon.io/nc-app-bff
              │                 /nissan/remote-action/v1/cars/{vin}/hvac-control
              └─BL→ RegisterRemoteActionUseCaseImpl::execute (0x108f020)
                     ローカル DB (drift) へ保存 + 遅延タスク登録 + analytics。ネットワーク呼び出し無し
```

**POST の前に wake-up 等のネットワーク呼び出しは無い。** `wake_up_vehicle_requester.dart` の closure (0xcc6230) が参照されているが、これはプールから 19 箇所参照される重複排除された汎用 closure であり wake-up の呼び出しではない。

### 1.2 hvac-control のボディ

`_$HvacControlRequestImpl.toJson` (0x114e4c8) が出すキー。値が null のキーは省略される（各キーで null チェック → スキップ）。

| キー | 値の出所 | ダッシュボードの始動での値 |
|---|---|---|
| `action` | HvacControl enum index: 2 → `"stop"`、それ以外 → `"start"` | `"start"` |
| `targetTemperature` | 名前付き引数 `targetTemperature` | **渡されない → 省略** |
| `startDateTime` | executor では一切書き込まれない | **省略** |
| `hvacAccessorySetting` | `toHvacAccessorySettingTurnOn` (0x11512c4) → `HvacAccessorySettingExt.toParameterValue` (0x114e990) | `hvacFunctionRequest: 1` + 装備のあるシート/デフロスタ |
| `targetCycleTime` | int: `<=0` → `"normalStart"`、`==1` → `"doubleStart"` | 通常: normalStart / 20分: doubleStart |

`_executeRemoteHvacStart` が `start()` に渡す名前付き引数は `hvacAccessorySetting` と `targetCycleTime` の2つだけ（PP[0x357b8] の引数名リスト）。`targetTemperature` は含まれない。

`hvacAccessorySetting` の取りうるキー（`_$$HvacAccessorySettingRequestImplToJson` 0x114e6a4）:
`hvacFunctionRequest`, `frontLeftSeatControl`, `frontRightSeatControl`, `rearLeftSeatControl`, `rearRightSeatControl`, `rearCenterSeatControl`, `thirdLeftSeatControl`, `thirdRightSeatControl`, `steeringHeaterControl`, `frontDefrostMode`, `rearDefrostMode`
各値は 0/1/2 の整数（3 状態 enum の index を写像）。`hvacFunctionRequest` は bool → 1/0 で、TurnOn では true。

### 1.3 ヘッダ

全 API 共通（117 箇所で同じ組み立て）:

| ヘッダ | 値 |
|---|---|
| `Authorization` | Bearer トークン |
| `X-Vehicle-Gateway` | `VehicleGateway::toName` (0xbdaf64) の戻り値: `"AVN"` / `"NGDC"` / `"MOCK"` / other は名前そのもの |
| `Content-Type` | `application/vnd.api+json` |

`VehicleGateway` は token-info レスポンスの `gateway` キー（`_$TokenInfoVehicleResponseImplFromJson` 0xce42fc で読む）から作られる。**VIN ではない。**

### 1.4 遠隔操作の結果ポーリング

`RemoteActionStatusGetterImpl` → `RemoteActionApi::getRemoteActionStatus` (0x1087708):

```
GET {BFF}/alliance/action-status-polling/v1/cars/{vin}/actions/status?actionId={id}
ヘッダ: Authorization, X-Vehicle-Gateway
```

- `actionId` は POST レスポンスの `data.id`（`EngineStartExecutorImpl` / `HvacControlExecutorImpl` とも `"id"` を取り出す）。
- レスポンスの `status` → `RemoteActionStatus` enum:
  `PRISTINE(0) CREATED(1) PENDING(2) REJECTED(3) CANCELLED(4) COMPLETED(5) SYNCHRONIZED(6) PARTIAL(7) $unknown(8)`
- `REJECTED` / `CANCELLED` はそれぞれ別のオブジェクトを生成し `error.code` を保持する（`toRemoteActionStatus` 0x1086efc）。つまり**アプリでも終端の失敗**扱い。
- 最初のポーリングは POST 完了後 **1 秒**（`Duration` 0xf4240 µs、poller 0x10860f4）。
- 打ち切り時間は `RemoteActionTimeoutResolverImpl` (0x16d45a8) がゲートウェイのクラスで決める:

| VehicleGateway | 打ち切り |
|---|---|
| Other (class 0x1f24) | `"Unsupported gateway: "` エラー |
| Mock (0x1f26) | 400 秒 / 200 秒（feature が engineDoubleStart かどうか） |
| NGdc (0x1f28) | 300 秒 |
| それ以外 (AVN) | 400 秒 / 200 秒（同上） |

- ポーリング間隔（"N seconds later" の N）の出所は**未確定**。

### 1.5 エンジン停止は専用エンドポイント

```
停止確認ダイアログの確定 closure (0x114fa88)   ← _dialogRequestConfirmRemoteEngineStop 内
 └─BL→ DashboardViewModel::_executeRemoteEngine  (0x114faf0)
        0x114fb50: EngineAction "stop" を **定数で** 渡す（引数ではない）
        └─BL→ ExecuteEngineStartUseCaseImpl::execute (0x114fc08)
              └─BL→ EngineStartExecutorImpl::execute  (0x114fd90)
                     body: {"data":{"type":"EngineStart","attributes":{"action":"stop"}}}
                     └─closure (0x11500ec) で API を選択:
                          EngineAction の index == 1 (startAndKeepLonger) →
                            EngineStartInstantDoubleApi (0x11504a8)
                            POST {BFF}/alliance/nissan-connector/v1/cars/{vin}/actions/engine-start
                          それ以外 (start=0 / stop=2) →
                            EngineStartApi (0x115019c)
                            POST {BFF}/alliance/car-adapter/v1/cars/{vin}/actions/engine-start
```

- `_executeRemoteEngine` の BL 呼び出し元は停止ダイアログの 1 箇所だけ。
- `EngineAction.start` / `startAndKeepLonger` を実際に生成しているのは
  `remote_hvac_start_alert_use_case`（確認ダイアログ）、`vehicle_preferences`（前回選択の保存）、
  `execute_hvac_control_use_case::start`（HvacControl への変換）だけで、engine-start 経路には流れない。
- **したがって出荷されているアプリでは `/actions/engine-start` は停止専用。始動には使わない。**
  （実装上は start / startAndKeepLonger の分岐も残っているが、そこへ到達する画面が無い。3.1 参照）
- `EngineStartRequest.toJson` (0x115004c) のキーは `action` のみ。

### 1.6 features（機能可用性マップ）のキー

`/nissan/config/v1/cars/{uuid}/features` を `_$$VehicleConfigResponseImplToJson` (0xcd7098) が扱うトップレベルキー:

`appRatingDialog batteryLevel blueSwitchCard chargeRequiredTime contact docomoIccUrl drivingHistoryDetail dvrAppLink estimatedChargingTime faqLink generalContact headUnit healthStatus hvacTemperature inAppFeedback informationChannel instructionMovieLink irManualLink minimalSocSetting myCarFinder myNissan notificationSettings otherSettings ownersManualLink preWakeUpVehicle remoteAction remoteEngineStart remoteLockPreviousAttentionState routePlanner seatHeater steeringHeat sunRoof vehicleImageSetting vehicleStatus vehicleToLoad vpaLink`

`remoteEngineStart` ブロックのキー（0xcd8798）:
`available carId hvacPreviousAttentionState lastMileNavi memo mil operationTimeSetting page score smartRoutePlanner temperatureCondition type wayPointMaxNumber`

`RemoteActionFeature` enum（アプリ内の機能名）:
`minimumCharge v2lTelematics chargeSchedule hvacRepeatSchedule cancelHvacTimer setHvacTimer updateHvacSettings hvacOff hvacOnRes20mins hvacOn sendNavigation refreshLockStatus refreshLocation refreshHvacStatus refreshBatteryStatus lockDoor engineStop engineDoubleStart engineStart chargingStop chargingStart dataReset speedRestriction curfewRestriction areaRestriction`

`HvacControl` enum → `RemoteActionFeature` の対応（`_execute` 0x114dfa8〜）:
`start → hvacOn`、`startRes20Mins → hvacOnRes20mins`、`stop → hvacOff`、`updateSettings → updateHvacSettings`

---

### 1.7 その他の遠隔操作（同じ方法で確認）

| 操作 | アプリ | 根拠 |
|---|---|---|
| ドア施錠 | `POST {BFF}/nissan/remote-action/v2/cars/{vin}/lock`、`{"data":{"type":"ncRemoteLock","attributes":{}}}`、呼び出し順は hvac-control と同じ（POST → ローカル登録 → ポーリング）。解錠 API は無い | `RemoteLockExecutorImpl::execute` 0x114cc4c、`LockApi` 0x114cfc8、`_executeRemoteLock` 0x114c93c の BL 列 |
| wake-up | `POST {BFF}/alliance/car-adapter/v1/cars/{vin}/actions/wake-up-vehicle`、`{"data":{"type":"WakeUpVehicle"}}`。呼び出し元は `DashboardViewModel::didChangeAppLifecycleState`（アプリ復帰時）と garage のみ | `WakeUpVehicleUseCaseImpl::execute` 0xcc5e9c の呼び出し元、requester 0xcc5f8c |
| refresh-location / lock-status / hvac-status / battery-status | `POST {BFF}/alliance/car-adapter/v1/cars/{vin}/actions/refresh-*`、type は `RefreshLocation` / `RefreshLockStatus` / `RefreshHvacStatus` / `RefreshBatteryStatus` | refresh_*_api.dart の request closure |
| エアコン停止（乗る前エアコン OFF） | hvac-control、`action: "stop"`、他のキー無し | `ExecuteHvacControlUseCaseImpl::stop` 0x114dde0（HvacControl "stop" index 2） |
| ダッシュボード表示時の取得順 | carLocation → cockpit → monthlyDistance → healthStatus → lockStatus → hvacStatus → engineStatus(res-state) → batteryStatus → …（全部 GET。refresh-* の POST は含まない） | `DashboardViewModel::fetch` の closure 0xcee02c の BL 列 |
| 20分始動の出し分け | `features.remoteEngineStart.operationTimeSetting`（bool）が真のときだけ確認ダイアログに選択肢が出る | `EngineStartConfig.isOperationTimeSettingAvailable` 0x114f514（`EngineStartConfig.available(usePreviousHvacState@8, temperatureCondition@0xc, operationTimeSetting@0x10)`）、呼び出し元 `remote_hvac_start_alert_use_case._showConfirmDialog` |
| `ncx_sensitive_strings` | 全リクエスト closure に出てくるが、ヘッダではなくログ伏せ字用の文字列リストをリクエストオプションに付けているだけ | postEngineStart closure 0x11503b0〜 |

## 2. HA 実装との差分

| 項目 | アプリ | HA (現状) | 判定 |
|---|---|---|---|
| 始動エンドポイント | hvac-control | hvac-control | ✅ |
| `action` | `"start"` | `"start"` | ✅ |
| `targetCycleTime` | normalStart / doubleStart | 同じ | ✅ |
| `hvacAccessorySetting.hvacFunctionRequest` | 1 | 1 | ✅ |
| `startDateTime` | 省略 | 省略 | ✅ |
| 呼び出し順 | POST → 1 秒後ポーリング。前に何も呼ばない | 同じ | ✅ |
| CANCELLED の扱い | 失敗 | 失敗 | ✅ |
| `targetTemperature` | 送らない | 送らない（v1.5.0 で修正） | ✅ |
| `X-Vehicle-Gateway` | gateway 名 (AVN/NGDC/…) | token-info の gateway をそのまま送る（v1.5.0 で修正、無ければ VIN） | ✅ |
| ポーリング打ち切り | 200〜400 秒 | 同じ表（v1.5.0 で修正） | ✅ |
| 施錠 | v2/lock, ncRemoteLock, attributes {} | 同じ | ✅ |
| wake-up / refresh-* | 上記 1.7 | 同じ URL・type | ✅ |
| 20分ボタンの出し分け | `remoteEngineStart.operationTimeSetting` | 同じ（v1.5.0 で修正。features が取れないときは従来どおり表示） | ✅ |
| シート/デフロスタのキー | 装備があれば付く | 付けない | 車の features 次第（未確認） |
| エンジン停止 | `/actions/engine-start` + `action:"stop"` | 未実装 | ❌（分岐条件未特定のため未実装） |

### 2.1 v1.5.0 で足したログ取得

- 遠隔操作（hvac_start / hvac_stop / lock）ごとに、リクエスト（URL・ヘッダ・ボディ。Authorization は伏せる）、レスポンス、ポーリングの各レスポンス、結果を `Vehicle.remote_action_log` に残す（直近 20 件）。
- 診断センサー「直近の遠隔操作」: state = `操作 -> 結果`、属性に上記（recorder 上限に収まるよう切り詰め）。
- HA 設定ディレクトリの `nissan_connect_remote_actions.jsonl` に 1 操作 1 行で追記。ログレベルや再起動に関係なく残る。
- 診断センサー「アプリ機能マップ」: features の中身（20分の出し分け根拠の確認用）。
- セットアップ時に `gateway` と `remoteEngineStart` ブロックを INFO で出す。

---

## 3. 未確定・詰まっている点

### 3.1 エンジン停止 API の分岐条件（解決済み）

以前「Vehicle の offset 0x14 を int として 1 と比較していて意味が通らない」と書いた件の答え。
比較対象は `Vehicle` ではなく **`EngineAction` enum** だった。

```
EngineAction  (asm/nml_ncx/domain/engine_start/engine_action.dart)
  index 0 = "start"               pp+0x359a8
  index 1 = "startAndKeepLonger"  pp+0x35820
  index 2 = "stop"                pp+0x35a28
```

closure 0x11500ec の `ldur x3,[x0,#7]` は enum の index（unboxed int64）であって
`Vehicle.uuid` ではない。したがって:

| EngineAction | 投げ先 |
|---|---|
| `startAndKeepLonger` (1) | `EngineStartInstantDoubleApi` → `POST {BFF}/alliance/nissan-connector/v1/cars/{vin}/actions/engine-start` |
| `start` (0) / `stop` (2) | `EngineStartApi` → `POST {BFF}/alliance/car-adapter/v1/cars/{vin}/actions/engine-start` |

そして `ExecuteEngineStartUseCaseImpl::execute` (0x114fc08) は **定数 `EngineAction.stop` しか渡さない**
（0x114fcbc で pp+0x35a28 をロードし、登録する機能名も `RemoteActionFeature.engineStop` = pp+0x2e230）。
`EngineStartExecutorImpl` の start / startAndKeepLonger 側の分岐は、出荷されている画面からは到達しない。

**結論: 停止は常に `car-adapter` の engine-start。nissan-connector 側は使わない。**
`EngineStartRequest.toJson` (0x115004c) のキーは `action` のみ。

### 3.2 ポーリング間隔
- 1 秒後の初回は確定。以後の間隔（"Remote action X is not finished. Will Get its status again N seconds later" の N）の出所は未確定。

### 3.3 20分ボタンの可用性判定のキー
- 解決済み: `features.remoteEngineStart.operationTimeSetting`（1.7 参照）。

### 3.4 実車で観測した事実（解析とは別）

#### 2026-09-19 16:31〜16:33 JST（v1.4 系、デバッグログ OFF）

| 時刻 | 操作 | HA のポーリング結果 | 実車（ユーザー報告） |
|---|---|---|---|
| 16:31:28 | エンジン始動 (通常) | 22 秒後 CANCELLED / error code `Failed` | かかった |
| 16:32:26 | ドア施錠 | 22 秒後 CANCELLED / `Failed` | 施錠された |
| 16:32:54 | エンジン始動 (20分) | 8 秒後 CANCELLED / `Failed` | エラー |

- **この車両では、車が実際に実行した操作にも `CANCELLED` / `Failed` が返る。**
  つまり `CANCELLED` は「失敗」を意味しない。現状の HA は `CANCELLED` を例外にしているので、
  成功した操作まで失敗として扱ってしまう。
- 16:34:52 の `res-state` は `{'remoteEngineStatus': '6'}`（readyForRemoteStart）。

#### 2026-09-19 22:27:30 JST（v1.5.0、アプリと同一形式で送信、ログ取得あり）

送信内容（アプリの `postHvacControl` と同じ）:

```
POST {BFF}/nissan/remote-action/v1/cars/{vin}/hvac-control
Authorization: Bearer ***   X-Vehicle-Gateway: AVN   Content-Type: application/vnd.api+json
{"data":{"type":"HvacControl","attributes":{
  "action":"start","targetCycleTime":"normalStart",
  "hvacAccessorySetting":{"hvacFunctionRequest":1}}}}
```

ポーリング（actionId `5572abde-…`、1 秒間隔）:

| 経過 | status |
|---|---|
| +1 秒 | 404 `No action(s) found for this vehicle.` |
| +2〜10 秒 | `CREATED` |
| +11〜16 秒 | `PENDING` |
| +17 秒 | `CANCELLED` / `error.code: "Failed"` |

記録本体: `actionType: ENGINE_START`, `clientId: "test"`, `realm: n-nissan-nc`, `userId: 437886`。
**エンジンはかからなかった。**

- 16:31 の成功時も同じ `CANCELLED` / `Failed` が返っていたので、
  **このポーリング結果からは成功・失敗を区別できない。** 失敗の原因はここからは読み取れない。
- `clientId: "test"` の出所は未確認。アプリ発の操作記録と突き合わせないと比較できない。
- 車両側の制約（`RemoteEngineErrorStatus.errorDueToDurationBetween2ResCycles` =
  連続リモート始動の間隔制限）が効いている可能性がある。16:31 に一度リモート始動している。
  22:27 時点の `res-state` を取っていないので確認できていない。**未検証の仮説。**

#### この回で追加した調査用プローブ（GET のみ、車両には何も送らない）

| キー | エンドポイント | 目的 |
|---|---|---|
| `token_info` | `{BFF}/nissan/account/v1/token-info` | アクセストークンが何者として扱われているか |
| `action_status_all` | `{BFF}/alliance/action-status-polling/v1/cars/{vin}/actions/status`（actionId なし） | アプリ発の操作記録が返るなら `clientId` を突き合わせる |

結果（2026-09-19 22:58 JST）:

| キー | 返り |
|---|---|
| `token_info` | `{"ropId":"***","vehicles":[{"vin":"***","uuid":"***","role":"OWNER","services":[…],"gateway":"AVN","canGeneration":"C1A"}],"services":null}` |
| `action_status_all` | `0399 unmapped external system error.`（actionId 無しでは一覧は返らない） |

- token-info に `clientId` に相当するものは無い。`clientId: "test"` は BFF / Kamereon 側で
  付けている値で、こちらからは見えない。**この線は追えない。**
- 操作記録の一覧も取れないので、アプリ発の操作との突き合わせもできない。
- 同時刻の `remoteEngineStatus` は `6`（readyForRemoteStart）。
  recorder 上、14:27 JST 以降ずっと 6 のままで、22:27 の失敗時も 6 だったとみられる。
  つまり「連続リモート始動の制限で車が拒否した」説は、この値からは裏づけられない。

### 3.5 認証経路（アプリ側の事実のみ）

```
AuthApi.login          POST {BFF}/nissan/account/v1/login          header X-App-Id、body {data:{type:"token",attributes:{username,password}}}
AuthApi.exchangeToken  POST {BFF}/nissan/account/v1/exchange-token
AuthApi.getTokenInfo   GET  {BFF}/nissan/account/v1/token-info
AuthApi.refreshToken / logout
```

- `AuthType` enum: `ncid`(0) / `nuid`(1)（pp+0x1eeb8 / pp+0x1f0b8、`{ncid:"ncid", nuid:"nuid"}` のマップは pp+0x1ef28）。
- `GarageUseCaseImpl::_resolveAuthType` (0xccc33c) はローカル DB の `CachedAuthType` を読み、
  **無ければ `ncid` を既定にする**（closure 0xccca6c）。
- CIAM 側は flutter_appauth。client id `4db8eab8-8088-4b92-9c14-9bc89fa49c2a`、
  redirect `ncx://ciam.prd/login`（pp+0x2d6f0 / 0x2d6f8）。
- HA の統合は `login`（username/password）を使っている。アプリの ncid 経路と同じ。
  nuid 経路を使っているアカウントかどうかは APK からは決められない。

## 4. 私（AI）の作業上の誤りの記録

再発防止のために残す。

1. hvac-control で始動を実装したとき、自分で作った全エンドポイント一覧（384 行）に `/actions/engine-start` が載っていたのに参照しなかった。
2. その後 `/actions/engine-start` を見つけて「始動はこちら」と言ったが、文字列と型名の一致だけで呼び出し元を辿っていなかった。辿ったら停止専用だった。
3. 「確定」と言った内容が APK 上の読みにすぎず実車では未検証だったことが複数回あった。
4. HA を再起動するとデバッグログのレベルが戻るのを失念し、実車操作のログを取り損ねた。
5. CANCELLED の説明として「ユーザーがすぐエンジンを切ったから」と、指示されてもいない操作順序を根拠にした。撤回済み。

---

## 5. 主要アドレス一覧

| 関数 | アドレス | サイズ |
|---|---|---|
| DashboardViewModel::_executeRemoteHvacStart | 0x1150f94 | 0x1f4 |
| ExecuteHvacControlUseCaseImpl::start | 0x11511cc | 0xf8 |
| ExecuteHvacControlUseCaseImpl::_execute | 0x114de18 | 0x2cc |
| HvacControlExecutorImpl::execute | 0x114e0f0 | 0x390 |
| HvacControlApi::postHvacControl | 0x114ecec | 0x8c |
| _$HvacControlRequestImpl.toJson | 0x114e4c8 | 0x194 |
| _$$HvacAccessorySettingRequestImplToJson | 0x114e6a4 | 0x2e0 |
| HvacAccessorySettingExt.toParameterValue | 0x114e990 | 0x2f0 |
| DashboardViewModel::_executeRemoteEngine | 0x114faf0 | 0xdc |
| ExecuteEngineStartUseCaseImpl::execute | 0x114fc08 | 0x17c |
| EngineStartExecutorImpl::execute | 0x114fd90 | 0x2a4 |
| EngineStartExecutorImpl::execute の API 選択 closure | 0x11500ec | 0xb0 |
| EngineStartApi::postEngineStart | 0x115019c | 0x8c |
| EngineStartInstantDoubleApi::postEngineStartInstantDouble | 0x11504a8 | 0x8c |
| RegisterRemoteActionUseCaseImpl::execute | 0x108f020 | 0x1d0 |
| RemoteActionStatusPollerImpl::pollRemoteActionStatus | 0x1086004 | 0xc70 |
| RemoteActionApi::getRemoteActionStatus | 0x1087708 | — |
| toRemoteActionStatus (status 文字列 → enum) | 0x1086efc | 0x264 |
| RemoteActionTimeoutResolverImpl | 0x16d45a8 | 0x114 |
| VehicleGateway::toName | 0xbdaf64 | — |
| _$$VehicleConfigResponseImplToJson | 0xcd7098 | 0x93c |

作業用スクリプト（コンテナ内 `/tmp`）: `dasm.py`（逆アセンブル+プール解決）、`callers.py`（BL 総当たり）、`poolrefs.py`（プール参照箇所）、`whichfn.py`（アドレス→関数名）、`blseq.py`（関数内の呼び出し列）。
