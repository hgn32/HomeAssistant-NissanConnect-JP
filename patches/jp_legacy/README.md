# JP版(フラット構成)向け ログイン修正パッチ

`/config/custom_components/nissan_connect/` に入っている **JP独自改造版**
(kamereon.py が単一ファイルの旧構成、`nissan/account/v1/login` を使うもの)
向けの差し替えファイル。本リポジトリの main (kamereon/ パッケージ構成) とは
別系統なので、混同しないよう patches/ 配下に置いている。

## 背景 (2026-09-15 調査)

日産JPのBFF `POST https://nc-app-bff-prod.apps.jp.kamereon.io/nc-app-bff/nissan/account/v1/login`
が、どの資格情報を送っても 10 秒でupstreamタイムアウトし
`{"code":"0204","detail":"Internal server error(KAuth)"}` (HTTP 500) を返す状態になった。

* username/password を空にすると 0.65 秒で 400 (必須項目エラー) → エンドポイント自体は生存
* KAuth 本体 (`https://prod.jp.auth.kamereon.org/kauth/.../authenticate`) は正常
  (誤った資格情報なら 1.2 秒で 401 Authentication Failed)
* データ側 (`nissan/account/v1/token-info`, `nissan/config/v1/cars/{vin}/details`,
  `alliance/car-adapter/...`) は 401 (要トークン) で生存

つまり壊れているのは **BFF → KAuth のログイン中継のみ**。

## 変更点

### kamereon.py
* `login()` を2段構えに変更
  1. 従来の BFF ログイン (`_login_via_bff`) — 日産側が復旧すれば従来どおり
  2. 失敗したら KAuth (ForgeRock) 直接ログイン (`_login_via_kauth`)
     authenticate → authorize(code) → access_token
* `settings_map['nissan']['JP']` に KAuth 用の設定を追加
  (auth_base_url / realm / client_id / client_secret / redirect_uri / scope)
* トークン交換のクライアント認証方式が不明なため
  client_secret_post → client_secret_basic → なし の順に試し、結果を debug ログに出す

### __init__.py
* ログイン失敗時に `ConfigEntryNotReady` を投げ、HAに自動リトライさせる
  (従来は例外がそのまま出て「セットアップに失敗しました」で停止した)
* 資格情報そのものが拒否された場合のみ `ConfigEntryAuthFailed` (再認証フロー)

## 適用方法

```bash
cp kamereon.py __init__.py /config/custom_components/nissan_connect/
```
のあとHAを再起動。うまくいかない場合は `configuration.yaml` に

```yaml
logger:
  logs:
    custom_components.nissan_connect: debug
```

を入れて再起動し、`KAuth token request failed:` の行を確認する。

## 未検証の点

* JP の client_secret が不明。上記設定に入れてあるのは EU 版の値で、
  こちらからの試験では `client_secret_post` で `invalid_client` だった。
  そのため basic / なし も順に試す実装にしている。
* KAuth が発行したアクセストークンを JP の BFF がそのまま受け付けるかは、
  正規の資格情報でしか確認できない。

## 追記 (2026-09-16)

* BFF の `nissan/account/v1/login` は復旧した。誤った資格情報に対して
  約1秒で `400 {"code":"0205","detail":"User authentication error"}` を返す
  （障害中は10秒待って `500 / 0204 Internal server error(KAuth)`）。
  つまり 9/15 夜の件は日産側の一時障害だった。
* 同じ KAuth フォールバックは `custom_components/nissan_connect/`
  （パッケージ構成の本体）側にも移植済み。こちらのフラット版は参考用。
