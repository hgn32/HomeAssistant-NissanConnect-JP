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
                          Vehicle の offset 0x14 の値 == 1 →
                            EngineStartInstantDoubleApi (0x11504a8)
                            POST {BFF}/alliance/nissan-connector/v1/cars/{vin}/actions/engine-start
                          それ以外 →
                            EngineStartApi (0x115019c)
                            POST {BFF}/alliance/car-adapter/v1/cars/{vin}/actions/engine-start
```

- `_executeRemoteEngine` の BL 呼び出し元は停止ダイアログの 1 箇所だけ。
- `EngineAction.start` / `startAndKeepLonger` のプール参照先は `remote_hvac_start_alert_use_case.dart` と `vehicle_preferences.dart` のみで、engine-start 経路には出てこない。
- **したがって `/actions/engine-start` は停止専用。始動には使わない。**
- `EngineStartRequest.toJson` (0x115007c) のキーは `action` のみ。

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

### 3.1 エンジン停止 API の分岐条件
- 分岐は closure 0x11500ec の `cmp w0, #2`（smi タグ付き 2 = 値 1）。
- 比較対象は `S.field@0x14` のオブジェクトの +8 にある int64。
- `S` = `ExecuteEngineStartUseCaseImpl.field@0x14`（= `selectedSessionProvider` の値、null チェックあり）`.field@0xc`。
- `Session` は `(user@0x8, vehicle@0xc, ccsGeneration@0x10)`、size 0x14（toString / == で確認）。よって `S` = `Session.vehicle`。
- `Vehicle` の toString によるレイアウトは `(overview@8, config@0xc, primeMover@0x10, uuid@0x14, modelName@0x18, modelCode@0x1c, modelYear@0x20, displayName@0x24, displayModelName@0x28, displayGradeName@0x2c, nickname@0x30, thumbnailUrl@0x34)`。
- **`S.field@0x14` = `uuid` になり、文字列を int64 として読んで 1 と比較することになる。意味が通らない。**
  - 私のトレースに誤りがあるか、`Vehicle.uuid` が文字列ではない型か、`Session.vehicle` が別の `Vehicle` クラスか、のいずれか。判別できていない。
- 参考: `primeMover` enum は `ice(0)`, `electricMotor(1)`。値 1 で nissan-connector に振り分けるなら意味は通るが、offset が 0x10 で一致しない。**推測にすぎないので採用しない。**
- 分岐条件が決まるまで停止は実装しない（間違った系統に POST するリスク）。

### 3.2 ポーリング間隔
- 1 秒後の初回は確定。以後の間隔（"Remote action X is not finished. Will Get its status again N seconds later" の N）の出所は未確定。

### 3.3 20分ボタンの可用性判定のキー
- 解決済み: `features.remoteEngineStart.operationTimeSetting`（1.7 参照）。

### 3.4 実車で観測した事実（解析とは別）
2026-09-19 16:31〜16:33 JST に HA から操作したときの結果:

| 時刻 | 操作 | HA のポーリング結果 | 実車（ユーザー報告） |
|---|---|---|---|
| 16:31:28 | エンジン始動 (通常) | 22 秒後 CANCELLED / error code `Failed` | かかった |
| 16:32:26 | ドア施錠 | 22 秒後 CANCELLED / `Failed` | 施錠された |
| 16:32:54 | エンジン始動 (20分) | 8 秒後 CANCELLED / `Failed` | エラー |

- この 3 回はデバッグログが OFF の時間帯で、ポーリングの生レスポンスは残っていない。
- 16:34:52 に `res-state` を読むと `{'remoteEngineStatus': '6'}`（readyForRemoteStart）。`cycleRemainingTime` 無し。
- HA の履歴では `sensor.note_remote_engine_status` は 16:25 以降 6 のまま変化記録なし（ポーリング間隔内で取れていない）。
- **成功した操作にも CANCELLED が返る原因は分かっていない。** 候補として `X-Vehicle-Gateway` が VIN であること（アプリと不一致）があるが、未検証。

---

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
