"""遠隔操作ログの sink (`_remote_action_log_sink`) のテスト。

HA の設定ディレクトリと、統合パッケージ自身のディレクトリ配下 (`tmp/`) の
両方に同じ内容が追記されること、片方が書けなくてももう片方は書けて
例外が呼び出し側に漏れないことを確認する。

背景: HA ホストにシェルが無く、MCP のファイル読み取りが統合ディレクトリ配下
しか許可されていないため、開発中はそちらにもログを複製する運用にしている。
"""
import json
from unittest.mock import MagicMock

import custom_components.nissan_connect as nissan_connect
from custom_components.nissan_connect.kamereon.kamereon_jp_const import (
    REMOTE_ACTION_LOG_FILE,
    REMOTE_ACTION_LOG_LOCAL_DIR,
)


def test_sink_writes_to_both_locations(tmp_path, monkeypatch):
    # 統合ディレクトリを tmp_path に差し替える (__file__ を直接置き換える)
    fake_module_file = tmp_path / "integration" / "__init__.py"
    fake_module_file.parent.mkdir(parents=True)
    monkeypatch.setattr(nissan_connect, "__file__", str(fake_module_file))

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    hass = MagicMock()
    hass.config.path.side_effect = lambda name: str(config_dir / name)

    sink = nissan_connect._remote_action_log_sink(hass)
    trace = {"action": "hvac_start", "result": "posted"}
    sink(trace)

    config_log = config_dir / REMOTE_ACTION_LOG_FILE
    local_log = fake_module_file.parent / REMOTE_ACTION_LOG_LOCAL_DIR / REMOTE_ACTION_LOG_FILE

    assert config_log.exists()
    assert local_log.exists()
    assert json.loads(config_log.read_text(encoding="utf-8").strip()) == trace
    assert json.loads(local_log.read_text(encoding="utf-8").strip()) == trace


def test_sink_survives_failure_on_one_location(tmp_path, monkeypatch):
    """片方の書き込みが失敗しても、もう片方には書けて例外も出ないこと。"""
    fake_module_file = tmp_path / "integration" / "__init__.py"
    fake_module_file.parent.mkdir(parents=True)
    monkeypatch.setattr(nissan_connect, "__file__", str(fake_module_file))

    # 設定ディレクトリ側は存在しないディレクトリを指し、open が失敗するようにする
    missing_dir = tmp_path / "does-not-exist"
    hass = MagicMock()
    hass.config.path.side_effect = lambda name: str(missing_dir / name)

    sink = nissan_connect._remote_action_log_sink(hass)
    trace = {"action": "hvac_stop", "result": "posted"}

    # 例外を投げずに、ローカル側だけ書ければ良い
    sink(trace)

    local_log = fake_module_file.parent / REMOTE_ACTION_LOG_LOCAL_DIR / REMOTE_ACTION_LOG_FILE
    assert local_log.exists()
    assert json.loads(local_log.read_text(encoding="utf-8").strip()) == trace
    assert not (missing_dir / REMOTE_ACTION_LOG_FILE).exists()
