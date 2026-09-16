"""Support for Nissan car door locks."""
import logging
import asyncio

from homeassistant.components.lock import LockEntity

from .base import KamereonEntity
from .kamereon import LockStatus, Feature
from .const import DOMAIN, DATA_VEHICLES, DATA_COORDINATOR_POLL

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config, async_add_entities):
    account_id = config.data['email']

    data = hass.data[DOMAIN][account_id][DATA_VEHICLES]
    coordinator = hass.data[DOMAIN][account_id][DATA_COORDINATOR_POLL]

    entities = []

    for vehicle in data:
        if Feature.APP_DOOR_LOCKING in data[vehicle].features:
            entities.append(CarDoorLock(coordinator, data[vehicle]))

    async_add_entities(entities, update_before_add=True)


class CarDoorLock(KamereonEntity, LockEntity):
    _attr_translation_key = "door_lock"

    def __init__(self, coordinator, vehicle):
        KamereonEntity.__init__(self, coordinator, vehicle)

    @property
    def icon(self):
        """Return the icon."""
        if self.is_locked:
            return 'mdi:lock'
        return 'mdi:lock-open'

    @property
    def is_locked(self):
        """Return true if lock is locked."""
        if self.vehicle.lock_status is None:
            return None
        return self.vehicle.lock_status == LockStatus.LOCKED

    async def async_lock(self, **kwargs):
        """Lock the car."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.vehicle.lock)
        await self.coordinator.async_refresh()

    async def async_unlock(self, **kwargs):
        """Unlock the car."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.vehicle.unlock)
        await self.coordinator.async_refresh()
