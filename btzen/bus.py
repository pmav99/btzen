#
# BTZen - Bluetooh Smart sensor reading library.
#
# Copyright (C) 2015 by Artur Wroblewski <wrobell@pld-linux.org>
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

import asyncio
import logging
import threading
import time
from collections import namedtuple

from _btzen import ffi, lib

logger = logging.getLogger(__name__)

Parameters = namedtuple('Parameters', [
    'name', 'path_data', 'path_conf', 'path_period', 'config_on',
    'config_on_notify', 'config_off',
])

def _mac(mac):
    return mac.replace(':', '_').upper()

class Bus:
    thread_local = threading.local()
    def __init__(self, loop=None):
        self._loop = asyncio.get_event_loop() if loop is None else loop

        self._fd = lib.sd_bus_get_fd(self.get_bus())
        self._loop.add_reader(self._fd, self._process_event)

        self._sensors = {}
        self._chr_uuid = []
        self._dev_names = {}

    @staticmethod
    def get_bus():
        local = Bus.thread_local
        if not hasattr(local, 'bus'):
            local.bus = SDBus()
        return local.bus.bus

    def connect(self, *args):
        for mac in args:
            path = '/org/bluez/hci0/dev_{}'.format(_mac(mac))
            path = ffi.new('char[]', path.encode())
            r = lib.bt_device_connect(self.get_bus(), path)
            for i in range(10):
                resolved = lib.bt_device_property_bool(
                    self.get_bus(), path, 'ServicesResolved'.encode()
                )
                if resolved == 1:
                    break
                logger.debug(
                    'bluetooth device {} services not resolved, wait 1s...'
                    .format(mac)
                )
                time.sleep(1)
            if i == 9:
                raise ValueError('not resolved')

            name = ffi.new('char**')
            r = lib.bt_device_property_str(self.get_bus(), path, 'Name'.encode(), name)
            self._dev_names[mac] = ffi.string(name[0]).decode()

        items = []
        root = dev_chr = ffi.new('t_bt_device_chr **')
        r = lib.bt_device_chr_list(self.get_bus(), dev_chr)
        while dev_chr != ffi.NULL and dev_chr[0] != ffi.NULL:
            uuid = ffi.string(dev_chr[0].uuid)[:]
            path = ffi.string(dev_chr[0].path)[:]
            dev_chr = dev_chr[0].next
            items.append((path, uuid))
        lib.bt_device_chr_list_free(root[0]);

        self._chr_uuid = items

    def sensor(self, mac, cls, notifying=False):
        assert isinstance(cls.UUID_DATA, str)
        assert isinstance(cls.UUID_CONF, str) or cls.UUID_CONF is None
        assert isinstance(cls.UUID_PERIOD, str) or cls.UUID_PERIOD is None

        params = Parameters(
            self._dev_names[mac],
            self._find_path(mac, cls.UUID_DATA),
            self._find_path(mac, cls.UUID_CONF),
            self._find_path(mac, cls.UUID_PERIOD),
            cls.CONFIG_ON,
            cls.CONFIG_ON_NOTIFY,
            cls.CONFIG_OFF,
        )
        reader = cls(params, self, self._loop, notifying)
        self._sensors[reader._device] = reader
        return reader

    def _process_event(self):
        processed = lib.sd_bus_process(self.get_bus(), ffi.NULL)
        while processed > 0:
            device = lib.bt_device_last()
            if device != ffi.NULL:
                assert device in self._sensors
                sensor = self._sensors[device]
                sensor._process_event()

            processed = lib.sd_bus_process(self.get_bus(), ffi.NULL)

    def _find_path(self, mac, uuid):
        if uuid is None:
            return b''
        mac = _mac(mac).encode()
        uuid = uuid.encode()
        items = (p for p, u in self._chr_uuid if mac in p and uuid == u)
        return next(items, None)


class SDBus:
    """
    Reference to default system bus (sd-bus).
    """
    def __init__(self):
        self.bus_ref = ffi.new('sd_bus **')
        lib.sd_bus_default_system(self.bus_ref)
        self.bus = self.bus_ref[0]

    def __del__(self):
        logger.info('destroy reference to a bus')
        lib.sd_bus_unref(self.bus)

# vim: sw=4:et:ai
