# Python / Home Assistant 統合 コーディング規約

## 基本

- Python 3.11 系で動作すること。Home Assistant 2023.11 以降で使えない API（新しすぎる `homeassistant.*` の型・ヘルパー）に依存しない。
- 4 スペースインデント、PEP 8 準拠。1 行は 120 桁を目安にする。
- 公開クラス・関数には docstring を付ける。既存ファイルの言語（日本語 / 英語）に合わせる。
- 新規・変更する関数には型ヒントを付ける。`Any` は JSON 由来の値を受ける箇所に限定し、取り出した直後に具体的な型へ落とす。
- import は標準 → サードパーティ → `homeassistant` → 相対 import の順。未使用 import を残さない。
- 例外を握りつぶさない。`except Exception:` で拾う場合は必ず `_LOGGER.warning` 以上で記録し、なぜ握りつぶすかコメントを書く。`except BaseException:` は新規に書かない。

## ログ・秘匿情報

- `print` 禁止。必ずモジュール先頭の `_LOGGER = logging.getLogger(__name__)` を使う。
- `_LOGGER.debug("... %s", value)` の遅延フォーマット形式を使う（f-string をログに渡さない）。
- **メールアドレス・パスワード・アクセストークン・リフレッシュトークン・VIN・UUID・位置情報をログに出さない。** レスポンスをまるごとログに出す場合は `kamereon_jp_const.py` の `PROBE_REDACT_KEYS` によるマスク処理を通す。
- テストデータの VIN・ID はダミー値（例: `VIN0000000000000`）を使い、実車の値を入れない。

## Home Assistant 層（`custom_components/nissan_connect/*.py`）

- エンティティのセットアップ・状態更新は `async def`。イベントループ内でブロッキング I/O（`requests`、`time.sleep`）を直接呼ばない。`kamereon` パッケージの同期メソッドは `hass.async_add_executor_job(...)` 経由で呼ぶ。
- データ取得は `coordinator.py` の `DataUpdateCoordinator` に集約し、エンティティ側で API を直接叩かない。
- エンティティは `base.py` の共通基底クラスを継承し、`unique_id` / `device_info` の付け方を既存に揃える。
- 車両が機能を持たない場合はエンティティを生成しない（`Feature` の有無で判定）。エンティティを追加したら README の Entities 一覧も更新する。
- 定数・マジックナンバーは `const.py` に置く。ユーザーに見える文言はハードコードせず `translations/en.json` と `ja.json` の**両方**に追加する（他言語は英語のままで可）。
- セットアップ失敗時の例外は使い分ける。一時的な障害は `ConfigEntryNotReady`（HA が自動リトライ）、資格情報の拒否は `ConfigEntryAuthFailed`（再認証フロー）。
- `manifest.json` の `requirements` を増やすときは `requirements.txt` にも同じものを書く。

## API クライアント層（`custom_components/nissan_connect/kamereon/`）

- この統合は JP 専用。共通処理は `kamereon.py`、**JP 固有の処理は `kamereon_jp.py`（Mixin）に閉じる**。車種ごとの機能差は `Feature` の有無で判定する（`session.region` による分岐は使わない）。
- 定数・Enum は `kamereon_const.py`（共通）/ `kamereon_jp_const.py`（JP）に置く。エンドポイントの文字列・状態値・タイムアウト秒数をメソッド内に直書きしない。
- JP の API 仕様（エンドポイント・パラメータ・状態値）を変えるときは `docs/jp_api.md` の記載を根拠にし、根拠の節（例: 「遠隔操作」「結果ポーリング」）をコメントかコミット説明に書く。未確認の推測でコードを変える場合はコメントで「未確認」と明記する。
- 遠隔操作（remote-action）は「投げる → 結果をポーリングする」の 2 段構え。タイムアウトと失敗ステータスの扱いは既存の `REMOTE_ACTION_*` 定数に従う。
- `kamereon.py` ↔ `kamereon_jp.py` の循環 import は関数内の遅延 import で回避する（既存の書き方に合わせる）。

## テスト（`tests/`）

- 外部サービス（日産 BFF / KAuth）へ実際に接続しない。`requests` / OAuth セッションは `MagicMock` でモックする。
- `kamereon` 単体のテストは `Vehicle.__new__(Vehicle)` などで HA に依存せず組み立てる（`test_kamereon_jp.py` のヘルパーを参考にする）。
- HA 層のテストは `pytest_homeassistant_custom_component` の `hass` フィクスチャを使う。`conftest.py` で `enable_custom_integrations` が自動有効化されている。
- 不具合修正には必ず回帰テストを付ける。
