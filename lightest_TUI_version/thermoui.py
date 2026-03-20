#!/usr/bin/env python3
"""
#################################################
this script fully build with generative AI
#################################################
"""
import curses
import curses.textpad
import json
import socket
import time
import argparse
import os
import threading
from typing import Optional, Dict, Any, List

DEFAULT_SOCKET_PATH = "/tmp/thermoguard.sock"
DEFAULT_TOKEN_PATH = os.path.expanduser("~/.local/share/thermoguard/thermoguard.token")
POLL_INTERVAL = 1.0
COLOR_HEADER = 1
COLOR_NORMAL = 2
COLOR_HIGH = 3
COLOR_BAR = 4
class FramedSocketClient:
    def __init__(self, socket_path: str, token: Optional[str] = None):
        self.socket_path = socket_path
        self.token = token
        self.sock: Optional[socket.socket] = None
    def connect(self) -> bool:
        try:
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.connect(self.socket_path)
            self.sock.settimeout(POLL_INTERVAL / 2)
            if self.token:
                self._send({"token": self.token})
                resp = self._receive()
                if resp and resp.get("error") == "Authentication failed":
                    self.close()
                    return False
            return True
        except Exception:
            self.close()
            return False
    def _send(self, obj: Dict) -> None:
        data = json.dumps(obj).encode('utf-8')
        length = len(data).to_bytes(4, 'big')
        self.sock.sendall(length + data)
    def _receive(self) -> Optional[Dict]:
        try:
            raw_len = self.sock.recv(4)
            if len(raw_len) < 4:
                return None
            length = int.from_bytes(raw_len, 'big')
            data = b''
            while len(data) < length:
                chunk = self.sock.recv(length - len(data))
                if not chunk:
                    return None
                data += chunk
            return json.loads(data.decode('utf-8'))
        except socket.timeout:
            return None
        except Exception:
            return None
    def get_snapshot(self) -> Optional[Dict]:
        if not self.sock:
            return None
        self._send({"command": "get_all"})
        return self._receive()
    def close(self):
        if self.sock:
            self.sock.close()
            self.sock = None
class DataFetcher(threading.Thread):
    def __init__(self, socket_path: str, token: Optional[str]):
        super().__init__(daemon=True)
        self.socket_path = socket_path
        self.token = token
        self.lock = threading.Lock()
        self.snapshot: Dict[str, Any] = {}
        self.connected = False
        self.running = True

    def run(self):
        client = FramedSocketClient(self.socket_path, self.token)
        while self.running:
            if not client.sock:
                if not client.connect():
                    time.sleep(2)
                    continue
                with self.lock:
                    self.connected = True

            snapshot = client.get_snapshot()
            if snapshot is None:
                client.close()
                with self.lock:
                    self.connected = False
                    self.snapshot = {}
                time.sleep(1)
                continue

            with self.lock:
                self.snapshot = snapshot
            time.sleep(0.5)

    def stop(self):
        self.running = False

    def get_data(self) -> Dict[str, Any]:
        with self.lock:
            return self.snapshot.copy()

    def is_connected(self) -> bool:
        with self.lock:
            return self.connected
class TUI:
    def __init__(self, stdscr, fetcher: DataFetcher):
        self.stdscr = stdscr
        self.fetcher = fetcher
        self.height, self.width = stdscr.getmaxyx()
        self.pad_top = 0
        self.init_colors()

    def init_colors(self):
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(COLOR_HEADER, curses.COLOR_CYAN, -1)
        curses.init_pair(COLOR_NORMAL, curses.COLOR_WHITE, -1)
        curses.init_pair(COLOR_HIGH, curses.COLOR_RED, -1)
        curses.init_pair(COLOR_BAR, curses.COLOR_GREEN, -1)

    def draw(self):
        self.stdscr.erase()
        self.height, self.width = self.stdscr.getmaxyx()

        resp = self.fetcher.get_data()
        connected = self.fetcher.is_connected()
        if resp and resp.get("status") == "ok":
            data = resp.get("data", {})
        else:
            data = {}
        header = " ThermoGuard V5 "
        if connected:
            header += " [Connected] "
        else:
            header += " [DISCONNECTED – reconnecting...] "
        self.draw_header(header)

        y = 2
        if not data:
            self.stdscr.addstr(y, 2, "Waiting for data...", curses.color_pair(COLOR_NORMAL))
        else:
            y = self.draw_cpu(data, y)
            y = self.draw_memory(data, y)
            y = self.draw_thermal(data, y)
            y = self.draw_disk(data, y)
            y = self.draw_battery(data, y)
        self.draw_footer()
        self.stdscr.refresh()
    def draw_header(self, text: str):
        x = max(0, (self.width - len(text)) // 2)
        self.stdscr.addstr(0, x, text, curses.color_pair(COLOR_HEADER) | curses.A_BOLD)
        if self.width > 0:
            self.stdscr.addstr(1, 0, "─" * self.width, curses.color_pair(COLOR_HEADER))

    def draw_footer(self):
        footer = " [q] quit | [r] refresh "
        x = max(0, (self.width - len(footer)) // 2)
        self.stdscr.addstr(self.height - 1, x, footer, curses.color_pair(COLOR_NORMAL))
    def draw_cpu(self, data: Dict, y: int) -> int:
        cpu_info = data.get("cpu") or data.get("cpu_psutil")
        if not cpu_info:
            self.stdscr.addstr(y, 2, "CPU: No data", curses.color_pair(COLOR_NORMAL))
            return y + 2

        overall = cpu_info.get("overall", 0)
        cores = cpu_info.get("cores", [])
        count = cpu_info.get("count", len(cores))
        self.stdscr.addstr(y, 2, "CPU Total: ", curses.color_pair(COLOR_NORMAL))
        self.draw_bar(y, 13, overall, 100, width=self.width - 15)
        y += 1
        max_cores = min(len(cores), self.height - y - 10)
        for i in range(max_cores):
            core_usage = cores[i]
            self.stdscr.addstr(y, 2, f"Core {i:2d}: ", curses.color_pair(COLOR_NORMAL))
            self.draw_bar(y, 10, core_usage, 100, width=self.width - 13)
            y += 1
        if len(cores) > max_cores:
            self.stdscr.addstr(y, 2, f"... and {len(cores) - max_cores} more cores", curses.color_pair(COLOR_NORMAL))
            y += 1
        y += 1
        return y
    def draw_memory(self, data: Dict, y: int) -> int:
        mem_info = data.get("memory") or data.get("memory_psutil")
        if not mem_info:
            self.stdscr.addstr(y, 2, "Memory: No data", curses.color_pair(COLOR_NORMAL))
            return y + 2
        total = mem_info.get("MemTotal", 0) / (1024**3)
        available = mem_info.get("MemAvailable", 0) / (1024**3)
        used = total - available
        percent = mem_info.get("UsedPercent", 0) or (used / total * 100 if total else 0)
        self.stdscr.addstr(y, 2, f"Memory: {used:.1f} GB / {total:.1f} GB ({percent:.1f}%)", curses.color_pair(COLOR_NORMAL))
        self.draw_bar(y, 10, percent, 100, width=self.width - 12)
        y += 2
        return y

    def draw_thermal(self, data: Dict, y: int) -> int:
        hwmon = data.get("hwmon", {})
        if not hwmon:
            return y

        self.stdscr.addstr(y, 2, "Temperatures:", curses.color_pair(COLOR_NORMAL) | curses.A_BOLD)
        y += 1
        count = 0
        for name, value in hwmon.items():
            if count >= 5:
                self.stdscr.addstr(y, 4, "...", curses.color_pair(COLOR_NORMAL))
                y += 1
                break
            self.stdscr.addstr(y, 4, f"{name[:20]:20}: {value:.1f} °C", curses.color_pair(COLOR_NORMAL))
            y += 1
            count += 1
        y += 1
        return y
    def draw_disk(self, data: Dict, y: int) -> int:
        disk = data.get("disk", {})
        if not disk:
            return y
        self.stdscr.addstr(y, 2, "Disk I/O:", curses.color_pair(COLOR_NORMAL) | curses.A_BOLD)
        y += 1
        count = 0
        for name, stats in disk.items():
            if count >= 3:
                self.stdscr.addstr(y, 4, "...", curses.color_pair(COLOR_NORMAL))
                y += 1
                break
            reads = stats.get('reads', 0)
            writes = stats.get('writes', 0)
            self.stdscr.addstr(y, 4, f"{name}: R {reads}  W {writes}", curses.color_pair(COLOR_NORMAL))
            y += 1
            count += 1
        y += 1
        return y

    def draw_battery(self, data: Dict, y: int) -> int:
        battery = data.get("battery", {})
        if not battery:
            return y

        self.stdscr.addstr(y, 2, "Battery:", curses.color_pair(COLOR_NORMAL) | curses.A_BOLD)
        y += 1
        for name, info in battery.items():
            cap = info.get('capacity', 0)
            status = info.get('status', 'Unknown')
            self.stdscr.addstr(y, 4, f"{name}: {cap}% ({status})", curses.color_pair(COLOR_NORMAL))
            y += 1
        y += 1
        return y
    def draw_bar(self, y: int, x: int, value: float, max_val: float, width: int = 20):
        """Draw a horizontal progress bar at (y, x)."""
        bar_width = width - 2  # leave space for brackets and percentage
        filled = int(bar_width * value / max_val)
        bar = "[" + "█" * filled + " " * (bar_width - filled) + "]"
        percent_str = f"{value:.1f}%"
        # Truncate if needed
        if len(bar) + len(percent_str) + 1 > width:
            bar = bar[:width - len(percent_str) - 2] + "…"
        self.stdscr.addstr(y, x, bar, curses.color_pair(COLOR_BAR))
        self.stdscr.addstr(y, x + len(bar) + 1, percent_str, curses.color_pair(COLOR_NORMAL))
def main(stdscr, args):
    curses.curs_set(0)
    stdscr.nodelay(1)
    stdscr.timeout(500)
    fetcher = DataFetcher(args.socket, args.token)
    fetcher.start()
    tui = TUI(stdscr, fetcher)
    while True:
        tui.draw()
        key = stdscr.getch()
        if key in (ord('q'), ord('Q'), 27):
            break
        elif key == ord('r'):
            pass
    fetcher.stop()
    fetcher.join(timeout=1)
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ThermoGuard Terminal UI")
    parser.add_argument("--socket", default=DEFAULT_SOCKET_PATH, help="Unix socket path")
    parser.add_argument("--token", help="Authentication token (if not provided, try to read from default file)")
    args = parser.parse_args()
    if args.token is None:
        if os.path.exists(DEFAULT_TOKEN_PATH):
            with open(DEFAULT_TOKEN_PATH, 'r') as f:
                args.token = f.read().strip()
    curses.wrapper(main, args)
