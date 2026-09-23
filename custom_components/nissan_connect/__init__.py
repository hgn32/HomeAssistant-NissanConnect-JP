import json
import logging
from datetime import timedelta

from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from .kamereon import NCISession
from .kamereon.kamereon_jp_const import REMOTE_ACTION_LOG_FILE
from .coordinator import KamereonFetchCoordinator, KamereonPollCoordinator, StatisticsCoordinator
from .const import *

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass, config) -> bool:
    return True


def _remote_action_log_sink(hass):
    """遠隔操作の記録を JSON Lines として HA の設定ディレクトリ直下に追記する。

    ログレベルや再起動に関係なく残す。遠隔操作は executor スレッドで動くので
    同期のファイル書き込みでよい。失敗しても遠隔操作自体は止めない。
    """
    remote_path = hass.config.path(REMOTE_ACTION_LOG_FILE)

    def sink(trace):
        line = json.dumps(trace, ensure_ascii=False, default=str) + '\n'
        try:
            with open(remote_path, 'a', encoding='utf-8') as handle:
                handle.write(line)
        except OSError as err:
            _LOGGER.warning("Could not write remote-action log to %s: %s", remote_path, err)

    return sink


async def async_update_listener(hass, entry):
    """Handle options flow credentials update."""
    config = entry.data
    account_id = config['email']

    # Loop each vehicle and update its session with the new credentials
    for vehicle in hass.data[DOMAIN][account_id][DATA_VEHICLES]:
        await hass.async_add_executor_job(hass.data[DOMAIN][account_id][DATA_VEHICLES][vehicle].session.login,
                                            config.get("email"),
                                            config.get("password")
                                            )

    # Update intervals for coordinators
    hass.data[DOMAIN][account_id][DATA_COORDINATOR_STATISTICS].update_interval = timedelta(minutes=config.get("interval_statistics", DEFAULT_INTERVAL_STATISTICS))
    hass.data[DOMAIN][account_id][DATA_COORDINATOR_FETCH].update_interval = timedelta(minutes=config.get("interval_fetch", DEFAULT_INTERVAL_FETCH))
    
    # Refresh fetch coordinator
    await hass.data[DOMAIN][account_id][DATA_COORDINATOR_FETCH].async_refresh()


async def async_setup_entry(hass, entry):
    """This is called from the config flow."""
    account_id = entry.data['email']

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault(account_id, {})

    config = dict(entry.data)

    kamereon_session = NCISession(
        region=config["region"],
        unique_id=entry.unique_id
    )

    data = hass.data[DOMAIN][account_id] = {
        DATA_VEHICLES: {}
    }

    _LOGGER.info("Logging in to service")
    try:
        await hass.async_add_executor_job(kamereon_session.login,
                                          config.get("email"),
                                          config.get("password")
                                          )
    except Exception as err:
        # 資格情報が拒否された場合のみ再認証を促し、それ以外
        # (サービス側の障害など) は Home Assistant に再試行させる
        if "Invalid credentials" in str(err):
            raise ConfigEntryAuthFailed(str(err)) from err
        raise ConfigEntryNotReady(
            "NissanConnect login failed: {}".format(err)) from err

    _LOGGER.debug("Finding vehicles")
    for vehicle in await hass.async_add_executor_job(kamereon_session.fetch_vehicles):
        if config["region"] == 'JP':
            vehicle.action_log_sink = _remote_action_log_sink(hass)
        await hass.async_add_executor_job(vehicle.fetch_all)
        if vehicle.vin not in data[DATA_VEHICLES]:
            data[DATA_VEHICLES][vehicle.vin] = vehicle

    coordinator = data[DATA_COORDINATOR_FETCH] = KamereonFetchCoordinator(hass, config)
    poll_coordinator = data[DATA_COORDINATOR_POLL] = KamereonPollCoordinator(hass, config)
    stats_coordinator = data[DATA_COORDINATOR_STATISTICS] = StatisticsCoordinator(
        hass, config)

    _LOGGER.debug("Initialising entities")
    await hass.config_entries.async_forward_entry_setups(entry, ENTITY_TYPES)

    # Init fetch and state coordinators
    await coordinator.async_config_entry_first_refresh()
    await stats_coordinator.async_config_entry_first_refresh()

    # Init poll coordinator and ensure it runs
    entry.async_on_unload(
            poll_coordinator.async_add_listener(
                lambda *args: None, None
            )
    )
    await poll_coordinator.async_config_entry_first_refresh()

    entry.async_on_unload(entry.add_update_listener(async_update_listener))

    return True


async def async_unload_entry(hass, entry):
    """Unload a config entry."""

    return await hass.config_entries.async_unload_platforms(entry, ENTITY_TYPES)


async def async_migrate_entry(hass, config_entry) -> bool:
    """Migrate old entry."""
    # Version number has gone backwards
    if CONFIG_VERSION < config_entry.version:
        _LOGGER.error(
            "Backwards migration not possible. Please update the integration.")
        return False

    # Version number has gone up
    if config_entry.version < CONFIG_VERSION:
        _LOGGER.debug("Migrating from version %s", config_entry.version)
        new_data = config_entry.data

        config_entry.version = CONFIG_VERSION
        hass.config_entries.async_update_entry(config_entry, data=new_data)

        _LOGGER.debug("Migration to version %s successful",
                      config_entry.version)

    return True
