#!/usr/bin/env python3
import asyncio
import json
import os
import time
import logging
import logging.handlers
import argparse
import signal
import sys
import atexit
import secrets
import tomllib
from collections import deque
from typing import Dict, List, Any, Optional, Tuple, Union
from dataclasses import dataclass
from pathlib import Path

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

try:
    import pynvml
    NVML_AVAILABLE = True
except ImportError:
    NVML_AVAILABLE = False

try:
    import aiofiles
    AIOFILES_AVAILABLE = True
except ImportError:
    AIOFILES_AVAILABLE = False

try:
    import msgpack
    MSGPACK_AVAILABLE = True
except ImportError:
    MSGPACK_AVAILABLE = False

CONFIG_PATHS = [
    Path("/etc/thermoguard/config.toml"),
    Path.home() / ".config/thermoguard/config.toml",
    Path.cwd() / "thermoguard.toml",
]

@dataclass
class Config:
    socket_path: str = "/tmp/thermoguard.sock"
    user_data_dir: str = str(Path.home() / ".local/share/thermoguard")
    rescan_interval: int = 60
    volatile_interval: float = 1.0
    thermal_interval: float = 2.0
    static_interval: float = 30.0
    volatile_buffer_size: int = 3600
    thermal_buffer_size: int = 1800
    static_buffer_size: int = 120
    binary: bool = False
    debug: bool = False
    token: bool = False
    max_failures: int = 5
    failure_backoff: int = 300

    @classmethod
    def load(cls, paths: List[Path] = CONFIG_PATHS) -> "Config":
        config = cls()
        for path in paths:
            if path.exists():
                try:
                    with open(path, "rb") as f:
                        data = tomllib.load(f)
                    for key, value in data.get("thermoguard", {}).items():
                        if hasattr(config, key):
                            setattr(config, key, value)
                except Exception:
                    pass
                break
        return config

SOCKET_PATH = "/tmp/thermoguard.sock"
USER_DATA_DIR = os.path.expanduser("~/.local/share/thermoguard")
LOG_PATH = os.path.join(USER_DATA_DIR, "thermoguard.log")
TOKEN_FILE = os.path.join(USER_DATA_DIR, "thermoguard.token")
RESCAN_INTERVAL = 60
VOLATILE_INTERVAL = 1.0
THERMAL_INTERVAL = 2.0
STATIC_INTERVAL = 30.0
VOLATILE_BUFFER_SIZE = 3600
THERMAL_BUFFER_SIZE = 1800
STATIC_BUFFER_SIZE = 120

def setup_logging(debug: bool = False) -> None:
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    root_logger.setLevel(logging.DEBUG if debug else logging.INFO)
    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG if debug else logging.INFO)
    console.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    root_logger.addHandler(console)
    os.makedirs(USER_DATA_DIR, exist_ok=True)
    try:
        file_handler = logging.handlers.RotatingFileHandler(
            LOG_PATH, maxBytes=10_485_760, backupCount=5
        )
        file_handler.setLevel(logging.DEBUG if debug else logging.INFO)
        file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
        root_logger.addHandler(file_handler)
    except Exception:
        pass

def load_token() -> Optional[str]:
    os.makedirs(USER_DATA_DIR, exist_ok=True)
    try:
        with open(TOKEN_FILE, 'r') as f:
            return f.read().strip()
    except Exception:
        token = secrets.token_hex(32)
        try:
            with open(TOKEN_FILE, 'w') as f:
                f.write(token)
            os.chmod(TOKEN_FILE, 0o600)
            return token
        except Exception:
            return None

class FramedSerializer:
    def __init__(self, binary: bool = False):
        self.binary = binary
        if binary and not MSGPACK_AVAILABLE:
            self.binary = False

    def encode(self, obj: Any) -> bytes:
        if self.binary:
            payload = msgpack.packb(obj)
        else:
            payload = json.dumps(obj).encode('utf-8')
        return len(payload).to_bytes(4, 'big') + payload

    async def decode(self, reader: asyncio.StreamReader) -> Any:
        try:
            raw_len = await reader.readexactly(4)
            length = int.from_bytes(raw_len, 'big')
            payload = await reader.readexactly(length)
        except (asyncio.IncompleteReadError, ConnectionError):
            return None
        if self.binary:
            return msgpack.unpackb(payload)
        else:
            return json.loads(payload.decode('utf-8'))

async def read_sys_file_async(path: str) -> Optional[str]:
    if AIOFILES_AVAILABLE:
        try:
            async with aiofiles.open(path, 'r') as f:
                return (await f.read()).strip()
        except (OSError, IOError):
            return None
    else:
        return await asyncio.get_running_loop().run_in_executor(
            None, _read_file_sync, path
        )

def _read_file_sync(path: str) -> Optional[str]:
    try:
        with open(path, 'r') as f:
            return f.read().strip()
    except Exception:
        return None

class Sensor:
    def __init__(self, name: str, tier: str = 'volatile', buffer_size: int = VOLATILE_BUFFER_SIZE, config: Optional[Config] = None):
        self.name = name
        self.tier = tier
        self.buffer = deque(maxlen=buffer_size)
        self.last_value: Any = None
        self.last_update: float = 0.0
        self.logger = logging.getLogger(f"thermoguard.sensor.{name}")
        self.failure_count = 0
        self.disabled_until = 0.0
        self.config = config or Config()
        self.max_failures = self.config.max_failures
        self.failure_backoff = self.config.failure_backoff

    async def update(self) -> None:
        if self.disabled_until > time.time():
            return
        try:
            value = await self._read_async()
            self.last_value = value
            self.last_update = time.time()
            self.buffer.append((self.last_update, value))
            self.failure_count = 0
            self.logger.debug(f"Updated {self.name}: {value}")
        except Exception as e:
            self.failure_count += 1
            self.logger.warning(f"Failed to update {self.name}: {e}")
            if self.failure_count >= self.max_failures:
                self.disabled_until = time.time() + self.failure_backoff
                self.logger.error(f"Disabling {self.name} for {self.failure_backoff}s due to repeated failures")

    async def _read_async(self) -> Any:
        raise NotImplementedError

    def get_latest(self) -> Optional[Any]:
        return self.last_value

    def get_history(self) -> List[Tuple[float, Any]]:
        return list(self.buffer)

class FileSensor(Sensor):
    def __init__(self, name: str, path: str, transform=None, tier='volatile', buffer_size=VOLATILE_BUFFER_SIZE, config=None):
        super().__init__(name, tier, buffer_size, config)
        self.path = path
        self.transform = transform or (lambda x: x)

    async def _read_async(self) -> Any:
        data = await read_sys_file_async(self.path)
        if data is None:
            raise IOError(f"Cannot read {self.path}")
        return self.transform(data)

class CalculatedSensor(Sensor):
    def __init__(self, name: str, calculator_async, tier='volatile', buffer_size=VOLATILE_BUFFER_SIZE, config=None):
        super().__init__(name, tier, buffer_size, config)
        self.calculator_async = calculator_async

    async def _read_async(self) -> Any:
        return await self.calculator_async()

class CPUSensor(CalculatedSensor):
    def __init__(self, config=None):
        super().__init__("cpu", self._calc_cpu_async, tier='volatile', buffer_size=config.volatile_buffer_size if config else VOLATILE_BUFFER_SIZE, config=config)
        self.last_total = 0
        self.last_idle = 0
        self.num_cores = self._get_core_count()
        self.core_buffers = [deque(maxlen=config.volatile_buffer_size if config else VOLATILE_BUFFER_SIZE) for _ in range(self.num_cores)]
        self.last_core_totals = [0] * self.num_cores
        self.last_core_idles = [0] * self.num_cores

    @staticmethod
    def _get_core_count() -> int:
        try:
            with open('/proc/cpuinfo', 'r') as f:
                return sum(1 for line in f if line.startswith('processor'))
        except Exception:
            return os.cpu_count() or 1

    async def _parse_proc_stat_async(self) -> Tuple[int, int, List[Tuple[int, int]]]:
        data = await read_sys_file_async('/proc/stat')
        if not data:
            return 0, 0, []
        total = idle = 0
        cores = []
        for line in data.split('\n'):
            parts = line.split()
            if not parts:
                continue
            if parts[0].startswith('cpu'):
                if parts[0] == 'cpu':
                    nums = list(map(int, parts[1:]))
                    total = sum(nums)
                    idle = nums[3] + nums[4]
                else:
                    nums = list(map(int, parts[1:]))
                    core_total = sum(nums)
                    core_idle = nums[3] + nums[4]
                    cores.append((core_total, core_idle))
        return total, idle, cores

    async def _calc_cpu_async(self) -> Dict[str, Any]:
        total, idle, cores = await self._parse_proc_stat_async()
        now = time.time()
        if self.last_total != 0 and self.last_idle != 0:
            delta_total = total - self.last_total
            delta_idle = idle - self.last_idle
            usage = 100.0 * (1.0 - delta_idle / delta_total) if delta_total else 0.0
        else:
            usage = 0.0
        self.last_total, self.last_idle = total, idle
        core_usages = []
        for i, (core_total, core_idle) in enumerate(cores):
            if i < len(self.last_core_totals):
                last_total = self.last_core_totals[i]
                last_idle = self.last_core_idles[i]
                if last_total != 0 and last_idle != 0:
                    delta_total = core_total - last_total
                    delta_idle = core_idle - last_idle
                    core_usage = 100.0 * (1.0 - delta_idle / delta_total) if delta_total else 0.0
                else:
                    core_usage = 0.0
            else:
                core_usage = 0.0
            core_usages.append(core_usage)
            if i < len(self.last_core_totals):
                self.last_core_totals[i] = core_total
                self.last_core_idles[i] = core_idle
        for i, usage in enumerate(core_usages):
            self.core_buffers[i].append((now, usage))
        return {
            'overall': round(usage, 1),
            'cores': [round(u, 1) for u in core_usages],
            'count': self.num_cores
        }

    def get_core_history(self, core: int) -> List[Tuple[float, float]]:
        if 0 <= core < len(self.core_buffers):
            return list(self.core_buffers[core])
        return []

class MemorySensor(FileSensor):
    def __init__(self, config=None):
        super().__init__("memory", '/proc/meminfo', self._parse_meminfo, tier='volatile', buffer_size=config.volatile_buffer_size if config else VOLATILE_BUFFER_SIZE, config=config)

    @staticmethod
    def _parse_meminfo(data: str) -> Dict[str, Union[int, float]]:
        meminfo = {}
        for line in data.strip().split('\n'):
            parts = line.split(':')
            if len(parts) == 2:
                key = parts[0].strip()
                val_str = parts[1].strip().split()[0]
                try:
                    meminfo[key] = int(val_str) * 1024
                except ValueError:
                    pass
        if 'MemTotal' in meminfo and 'MemAvailable' in meminfo:
            meminfo['UsedPercent'] = 100.0 * (1.0 - meminfo['MemAvailable'] / meminfo['MemTotal'])
        return meminfo

class HwmonSensor(Sensor):
    def __init__(self, config=None):
        super().__init__("hwmon", tier='thermal', buffer_size=config.thermal_buffer_size if config else THERMAL_BUFFER_SIZE, config=config)
        self.sensors: Dict[str, Dict[str, Any]] = {}
        self.last_rescan = 0.0
        self._discover_sync()

    def _discover_sync(self):
        base = '/sys/class/hwmon'
        if not os.path.exists(base):
            return
        for hwmon in os.listdir(base):
            hwmon_path = os.path.join(base, hwmon)
            name_file = os.path.join(hwmon_path, 'name')
            if not os.path.isfile(name_file):
                continue
            try:
                with open(name_file, 'r') as f:
                    chip_name = f.read().strip()
            except Exception:
                continue
            for entry in os.listdir(hwmon_path):
                if entry.startswith('temp') and entry.endswith('_input'):
                    label_file = entry.replace('_input', '_label')
                    label_path = os.path.join(hwmon_path, label_file)
                    label = chip_name
                    if os.path.isfile(label_path):
                        try:
                            with open(label_path, 'r') as f:
                                label = f.read().strip()
                        except Exception:
                            pass
                    full_name = f"{chip_name} - {label}"
                    self.sensors[full_name] = {
                        'type': 'temperature',
                        'path': os.path.join(hwmon_path, entry),
                        'chip': chip_name,
                        'label': label
                    }
                elif entry.startswith('fan') and entry.endswith('_input'):
                    label_file = entry.replace('_input', '_label')
                    label_path = os.path.join(hwmon_path, label_file)
                    label = chip_name
                    if os.path.isfile(label_path):
                        try:
                            with open(label_path, 'r') as f:
                                label = f.read().strip()
                        except Exception:
                            pass
                    full_name = f"{chip_name} - {label}"
                    self.sensors[full_name] = {
                        'type': 'fan',
                        'path': os.path.join(hwmon_path, entry),
                        'chip': chip_name,
                        'label': label
                    }

    async def _rescan_async(self):
        new_sensors = {}
        base = '/sys/class/hwmon'
        if not os.path.exists(base):
            self.sensors = {}
            return
        for hwmon in os.listdir(base):
            hwmon_path = os.path.join(base, hwmon)
            name_file = os.path.join(hwmon_path, 'name')
            if not os.path.isfile(name_file):
                continue
            chip_name = await read_sys_file_async(name_file)
            if chip_name is None:
                continue
            chip_name = chip_name.strip()
            for entry in os.listdir(hwmon_path):
                if entry.startswith('temp') and entry.endswith('_input'):
                    label_file = entry.replace('_input', '_label')
                    label_path = os.path.join(hwmon_path, label_file)
                    label = chip_name
                    label_data = await read_sys_file_async(label_path)
                    if label_data:
                        label = label_data.strip()
                    full_name = f"{chip_name} - {label}"
                    new_sensors[full_name] = {
                        'type': 'temperature',
                        'path': os.path.join(hwmon_path, entry),
                        'chip': chip_name,
                        'label': label
                    }
                elif entry.startswith('fan') and entry.endswith('_input'):
                    label_file = entry.replace('_input', '_label')
                    label_path = os.path.join(hwmon_path, label_file)
                    label = chip_name
                    label_data = await read_sys_file_async(label_path)
                    if label_data:
                        label = label_data.strip()
                    full_name = f"{chip_name} - {label}"
                    new_sensors[full_name] = {
                        'type': 'fan',
                        'path': os.path.join(hwmon_path, entry),
                        'chip': chip_name,
                        'label': label
                    }
        self.sensors = new_sensors
        self.last_rescan = time.time()

    async def _read_async(self) -> Dict[str, float]:
        if time.time() - self.last_rescan > (self.config.rescan_interval if self.config else RESCAN_INTERVAL):
            await self._rescan_async()
        result = {}
        for name, info in self.sensors.items():
            raw = await read_sys_file_async(info['path'])
            if raw is None:
                continue
            try:
                value = int(raw)
                if info['type'] == 'temperature':
                    value = value / 1000.0
                result[name] = value
            except Exception:
                pass
        return result

class DiskSensor(Sensor):
    def __init__(self, config=None):
        super().__init__("disk", tier='static', buffer_size=config.static_buffer_size if config else STATIC_BUFFER_SIZE, config=config)
        self.disks = []
        self.last_rescan = 0.0
        self._discover_sync()

    def _discover_sync(self):
        try:
            with open('/proc/diskstats', 'r') as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 3:
                        name = parts[2]
                        if not name.startswith(('loop', 'ram', 'sr')) and not name[-1].isdigit():
                            self.disks.append(name)
        except Exception:
            pass

    async def _rescan_async(self):
        loop = asyncio.get_running_loop()
        def _update():
            disks = []
            try:
                with open('/proc/diskstats', 'r') as f:
                    for line in f:
                        parts = line.split()
                        if len(parts) >= 3:
                            name = parts[2]
                            if not name.startswith(('loop', 'ram', 'sr')) and not name[-1].isdigit():
                                disks.append(name)
            except Exception:
                pass
            return disks
        self.disks = await loop.run_in_executor(None, _update)
        self.last_rescan = time.time()

    async def _read_async(self) -> Dict[str, Dict[str, int]]:
        if time.time() - self.last_rescan > (self.config.rescan_interval if self.config else RESCAN_INTERVAL):
            await self._rescan_async()
        data = await read_sys_file_async('/proc/diskstats')
        if not data:
            return {}
        result = {}
        for line in data.split('\n'):
            parts = line.split()
            if len(parts) < 14:
                continue
            name = parts[2]
            if name in self.disks:
                result[name] = {
                    'reads': int(parts[3]),
                    'reads_merged': int(parts[4]),
                    'sectors_read': int(parts[5]),
                    'read_time': int(parts[6]),
                    'writes': int(parts[7]),
                    'writes_merged': int(parts[8]),
                    'sectors_written': int(parts[9]),
                    'write_time': int(parts[10]),
                    'io_in_progress': int(parts[11]),
                    'io_time': int(parts[12]),
                    'weighted_io_time': int(parts[13])
                }
        return result

class BatterySensor(Sensor):
    def __init__(self, config=None):
        super().__init__("battery", tier='static', buffer_size=config.static_buffer_size if config else STATIC_BUFFER_SIZE, config=config)
        self.batteries = []
        self.last_rescan = 0.0
        self._discover_sync()

    def _discover_sync(self):
        base = '/sys/class/power_supply'
        if os.path.exists(base):
            for name in os.listdir(base):
                if name.startswith('BAT'):
                    self.batteries.append(name)

    async def _rescan_async(self):
        loop = asyncio.get_running_loop()
        def _update():
            bats = []
            base = '/sys/class/power_supply'
            if os.path.exists(base):
                for name in os.listdir(base):
                    if name.startswith('BAT'):
                        bats.append(name)
            return bats
        self.batteries = await loop.run_in_executor(None, _update)
        self.last_rescan = time.time()

    async def _read_async(self) -> Dict[str, Dict[str, Any]]:
        if time.time() - self.last_rescan > (self.config.rescan_interval if self.config else RESCAN_INTERVAL):
            await self._rescan_async()
        result = {}
        for bat in self.batteries:
            bat_path = os.path.join('/sys/class/power_supply', bat)
            capacity = await read_sys_file_async(os.path.join(bat_path, 'capacity'))
            if capacity is None:
                continue
            status = await read_sys_file_async(os.path.join(bat_path, 'status'))
            entry = {'capacity': int(capacity), 'status': status.strip() if status else "Unknown"}
            for attr in ['voltage_now', 'current_now', 'power_now']:
                val = await read_sys_file_async(os.path.join(bat_path, attr))
                if val:
                    entry[attr] = int(val)
            result[bat] = entry
        return result

class NvidiaSensor(Sensor):
    def __init__(self, config=None):
        super().__init__("nvidia_gpu", tier='thermal', buffer_size=config.thermal_buffer_size if config else THERMAL_BUFFER_SIZE, config=config)
        self.available = False
        self.ready = False
        self._init_task = None
        if NVML_AVAILABLE:
            self._init_task = asyncio.create_task(self._init_nvml_async())

    async def _init_nvml_async(self):
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, self._init_nvml_sync)
            self.ready = True
            self.available = True
        except Exception:
            pass
        finally:
            self._init_task = None

    def _init_nvml_sync(self):
        pynvml.nvmlInit()
        self.device_count = pynvml.nvmlDeviceGetCount()
        self.handles = [pynvml.nvmlDeviceGetHandleByIndex(i) for i in range(self.device_count)]

    async def _read_async(self) -> Optional[Dict[int, Dict[str, Any]]]:
        if not self.ready:
            return None
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._read_nvml_sync)

    def _read_nvml_sync(self) -> Dict[int, Dict[str, Any]]:
        result = {}
        for i, handle in enumerate(self.handles):
            try:
                name = pynvml.nvmlDeviceGetName(handle).decode()
                temp = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
                util = pynvml.nvmlDeviceGetUtilizationRates(handle)
                mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
                result[i] = {
                    'name': name,
                    'temperature': temp,
                    'gpu_util': util.gpu,
                    'memory_util': util.memory,
                    'memory_used': mem_info.used,
                    'memory_total': mem_info.total,
                }
            except Exception:
                pass
        return result

class PsutilCPUSensor(Sensor):
    def __init__(self, config=None):
        super().__init__("cpu_psutil", tier='volatile', buffer_size=config.volatile_buffer_size if config else VOLATILE_BUFFER_SIZE, config=config)
        self.available = PSUTIL_AVAILABLE

    @staticmethod
    def _get_cpu_percent():
        return psutil.cpu_percent(interval=None, percpu=True)

    async def _read_async(self) -> Optional[Dict]:
        if not self.available:
            return None
        loop = asyncio.get_running_loop()
        percents = await loop.run_in_executor(None, self._get_cpu_percent)
        return {
            'overall': sum(percents) / len(percents),
            'cores': percents,
            'count': len(percents)
        }

class PsutilMemorySensor(Sensor):
    def __init__(self, config=None):
        super().__init__("memory_psutil", tier='volatile', buffer_size=config.volatile_buffer_size if config else VOLATILE_BUFFER_SIZE, config=config)
        self.available = PSUTIL_AVAILABLE

    @staticmethod
    def _get_memory():
        return psutil.virtual_memory()

    async def _read_async(self) -> Optional[Dict]:
        if not self.available:
            return None
        loop = asyncio.get_running_loop()
        mem = await loop.run_in_executor(None, self._get_memory)
        return {
            'total': mem.total,
            'available': mem.available,
            'used': mem.used,
            'percent': mem.percent,
        }

class TelemetryCollector:
    def __init__(self, config: Config):
        self.config = config
        self.sensors: Dict[str, Sensor] = {}
        self.volatile_sensors: List[Sensor] = []
        self.thermal_sensors: List[Sensor] = []
        self.static_sensors: List[Sensor] = []
        self.last_volatile_poll = 0.0
        self.last_thermal_poll = 0.0
        self.last_static_poll = 0.0
        self._create_sensors()

    def _create_sensors(self):
        try:
            self._add_sensor(CPUSensor(self.config))
        except Exception:
            pass
        try:
            self._add_sensor(MemorySensor(self.config))
        except Exception:
            pass
        try:
            self._add_sensor(HwmonSensor(self.config))
        except Exception:
            pass
        try:
            self._add_sensor(DiskSensor(self.config))
        except Exception:
            pass
        try:
            self._add_sensor(BatterySensor(self.config))
        except Exception:
            pass
        if NVML_AVAILABLE:
            try:
                self._add_sensor(NvidiaSensor(self.config))
            except Exception:
                pass
        try:
            self._add_sensor(PsutilCPUSensor(self.config))
        except Exception:
            pass
        try:
            self._add_sensor(PsutilMemorySensor(self.config))
        except Exception:
            pass

    def _add_sensor(self, sensor: Sensor):
        self.sensors[sensor.name] = sensor
        if sensor.tier == 'volatile':
            self.volatile_sensors.append(sensor)
        elif sensor.tier == 'thermal':
            self.thermal_sensors.append(sensor)
        elif sensor.tier == 'static':
            self.static_sensors.append(sensor)

    async def poll_once(self):
        now = time.time()
        tasks = []
        if now - self.last_volatile_poll >= self.config.volatile_interval:
            for sensor in self.volatile_sensors:
                tasks.append(sensor.update())
            self.last_volatile_poll = now
        if now - self.last_thermal_poll >= self.config.thermal_interval:
            for sensor in self.thermal_sensors:
                tasks.append(sensor.update())
            self.last_thermal_poll = now
        if now - self.last_static_poll >= self.config.static_interval:
            for sensor in self.static_sensors:
                tasks.append(sensor.update())
            self.last_static_poll = now
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def run_periodic(self):
        while True:
            await self.poll_once()
            await asyncio.sleep(0.1)

    def get_snapshot(self) -> Dict[str, Any]:
        snapshot = {}
        for name, sensor in self.sensors.items():
            latest = sensor.get_latest()
            if latest is not None:
                snapshot[name] = latest
        return snapshot

    def get_sensor_names(self) -> List[str]:
        return list(self.sensors.keys())

    def get_history(self, sensor_name: str) -> Optional[List[Tuple[float, Any]]]:
        sensor = self.sensors.get(sensor_name)
        if sensor:
            return sensor.get_history()
        return None

    def get_core_history(self, core: int) -> Optional[List[Tuple[float, float]]]:
        cpu_sensor = self.sensors.get('cpu')
        if isinstance(cpu_sensor, CPUSensor):
            return cpu_sensor.get_core_history(core)
        return None

class TelemetryServer:
    def __init__(self, collector: TelemetryCollector, socket_path: str, serializer: FramedSerializer, token: Optional[str]):
        self.collector = collector
        self.socket_path = socket_path
        self.serializer = serializer
        self.token = token
        self.server = None
        self.logger = logging.getLogger("thermoguard.server")

    async def authenticate(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> bool:
        if not self.token:
            return True
        try:
            msg = await self.serializer.decode(reader)
            if msg and msg.get("token") == self.token:
                return True
            else:
                return False
        except Exception:
            return False

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        peer = writer.get_extra_info('peername')
        self.logger.info(f"Client connected: {peer}")
        try:
            if not await self.authenticate(reader, writer):
                writer.write(self.serializer.encode({"error": "Authentication failed"}))
                await writer.drain()
                writer.close()
                return
            while True:
                request = await self.serializer.decode(reader)
                if request is None:
                    break
                response = await self.process_command(request)
                writer.write(self.serializer.encode(response))
                await writer.drain()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.logger.error(f"Error handling client {peer}: {e}")
        finally:
            writer.close()
            await writer.wait_closed()
            self.logger.info(f"Client disconnected: {peer}")

    async def process_command(self, request: Any) -> Dict:
        if not isinstance(request, dict):
            return {"error": "Request must be a JSON object"}
        command = request.get("command", "")
        if command == "get_all":
            return {"status": "ok", "data": self.collector.get_snapshot()}
        elif command == "get_sensors":
            return {"status": "ok", "sensors": self.collector.get_sensor_names()}
        elif command == "get_history":
            sensor = request.get("sensor")
            if not sensor:
                return {"error": "Missing 'sensor' field"}
            history = self.collector.get_history(sensor)
            if history is not None:
                return {"status": "ok", "history": history}
            else:
                return {"error": f"Sensor '{sensor}' not found"}
        elif command == "get_core_history":
            core = request.get("core")
            if core is None:
                return {"error": "Missing 'core' field"}
            try:
                core = int(core)
                history = self.collector.get_core_history(core)
                if history is not None:
                    return {"status": "ok", "history": history}
                else:
                    return {"error": f"Core '{core}' history not available"}
            except ValueError:
                return {"error": "Core must be integer"}
        else:
            return {"error": f"Unknown command '{command}'"}

    async def start(self):
        try:
            os.unlink(self.socket_path)
        except OSError:
            pass
        self.server = await asyncio.start_unix_server(
            self.handle_client, path=self.socket_path
        )
        os.chmod(self.socket_path, 0o600)
        self.logger.info(f"Server listening on {self.socket_path}")
        async with self.server:
            await self.server.serve_forever()

    async def shutdown(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        try:
            os.unlink(self.socket_path)
        except OSError:
            pass
        self.logger.info("Server shut down")

def cleanup_socket(path: str):
    try:
        os.unlink(path)
    except OSError:
        pass

async def main_async(args):
    config = Config.load()
    if args.socket:
        config.socket_path = args.socket
    if args.binary:
        config.binary = args.binary
    if args.debug:
        config.debug = args.debug
    if args.token:
        config.token = args.token
    setup_logging(config.debug)
    logger = logging.getLogger("thermoguard")
    token = None
    if config.token:
        token = load_token()
        if not token:
            logger.error("Failed to load/generate token. Exiting.")
            sys.exit(1)
        logger.info("Authentication token loaded.")
    serializer = FramedSerializer(binary=config.binary)
    collector = TelemetryCollector(config)
    server = TelemetryServer(collector, config.socket_path, serializer, token)
    atexit.register(cleanup_socket, config.socket_path)
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    def signal_handler():
        logger.info("Received stop signal, shutting down...")
        stop_event.set()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, signal_handler)
    try:
        collector_task = asyncio.create_task(collector.run_periodic())
        server_task = asyncio.create_task(server.start())
        await stop_event.wait()
        collector_task.cancel()
        server_task.cancel()
        await asyncio.gather(collector_task, server_task, return_exceptions=True)
    finally:
        await server.shutdown()
        logger.info("Daemon stopped.")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", help="Unix socket path")
    parser.add_argument("--binary", action="store_true", help="Use MessagePack binary serialization")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument("--token", action="store_true", help="Enable token authentication")
    args = parser.parse_args()
    if args.binary and not MSGPACK_AVAILABLE:
        print("MessagePack not installed. Please install with: pip install msgpack", file=sys.stderr)
        sys.exit(1)
    if os.geteuid() != 0:
        print("Warning: Not running as root – some sensors may be inaccessible.", file=sys.stderr)
        print("Consider using capabilities: setcap cap_dac_read_search+ep ./thermoguard.py", file=sys.stderr)
    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("Keyboard interrupt, exiting.")
    except Exception:
        logging.exception("Unhandled exception")
        sys.exit(1)

if __name__ == "__main__":
    main()
