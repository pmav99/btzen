#
# BTZen - Bluetooth Smart sensor reading library.
#
# Copyright (C) 2015-2018 by Artur Wroblewski <wrobell@riseup.net>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#

"""
Generic access to Bluetooth devices.

See also

    https://www.bluetooth.com/specifications/gatt/characteristics
"""

import logging
import typing as tp
from dataclasses import dataclass
from functools import partial

from . import _btzen
from .bus import Bus

logger = logging.getLogger(__name__)

# function to convert 16-bit UUID to full 128-bit Bluetooth normative UUID
# string
to_uuid = '0000{:04x}-0000-1000-8000-00805f9b34fb'.format

@dataclass(frozen=True)
class Info:
    """
    Bluetooth device information.

    :var service: UUID of Bluetooth service.
    """
    service: str

@dataclass(frozen=True)
class InfoInterface(Info):
    """
    Bluetooth device information for device providing data via Bluez
    interface property.

    Example of interface for Battery Level Bluetooth characteristics is
    `org.bluez.Battery1` Bluez interface, which provides data via
    `Percentage` property.

    :var interface: Bluez interface name.
    :var property: Property name of the interface.
    :var type: Type of property value.
    """
    interface: str
    property: str
    type: str

@dataclass(frozen=True)
class InfoCharacteristic(Info):
    """
    Bluetooth device information for device providing data via Bluetooth
    characteristic.

    :var uuid_data: UUID of characteristic to read data from.
    :var size: Length of data received from Bluetooth characteristic on
        read.
    """
    uuid_data: str
    size: int

@dataclass(frozen=True)
class InfoEnvSensing(InfoCharacteristic):
    """
    Bluetooth device information for device providing data via Bluetooth
    Environmental Sensing characteristic.

    :var uuid_conf: UUID of characteristic to write and read device
        configuration.
    :var uuid_trigger: UUID of characteristic to write and read device
        trigger data.
    :var config_on: Default configuration of device to switch device on.
    :var config_off: Default configuration of device to switch device off.
    :var config_notify_on: Default configuration of device to switch device
        on in notifying mode. If none, then `config_on` is used.
    """
    uuid_conf: tp.Optional[str] = None
    uuid_trigger: tp.Optional[str] = None
    config_on: tp.Optional[bytes] = None
    config_off: tp.Optional[bytes] = None
    config_on_notify: tp.Optional[bytes] = None

class Device:
    """
    Base class for all Bluetooth devices.

    :var mac: Address of Bluetooth device.
    :var notifying: Indicates if data is read in notifying mode.
    """
    def __init__(self, mac, *, notifying=False):
        self.mac = mac
        self.notifying = notifying

        self._bus = Bus.get_bus()
        self._cm = None
        self._task = None
        self._read_data = None

        # FIXME: remove after api change stabilized
        self._mac = mac
        self._enable = self.enable
        self._interval = 0


    async def enable(self):
        """
        Enable Bluetooth device.

        If a Bluetooth device is connected or reconnected, the enable
        method shall be called to configure/reconfigure the device.
        """
        raise NotImplementedError('Enable method is not implemented')

    async def read(self):
        """
        Read data from Bluetooth device.

        If device is in notifying mode, the data might be returned after
        long period of time.
        """
        await self._cm.connected(self.mac)
        self._task = self._read_data()
        data = await self._task
        self._task = None
        return self.get_value(data)

    def get_value(self, data):
        """
        Convert data returned by Bluetooth device into value.

        By default no conversion is performed.
        """
        return data

    def close(self):
        """
        Disable device and stop reading its data.

        Pending, asynchronous coroutines are closed.
        """
        task = self._task
        if task is not None:
            task.close()
            self._task = None

        logger.info('device {} closed'.format(self))

    def __repr__(self):
        return '{}/{}'.format(self.mac, self.__class__.__name__)

class DeviceInterface(Device):
    """
    Bluetooth device reading data from Bluez interface property.
    """
    info: InfoInterface

    def __init__(self, mac, *, notifying=False):
        super().__init__(mac, notifying=notifying)

        self._params = {
            'mac': self.mac,
            'name': self.info.property,
            'iface': self.info.interface,
        }

        read_notify = partial(self._bus._dev_property_get, **self._params)
        read = partial(self._bus._property, **self._params, type=self.info.type)
        self._read_data = read_notify if notifying else read

    async def enable(self):
        if self.notifying:
            self._bus._dev_property_start(**self._params)

    def close(self):
        if self.notifying:
            self._bus._dev_property_stop(**self._params)
        super().close()

class DeviceCharacteristic(Device):
    """
    Bluetooth device reading data from Bluetooth characteristics.
    """
    info: InfoCharacteristic

    def __init__(self, mac, notifying=False):
        super().__init__(mac, notifying=notifying)

        self._path_data = None

    async def enable(self):
        logger.info('enabling device: {}'.format(self))
        notify = self.notifying

        self._path_data = path = self._get_path(self.info.uuid_data)

        read = partial(_btzen.bt_read, self._bus.system_bus, path)
        read_notify = partial(self._bus._gatt_get, path)
        self._read_data = read_notify if notify else read

        await self._pre_notify_start()

        if notify:
            self._bus._gatt_start(path)

        await self._post_notify_start()

        logger.info('enabled device: {}'.format(self))

    def close(self):
        if self.notifying:
            try:
                self._bus._gatt_stop(self._path_data)
            except Exception as ex:
                logger.warning('cannot stop notifications: {}'.format(ex))

        super().close()

    async def _pre_notify_start(self):
        return None

    async def _post_notify_start(self):
        return None

    def _get_path(self, uuid):
        return self._bus.sensor_path(self.mac, uuid)

class DeviceEnvSensing(DeviceCharacteristic):
    """
    Bluetooth device providing data via Bluetooth Environmental Sensing
    characteristic.
    """
    info: InfoEnvSensing

    def __init__(self, mac, notifying=False):
        super().__init__(mac, notifying=notifying)

        self._path_conf = None
        self._path_trigger = None
        self._data_trigger = None

    def set_trigger(self, data: bytes):
        self._data_trigger = data

    def close(self):
        super().close()

        info = self.info
        system_bus = self._bus.system_bus
        if info.config_off:
            try:
                _btzen.bt_write_sync(system_bus, self._path_conf, info.config_off)
            except Exception as ex:
                logger.warning('cannot switch device off: {}'.format(ex))


    async def _pre_notify_start(self):
        info = self.info

        self._path_conf = self._get_path(info.uuid_conf)
        print(info.uuid_conf)
        self._path_trigger = self._get_path(info.uuid_trigger)

        config_on = info.config_on_notify if self.notifying else info.config_on

        if config_on:
            if __debug__:
                logger.debug(
                    'writing {} configuration data to {}'
                    .format(config_on, self._path_conf)
                )
            await self._write(self._path_conf, config_on)

    async def _post_notify_start(self):
        path = self._path_trigger
        data = self._data_trigger
        if path is not None and data is not None:
            await self._write(path, data)
        else:
            logger.warning('setting trigger for {} not supported'.format(self))

    async def _write(self, path: str, data: bytes) -> None:
        await _btzen.bt_write(self._bus.system_bus, path, data)


class BatteryLevel(DeviceInterface):
    """
    The current charge level of a Bluetooth device battery.
    """
    info = InfoInterface(
        to_uuid(0x180f),
        'org.bluez.Battery1',
        'Percentage',
        'y'
    )
    UUID_SERVICE = info.service  # FIXME: remove

# vim: sw=4:et:ai