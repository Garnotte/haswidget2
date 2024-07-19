import json
import logging
import time

from aiohttp import ClientSession, TCPConnector
from enum import auto, Enum
from typing import Any, Dict, List

from .exceptions import SwidgetException
from .websocket import SwidgetWebsocket

_LOGGER = logging.getLogger(__name__)


class DeviceType(Enum):
    """Device type enum."""

    Dimmer = "dimmer"
    Outlet = "outlet"
    Switch = "switch"
    TimerSwitch = "pana_switch"
    RelaySwitch = "relay_switch"
    Unknown = -1


class SwidgetDevice:
    def __init__(self, host, secret_key, ssl=False, use_websockets=True):
        self.ip_address = host
        self.ssl = ssl
        self.secret_key = secret_key
        self.use_websockets = use_websockets
        self._friendly_name = "Unknown Swidget Device"
        headers = {"x-secret-key": self.secret_key}
        connector = TCPConnector(force_close=True)
        self._session = ClientSession(headers=headers, connector=connector)
        self._last_update = None
        if self.use_websockets:
            self._websocket = SwidgetWebsocket(
                host=self.ip_address,
                secret_key=self.secret_key,
                callback=self.message_callback,
                session=self._session)


    async def stop(self):
        """Stop the websocket."""
        if self._websocket is not None:
            await self._websocket.stop()

    async def message_callback(self, message):
        """Entrypoint for a websocket callback"""
        if message["request_id"] == "summary":
            await self.process_summary(message)
        elif message["request_id"] == "state" or message["request_id"] == "DYNAMIC_UPDATE" or message["request_id"] == "command":
            await self.process_state(message)

    async def get_summary(self):
        """Get a summary of the device over HTTP"""
        async with self._session.get(
            url=f"https://{self.ip_address}/api/v1/summary", ssl=self.ssl
        ) as response:
            summary = await response.json()
        await self.process_summary(summary)

    async def process_summary(self, summary):
        """ Process the data around the summary of the device"""
        self.model = summary["model"]
        self.mac_address = summary["mac"]
        self.version = summary["version"]
        self.assemblies = {
            "host": SwidgetAssembly(summary["host"]),
            "insert": SwidgetAssembly(summary["insert"]),
        }
        self.device_type = self.assemblies['host'].type
        self.insert_type = self.assemblies['insert'].type
        self.id = self.assemblies['host'].id
        self._last_update = int(time.time())

    async def get_state(self):
        """ Get the state of the device over HTTP"""
        async with self._session.get(
            url=f"https://{self.ip_address}/api/v1/state", ssl=self.ssl
        ) as response:
            state = await response.json()
        await self.process_state(state)

    async def process_state(self, state):
        """ Process any information about the state of the device or insert"""
        _LOGGER.error(f"Processing state: {state}")
        # State is not always in the state (during callback)
        try:
            self.rssi = state["connection"]["rssi"]
        except:
            pass
        """
        2022-06-28 14:53:13 ERROR (MainThread) [custom_components.swidget.swidgetclient.device] Self.assemblies: {'host': <custom_components.swidget.swidgetclient.device.SwidgetAssembly object at 0xffff862b6280>, 'insert': <custom_components.swidget.swidgetclient.device.SwidgetAssembly object at 0xffff8f0b5520>}
        Processing state: {'request_id': 'command', 'insert': {'components': {'usb': {'toggle': {}, 'state': 'on'}}}}

        """
        for assembly in self.assemblies:
            _LOGGER.error(assembly)
            for id, component in self.assemblies[assembly].components.items():
                _LOGGER.error(f"id:{id}   component: {component.__dict__}")
                _LOGGER.error(f"component.function: {component.functions}")
                try:
                    _LOGGER.error(f"Setting State: {state[assembly]['components'][id]}")
                    component.functions.update(state[assembly]["components"][id])
                except:
                    pass
        self._last_update = int(time.time())
        _LOGGER.error(f"Finished getting state: {self.__dict__}")
        a = self.assemblies['host'].__dict__
        b = self.assemblies['insert'].__dict__
        _LOGGER.error(f"Finished getting state: {self.__dict__}")
        _LOGGER.error(f"Finished getting state: {a}")
        _LOGGER.error(f"Finished getting state: {b}")

    async def get_friendly_name(self):
        try:
            async with self._session.get(
                url=f"https://{self.ip_address}/api/v1/name", ssl=self.ssl
            ) as response:
                name = await response.json()
        except Exception:
            name = {"name": f"Swidget {self.device_type} w/{self.insert_type} insert"}
        await self.process_friendly_name(name['name'])

    async def process_friendly_name(self, name):
        self._friendly_name = name

    async def update(self):
        if self._last_update is None:
            _LOGGER.debug("Performing the initial update to obtain sysinfo")
        await self.get_summary()
        await self.get_state()
        await self.get_friendly_name()

    async def send_config(self, payload: dict):
        data = json.dumps({"type":"config","request_id":"abcd", "payload": payload})
        await self._websocket.send_str(data)

    async def send_command(
        self, assembly: str, component: str, function: str, command: dict
    ):
        """Send a command to the Swidget device either using a HTTP call or the existing websocket"""
        data = {assembly: {"components": {component: {function: command}}}}

        if self.use_websockets:
            data = json.dumps({"type": "command",
                               "request_id": "command",
                               "payload": data
                               })
            _LOGGER.error(f"About to send data: {data}")
            await self._websocket.send_str(data)
        else:
            async with self._session.post(
                url=f"https://{self.ip_address}/api/v1/command",
                ssl=self.ssl,
                data=json.dumps(data),
            ) as response:
                state = await response.json()

            function_value = state[assembly]["components"][component][function]
            self.assemblies[assembly].components[component].functions[function] = function_value  # fmt: skip

    async def ping(self):
        """Ping the device to ensure it's devices

        :raises SwidgetException: Raise the exception if there we are unable to connect to the Swidget device
        """
        try:
            async with self._session.get(
                url=f"https://{self.ip_address}/ping",
                ssl=self.ssl
            ) as response:
                return response.text
        except:
            raise SwidgetException

    async def blink(self):
        """Make the device LED blink

        :raises SwidgetException: Raise the exception if there we are unable to connect to the Swidget device
        """
        try:
            async with self._session.get(
                url=f"https://{self.ip_address}/blink?x-user-key=dqMMBX9deuwtkkp784ewTjqo76IYfThV",
                ssl=self.ssl
            ) as response:
                return response.text
        except:
            raise SwidgetException

    @property
    def hw_info(self) -> Dict:
        """
        Return hardware information.

        This returns just a selection of attributes that are related to hardware.
        """
        return {
            "version": self.version,
            "mac_address": self.mac_address,
            "type": self.device_type,
            "id": self.id,
            "model": self.model,
            "insert_type": self.insert_type,
            "features": self.features,
            "rssi": self.rssi
        }

    def get_child_consumption(self, plug_id=0):
        """Get the power consumption of a plug in watts."""
        if plug_id == "all":
            return_dict = {}
            for id, properties in self.assemblies['host'].components.items():
                try:
                    return_dict[f"power_{id}"] = properties.functions['power']['current']
                except KeyError: # Hits this when there is no power metering
                    return None
            return return_dict
        return self.assemblies['host'].components[str(plug_id)].functions['power']['current']

    async def total_consumption(self):
        """Get the total power consumption in watts."""
        # await self.get_state()
        total_consumption = 0
        for id, properties in self.assemblies['host'].components.items():
            print(vars(properties))
            total_consumption += properties.functions['power']['current']
        return total_consumption

    @property
    def realtime_values(self):
        """Get a dict of realtime value attributes from the insert and host

        :return: A dictionary of insert sensor values and power consumption values
        :rtype: dict
        """
        return_dict = {}
        for feature in self.features:
            return_dict.update(self.get_function_values(feature))
        return_dict.update({'rssi': self.rssi})
        power_values = self.get_child_consumption("all")
        if power_values:
            return_dict.update(power_values)
        return return_dict

    @property
    def features(self) -> List[str]:
        """Return a set of features that the device supports."""
        try:
            return list(self.assemblies['insert'].components.keys())
        except KeyError:
            _LOGGER.debug("Device does not have feature information")
            return set()

    def get_function_values(self, function: str):
        """Return the values of an insert function."""
        return_values = dict()
        for function, data in self.assemblies['insert'].components[function].functions.items():
            if function == "occupied":
                return_values[function] = data['state']
            elif function == "toggle":
                pass
            else:
                return_values[function] = data['now']
        return return_values

    def get_sensor_value(self, function, sensor):
        """Return the value of a sensor given a function and sensor"""
        if sensor == "occupied":
            return self.assemblies['insert'].components[function].functions['occupied']['state']
        else:
            return self.assemblies['insert'].components[function].functions[sensor]['now']

    @property
    def is_outlet(self) -> bool:
        """Return True if the device is an outlet."""
        return self.device_type == "outlet" or self.device_type == "outlet_20a"

    @property
    def is_switch(self) -> bool:
        """Return True if the device is a switch"""
        return self.device_type == "switch" or self.device_type == "relay_switch"

    @property
    def is_pana_switch(self) -> bool:
        """Return True if the device is a pana_switch"""
        return self.device_type == "pana_switch"

    @property
    def is_dimmer(self) -> bool:
        """Return True if the device is a dimmer"""
        return self.device_type == "dimmer"

    @property  # type: ignore
    def friendly_name(self) -> str:
        """Return a friendly description of the device"""
        return self._friendly_name

    def __repr__(self):
        if self._last_update is None:
            return f"<{self.device_type} at {self.ip_address} - update() needed>"
        return f"<{self.device_type} model {self.model} at {self.ip_address}>"

class SwidgetAssembly:
    def __init__(self, summary: dict):
        self.type = summary["type"]
        self.components = {
            c["id"]: SwidgetComponent(c["functions"]) for c in summary["components"]
        }
        self.id = summary.get("id")
        self.error = summary.get("error")


class SwidgetComponent:
    def __init__(self, functions):
        self.functions = {f: None for f in functions}
