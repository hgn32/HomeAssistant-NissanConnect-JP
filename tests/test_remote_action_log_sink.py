"""遠隔操作ログの sink (`_remote_action_log_sink`) のテスト。

HA の設定ディレクトリ直下に JSON Lines として追記されること、書き込みに
失敗しても例外が呼び出し側に漏れないことを確認する。
"""
import json
from unittest.mock import MagicMock

import custom_components.nissan_connect as nissan_connect
from custom_components.nissan_connect.kamereon.kamereon_jp_const import REMOTE_ACTION_LOG_FILE


def test_sink_writes_to_config_dir(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    hass = MagicMock()
    hass.config.path.side_effect = lambda name: str(config_dir / name)

    sink = nissan_connect._remote_action_log_sink(hass)
    trace = {"action": "hvac_start", "result": "posted"}
    sink(trace)

    config_log = config_dir / REMOTE_ACTION_LOG_FILE
    assert config_log.exists()
    assert json.loads(config_log.read_text(encoding="utf-8").strip()) == trace


def test_sink_survives_write_failure():
    """書き込みに失敗しても例外を呼び出し側に漏らさないこと。"""
    hass = MagicMock()
    # 存在しないディレクトリを指し、open が失敗するようにする
    hass.config.path.side_effect = lambda name: "/nonexistent-dir/{}".format(name)

    sink = nissan_connect._remote_action_log_sink(hass)
    trace = {"action": "hvac_stop", "result": "posted"}

    # 例外を投げないこと
    sink(trace)
