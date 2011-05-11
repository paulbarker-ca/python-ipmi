#
# pyimpi
#
# author: Heiko Thiery <heiko.thiery@kontron.com>
# author: Michael Walle <michael.walle@kontron.com>
#
import math
import errors
import array
import time
from pyipmi.errors import DecodingError, CompletionCodeError
from pyipmi.utils import check_completion_code, pop_unsigned_int
import pyipmi.msgs.sdr

SDR_TYPE_FULL_SENSOR_RECORD = 0x01
SDR_TYPE_COMPACT_SENSOR_RECORD = 0x02
SDR_TYPE_ENTITY_ASSOCIATION_RECORD = 0x08
SDR_TYPE_FRU_DEVICE_LOCATOR_RECORD = 0x11
SDR_TYPE_MANAGEMENT_CONTROLLER_DEVICE_LOCATOR_RECORD = 0x12
SDR_TYPE_MANAGEMENT_CONTROLLER_CONFIRMATION_RECORD = 0x13
SDR_TYPE_BMC_MESSAGE_CHANNEL_INFO_RECORD = 0x14

# THRESHOLD BASED STATES
EVENT_READING_TYPE_CODE_THRESHOLD = 0x01
# DMI-based "Usage States" STATES
EVENT_READING_TYPE_CODE_DISCRETE = 0x02
# DIGITAL/DISCRETE EVENT STATES
EVENT_READING_TYPE_CODE_STATE = 0x03
EVENT_READING_TYPE_CODE_PREDICTIVE_FAILIRE = 0x04
EVENT_READING_TYPE_CODE_LIMIT = 0x05
EVENT_READING_TYPE_CODE_PERFORMANCE = 0x06

class Helper:
    def get_reservation_id(self, fn):
        """
        """
        m = pyipmi.msgs.sdr.ReserveDeviceSdrRepository()
        fn(m)
        check_completion_code(m.rsp.completion_code)
        return  m.rsp.reservation_id

    def get_sdr(self, fn, record_id, reservation_id=None):
        """Collects all data for the given SDR record ID and returns
        the decoded SDR object.

        `record_id` the Record ID.

        `reservation_id=None` can be set. if None the reservation ID will
        be determined.
        """
        if reservation_id is None:
            reservation_id = self.get_reservation_id(fn)

        # get record header ... 5 bytes
        m = pyipmi.msgs.sdr.GetDeviceSdr()
        m.req.reservation_id = reservation_id
        m.req.record_id = record_id
        m.req.offset = 0
        m.req.length = 5
        retry = 5
        while True:
            if retry == 0:
                raise pyipmi.errors.RetryError()
            fn(m)
            if m.rsp.completion_code == 0:
                break
            elif m.rsp.completion_code == pyipmi.msgs.constants.CC_RES_CANCELED:
                m.req.reservation_id = self.get_reservation_id(fn)
                time.sleep(0.1)
                retry -= 1
                continue
            elif m.rsp.completion_code == 0xce:
                time.sleep(0.1 * retry)
                retry -= 1
                continue
            else:
                check_completion_code(m.rsp.completion_code)

        next_record_id = m.rsp.next_record_id

        # pop will change data, therefore copy it
        record_data = m.rsp.record_data[:]
        record_id = pop_unsigned_int(m.rsp.record_data, 2)
        record_version = pop_unsigned_int(m.rsp.record_data, 1)
        record_type = pop_unsigned_int(m.rsp.record_data, 1)
        record_length = pop_unsigned_int(m.rsp.record_data, 1)
        record_length += 5

        m.req.offset = len(record_data)
        self.max_req_len = 20
        retry = 5
        # now get the other record data
        while True:
            if retry == 0:
                raise pyipmi.errors.RetryError()

            m.req.length = self.max_req_len
            if (m.req.offset + m.req.length) > record_length:
                m.req.length = record_length - m.req.offset
            fn(m)

            if (m.rsp.completion_code
                        == pyipmi.msgs.constants.CC_CANT_RET_NUM_REQ_BYTES):
                self.max_req_len -= 4
                if self.max_req_len <= 0:
                    retry = 0
                continue
            elif m.rsp.completion_code == pyipmi.msgs.constants.CC_RES_CANCELED:
                m.req.reservation_id = self.get_reservation_id(fn)
                time.sleep(0.1 * retry)
                retry -= 1
                continue
            elif m.rsp.completion_code == 0xce:
                time.sleep(0.1 * retry)
                retry -= 1
                continue
            else:
                check_completion_code(m.rsp.completion_code)

            record_data += m.rsp.record_data[:]
            m.req.offset = len(record_data)
            if len(record_data) >= record_length:
                break

        return create_sdr(record_data, next_record_id)

    def sdr_entries(self, fn):
        """A generator that returns the SDR list. Starting with ID=0x0000 and
        end when ID=0xffff is returned.
        """
        reservation_id = self.get_reservation_id(fn)
        record_id = 0

        while True:
            s = self.get_sdr(fn, record_id, reservation_id)
            yield s
            if s.next_id == 0xffff:
                break
            record_id = s.next_id

    def get_sdr_list(self, fn, reservation_id=None):
        """Returns the complete SDR list.
        """
        return list(self.sdr_entries(fn))

    def get_sensor_reading(self, fn, sensor_number, sdr=None):
        """Returns the sensor reading at the assertion states for the given
        sensor number.

        `sensor_number`

        Returns a tuple with `raw reading`and `assertion states`.
        """
        m = pyipmi.msgs.sdr.GetSensorReading()
        m.req.sensor_number = sensor_number
        fn(m)
        check_completion_code(m.rsp.completion_code)
        states = None
        if m.rsp.states1 is not None:
            states = m.rsp.states1
        if m.rsp.states2 is not None:
            states |= (m.rsp.states2 << 8)
        return (m.rsp.sensor_reading, states)


def create_sdr(data, next_id=None):
    sdr_type = ord(data[3])

    if sdr_type == SDR_TYPE_FULL_SENSOR_RECORD:
        return SdrFullSensorRecord(data, next_id)
    elif sdr_type == SDR_TYPE_COMPACT_SENSOR_RECORD:
        return SdrCompactSensorRecord(data, next_id)
    elif sdr_type == SDR_TYPE_FRU_DEVICE_LOCATOR_RECORD:
        return SdrFruDeviceLocator(data, next_id)
    elif sdr_type == SDR_TYPE_MANAGEMENT_CONTROLLER_DEVICE_LOCATOR_RECORD:
        return SdrManagementContollerDeviceLocator(data, next_id)
    elif sdr_type == SDR_TYPE_MANAGEMENT_CONTROLLER_CONFIRMATION_RECORD:
        raise DecodingError('Unsupported SDR type(0x%02x)' % sdr_type)
    elif sdr_type == SDR_TYPE_BMC_MESSAGE_CHANNEL_INFO_RECORD:
        raise DecodingError('Unsupported SDR type(0x%02x)' % sdr_type)
    else:
        raise DecodingError('Unsupported SDR type(0x%02x)' % sdr_type)


class Sdr:

    def __init__(self, rsp=None, next_id=None):
        if rsp:
            self.from_response(rsp)
        if next_id:
            self.next_id = next_id

    def __str__(self):
        s = '["%-16s"] [%s]' % (self.device_id_string, ' '.join(['%02x' % ord(b) for b in self.data]))
        return s

    def from_response(self, data):
        if len(data) < 5:
            raise DecodingError('Invalid SDR length (%d)' % len(data))

        self.data = data
        # pop will change data, therefore copy it
        tmp_data = data[:]
        self.id = pop_unsigned_int(tmp_data, 2)
        self.version = pop_unsigned_int(tmp_data, 1)
        self.type = pop_unsigned_int(tmp_data, 1)
        self.lenght = pop_unsigned_int(tmp_data, 1)

###
# SDR type 0x01
##################################################
class SdrFullSensorRecord(Sdr):
    DATA_FMT_UNSIGNED = 0
    DATA_FMT_1S_COMPLEMENT = 1
    DATA_FMT_2S_COMPLEMENT = 2
    DATA_FMT_NONE = 3

    L_LINEAR = 0
    L_LN = 1
    L_LOG = 2
    L_LOG2 = 3
    L_E = 4
    L_EXP10 = 5
    L_EXP2 = 6
    L_1_X = 7
    L_SQR = 8
    L_CUBE = 9
    L_SQRT = 10
    L_CUBERT = 11

    def __init__(self, data, next_id=None):
        if data:
            Sdr.__init__(self, data, next_id)
            self.from_data(data)

    def __str__(self):
        s = '["%-16s"] [%s]' % (self.device_id_string, ' '.join(['%02x' % ord(b) for b in self.data]))
        return s

    def convert_sensor_reading(self, value):
        fmt = self.analog_data_format
        if (fmt == self.DATA_FMT_1S_COMPLEMENT):
            if value & 0x80:
                value = -((value & 0x7f) ^ 0x7f)
        elif (fmt == self.DATA_FMT_2S_COMPLEMENT):
            if value & 0x80:
                value = -((value & 0x7f) ^ 0x7f) - 1
        value = float(value)

        return self.l((self.m * value + (self.b * 10**self.k1)) * 10**self.k2)

    @property
    def l(self):
        linearization = self.linearization & 0x7f
        if linearization == self.L_LN:
            l = math.log
        elif linearization == self.L_LOG:
            l = lambda x: math.log(x, 10)
        elif linearization == self.L_LOG2:
            l = lambda x: math.log(x, 2)
        elif linearization == self.L_E:
            l = math.exp
        elif linearization == self.L_EXP10:
            l = lambda x: math.pow(10, x)
        elif linearization == self.L_EXP2:
            l = lambda x: math.pow(2, x)
        elif linearization == self.L_1_X:
            l = lambda x: 1.0 / x
        elif linearization == self.L_SQR:
            l = lambda x: math.pow(x, 2)
        elif linearization == self.L_CUBE:
            l = lambda x: math.pow(x, 3)
        elif linearization == self.L_SQRT:
            l = math.sqrt
        elif linearization == self.L_CUBERT:
            l = lambda x: math.pow(x, 1.0/3)
        elif linearization == self.L_LINEAR:
            l = lambda x: x
        else:
            raise errors.DecodingError('unknown linearization %d' %
                    linearization)
        return l

    def _convert_complement(self, value, size):
        if (value & (1 << (size-1))):
            value = -(1<<size) + value
        return value

    def from_data(self, data):
        # pop will change data, therefore copy it
        tmp_data = data[5:]
        # record key bytes
        self.owner_id = pop_unsigned_int(tmp_data, 1)
        self.owner_lun = pop_unsigned_int(tmp_data, 1)
        self.number = pop_unsigned_int(tmp_data, 1)
        # record body bytes
        self.entity_id = pop_unsigned_int(tmp_data, 1)
        self.entity_instance = pop_unsigned_int(tmp_data, 1)

        # byte 11
        initialization = pop_unsigned_int(tmp_data, 1)
        self.initialization = []
        if initialization & 0x40:
            self.initialization.append('scanning')
        if initialization & 0x20:
            self.initialization.append('events')
        if initialization & 0x10:
            self.initialization.append('thresholds')
        if initialization & 0x08:
            self.initialization.append('hysteresis')
        if initialization & 0x04:
            self.initialization.append('type')
        if initialization & 0x02:
            self.initialization.append('default_event_generation')
        if initialization & 0x01:
            self.initialization.append('default_scanning')

        # byte 12
        capabilities = pop_unsigned_int(tmp_data, 1)
        self.capabilities = []
        if capabilities & 0x80:
            self.capabilities.append('ignore')
        if capabilities & 0x40:
            self.capabilities.append('auto_rearm')

        self.sensor_type = pop_unsigned_int(tmp_data, 1)
        self.event_reading_type_code = pop_unsigned_int(tmp_data, 1)
        self.assertion_mask = pop_unsigned_int(tmp_data, 2)
        self.deassertion_mask = pop_unsigned_int(tmp_data, 2)
        self.discrete_reading_mask = pop_unsigned_int(tmp_data, 2)
        # byte 21, 22, 23
        units_1 = pop_unsigned_int(tmp_data, 1)
        units_2 = pop_unsigned_int(tmp_data, 1)
        units_3 = pop_unsigned_int(tmp_data, 1)
        self.analog_data_format = (units_1 >> 6) & 0x3
        self.rate_unit = (units_1 >> 3) >> 0x7
        self.modifier_unit = (units_1 >> 1) & 0x2
        self.percentage = units_1 & 0x1
        # byte 24
        self.linearization = pop_unsigned_int(tmp_data, 1) & 0x7f
        # byte 25, 26
        m = pop_unsigned_int(tmp_data, 1)
        m_tol = pop_unsigned_int(tmp_data, 1)
        self.m = (m & 0xff) | ((m_tol & 0xc0) << 2)
        self.tolerance = (m_tol & 0x3f)

        # byte 27, 28, 29
        b = pop_unsigned_int(tmp_data, 1)
        b_acc = pop_unsigned_int(tmp_data, 1)
        acc_accexp = pop_unsigned_int(tmp_data, 1)
        self.b = (b & 0xff) | ((b_acc & 0xc0) << 2)
        self.b = self._convert_complement(self.b, 10)
        self.accuracy = (b_acc & 0x3f) | ((acc_accexp & 0xf0) << 4)
        self.accuracy_exp = (acc_accexp & 0x0c) >> 2
        # byte 30
        rexp_bexp = pop_unsigned_int(tmp_data, 1)
        self.k2 = (rexp_bexp & 0xf0) >> 4
        # convert 2s complement
        #if self.k2 & 0x8: # 4bit
        #    self.k2 = -0x10 + self.k2
        self.k2 = self._convert_complement(self.k2, 4)

        self.k1 = rexp_bexp & 0x0f
        # convert 2s complement
        #if self.k1 & 0x8:
        #    self.k1 = -0x10 + self.k1
        self.k1 = self._convert_complement(self.k1, 4)

        # byte 31
        analog_characteristics = pop_unsigned_int(tmp_data, 1)
        self.analog_characteristic = []
        if analog_characteristics & 0x01:
            self.analog_characteristic.append('nominal_reading')
        if analog_characteristics & 0x02:
            self.analog_characteristic.append('normal_max')
        if analog_characteristics & 0x04:
            self.analog_characteristic.append('normal_min')

        self.nominal_reading = pop_unsigned_int(tmp_data, 1)
        self.normal_maximum = pop_unsigned_int(tmp_data, 1)
        self.normal_minimum = pop_unsigned_int(tmp_data, 1)
        self.sensor_maximum_reading = pop_unsigned_int(tmp_data, 1)
        self.sensor_minimum_reading = pop_unsigned_int(tmp_data, 1)
        self.threshold_unr = pop_unsigned_int(tmp_data, 1)
        self.threshold_ucr = pop_unsigned_int(tmp_data, 1)
        self.threshold_unc = pop_unsigned_int(tmp_data, 1)
        self.threshold_lnr = pop_unsigned_int(tmp_data, 1)
        self.threshold_lcr = pop_unsigned_int(tmp_data, 1)
        self.threshold_lnc = pop_unsigned_int(tmp_data, 1)
        self.positive_going_hysteresis = pop_unsigned_int(tmp_data, 1)
        self.negative_going_hysteresis = pop_unsigned_int(tmp_data, 1)
        self.reserved = pop_unsigned_int(tmp_data, 2)
        self.oem = pop_unsigned_int(tmp_data, 1)
        self.device_id_string_type_length = pop_unsigned_int(tmp_data, 1)
        self.device_id_string = tmp_data.tostring()


###
# SDR type 0x02
##################################################
class SdrCompactSensorRecord(Sdr):
    def __init__(self, data, next_id=None):
        if data:
            Sdr.__init__(self, data, next_id)
            self.from_data(data)

    def __str__(self):
        s = '["%-16s"] [%s]' % (self.device_id_string, ' '.join(['%02x' % ord(b) for b in self.data]))
        return s

    def from_data(self, data):
        # pop will change data, therefore copy it
        tmp_data = data[5:]
        self.owner_id = pop_unsigned_int(tmp_data, 1)
        self.owner_lun = pop_unsigned_int(tmp_data, 1)
        self.number = pop_unsigned_int(tmp_data, 1)
        self.entity_id = pop_unsigned_int(tmp_data, 1)
        self.entity_instance = pop_unsigned_int(tmp_data, 1)
        self.sensor_initialization = pop_unsigned_int(tmp_data, 1)
        self.capabilities = pop_unsigned_int(tmp_data, 1)
        self.sensor_type = pop_unsigned_int(tmp_data, 1)
        self.event_reading_type_code = pop_unsigned_int(tmp_data, 1)
        self.assertion_mask = pop_unsigned_int(tmp_data, 2)
        self.deassertion_mask = pop_unsigned_int(tmp_data, 2)
        self.discrete_reading_mask = pop_unsigned_int(tmp_data, 2)
        self.units_1 = pop_unsigned_int(tmp_data, 1)
        self.units_2 = pop_unsigned_int(tmp_data, 1)
        self.units_3 = pop_unsigned_int(tmp_data, 1)
        self.record_sharing = pop_unsigned_int(tmp_data, 2)
        self.positive_going_hysteresis = pop_unsigned_int(tmp_data, 1)
        self.negative_going_hysteresis = pop_unsigned_int(tmp_data, 1)
        self.reserved = pop_unsigned_int(tmp_data, 3)
        self.oem = pop_unsigned_int(tmp_data, 1)
        self.device_id_string_type_length = pop_unsigned_int(tmp_data, 1)
        self.device_id_string = tmp_data.tostring()


###
# SDR type 0x11
##################################################
class SdrFruDeviceLocator(Sdr):
    def __init__(self, data, next_id=None):
        if data:
            Sdr.__init__(self, data, next_id)
            self.from_data(data)

    def __str__(self):
        s = '["%-16s"] [%s]' % (self.device_id_string, ' '.join(['%02x' % ord(b) for b in self.data]))
        return s

    def from_data(self, data):
        # pop will change data, therefore copy it
        tmp_data = data[5:]
        # record key bytes
        self.device_access_address = pop_unsigned_int(tmp_data, 1) >> 1
        self.fru_device_id = pop_unsigned_int(tmp_data, 1)
        self.logical_physical = pop_unsigned_int(tmp_data, 1)
        self.channel_number = pop_unsigned_int(tmp_data, 1)
        # record body bytes
        self.reserved = pop_unsigned_int(tmp_data, 1)
        self.device_type = pop_unsigned_int(tmp_data, 1)
        self.device_type_modifier= pop_unsigned_int(tmp_data, 1)
        self.entity_id = pop_unsigned_int(tmp_data, 1)
        self.entity_instance = pop_unsigned_int(tmp_data, 1)
        self.oem = pop_unsigned_int(tmp_data, 1)
        self.device_id_string_type_length = pop_unsigned_int(tmp_data, 1)
        self.device_id_string = tmp_data.tostring()


###
# SDR type 0x12
##################################################
class SdrManagementContollerDeviceLocator(Sdr):
    def __init__(self, data, next_id=None):
        if data:
            Sdr.__init__(self, data, next_id)
            self.from_data(data)

    def __str__(self):
        s = '["%-16s"] [%s]' % (self.device_id_string, ' '.join(['%02x' % ord(b) for b in self.data]))
        return s

    def from_data(self, data):
        # pop will change data, therefore copy it
        tmp_data = data[5:]
        self.device_slave_address = pop_unsigned_int(tmp_data, 1) >> 1
        self.channel_number = pop_unsigned_int(tmp_data, 1) & 0xf
        self.power_state_notification = pop_unsigned_int(tmp_data, 1)
        self.global_initialization = 0
        self.device_capabilities = pop_unsigned_int(tmp_data, 1)
        self.reserved = pop_unsigned_int(tmp_data, 3)
        self.entity_id = pop_unsigned_int(tmp_data, 1)
        self.entity_instance = pop_unsigned_int(tmp_data, 1)
        self.oem = pop_unsigned_int(tmp_data, 1)
        self.device_id_string_type_length = pop_unsigned_int(tmp_data, 1)
        self.device_id_string = tmp_data.tostring()