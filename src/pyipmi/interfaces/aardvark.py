#
# Kontron Aardvark Interface
#
# author: Michael Walle <michael.walle@kontron.com>
#

import time
import array

from pyipmi import Session
from pyipmi.errors import TimeoutError
from pyipmi.logger import log

import pyipmi.ext.totalphase.aardvark as api

class ChecksumError(Exception):
    pass

class TinyAardvarkWrapper:
    BUFFER_SIZE = 65535

    ERR_UNABLE_TO_LOAD_LIBRARY = -1
    ERR_UNABLE_TO_LOAD_DRIVER = -2
    ERR_UNABLE_TO_LOAD_FUNCTION = -3
    ERR_INCOMPATIBLE_LIBRARY = -4
    ERR_INCOMPATIBLE_DEVICE = -5
    ERR_COMMUNICATION_ERROR = -6
    ERR_UNABLE_TO_OPEN = -7
    ERR_UNABLE_TO_CLOSE = -8
    ERR_INVALID_HANDLE = -9
    ERR_CONFIG_ERROR = -10
    ERR_I2C_NOT_AVAILABLE = -100
    ERR_I2C_NOT_ENABLED = -101
    ERR_I2C_READ_ERROR = -102
    ERR_I2C_WRITE_ERROR = -103
    ERR_I2C_SLAVE_BAD_CONFIG = -104
    ERR_I2C_SLAVE_READ_ERROR = -105
    ERR_I2C_SLAVE_TIMEOUT = -106
    ERR_I2C_DROPPED_EXCESS_BYTES = -107
    ERR_I2C_BUS_ALREADY_FREE = -108
    ERR_SPI_NOT_AVAILABLE = -200
    ERR_SPI_NOT_ENABLED = -201
    ERR_SPI_WRITE_ERROR = -202
    ERR_SPI_SLAVE_READ_ERROR = -203
    ERR_SPI_SLAVE_TIMEOUT = -204
    ERR_SPI_DROPPED_EXCESS_BYTES = -205

    def _error_to_string(self, err):
        for attr in dir(self):
            if attr.startswith('ERR_'):
                if getattr(self, attr) == err:
                    return attr

    def __init__(self):
        self._handle = None

    def open(self, port):
        handle = api.py_aa_open(port)
        if handle <= 0:
            raise IOError('aardvark device on port %d not found')

        self._handle = handle

    def close(self):
        api.py_aa_close(self._handle)
        self._dev = None

    CONFIG_GPIO_ONLY = 0x00
    CONFIG_SPI_GPIO = 0x01
    CONFIG_GPIO_I2C = 0x02
    CONFIG_SPI_I2C = 0x03
    CONFIG_QUERY = 0x80

    def configure(self, config):
        ret = api.py_aa_configure(self._handle, config)
        if ret < 0:
            raise IOError(self._error_to_string(ret))
        
    def i2c_bitrate(self, khz):
        ret = api.py_aa_i2c_bitrate(self._handle, khz)
        if ret < 0:
            raise IOError(self._error_to_string(ret))
    
    def i2c_slave_enable(self, slave_address):
        ret = api.py_aa_i2c_slave_enable(self._handle, slave_address,
                self.BUFFER_SIZE, self.BUFFER_SIZE)
        if ret < 0:
            raise IOError(self._error_to_string(ret))

    I2C_PULLUP_NONE = 0x00
    I2C_PULLUP_BOTH = 0x03
    I2C_PULLUP_QUERY = 0x80
    def i2c_enable_pullups(self, enabled):
        if enabled:
            ret = api.py_aa_i2c_pullup(self._handle, self.I2C_PULLUP_BOTH)
        else:
            ret = api.py_aa_i2c_pullup(self._handle, self.I2C_PULLUP_NONE)
        if ret < 0:
            raise IOError(self._error_to_string(ret))


    TARGET_POWER_NONE = 0x00
    TARGET_POWER_BOTH = 0x03
    TARGET_POWER_QUERY = 0x80
    def enable_target_power(self, enabled):
        if enabled:
            power = self.TARGET_POWER_BOTH
        else:
            power = self.TARGET_POWER_NONE
        ret = api.py_aa_target_power(self._handle, power)
        if ret < 0:
            raise IOError(self._error_to_string(ret))

    I2C_NO_FLAGS = 0x00
    def i2c_write(self, slave_address, data):
        data = array.array('B', data)
        ret = api.py_aa_i2c_write(self._handle, slave_address,
                self.I2C_NO_FLAGS, len(data), data)
        if ret < 0:
            raise IOError(self._error_to_string(ret))

    POLL_NO_DATA = 0x00
    POLL_I2C_READ = 0x01
    POLL_I2C_WRITE = 0x02
    POLL_SPI = 0x04
    POLL_I2C_MONITOR = 0x08
    def poll(self, timeout_ms):
        ret = api.py_aa_async_poll(self._handle, timeout_ms)
        if ret < 0:
            raise IOError(self._error_to_string(ret))
        if ret == self.POLL_NO_DATA:
            raise TimeoutError()
        return ret

    def i2c_slave_read(self):
        data = array.array('B', (0,) * self.BUFFER_SIZE)
        (ret, slave_addr) = api.py_aa_i2c_slave_read(self._handle, self.BUFFER_SIZE,
                data)
        if ret < 0:
            raise IOError(self._error_to_string(ret))
        del data[ret:]
        return (slave_addr, data)

class Aardvark:
    NAME = 'aardvark'

    def __init__(self):
        self.slave_address = 0x20
        self.timeout = 0.1 # 100 ms
        self.next_sequence_number = 0
        self._dev = TinyAardvarkWrapper()
        self._dev.open(0)
        self._dev.configure(TinyAardvarkWrapper.CONFIG_SPI_I2C)
        self._dev.i2c_enable_pullups(False)
        self._dev.i2c_slave_enable(self.slave_address >> 1)
        self._dev.i2c_bitrate(100)

    def enable_pullups(self, enabled):
        self._dev.i2c_enable_pullups(enabled)

    def enable_target_power(self, enabled):
        self._dev.enable_target_power(enabled)

    def raw_write(self, address, data):
        self._dev.i2c_write(address, data)

    def establish_session(self, session):
        # just remember session parameters here
        self._session = session

    def _csum(self, data):
        csum = 0
        for b in data:
            csum += b
        return -csum % 256

    def _encode_ipmb_msg(self):
        pass

    def _decode_ipmb_msg(self, addr, data):
        # shift i2c address by one
        addr = addr << 1

        # verify checksums
        if self._csum((addr, data[0], data[1])) != 0:
            raise ChecksumError()

        if self._csum(data[2:]) != 0:
            raise ChecksumError()

        # decode fields
        rs_sa = addr
        netfn = (data[0] >> 2) & 0x3f
        lun = data[0] & 0x3
        rq_sa = data[2]
        rq_seq = data[3]
        cmd_id = data[4]
        cmd_data = data[5:-1]
        return (rs_sa, netfn, lun, rq_sa, rq_seq, cmd_id, cmd_data)

    def send_and_receive(self, target, msg):
        """Sends an IPMI request message and waits for its response.

        `msg` is a IPMI Message containing both the request and response.
        """

        log().debug('IPMI Request [%s]', msg.req)

        req = array.array('B')
        req.append(msg.NETFN << 2 | msg.LUN)
        req.append(self._csum((target.target_address, req[0])))
        req.append(self.slave_address)
        req.append(self.next_sequence_number)
        req.append(msg.CMDID)
        req.extend(msg.req.encode())
        req.append(self._csum(req[2:]))

        log().debug('IPMB Req to 0x%02x [%s]'
                % (target.target_address, ' '.join(['%02x' % b for b in req])))
        self._dev.i2c_write(target.target_address >> 1, req.tostring())

        rsp_received = False
        start_time = time.time()
        while time.time() < start_time + self.timeout:
            self._dev.poll(100)
            (addr, data) = self._dev.i2c_slave_read()

            (rs_sa, netfn, lun, rq_sa, rq_seq, cmd_id, cmd_data) = \
                    self._decode_ipmb_msg(addr, data)

            if (rs_sa == self.slave_address
                    and netfn == msg.NETFN + 1
                    and lun == msg.LUN
                    and rq_sa == target.target_address
                    and rq_seq == self.next_sequence_number
                    and cmd_id == msg.CMDID):
                rsp_received = True
                log().debug('IPMB Rsp to 0x%02x [%s]'
                        % (rs_sa, ' '.join(['%02x' % b for b in data])))
                break

        if not rsp_received:
            raise TimeoutError()

        self.next_sequence_number += 1

        msg.rsp.decode(cmd_data.tostring())
        log().debug('IPMI Response [%s])', msg.rsp)
 
if __name__ == '__main__':
    import pyipmi.msgs.bmc
    import logging

    handler = logging.StreamHandler()
    handler.setLevel(logging.DEBUG)
    pyipmi.logger.add_log_handler(handler)
    pyipmi.logger.set_log_level(logging.DEBUG)

    m = pyipmi.msgs.bmc.GetDeviceId()
    a = Aardvark()
    #a.enable_pullups(False)
    a.send_and_receive(0x72, m)
    print m.rsp
