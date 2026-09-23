# プロジェクト共通ルール

## 禁止事項（絶対厳守 / 最優先）

プロジェクト共通の開発ルールを以下の取り込みファイルに定義しています（Claude Code は `@path` 記法でファイル内容を自動インポートします）。

@.claude/instructions/common.instructions.md

特に以下は再掲する。

- **ユーザーの明示的な指示なしに `git commit` / `git push` / ブランチ作成・切替 / `reset` / `stash` などの履歴・作業ツリーを変える git 操作を行わないこと。** `git status` / `git diff` / `git log` は可。
- 実装・修正・削除の前に、変更対象ファイル・変更概要・影響範囲を提示して承認を得ること。

## プロジェクト概要

Home Assistant のカスタム統合 **NissanConnect [JP]**（HACS 配布）。
日産 NissanConnect (MyNISSAN アプリ) の JP 版 API から車両情報を取得し、遠隔操作 (ロック・エンジン始動など) を行う。
[dan-r/HomeAssistant-NissanConnect](https://github.com/dan-r/HomeAssistant-NissanConnect) を JP 向けに改造したもの。

| パス | 役割 |
|---|---|
| `custom_components/nissan_connect/` | 統合本体。`__init__.py` / `coordinator.py` / 各プラットフォーム (`sensor.py` `button.py` `climate.py` など) |
| `custom_components/nissan_connect/kamereon/kamereon.py` | JP 専用の API クライアント共通処理 (`KamereonSession` / `Vehicle`) |
| `custom_components/nissan_connect/kamereon/kamereon_jp.py` | **JP 固有**の取得・遠隔操作 (Mixin)。JP でしか使わない処理はここに閉じる |
| `custom_components/nissan_connect/kamereon/*_const.py` | 定数・Enum。マジックナンバーや文字列はここに置く |
| `custom_components/nissan_connect/translations/` | 翻訳。`en.json` と `ja.json` を必ず両方更新する |
| `tests/` | pytest (`pytest-homeassistant-custom-component`) |
| `docs/jp_api.md` | この統合が実際に呼ぶ JP API のリファレンス |

## Python ファイル編集時の必須ルール

`.py` ファイルを編集する前に、必ず以下を読み込み・遵守すること。

@.claude/instructions/python.instructions.md

## このプロジェクト固有の注意

- **API 仕様の根拠は `docs/jp_api.md` にある。** JP の API の挙動 (エンドポイント・パラメータ・状態値) を変えるときは同ファイルを根拠にし、文字列や型名が一致しただけのものを「確認済み」と書かない。実車で観測した事実と、コードから読んだ推測は区別し、未確認のものは未確認と明記する。
- **資格情報・トークン・VIN・位置情報をログに出さない。** デバッグ出力は `kamereon_jp_const.py` の `PROBE_REDACT_KEYS` によるマスクを通す。
- **日産側の障害と自コードのバグを混同しない。** BFF のログインが 500 を返す等の事象は過去に日産側の一時障害だった経緯がある。
- この統合は **JP 専用**（EU リージョンは削除済み）。車種ごとの機能差は `session.region` ではなく `Feature` の有無で判定する。EV（リーフ / アリア / サクラ等）向けのバッテリー・充電関連のエンティティ・処理は JP でも使うため削除しないこと。
- 対応 HA の下限は `hacs.json` の `homeassistant` (2023.11.0)。それより新しい API に依存しない。

## サブエージェント

- コード実装（計画確定後の実装作業）は [`sonnet-implementer`](.claude/agents/sonnet-implementer.md) サブエージェントを利用すること。

## 編集後の確認コマンド

コード変更後は必ず以下を実行し、エラーがないことを確認すること（再掲・絶対厳守）。
**テストがすべてパスすることまで確認する**こと。

```bash
# 初回のみ (Python 3.11 系。.tool-versions 参照。Windows では python の代わりに py -3.11)
python -m venv .venv
.venv/Scripts/activate        # Windows (bash なら source .venv/Scripts/activate)
pip install -r requirements.test.txt

# 変更のたびに
python -m pytest tests
```

CI (`.github/workflows/main.yml`) では上記 pytest に加えて hassfest と HACS の検証が走る。
`manifest.json` / `hacs.json` / `translations/` を変えたときは、それらの形式要件にも注意すること。
