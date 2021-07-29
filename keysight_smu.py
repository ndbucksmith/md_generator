"""
driver for keysight 2901 - 2902 SMUs
#   f"build string {var}" will not work within visa session.write
this driver now accepts channel input as string or integer
copyright 2020 buck smith
"""

import datetime
import time
from typing import List, Tuple, Union
import numpy as np
import pyvisa as visa
from vtf.util import log

from hardware.iinstrument import IInstrument, MockInstrument

CONFIG_SEED = {
    "model": "B2902A",
    "SN": "MY51144607",
    "visa_addr": "USB0::0x0957::0x8C18::MY51144607::INSTR",
    "CalDate": "20210112",
}

import logging
from vtf.util.config import get_hardware

logging.getLogger("visa").disabled = True


class Channels:
    CH1 = 1
    CH2 = 2


class KeysightSMU(IInstrument):
    def __init__(self, **kwargs):
        """
        verify instrument exists in visa resource manager

        returns None
        """
        if "hw_config" not in kwargs:
            log.error(f"No instrument info for {self.__class__.__name__}")
            return
        self.instr_name = kwargs["unique_identifier"]
        self.idns = None
        self.model = None
        self.serial_number = None
        self.visa_addr = None
        self.trig_time = 0.01
        self.trig_count = 60
        self._nplc = 2
        self.aper = 0.005
        self._session = None
        self._port_handle = ""
        try:
            self.Channels = Channels()
            self._read_instr(kwargs["hw_config"][self.instr_name])
        except Exception as ex:
            print(
                f"error connecting to Source Measure Unit:\n {ex}\n check power and USB connection"
            )

    def open(self, retry_delay=1.0) -> None:
        """
        Performs resets and queries instrument ID info
        """
        try:
            rm = visa.ResourceManager()
            # If visa_addr hasn't yet been specified (specified in config.json or by ip address), find USB device
            if not self.visa_addr:
                instr_list = rm.list_resources()
                self.visa_addr = [addr for addr in instr_list if self.serial_number in addr][0]
            self._session = rm.open_resource(self.visa_addr)
            self._session.write_termination = "\n"
            self._session.timeout = 4000

            try:
                self._session.write("*RST")
                self.idns = self._session.query("*IDN?").split(",")
                self.model = self.idns[1]
                self.serial_number = self.idns[2]
            except Exception:
                time.sleep(retry_delay)
                try:
                    self._session.write("*RST")
                    self.idns = self._session.query("*IDN?").split(",")
                    self.model = self.idns[1]
                    self.serial_number = self.idns[2]
                except Exception:
                    log.exception("Unable to get Keysight serial number")
            finally:
                if self.serial_number == "":
                    log.error("Unable to find Keysight SMU serial number")

        except Exception as ex:
            log.exception(
                "Error connecting to SMU - check SMU power, network connection, and serial number"
            )
            raise Exception(
                "Error connecting to SMU - check SMU power, network connection, and serial number"
            )

    def close(self):
        """  close visa session"""
        if self._session is not None:
            self._session.close()
            self._session = None

    def _get_id(self):
        """  return ID info from IDN? query in open()"""
        return self.idns.join(",")

    def _read_instr(self, hw_config):
        """parse dict from config file and check to see
        if instrument is in calibration"""
        try:
            if "visa_addr" in hw_config:
                self.visa_addr = hw_config["visa_addr"]
            elif "ip_address" in hw_config:
                self.visa_addr = f"TCPIP0::{hw_config['ip_address']}::inst0::INSTR"
            self.serial_number = hw_config["serial_number"]
            self.model = hw_config["model"]
        except KeyError as e:
            log.error(f"Missing {e} for {self.__class__.__name__}")

    def _read_check_caldate(self, kwargs):
        self.calDate = kwargs["calibration_expiration"]
        compStr = str(int(self.calDate))
        today = datetime.datetime.now().replace(microsecond=0).strftime("%Y%m%d")
        if compStr < today:  # string compare works with iso date strings
            self.calStatus = False
            log.info(f"Smu Out of Cal")
        else:
            self.calStatus = True

    def reset(self) -> None:
        """ perform *RST on smu"""
        if self._session:
            for channel in [1, 2]:
                self.channel_off(channel)
            self._session.write("*RST")

    # source voltage measure current
    # using commands from https://literature.cdn.keysight.com/litweb/pdf/B2910-90030.pdf?id=1240049
    # modified from 1842 project to be pure _svmi with no power down and sleep(4)
    def _svmi(self, channel: Union[str, int], volts: float, i_limit: float, settle: float) -> List[float]:
        """
        Source voltage measure current function
        default data format returns a list of values for
        VOLTage|CURRent|RESistance|TIME|STATus|SOUR
        """
        ch = str(channel)
        if ch in ("1", "2"):
            try:
                forcev = f"{volts}"
                comply = f"{i_limit}"
                self._session.write(":SENS" + ch + ":CURR:PROT " + comply)
                self._session.write(":SENS" + ch + ":REM OFF")
                self._session.write(":SENS" + ch + ':FUNC:ON "VOLT","CURR"')
                fv = self._session.write(":SOUR" + ch + ":VOLT:LEV:IMM " + forcev)
                self.err_check(self._session.query(":SYST:ERR:CODE:ALL?"))
                self._session.write(":SOUR" + ch + ":FUNC:MODE VOLT")
                self._session.write(":SOUR" + ch + ":FUNC:SHAP DC")
                self._session.write(":SOUR" + ch + ":VOLT:MODE FIX")
                self.err_check(self._session.query(":SYST:ERR:CODE:ALL?"))
                self._session.write(f":SENS{ch}:CURR:DC:NPLC 5")
                self._session.write(":OUTP" + ch + ":STAT ON")
                frmEl = self._session.query(":FORM:ELEM:SENS?")
                self.err_check(self._session.query(":SYST:ERR:CODE:ALL?"))
                self._session.write(":INIT:IMM:ACQ (@" + ch + ")")
                time.sleep(settle)
                self.err_check(self._session.query(":SYST:ERR:CODE:ALL?"))
                measureds = self._session.query(":SENS" + ch + ":DATA?")
                meas_np = [float(x) for x in measureds.split(",")]
                return meas_np
            except Exception as ex:
                log.exception("Current measurement exception")
                raise Exception(f"ERROR: {ex}")

        else:
            log.error("Invalid SMU Channel")

    # simple measure of V and I
    def smu_meas(self, channel: Union[str, int], settle: float, curr_range: float = None, four_wire: bool = False, aper_val: float = None, auto_aper: bool = False) -> (float, float):
        """
        Waits for time settle and then reads voltage and current
        returns V, I
        """
        channel = str(channel)

        if four_wire:
            # print("4-wire enabled = " + self._session.query(f":SENS{channel}:REM?"))
            self._session.write(f"SENS{channel}:REM {1 if four_wire else 0}")
            # print("4-wire enabled = " + self._session.query(f":SENS{channel}:REM?"))

        if curr_range:
            self._session.write(f":SENS{channel}:CURR:DC:RANG:UPP {curr_range}")

        if auto_aper:
            self._session.write(f":SENS{channel}:CURR:DC:APER:AUTO 1")
        elif aper_val:
            # print("Aperture = " + self._session.query(f":SENS{channel}:CURR:APER?"))
            self._session.write(f":SENS{channel}:CURR:DC:APER {aper_val}")
            # print("Aperture = " + self._session.query(f":SENS{channel}:CURR:APER?"))

        time.sleep(settle)

        V = self._session.query(f":MEAS:VOLT? (@{channel})")
        I = self._session.query(f":MEAS:CURR? (@{channel})")
        V = float(V)
        I = float(I)
        return V, I

    # use _svmi to source then scan current values
    def source_dci(self, channel: Union[str, int], amps: float, v_comply: float):
        """
        enables current limited source of DC volts on a single channel
        comply is current limit in amps
        returns v, i measurements
        """
        channel = str(channel)
        forcei = f"{amps}"

        self._session.write(":SOUR" + channel + ":FUNC:MODE  CURR")
        fv = self._session.write(":SOUR" + channel + ":CURR:LEV:IMM " + forcei)
        self._session.write(":SOUR" + channel + ":FUNC:SHAP DC")
        self._session.write(":SOUR" + channel + ":VOLT:MODE FIX")
        self._session.write(":SENS" + channel + ":VOLT:PROT " + str(v_comply))
        er2 = self._session.query(":SYST:ERR:CODE:ALL?")
        self._session.write(":OUTP" + channel + ":STAT ON")
        self._session.write(":INIT:IMM:TRAN (@" + channel + ")")
        er3 = self._session.query(":SYST:ERR:CODE:ALL?")
        # stb1 = self._session.query("*STB?")
        time.sleep(0.05)
        self.err_check(self._session.query(":SYST:ERR:CODE:ALL?"), id="source_dc1")
        return self.smu_meas(channel, 0.2)

    def source_dcv(self, channel: Union[str, int], volts: float, comply: float):
        """
        enables current limited source of DC volts on a single channel
        comply is current limit in amps
        returns v, i measurements
        """
        channel = str(channel)
        forcev = f"{volts}"
        self._session.write(":SOUR" + channel + ":FUNC:MODE  VOLT")
        fv = self._session.write(":SOUR" + channel + ":VOLT:LEV:IMM " + forcev)
        self._session.write(":SOUR" + channel + ":FUNC:SHAP DC")
        self.err_check(self._session.query(":SYST:ERR:CODE:ALL?"), id="source_dcv_0")
        self._session.write(":SOUR" + channel + ":VOLT:MODE FIX")
        self._session.write(":SENS" + channel + ":CURR:PROT " + str(comply))
        # er2 = self._session.query(":SYST:ERR:CODE:ALL?")
        self._session.write(":OUTP" + channel + ":STAT ON")
        self._session.write(":INIT:IMM:TRAN (@" + channel + ")")
        # er3 = self._session.query(":SYST:ERR:CODE:ALL?")
        # stb1 = self._session.query("*STB?")
        time.sleep(0.25)
        self.err_check(self._session.query(":SYST:ERR:CODE:ALL?"), id="source_dcv")
        return self.smu_meas(channel, 0.2)

    def channel_off(self, channel: Union[str, int]):
        channel = str(channel)
        self._session.write(f":OUTP{channel}:STAT OFF")
        time.sleep(0.25)
        self.err_check(self._session.query(":SYST:ERR:CODE:ALL?"), id="channel_off")

    # sets up and triggers a scan of self.trig_ct readings at period of self._trigTime
    def initsv_vidaq(self, channel: Union[str, int], curr_range: float = None, four_wire: bool = False) -> str:
        """
        initiates scan of smu's trig_ct property readings
        with sample period of smu's aper property seconds
        """

        self.err_check(self._session.query(":SYST:ERR:CODE:ALL?"), id=f"init_daq0_ch{channel}")
        self.err_check(self._session.query(":SYST:ERR:CODE:ALL?"), id=f"init_daq1_ch{channel}")
        self._session.write(f":SENS{channel}:CURR:APER {self.aper}")
        self._session.write(f":SENS{channel}:REM OFF")

        if four_wire:
            print(f"4-wire enabled = " + self._session.query(f":SENS{channel}:REM?"))
            self._session.write(f"SENS{channel}:REM {1 if four_wire else 0}")
            print("4-wire enabled = " + self._session.query(f":SENS{channel}:REM?"))

        if curr_range:
            self._session.write(f":SENS{channel}:CURR:DC:RANG:UPP {curr_range}")
            print("range = " + self._session.query(f":SENS{channel}:CURR:DC:RANG:UPP?"))
        else:
            self._session.write(f":SENS{channel}:CURR:RANG:AUTO ON")

        self._session.write(f":SENS{channel}:CURR:APER {self.aper}")
        self._session.write(f":TRIG{channel}:ACQ:SOUR TIM")
        self.err_check(self._session.query(":SYST:ERR:CODE:ALL?"), id=f"init_daq2_ch{channel}")
        self._session.write(f":TRIG{channel}:ACQ:TIM {self.trig_time}")
        self._session.write(f":TRIG{channel}:ACQ:COUN {self.trig_count}")
        self.err_check(self._session.query(":SYST:ERR:CODE:ALL?"), id=f"init_daq3_ch{channel}")
        self._session.write(f":OUTP{channel}:STAT ON")
        log.info(f"starting acq of {self.trig_count} rdgs of V & I every {self.trig_time} seconds")
        self._session.write(f":INIT:ACQ (@{channel})")
        self.err_check(self._session.query(":SYST:ERR:CODE:ALL?"), id=f"init_daq4_ch{channel}")
        stb1 = self._session.query("*STB?")

        return stb1

    # return results from intSV_viDac
    def fetch_vi(self, channel: Union[str, int]) -> Tuple[List[float], List[float]]:
        """
        return two lists of the scanned v and i values

        """
        ch = str(channel)

        # TODO - if measureds returns any values > 1e30, replace with something more indicative of error

        measureds = self._session.query(":FETC:ARR:CURR? (@" + ch + ")")
        self.err_check(self._session.query(":SYST:ERR:CODE:ALL?"), id="fetch1")
        npI = [float(x) for x in measureds.split(",")]
        sourceVals = self._session.query(":FETC:ARR:VOLT? (@" + ch + ")")
        npV = [float(x) for x in sourceVals.split(",")]
        self.err_check(self._session.query(":SYST:ERR:CODE:ALL?"), id="fetch2'")

        return npV, npI

    def two_channel_qst(self, v_list, aperature):
        """
        For 2902A only - two channel quasi-static transfer curve (qst)
        Scan voltage and current on channels 1 and 2 while sourcing voltage on channel 1
        Use smu list method to step through a list source voltages
        case id  01001406
        :param v_list: List of voltages to source
        :param aperature:
        :return:  2D array of floats V,I,VI,??,?? by v_list length
        """
        self._session.write(f":sour1:func:mode volt")
        self._session.write(f":sour2:func:mode curr")
        self._session.write("FORM:ELEM:SENS VOLT,CURR")
        v_str = f"{v_list}"[1:-1]
        # make a list of zero amp current source settings for channel 2
        i_str = ""
        for ix in range(len(v_list)):
            i_str += "0.0,"
        i_str = i_str[0:-1]
        self._session.write(f":sour1:list:volt {v_str}")
        self._session.write(f":sour1:volt:mode list")
        self._session.write(f":sour2:list:curr {i_str}")
        self._session.write(f":sour2:curr:mode list")
        actual_v_list = self._session.query("SOUR1:LIST:VOLT?")
        actual_i_list = self._session.query("SOUR2:LIST:CURR?")
        self._session.write(f":SENS1:CURR:PROT 0.2")
        self._session.write(f":SENS2:VOLT:PROT 10.0")
        # self._session.write(":SOUR2:FUNC:SHAP DC")
        self.err_check(self._session.query(":SYST:ERR:CODE:ALL?"), id=f"ini0")
        self._session.write(':SENS1:FUNC "VOLT","CURR"')
        self._session.write(':SENS2:FUNC "VOLT","CURR"')
        self.err_check(self._session.query(":SYST:ERR:CODE:ALL?"), id=f"ini2")
        self._session.write(f":SENS1:CURR:APER {aperature-0.01}")
        self._session.write(f":SENS2:CURR:APER {aperature-0.01}")
        self._session.write(f":SENS1:VOLT:APER {aperature-0.01}")
        self._session.write(f":SENS2:VOLT:APER {aperature-0.01}")
        self.err_check(self._session.query(":SYST:ERR:CODE:ALL?"), id=f"ini3")
        # self._session.write(f":ARM1:SOUR AUTO")
        # self._session.write(f":ARM2:SOUR AUTO")
        _count = len(v_list)
        self._session.write(f":TRIG:SOUR TIM")
        self.err_check(self._session.query(":SYST:ERR:CODE:ALL?"), id=f"ini32")
        self._session.write(f":TRIG:TIM {aperature}")
        self.err_check(self._session.query(":SYST:ERR:CODE:ALL?"), id=f"ini31")
        self._session.write(f":TRIG:COUNT {_count}")
        self.err_check(self._session.query(":SYST:ERR:CODE:ALL?"), id=f"ini4")
        self._session.write(f":OUTP1 ON")
        self._session.write(f":OUTP2 ON")
        self.err_check(self._session.query(":SYST:ERR:CODE:ALL?"), id=f"ini5")
        self._session.write(f":INIT")
        self.err_check(self._session.query(":SYST:ERR:CODE:ALL?"), id=f"ini6")
        time.sleep(_count*aperature)
        #resp_data = self._session.query("SENS:DATA?").split(',')
        resp_data = self._session.query("FETC:ARR? (@1,2)").split(',')
        print(f"{len(resp_data)} values read for list of {_count}")
        float_data = np.array([float(val) for val in resp_data])
        float_data_resh = float_data.reshape((4, len(float_data)//4),  order='F')
        self.err_check(self._session.query(":SYST:ERR:CODE:ALL?"), id=f"ini4")
        return float_data_resh

    def err_query(self):
        return self._session.query(":SYST:ERR:CODE:ALL?")

    def err_check(self, stat: str, id=""):
        if stat == "+0\n":
            return False
        else:
            print(id, self.model)
            log.info(f"SCPI error: {stat}   {id}")
            raise ValueError(f"SCPI error {stat}  {id}")


class MockKeysightSMU(MockInstrument):
    def __init__(self, **kwargs):
        super().__init__(identity=kwargs['unique_identifier'])
        pass

    def _get_id(self):
        """  return ID info from IDN? query in open()"""
        return "Mock Keysight SMU"

    def _read_instr(self, hw_config):
        """parse dict from config file and check to see
        if instrument is in calibration"""
        pass

    def _read_check_caldate(self, kwargs):
        self.calStatus = True

    def reset(self) -> None:
        """ perform *RST on smu"""
        pass

    @staticmethod
    def _svmi(channel: Union[str, int], volts: float, iLimit: float, settle: float) -> List[float]:
        """
        Source voltage measure current function
        default data format returns a list of values for
        VOLTage|CURRent|RESistance|TIME|STATus|SOUR
        """
        return [0.0]

    @staticmethod
    def smu_meas(channel: Union[str, int], settle: float) -> (float, float):
        """
        Waits for time settle and then reads voltage and current
        returns V, I
        """
        V = 0.0
        I = 0.0
        return V, I

    @staticmethod
    def source_dci(channel: Union[str, int], amps: float, v_comply: float):
        """
        enables current limited source of DC volts on a single channel
        comply is current limit in amps
        returns v, i measurements
        """
        return 0.0

    @staticmethod
    def source_dcv(channel: Union[str, int], volts: float, comply: float):
        """
        enables current limited source of DC volts on a single channel
        comply is current limit in amps
        returns v, i measurements
        """
        return 0.0

    @staticmethod
    def initsv_vidaq(channel: Union[str, int], curr_range: float = None, four_wire: bool = False) -> str:
        stb1 = "Test STB"
        return stb1

    @staticmethod
    def fetch_vi(channel: Union[str, int]) -> Tuple[List[float], List[float]]:
        npV = ([0.0])
        npI = [0.0]
        return npV, npI

    @staticmethod
    def err_check(stat: str, id=""):
        return False


def smu_test():
    # simple tests
    smu = KeysightSMU(
        unique_identifier="smu_1",
        make="Keysight",
        model="E2902",
        serial_number="MY59002082",
        calibration_expiration="20210929",
        hw_config={'smu_1':{'visa_addr':"TCPIP0::192.168.50.4::INSTR",
                            'serial_number':"MY59002082",
                            'model':"E2902"}
                   }
    )
    smu.open()
    v_l = []
    for ix in range(120):
        v_l.append(1.8+(ix*0.005))
    values = smu.two_channel_qst(v_l, 0.04)
    print(values)
    print(smu.err_query())
    # smu.source_dcv(2, 3.0, 0.2)
    # m1s = smu.smu_meas(2, 0.1)
    # smu.source_dcv(1,4.0,0.5)
    # m2s = smu.smu_meas(1, 0.1)
    # print("V,I ", m2s)
    m1s = smu.smu_meas(1, 0.1)
    m12 = smu.smu_meas(2, 0.1)
    m1s = smu.smu_meas(2, 0.1)


if __name__ == "__main__":
    smu_test()
