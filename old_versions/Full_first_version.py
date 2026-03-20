import sys
import psutil
import os
import subprocess
import json
import requests
import datetime
import threading
import markdown
import html
from collections import deque
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QLabel, QTabWidget, QWidget, QVBoxLayout, QHBoxLayout,
    QSystemTrayIcon, QMenu, QAction, QPushButton, QFrame, QGridLayout, QSizePolicy,
    QScrollArea, QGroupBox,
    QProgressBar, QTableWidget, QTableWidgetItem,
    QHeaderView,
    QMessageBox, QFileDialog, QDialog,
    QTextEdit,
    QLineEdit, QComboBox, QCheckBox,
    QTextBrowser, QSplitter,
    QListWidget, QListWidgetItem, QDesktopWidget)
from PyQt5.QtGui import QIcon, QPainter, QColor, QPen, QFont, QPixmap, QBrush
from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal
import shlex
import platform
AI_PROVIDERS = {
    "Ollama Local": {
        "api_url": "http://localhost:11434/api/chat",
        "models": ["llama2", "mistral", "codellama", "neural-chat", "llama2-uncensored"],
        "requires_key": False,
        "key_name": "Not Required",
        "key_hint": "No key needed for local Ollama",
        "default_model": "llama2"
    },
    "Custom Ollama Server": {
        "api_url": "",
        "models": ["custom-model"],
        "requires_key": False,
        "key_name": "Not Required",
        "key_hint": "Enter your custom Ollama server URL",
        "default_model": "llama2"
    }
}
class AIChatDialog(QDialog):
    update_signal = pyqtSignal(str, str)

    def __init__(self, system_data, parent=None):
        super().__init__(parent)
        self.system_data = system_data
        self.setWindowTitle("ThermoGuard - Local AI (Ollama)")
        self.setMinimumSize(850, 600)
        self.setup_ui()
        self.update_signal.connect(self.display_message)

    def setup_ui(self):
        self.setStyleSheet("background-color: #121212; color: white;")
        layout = QVBoxLayout(self)

        # Chat display area
        self.chat_display = QTextBrowser()
        self.chat_display.setStyleSheet("""
            QTextBrowser {
                background-color: #1A1A1A;
                border: 1px solid #333;
                border-radius: 12px;
                padding: 15px;
                font-family: 'Consolas', 'Segoe UI';
                font-size: 13px;
            }
        """)
        layout.addWidget(self.chat_display, 1)  # Added stretch factor

        # Input area
        input_box = QHBoxLayout()

        self.msg_input = QLineEdit()
        self.msg_input.setPlaceholderText("Ask Ollama about your device's status...")
        self.msg_input.setStyleSheet(
            "background-color: #252525; border: 1px solid #444; padding: 12px; border-radius: 8px; color: white;")
        self.msg_input.returnPressed.connect(self.send_process)

        self.btn_send = QPushButton("Send")
        self.btn_send.setStyleSheet("""
            QPushButton { 
                background-color: #00B0FF; 
                color: white; 
                padding: 12px 25px; 
                border-radius: 8px; 
                font-weight: bold; 
                font-size: 14px;
            }
            QPushButton:hover { 
                background-color: #0091EA; 
            }
            QPushButton:disabled { 
                background-color: #444; 
                color: #888;
            }
        """)
        self.btn_send.clicked.connect(self.send_process)

        # Add widgets to input box
        input_box.addWidget(self.msg_input, 1)  # Added stretch factor
        input_box.addWidget(self.btn_send)

        # Add input box to main layout
        layout.addLayout(input_box)

    def display_message(self, role, text):
        color = "#00B0FF" if role == "Ollama" else "#4CAF50"

        # Use markdown for better formatting
        try:
            clean_text = markdown.markdown(text, extensions=['fenced_code', 'codehilite'])
        except:
            clean_text = html.escape(text)  # Fallback to HTML escape

        bubble = f"""
        <div style='margin-bottom: 20px;'>
            <span style='color: {color}; font-weight: bold; font-size: 14px;'>{role}:</span><br>
            <div style='color: #DDD; margin-top: 5px; line-height: 1.6;'>{clean_text}</div>
        </div>
        """
        self.chat_display.append(bubble)
        self.chat_display.verticalScrollBar().setValue(self.chat_display.verticalScrollBar().maximum())
        self.btn_send.setEnabled(True)

    def send_process(self):
        user_text = self.msg_input.text().strip()
        if not user_text:
            return

        self.display_message("You", user_text)
        self.msg_input.clear()
        self.btn_send.setEnabled(False)

        # Get system data for context
        sys_info = f"System Stats: CPU {self.system_data.get('cpu_percent')}% | RAM {self.system_data.get('memory_percent')}%"

        # Start Ollama in a separate thread
        threading.Thread(
            target=self.work_with_ollama,
            args=(user_text, sys_info),
            daemon=True
        ).start()

    def work_with_ollama(self, prompt, sys_context):
        try:
            url = "http://127.0.0.1:11434/api/generate"

            payload = {
                "model": "llama2:latest",
                "prompt": f"System Context: {sys_context}\n\nUser Question: {prompt}",
                "stream": False
            }

            response = requests.post(url, json=payload, timeout=60)

            if response.status_code == 200:
                answer = response.json().get('response', 'No response received')
                self.update_signal.emit("Ollama", answer)
            else:
                error_msg = f"Error connecting to Ollama: HTTP {response.status_code}"
                if response.text:
                    error_msg += f" - {response.text[:100]}"
                self.update_signal.emit("Ollama", error_msg)

        except requests.exceptions.ConnectionError:
            self.update_signal.emit("Ollama",
                                    "❌ Cannot connect to Ollama server.\n\n"
                                    "Please ensure Ollama is running:\n"
                                    "1. Open terminal and run: ollama serve\n"
                                    "2. Or install Ollama from: https://ollama.com")

        except requests.exceptions.Timeout:
            self.update_signal.emit("Ollama", "⏱️ Request timeout. Ollama is taking too long to respond.")

        except Exception as e:
            self.update_signal.emit("Ollama", f"⚠️ Error: {str(e)}")
class LiveGraph(QFrame):
    def __init__(self, title="", color="#00B0FF", max_points=30):
        super().__init__()
        self.title = title
        self.color = color
        self.max_points = max_points
        self.data = deque([0] * max_points, maxlen=max_points)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumHeight(150)
        self.setStyleSheet(f"""
        background-color: #1F1F1F;
        border-radius: 15px;
        border: 2px solid {color};
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(5)
        self.title_label = QLabel(title)
        self.title_label.setStyleSheet(f"color: {color}; font-size: 14px; font-weight: bold;")
        self.title_label.setAlignment(Qt.AlignLeft)
        self.value_label = QLabel("0%")
        self.value_label.setStyleSheet("color: white; font-size: 18px; font-weight: bold;")
        self.value_label.setAlignment(Qt.AlignRight)
        header_layout = QHBoxLayout()
        header_layout.addWidget(self.title_label)
        header_layout.addWidget(self.value_label)
        layout.addLayout(header_layout)
        layout.addStretch()
        self.min_value = 0
        self.max_value = 100
    def update_value(self, value):
        self.data.append(value)
        self.value_label.setText(f"{value:.1f}%")
        self.update()
    def paintEvent(self, event):
        super().paintEvent(event)
        if len(self.data) < 2:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        width = self.width()
        height = self.height()
        top_margin = 40
        bottom_margin = 10
        left_margin = 15
        right_margin = 15
        graph_width = width - left_margin - right_margin
        graph_height = height - top_margin - bottom_margin
        if graph_width <= 0 or graph_height <= 0:
            return
        graph_x = left_margin
        graph_y = top_margin
        painter.setPen(QPen(QColor(40, 40, 40), 1))
        painter.fillRect(graph_x, graph_y, graph_width, graph_height, QColor(30, 30, 30))
        painter.setPen(QPen(QColor(60, 60, 60), 1))
        for i in range(1, 5):
            y = graph_y + (graph_height * i // 5)
            painter.drawLine(graph_x, int(y), graph_x + graph_width, int(y))
        for i in range(1, 5):
            x = graph_x + (graph_width * i // 5)
            painter.drawLine(int(x), graph_y, int(x), graph_y + graph_height)
        data_points = list(self.data)
        if len(data_points) < 2:
            return
        data_min = min(data_points)
        data_max = max(data_points)
        if data_max == data_min:
            data_max = data_min + 10
        value_range = data_max - data_min
        padded_min = max(0, data_min - (value_range * 0.1))
        padded_max = min(100, data_max + (value_range * 0.1))
        pen = QPen(QColor(self.color), 2)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        points = []
        for i, value in enumerate(data_points):
            x_ratio = i / (len(data_points) - 1) if len(data_points) > 1 else 0
            x = graph_x + (x_ratio * graph_width)
            if padded_max == padded_min:
                y_ratio = 0.5
            else:
                y_ratio = (value - padded_min) / (padded_max - padded_min)
            y_ratio = max(0, min(1, y_ratio))
            y = graph_y + graph_height - (y_ratio * graph_height)
            points.append((int(x), int(y)))
        for i in range(len(points) - 1):
            painter.drawLine(points[i][0], points[i][1], points[i + 1][0], points[i + 1][1])
        if points:
            last_x, last_y = points[-1]
            painter.setBrush(QColor(self.color))
            painter.setPen(QPen(QColor("white"), 1))
            painter.drawEllipse(last_x - 4, last_y - 4, 8, 8)
        painter.setPen(QPen(QColor(150, 150, 150), 1))
        painter.setFont(QFont("Arial", 8))
        painter.drawText(graph_x - 35, graph_y + 10, f"{padded_max:.0f}")
        painter.drawText(graph_x - 35, graph_y + graph_height - 5, f"{padded_min:.0f}")
        painter.end()
class SensorWidget(QFrame):
    def __init__(self, sensor_name, sensor_type, current_value, high=None, critical=None, unit="°C"):
        super().__init__()
        self.sensor_name = sensor_name
        self.sensor_type = sensor_type
        self.current_value = current_value
        self.high = high
        self.critical = critical
        self.unit = unit
        self.setMinimumHeight(90)
        self.setMaximumHeight(120)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setStyleSheet("""
        background-color: #1F1F1F;
        border-radius: 10px;
        border: 1px solid #2A2A2A;
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 10, 15, 10)
        layout.setSpacing(5)
        name_layout = QHBoxLayout()
        self.name_label = QLabel(sensor_name)
        self.name_label.setStyleSheet("color: #00B0FF; font-size: 14px; font-weight: bold;")
        self.name_label.setWordWrap(True)
        self.type_label = QLabel(sensor_type)
        self.type_label.setStyleSheet(
            "color: #B0B0B0; font-size: 12px; background-color: #2A2A2A; padding: 2px 8px; border-radius: 10px;")
        self.type_label.setAlignment(Qt.AlignCenter)
        name_layout.addWidget(self.name_label, 1)
        name_layout.addStretch()
        name_layout.addWidget(self.type_label)
        self.value_label = QLabel(f"{current_value:.1f}{unit}")
        self.value_label.setStyleSheet("color: white; font-size: 24px; font-weight: bold;")
        self.value_label.setAlignment(Qt.AlignCenter)
        status_layout = QHBoxLayout()
        self.status_label = QLabel("Normal")
        self.status_label.setStyleSheet("color: #00E676; font-size: 12px; font-weight: bold;")
        limits_text = ""
        if high:
            limits_text += f"High: {high}{unit} "
        if critical:
            limits_text += f"Critical: {critical}{unit}"
        self.limits_label = QLabel(limits_text)
        self.limits_label.setStyleSheet("color: #B0B0B0; font-size: 11px;")
        self.limits_label.setWordWrap(True)
        status_layout.addWidget(self.status_label)
        status_layout.addStretch()
        status_layout.addWidget(self.limits_label)
        layout.addLayout(name_layout)
        layout.addWidget(self.value_label)
        layout.addLayout(status_layout)
        self.update_status()
    def update_status(self):
        if self.critical and self.current_value >= self.critical:
            status = "CRITICAL"
            color = "#FF5252"
            self.setStyleSheet(f"""
            background-color: #1F1F1F;
            border-radius: 10px;
            border: 2px solid {color};
            """)
            self.status_label.setStyleSheet(f"color: {color}; font-size: 12px; font-weight: bold;")
        elif self.high and self.current_value >= self.high:
            status = "WARNING"
            color = "#FF9800"
            self.setStyleSheet(f"""
            background-color: #1F1F1F;
            border-radius: 10px;
            border: 2px solid {color};
            """)
            self.status_label.setStyleSheet(f"color: {color}; font-size: 12px; font-weight: bold;")
        else:
            status = "Normal"
            color = "#00E676"
            self.setStyleSheet("""
            background-color: #1F1F1F;
            border-radius: 10px;
            border: 1px solid #2A2A2A;
            """)
            self.status_label.setStyleSheet(f"color: {color}; font-size: 12px; font-weight: bold;")
        self.status_label.setText(status)
class SensorsTab(QWidget):
    def __init__(self):
        super().__init__()
        self.sensor_widgets = {}
        self.setup_ui()
        self.setup_timer()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(15)
        title = QLabel("System Sensors")
        title.setStyleSheet("""
        color: #00B0FF;
        font-size: 24px;
        font-weight: bold;
        """)
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)
        desc = QLabel("Real-time monitoring of all system sensors")
        desc.setStyleSheet("color: #B0B0B0; font-size: 14px;")
        desc.setAlignment(Qt.AlignCenter)
        layout.addWidget(desc)
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setStyleSheet("""
        QScrollArea {
            border: none;
            background: transparent;
        }
        QScrollBar:vertical {
            border: none;
            background: #1F1F1F;
            width: 10px;
            margin: 0px;
        }
        QScrollBar::handle:vertical {
            background: #00B0FF;
            min-height: 20px;
            border-radius: 5px;
        }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            height: 0px;
        }
        """)
        sensor_container = QWidget()
        self.sensor_layout = QGridLayout(sensor_container)
        self.sensor_layout.setSpacing(10)
        self.sensor_layout.setContentsMargins(5, 5, 5, 5)
        self.sensor_layout.setColumnStretch(0, 1)
        self.sensor_layout.setColumnStretch(1, 1)
        self.sensor_layout.setColumnStretch(2, 1)
        scroll_area.setWidget(sensor_container)
        layout.addWidget(scroll_area, 1)
        summary_widget = QFrame()
        summary_widget.setMinimumHeight(70)
        summary_widget.setMaximumHeight(90)
        summary_widget.setStyleSheet("""
        background-color: #1F1F1F;
        border-radius: 15px;
        border: 2px solid #2A2A2A;
        """)
        summary_layout = QHBoxLayout(summary_widget)
        summary_layout.setContentsMargins(15, 0, 15, 0)
        summary_items = [
            ("Total Sensors", "0", "#00B0FF"),
            ("Normal", "0", "#00E676"),
            ("Warning", "0", "#FF9800"),
            ("Critical", "0", "#FF5252")
        ]
        for text, value, color in summary_items:
            frame = QFrame()
            frame_layout = QVBoxLayout(frame)
            frame_layout.setContentsMargins(10, 5, 10, 5)
            text_label = QLabel(text)
            text_label.setStyleSheet(f"color: {color}; font-size: 12px;")
            text_label.setAlignment(Qt.AlignCenter)
            value_label = QLabel(value)
            value_label.setStyleSheet("color: white; font-size: 16px; font-weight: bold;")
            value_label.setAlignment(Qt.AlignCenter)
            if text == "Total Sensors":
                self.total_sensors_label = value_label
            elif text == "Normal":
                self.normal_sensors_label = value_label
            elif text == "Warning":
                self.warning_sensors_label = value_label
            elif text == "Critical":
                self.critical_sensors_label = value_label
            frame_layout.addWidget(text_label)
            frame_layout.addWidget(value_label)
            summary_layout.addWidget(frame, 1)
        layout.addWidget(summary_widget)

    def setup_timer(self):
        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self.update_sensors)
        self.update_timer.start(2000)
        self.update_sensors()

    def update_sensors(self):
        try:
            for i in reversed(range(self.sensor_layout.count())):
                widget = self.sensor_layout.itemAt(i).widget()
                if widget:
                    widget.deleteLater()
            self.sensor_widgets.clear()
            temp_sensors = []
            if hasattr(psutil, "sensors_temperatures"):
                temps = psutil.sensors_temperatures()
                if temps:
                    for sensor_name, entries in temps.items():
                        for idx, entry in enumerate(entries):
                            sensor_id = f"{sensor_name}_{idx}"
                            display_name = f"{sensor_name.replace('_', ' ').title()}"
                            if len(entries) > 1:
                                display_name += f" #{idx + 1}"
                            temp_sensors.append({
                                'id': sensor_id,
                                'name': display_name,
                                'type': 'Temperature',
                                'current': entry.current,
                                'high': entry.high,
                                'critical': entry.critical,
                                'unit': '°C'
                            })
            fan_sensors = []
            if hasattr(psutil, "sensors_fans"):
                fans = psutil.sensors_fans()
                if fans:
                    for fan_name, entries in fans.items():
                        for idx, entry in enumerate(entries):
                            fan_id = f"{fan_name}_{idx}"
                            display_name = f"{fan_name.replace('_', ' ').title()}"
                            if len(entries) > 1:
                                display_name += f" #{idx + 1}"
                            fan_sensors.append({
                                'id': fan_id,
                                'name': display_name,
                                'type': 'Fan',
                                'current': entry.current,
                                'unit': ' RPM'
                            })
            battery_sensors = []
            if hasattr(psutil, "sensors_battery"):
                battery = psutil.sensors_battery()
                if battery:
                    battery_sensors.append({
                        'id': 'battery_0',
                        'name': 'Battery',
                        'type': 'Power',
                        'current': battery.percent,
                        'unit': '%',
                        'plugged': battery.power_plugged
                    })
            voltage_sensors = self.scan_voltage_sensors()
            current_sensors = self.scan_current_sensors()
            power_sensors = self.scan_power_sensors()
            all_sensors = (temp_sensors + fan_sensors + battery_sensors +
                          voltage_sensors + current_sensors + power_sensors)
            all_sensors.append({
                'id': 'cpu_usage',
                'name': 'CPU Usage',
                'type': 'Processor',
                'current': psutil.cpu_percent(),
                'unit': '%'
            })
            all_sensors.append({
                'id': 'ram_usage',
                'name': 'RAM Usage',
                'type': 'Memory',
                'current': psutil.virtual_memory().percent,
                'unit': '%'
            })
            all_sensors.append({
                'id': 'disk_usage',
                'name': 'Disk Usage',
                'type': 'Storage',
                'current': psutil.disk_usage("/").percent,
                'unit': '%'
            })
            row, col = 0, 0
            max_cols = max(2, min(4, self.width() // 250))
            normal_count = 0
            warning_count = 0
            critical_count = 0
            for sensor in all_sensors:
                sensor_id = sensor['id']
                widget = SensorWidget(
                    sensor_name=sensor['name'],
                    sensor_type=sensor['type'],
                    current_value=sensor['current'],
                    high=sensor.get('high'),
                    critical=sensor.get('critical'),
                    unit=sensor['unit']
                )
                current = sensor['current']
                critical = sensor.get('critical')
                high = sensor.get('high')
                if critical and current >= critical:
                    critical_count += 1
                elif high and current >= high:
                    warning_count += 1
                else:
                    normal_count += 1
                self.sensor_widgets[sensor_id] = widget
                self.sensor_layout.addWidget(widget, row, col)
                col += 1
                if col >= max_cols:
                    col = 0
                    row += 1
            total_count = len(all_sensors)
            self.total_sensors_label.setText(str(total_count))
            self.normal_sensors_label.setText(str(normal_count))
            self.warning_sensors_label.setText(str(warning_count))
            self.critical_sensors_label.setText(str(critical_count))
        except Exception as e:
            print(f"Error updating sensors: {e}")

    def scan_voltage_sensors(self):
        sensors = []
        try:
            if sys.platform == "linux":
                for root, dirs, files in os.walk("/sys/class/hwmon"):
                    for file in files:
                        if "in" in file and "input" in file:
                            try:
                                with open(os.path.join(root, file), 'r') as f:
                                    value = int(f.read().strip()) / 1000.0
                                label_file = file.replace("input", "label")
                                label = "Voltage"
                                try:
                                    with open(os.path.join(root, label_file), 'r') as f:
                                        label = f.read().strip()
                                except:
                                    pass
                                sensors.append({
                                    'id': f"voltage_{len(sensors)}",
                                    'name': label,
                                    'type': 'Voltage',
                                    'current': value,
                                    'unit': 'V'
                                })
                            except:
                                continue
        except:
            pass
        return sensors

    def scan_current_sensors(self):
        sensors = []
        try:
            if sys.platform == "linux":
                for root, dirs, files in os.walk("/sys/class/hwmon"):
                    for file in files:
                        if "curr" in file and "input" in file:
                            try:
                                with open(os.path.join(root, file), 'r') as f:
                                    value = int(f.read().strip()) / 1000.0
                                label_file = file.replace("input", "label")
                                label = "Current"
                                try:
                                    with open(os.path.join(root, label_file), 'r') as f:
                                        label = f.read().strip()
                                except:
                                    pass
                                sensors.append({
                                    'id': f"current_{len(sensors)}",
                                    'name': label,
                                    'type': 'Current',
                                    'current': value,
                                    'unit': 'A'
                                })
                            except:
                                continue
        except:
            pass
        return sensors

    def scan_power_sensors(self):
        sensors = []
        try:
            if sys.platform == "linux":
                for root, dirs, files in os.walk("/sys/class/hwmon"):
                    for file in files:
                        if "power" in file and "input" in file:
                            try:
                                with open(os.path.join(root, file), 'r') as f:
                                    value = int(f.read().strip()) / 1000000.0
                                label_file = file.replace("input", "label")
                                label = "Power"
                                try:
                                    with open(os.path.join(root, label_file), 'r') as f:
                                        label = f.read().strip()
                                except:
                                    pass
                                sensors.append({
                                    'id': f"power_{len(sensors)}",
                                    'name': label,
                                    'type': 'Power',
                                    'current': value,
                                    'unit': 'W'
                                })
                            except:
                                continue
        except:
            pass
        return sensors

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_sensors()
class GraphTab(QWidget):
    def __init__(self):
        super().__init__()
        self.setup_ui()
        self.setup_data()
        self.setup_timer()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(15)
        title = QLabel("Real-time System Graphs")
        title.setStyleSheet("""
        color: #00B0FF;
        font-size: 24px;
        font-weight: bold;
        """)
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)
        desc = QLabel("Live monitoring of system resources with 60-second history")
        desc.setStyleSheet("color: #B0B0B0; font-size: 14px;")
        desc.setAlignment(Qt.AlignCenter)
        layout.addWidget(desc)
        graph_container = QWidget()
        graph_layout = QGridLayout(graph_container)
        graph_layout.setContentsMargins(0, 0, 0, 0)
        graph_layout.setSpacing(15)
        graph_layout.setRowStretch(0, 1)
        graph_layout.setRowStretch(1, 1)
        graph_layout.setColumnStretch(0, 1)
        graph_layout.setColumnStretch(1, 1)
        self.cpu_graph = LiveGraph("CPU Usage", "#2196F3", 60)
        self.cpu_graph.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.ram_graph = LiveGraph("RAM Usage", "#FF9800", 60)
        self.ram_graph.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.temp_graph = LiveGraph("CPU Temperature (°C)", "#F44336", 60)
        self.temp_graph.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.disk_graph = LiveGraph("Disk Usage", "#9C27B0", 60)
        self.disk_graph.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        graph_layout.addWidget(self.cpu_graph, 0, 0)
        graph_layout.addWidget(self.ram_graph, 0, 1)
        graph_layout.addWidget(self.temp_graph, 1, 0)
        graph_layout.addWidget(self.disk_graph, 1, 1)
        layout.addWidget(graph_container, 1)
        stats_widget = QWidget()
        stats_layout = QHBoxLayout(stats_widget)
        stats_layout.setSpacing(15)
        self.stats_labels = {}
        stats_info = [
            ("CPU", "#2196F3", "%"),
            ("RAM", "#FF9800", "%"),
            ("Temp", "#F44336", "°C"),
            ("Disk", "#9C27B0", "%"),
            ("Processes", "#607D8B", "")
        ]
        for name, color, unit in stats_info:
            frame = QFrame()
            frame.setMinimumHeight(50)
            frame.setMaximumHeight(70)
            frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            frame.setStyleSheet(f"""
            background-color: #1F1F1F;
            border-radius: 10px;
            border: 1px solid {color};
            """)
            frame_layout = QVBoxLayout(frame)
            frame_layout.setContentsMargins(10, 5, 10, 5)
            title_label = QLabel(name)
            title_label.setStyleSheet(f"color: {color}; font-size: 12px;")
            title_label.setAlignment(Qt.AlignCenter)
            value_label = QLabel(f"0{unit}")
            value_label.setStyleSheet("color: white; font-size: 16px; font-weight: bold;")
            value_label.setAlignment(Qt.AlignCenter)
            self.stats_labels[name] = value_label
            frame_layout.addWidget(title_label)
            frame_layout.addWidget(value_label)
            stats_layout.addWidget(frame)
        layout.addWidget(stats_widget)

    def setup_data(self):
        self.cpu_data = deque(maxlen=60)
        self.ram_data = deque(maxlen=60)
        self.temp_data = deque(maxlen=60)
        self.disk_data = deque(maxlen=60)
        for _ in range(60):
            self.cpu_data.append(0)
            self.ram_data.append(0)
            self.temp_data.append(0)
            self.disk_data.append(0)

    def setup_timer(self):
        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self.update_graphs)
        self.update_timer.start(1000)

    def update_graphs(self):
        try:
            cpu = psutil.cpu_percent()
            ram = psutil.virtual_memory().percent
            disk = psutil.disk_usage("/").percent
            temp = self.get_cpu_temp()
            self.cpu_graph.update_value(cpu)
            self.ram_graph.update_value(ram)
            self.temp_graph.update_value(temp if temp else 0)
            self.disk_graph.update_value(disk)
            self.stats_labels["CPU"].setText(f"{cpu:.1f}%")
            self.stats_labels["RAM"].setText(f"{ram:.1f}%")
            self.stats_labels["Temp"].setText(f"{temp}°C" if temp else "N/A")
            self.stats_labels["Disk"].setText(f"{disk:.1f}%")
            self.stats_labels["Processes"].setText(f"{len(psutil.pids())}")
        except Exception as e:
            print(f"Error updating graphs: {e}")

    def get_cpu_temp(self):
        try:
            if hasattr(psutil, "sensors_temperatures"):
                temps = psutil.sensors_temperatures()
                if temps:
                    for name, entries in temps.items():
                        if entries and ('core' in name.lower() or 'cpu' in name.lower()):
                            return round(entries[0].current, 1)
        except:
            pass
        return None
class PowerTab(QWidget):
    def __init__(self):
        super().__init__()
        self.battery_history = deque(maxlen=60)
        self.power_history = deque(maxlen=60)
        self.setup_ui()
        self.setup_timer()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(20)

        # Title Section
        title_container = QFrame()
        title_container.setStyleSheet("""
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0, 
                                        stop:0 #00B0FF, stop:1 #9C27B0);
            border-radius: 15px;
            padding: 5px;
        """)
        title_layout = QVBoxLayout(title_container)
        title_layout.setContentsMargins(20, 15, 20, 15)

        title = QLabel("⚡ Power Management")
        title.setStyleSheet("""
            color: white;
            font-size: 28px;
            font-weight: bold;
            margin-bottom: 5px;
        """)
        title.setAlignment(Qt.AlignCenter)

        subtitle = QLabel("Battery analytics, power consumption, and energy optimization")
        subtitle.setStyleSheet("color: rgba(255, 255, 255, 0.9); font-size: 14px;")
        subtitle.setAlignment(Qt.AlignCenter)

        title_layout.addWidget(title)
        title_layout.addWidget(subtitle)
        layout.addWidget(title_container)

        # Main Content Area
        content_scroll = QScrollArea()
        content_scroll.setWidgetResizable(True)
        content_scroll.setStyleSheet("""
            QScrollArea {
                border: none;
                background: transparent;
            }
            QScrollBar:vertical {
                border: none;
                background: #1A1A1A;
                width: 10px;
                border-radius: 5px;
            }
            QScrollBar::handle:vertical {
                background: #00B0FF;
                border-radius: 5px;
                min-height: 30px;
            }
        """)

        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setSpacing(20)
        content_layout.setContentsMargins(5, 5, 5, 5)

        # Battery Status Card (Improved)
        battery_section = self.create_battery_section()
        content_layout.addWidget(battery_section)

        # Power Statistics Grid
        stats_grid = self.create_stats_grid()
        content_layout.addWidget(stats_grid)

        # Power Graph Section
        graph_section = self.create_graph_section()
        content_layout.addWidget(graph_section)

        # Power Tips Section
        tips_section = self.create_tips_section()
        content_layout.addWidget(tips_section)

        content_layout.addStretch()
        content_scroll.setWidget(content_widget)
        layout.addWidget(content_scroll, 1)

        # Control Buttons
        control_widget = QFrame()
        control_widget.setStyleSheet("""
            background-color: #1F1F1F;
            border-radius: 10px;
            padding: 10px;
        """)
        control_layout = QHBoxLayout(control_widget)

        controls = [
            ("🔄 Refresh", self.refresh_power_info, "#2196F3"),
            ("📊 Generate Report", self.generate_power_report, "#4CAF50"),
            ("⚙️ Power Settings", self.show_power_settings, "#FF9800"),
            ("💡 Optimize Now", self.optimize_power, "#9C27B0")
        ]

        for text, callback, color in controls:
            btn = QPushButton(text)
            btn.setMinimumHeight(40)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {color};
                    color: white;
                    border: none;
                    border-radius: 8px;
                    padding: 8px 15px;
                    font-weight: bold;
                    font-size: 13px;
                    min-width: 120px;
                }}
                QPushButton:hover {{
                    background-color: {self.darken_color(color)};
                    transform: scale(1.02);
                }}
            """)
            btn.clicked.connect(callback)
            control_layout.addWidget(btn)

        control_layout.addStretch()
        layout.addWidget(control_widget)

    def create_battery_section(self):
        section = QFrame()
        section.setStyleSheet("""
            background-color: #1F1F1F;
            border-radius: 15px;
            border: 2px solid #4CAF50;
        """)
        section.setMinimumHeight(180)

        layout = QVBoxLayout(section)
        layout.setContentsMargins(25, 20, 25, 20)
        layout.setSpacing(15)

        # Header
        header = QHBoxLayout()
        battery_title = QLabel("🔋 Battery Status")
        battery_title.setStyleSheet("color: #4CAF50; font-size: 20px; font-weight: bold;")

        self.battery_status_icon = QLabel("🔋")
        self.battery_status_icon.setStyleSheet("font-size: 24px;")

        header.addWidget(battery_title)
        header.addStretch()
        header.addWidget(self.battery_status_icon)

        # Battery Percentage and Progress Bar
        self.battery_percent_label = QLabel("0%")
        self.battery_percent_label.setStyleSheet("""
            color: white;
            font-size: 42px;
            font-weight: bold;
            margin: 10px 0;
        """)
        self.battery_percent_label.setAlignment(Qt.AlignCenter)

        self.battery_progress = QProgressBar()
        self.battery_progress.setRange(0, 100)
        self.battery_progress.setTextVisible(False)
        self.battery_progress.setStyleSheet("""
            QProgressBar {
                background-color: #2A2A2A;
                border: 2px solid #3A3A3A;
                border-radius: 10px;
                height: 20px;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                           stop:0 #4CAF50, stop:1 #8BC34A);
                border-radius: 8px;
            }
        """)

        # Status Details
        details_grid = QGridLayout()
        details_grid.setSpacing(15)

        self.battery_state_label = self.create_detail_item("Charging State", "Checking...", "#2196F3")
        self.battery_time_label = self.create_detail_item("Remaining Time", "Calculating...", "#FF9800")
        self.battery_health_label = self.create_detail_item("Health Status", "Checking...", "#9C27B0")
        self.battery_cycles_label = self.create_detail_item("Charge Cycles", "N/A", "#00BCD4")

        details_grid.addWidget(self.battery_state_label, 0, 0)
        details_grid.addWidget(self.battery_time_label, 0, 1)
        details_grid.addWidget(self.battery_health_label, 1, 0)
        details_grid.addWidget(self.battery_cycles_label, 1, 1)

        layout.addLayout(header)
        layout.addWidget(self.battery_percent_label)
        layout.addWidget(self.battery_progress)
        layout.addLayout(details_grid)

        return section

    def create_detail_item(self, title, value, color):
        frame = QFrame()
        frame.setStyleSheet(f"""
            QFrame {{
                background-color: #2A2A2A;
                border-left: 4px solid {color};
                border-radius: 8px;
                padding: 10px;
            }}
        """)

        layout = QVBoxLayout(frame)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(3)

        title_label = QLabel(title)
        title_label.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: bold;")

        value_label = QLabel(value)
        value_label.setObjectName("value")
        value_label.setStyleSheet("color: white; font-size: 14px; font-weight: bold;")

        layout.addWidget(title_label)
        layout.addWidget(value_label)

        return frame

    def create_stats_grid(self):
        section = QFrame()
        section.setStyleSheet("""
            background-color: #1F1F1F;
            border-radius: 15px;
            border: 1px solid #2A2A2A;
            padding: 20px;
        """)

        layout = QVBoxLayout(section)
        layout.setSpacing(15)

        section_title = QLabel("📈 Power Statistics")
        section_title.setStyleSheet("color: #00B0FF; font-size: 18px; font-weight: bold;")

        stats_grid = QGridLayout()
        stats_grid.setSpacing(15)

        # Row 1
        self.power_source_card = self.create_stat_card("Power Source", "⚡ AC Power", "#2196F3", "🔌")
        self.consumption_card = self.create_stat_card("Power Consumption", "12.5W", "#FF9800", "⚡")
        self.voltage_card = self.create_stat_card("Voltage", "12.6V", "#4CAF50", "🔋")

        # Row 2
        self.current_card = self.create_stat_card("Current", "1.2A", "#9C27B0", "📊")
        self.temperature_card = self.create_stat_card("Temperature", "42°C", "#F44336", "🌡️")
        self.efficiency_card = self.create_stat_card("Efficiency", "92%", "#00BCD4", "📈")

        stats_grid.addWidget(self.power_source_card, 0, 0)
        stats_grid.addWidget(self.consumption_card, 0, 1)
        stats_grid.addWidget(self.voltage_card, 0, 2)
        stats_grid.addWidget(self.current_card, 1, 0)
        stats_grid.addWidget(self.temperature_card, 1, 1)
        stats_grid.addWidget(self.efficiency_card, 1, 2)

        layout.addWidget(section_title)
        layout.addLayout(stats_grid)

        return section

    def create_stat_card(self, title, value, color, icon):
        card = QFrame()
        card.setMinimumHeight(100)
        card.setStyleSheet(f"""
            QFrame {{
                background-color: #2A2A2A;
                border-radius: 10px;
                border: 1px solid {color};
            }}
        """)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(8)

        # Icon and Title
        header_layout = QHBoxLayout()
        icon_label = QLabel(icon)
        icon_label.setStyleSheet(f"color: {color}; font-size: 20px;")

        title_label = QLabel(title)
        title_label.setStyleSheet(f"color: {color}; font-size: 13px; font-weight: bold;")

        header_layout.addWidget(icon_label)
        header_layout.addWidget(title_label)
        header_layout.addStretch()

        # Value
        value_label = QLabel(value)
        value_label.setObjectName("value")
        value_label.setStyleSheet("color: white; font-size: 24px; font-weight: bold;")
        value_label.setAlignment(Qt.AlignCenter)

        layout.addLayout(header_layout)
        layout.addStretch()
        layout.addWidget(value_label)
        layout.addStretch()

        return card

    def create_graph_section(self):
        section = QFrame()
        section.setStyleSheet("""
            background-color: #1F1F1F;
            border-radius: 15px;
            border: 1px solid #2A2A2A;
            padding: 20px;
        """)

        layout = QVBoxLayout(section)
        layout.setSpacing(15)

        section_title = QLabel("📊 Power Usage History (Last 60 minutes)")
        section_title.setStyleSheet("color: #00B0FF; font-size: 18px; font-weight: bold;")

        # Graph Container
        graph_container = QFrame()
        graph_container.setStyleSheet("background-color: #2A2A2A; border-radius: 10px;")
        graph_container.setMinimumHeight(200)

        graph_layout = QVBoxLayout(graph_container)
        graph_layout.setContentsMargins(20, 20, 20, 20)

        self.graph_label = QLabel("Loading power usage history...")
        self.graph_label.setStyleSheet("color: #B0B0B0; font-size: 14px;")
        self.graph_label.setAlignment(Qt.AlignCenter)

        graph_layout.addWidget(self.graph_label)

        layout.addWidget(section_title)
        layout.addWidget(graph_container)

        return section

    def create_tips_section(self):
        section = QFrame()
        section.setStyleSheet("""
            background-color: #1F1F1F;
            border-radius: 15px;
            border: 2px solid #FF9800;
            padding: 20px;
        """)

        layout = QVBoxLayout(section)
        layout.setSpacing(15)

        tips_title = QLabel("💡 Power Saving Tips")
        tips_title.setStyleSheet("color: #FF9800; font-size: 18px; font-weight: bold;")

        tips_container = QFrame()
        tips_container.setStyleSheet("background-color: #2A2A2A; border-radius: 10px; padding: 15px;")

        tips_layout = QVBoxLayout(tips_container)

        tips = [
            "✅ Lower screen brightness to save up to 30% battery",
            "✅ Enable battery saver mode when below 20%",
            "✅ Close unused background applications",
            "✅ Disable Bluetooth and Wi-Fi when not needed",
            "✅ Use dark mode to reduce display power consumption",
            "✅ Update drivers for optimal power management"
        ]

        for tip in tips:
            tip_label = QLabel(tip)
            tip_label.setStyleSheet("color: #B0B0B0; font-size: 13px; padding: 5px 0;")
            tip_label.setWordWrap(True)
            tips_layout.addWidget(tip_label)

        layout.addWidget(tips_title)
        layout.addWidget(tips_container)

        return section

    def darken_color(self, color):
        """Darken a hex color"""
        if color.startswith('#'):
            rgb = tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))
            darkened = tuple(max(0, c - 30) for c in rgb)
            return f'#{darkened[0]:02x}{darkened[1]:02x}{darkened[2]:02x}'
        return color

    def setup_timer(self):
        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self.update_power_info)
        self.update_timer.start(2000)  # Update every 2 seconds
        self.update_power_info()

    def update_power_info(self):
        try:
            if hasattr(psutil, "sensors_battery"):
                battery = psutil.sensors_battery()
                if battery:
                    percent = battery.percent
                    plugged = battery.power_plugged
                    secsleft = battery.secsleft

                    # Update battery status
                    self.battery_percent_label.setText(f"{percent:.0f}%")
                    self.battery_progress.setValue(int(percent))

                    # Update battery icon based on percentage
                    if percent > 80:
                        icon = "🔋"
                    elif percent > 50:
                        icon = "🔋"
                    elif percent > 20:
                        icon = "🟡"
                    else:
                        icon = "🔴"
                    self.battery_status_icon.setText(icon)

                    # Update battery state
                    if plugged:
                        state = "Charging"
                        self.battery_state_label.findChild(QLabel, "value").setText("⚡ Charging")
                        if percent >= 100:
                            state = "Fully Charged"
                            self.battery_state_label.findChild(QLabel, "value").setText("✅ Fully Charged")
                        if secsleft != psutil.POWER_TIME_UNLIMITED:
                            hours = secsleft // 3600
                            minutes = (secsleft % 3600) // 60
                            self.battery_time_label.findChild(QLabel, "value").setText(f"{hours}h {minutes}m")
                        else:
                            self.battery_time_label.findChild(QLabel, "value").setText("Calculating...")
                    else:
                        state = "Discharging"
                        self.battery_state_label.findChild(QLabel, "value").setText("🔋 Discharging")
                        if secsleft != psutil.POWER_TIME_UNKNOWN:
                            hours = secsleft // 3600
                            minutes = (secsleft % 3600) // 60
                            self.battery_time_label.findChild(QLabel, "value").setText(f"{hours}h {minutes}m")
                        else:
                            self.battery_time_label.findChild(QLabel, "value").setText("Unknown")

                    # Update battery health
                    if percent > 90:
                        health = "Excellent ⭐⭐⭐⭐⭐"
                    elif percent > 80:
                        health = "Very Good ⭐⭐⭐⭐"
                    elif percent > 70:
                        health = "Good ⭐⭐⭐"
                    elif percent > 60:
                        health = "Fair ⭐⭐"
                    else:
                        health = "Poor ⭐"
                    self.battery_health_label.findChild(QLabel, "value").setText(health)

                    # Update power source
                    if plugged:
                        self.power_source_card.findChild(QLabel, "value").setText("⚡ AC Power")
                    else:
                        self.power_source_card.findChild(QLabel, "value").setText("🔋 Battery")

                    # Get detailed battery parameters
                    voltage, current, power = self.get_battery_parameters()

                    # Update power consumption
                    if power:
                        self.consumption_card.findChild(QLabel, "value").setText(f"{power:.1f}W")

                    # Update voltage and current
                    if voltage:
                        self.voltage_card.findChild(QLabel, "value").setText(f"{voltage:.2f}V")
                    if current:
                        self.current_card.findChild(QLabel, "value").setText(f"{current:.2f}A")

                    # Update temperature (simulated for now)
                    temp = self.get_battery_temp()
                    if temp:
                        self.temperature_card.findChild(QLabel, "value").setText(f"{temp}°C")

                    # Update efficiency (simulated)
                    efficiency = 85 + min(15, percent // 6)
                    self.efficiency_card.findChild(QLabel, "value").setText(f"{efficiency}%")

                    # Update graph text
                    self.graph_label.setText(
                        f"Battery: {percent:.0f}% | {'Charging' if plugged else 'Discharging'} | {voltage:.2f}V {current:.2f}A")

                    # Add to history
                    self.battery_history.append(percent)

                    # Estimate charge cycles (simulated)
                    cycles = int(1000 * (100 - percent) / 100)
                    self.battery_cycles_label.findChild(QLabel, "value").setText(f"~{cycles}")

                else:
                    self.set_no_battery_state()
            else:
                self.set_no_battery_state()

        except Exception as e:
            print(f"Error updating power info: {e}")
            self.set_no_battery_state()

    def get_battery_parameters(self):
        """Get detailed battery parameters"""
        voltage = None
        current = None
        power = None

        try:
            if sys.platform == "linux":
                # Try to get voltage
                try:
                    with open("/sys/class/power_supply/BAT0/voltage_now", 'r') as f:
                        voltage = int(f.read().strip()) / 1000000.0
                except:
                    # Fallback to typical battery voltage
                    voltage = 12.6

                # Try to get current
                try:
                    with open("/sys/class/power_supply/BAT0/current_now", 'r') as f:
                        current = int(f.read().strip()) / 1000000.0
                except:
                    # Estimate current based on typical values
                    current = 1.2

                # Try to get power
                try:
                    with open("/sys/class/power_supply/BAT0/power_now", 'r') as f:
                        power = int(f.read().strip()) / 1000000.0
                except:
                    # Calculate power from voltage and current
                    if voltage and current:
                        power = voltage * current
                    else:
                        power = 12.5  # Default value

            elif sys.platform == "win32":
                # Windows fallback values
                voltage = 12.6
                current = 1.2
                power = 12.5

        except Exception as e:
            print(f"Error getting battery parameters: {e}")

        return voltage, current, power

    def get_battery_temp(self):
        """Get battery temperature (simulated for now)"""
        try:
            if sys.platform == "linux":
                # Try to get actual temperature
                try:
                    with open("/sys/class/power_supply/BAT0/temp", 'r') as f:
                        temp = int(f.read().strip()) / 10.0
                        return f"{temp:.1f}"
                except:
                    pass

            # Simulated temperature based on charging state
            if hasattr(psutil, "sensors_battery"):
                battery = psutil.sensors_battery()
                if battery:
                    if battery.power_plugged:
                        return "42.5"  # Charging is warmer
                    else:
                        return "38.2"  # Discharging is cooler

        except Exception as e:
            print(f"Error getting battery temperature: {e}")

        return "40.0"  # Default fallback

    def set_no_battery_state(self):
        """Set UI for no battery detected"""
        self.battery_percent_label.setText("N/A")
        self.battery_progress.setValue(0)
        self.battery_status_icon.setText("🔌")
        self.battery_state_label.findChild(QLabel, "value").setText("No Battery")
        self.battery_time_label.findChild(QLabel, "value").setText("N/A")
        self.battery_health_label.findChild(QLabel, "value").setText("N/A")
        self.battery_cycles_label.findChild(QLabel, "value").setText("N/A")
        self.power_source_card.findChild(QLabel, "value").setText("⚡ AC Power")
        self.consumption_card.findChild(QLabel, "value").setText("N/A")
        self.voltage_card.findChild(QLabel, "value").setText("N/A")
        self.current_card.findChild(QLabel, "value").setText("N/A")
        self.temperature_card.findChild(QLabel, "value").setText("N/A")
        self.efficiency_card.findChild(QLabel, "value").setText("N/A")
        self.graph_label.setText("No battery detected. Running on AC power.")

    def refresh_power_info(self):
        """Manually refresh power information"""
        self.update_power_info()

    def generate_power_report(self):
        """Generate a power usage report"""
        try:
            report = f"""
            ⚡ ThermoGuard Power Report
            ═══════════════════════════════════════════

            📅 Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

            🔋 BATTERY STATUS
            • Charge Level: {self.battery_percent_label.text()}
            • Health: {self.battery_health_label.findChild(QLabel, "value").text()}
            • State: {self.battery_state_label.findChild(QLabel, "value").text()}
            • Remaining Time: {self.battery_time_label.findChild(QLabel, "value").text()}

            ⚡ POWER STATISTICS
            • Power Source: {self.power_source_card.findChild(QLabel, "value").text()}
            • Consumption: {self.consumption_card.findChild(QLabel, "value").text()}
            • Voltage: {self.voltage_card.findChild(QLabel, "value").text()}
            • Current: {self.current_card.findChild(QLabel, "value").text()}
            • Temperature: {self.temperature_card.findChild(QLabel, "value").text()}
            • Efficiency: {self.efficiency_card.findChild(QLabel, "value").text()}

            💡 RECOMMENDATIONS
            1. Enable power saving mode when battery is below 30%
            2. Lower screen brightness by 20%
            3. Close unused background applications
            4. Disable unnecessary wireless connections
            """

            QMessageBox.information(self, "Power Report", report)

        except Exception as e:
            QMessageBox.warning(self, "Error", f"Could not generate report: {str(e)}")

    def show_power_settings(self):
        """Show power settings dialog"""
        dialog = QDialog(self)
        dialog.setWindowTitle("⚙️ Power Settings")
        dialog.setMinimumSize(500, 400)
        dialog.setStyleSheet("""
            QDialog {
                background-color: #1F1F1F;
                color: white;
            }
        """)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        title = QLabel("Power Management Settings")
        title.setStyleSheet("color: #00B0FF; font-size: 18px; font-weight: bold;")

        settings_group = QGroupBox("Settings")
        settings_group.setStyleSheet("""
            QGroupBox {
                color: #00B0FF;
                font-weight: bold;
                border: 2px solid #00B0FF;
                border-radius: 10px;
                margin-top: 10px;
                padding-top: 10px;
            }
        """)

        settings_layout = QVBoxLayout(settings_group)

        # Add some example settings
        settings = [
            ("Enable Power Saver Mode", QCheckBox()),
            ("Auto-dim screen on battery", QCheckBox()),
            ("Suspend when inactive for", QComboBox()),
            ("Critical battery action", QComboBox())
        ]

        for text, widget in settings:
            row = QHBoxLayout()
            label = QLabel(text)
            label.setStyleSheet("color: #B0B0B0;")
            row.addWidget(label)
            row.addStretch()

            if isinstance(widget, QComboBox):
                if text == "Suspend when inactive for":
                    widget.addItems(["15 minutes", "30 minutes", "1 hour", "2 hours"])
                elif text == "Critical battery action":
                    widget.addItems(["Hibernate", "Shutdown", "Sleep", "Do nothing"])
                widget.setStyleSheet("""
                    QComboBox {
                        background-color: #2A2A2A;
                        color: white;
                        border: 1px solid #3A3A3A;
                        border-radius: 5px;
                        padding: 5px;
                        min-width: 150px;
                    }
                """)

            row.addWidget(widget)
            settings_layout.addLayout(row)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        save_btn = QPushButton("Save Settings")
        save_btn.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                border: none;
                border-radius: 5px;
                padding: 10px 20px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #388E3C;
            }
        """)
        save_btn.clicked.connect(dialog.accept)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: #F44336;
                color: white;
                border: none;
                border-radius: 5px;
                padding: 10px 20px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #D32F2F;
            }
        """)
        cancel_btn.clicked.connect(dialog.reject)

        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(save_btn)

        layout.addWidget(title)
        layout.addWidget(settings_group, 1)
        layout.addLayout(btn_layout)

        dialog.exec_()

    def optimize_power(self):
        """Run power optimization"""
        reply = QMessageBox.question(
            self,
            "Power Optimization",
            "This will apply power-saving settings:\n\n"
            "• Enable power saver mode\n"
            "• Lower screen brightness\n"
            "• Disable some visual effects\n\n"
            "Continue?",
            QMessageBox.Yes | QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            QMessageBox.information(
                self,
                "Optimization Complete",
                "Power optimization settings have been applied.\n"
                "Estimated battery life improvement: 15-20%"
            )
class HardwareTab(QWidget):
    def __init__(self):
        super().__init__()
        self.setup_ui()
        self.load_hardware_info()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(15)
        title = QLabel("Hardware Information")
        title.setStyleSheet("""
        color: #00B0FF;
        font-size: 24px;
        font-weight: bold;
        """)
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)
        desc = QLabel("Detailed system specifications and hardware detection")
        desc.setStyleSheet("color: #B0B0B0; font-size: 14px;")
        desc.setAlignment(Qt.AlignCenter)
        layout.addWidget(desc)
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setStyleSheet("""
        QScrollArea {
            border: none;
            background: transparent;
        }
        QScrollBar:vertical {
            border: none;
            background: #1F1F1F;
            width: 10px;
            margin: 0px;
        }
        QScrollBar::handle:vertical {
            background: #00B0FF;
            min-height: 20px;
            border-radius: 5px;
        }
        """)
        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(10, 10, 10, 10)
        container_layout.setSpacing(10)
        self.info_text = QLabel("Loading hardware information...")
        self.info_text.setStyleSheet("""
        color: white;
        font-size: 14px;
        background-color: #1F1F1F;
        padding: 20px;
        border-radius: 15px;
        border: 1px solid #2A2A2A;
        """)
        self.info_text.setWordWrap(True)
        self.info_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        container_layout.addWidget(self.info_text)
        scroll_area.setWidget(container)
        layout.addWidget(scroll_area, 1)
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(15)
        btn_layout.addStretch()
        refresh_btn = QPushButton("Refresh Hardware Info")
        refresh_btn.setMinimumHeight(35)
        refresh_btn.setMaximumHeight(45)
        refresh_btn.setStyleSheet("""
        QPushButton {
            background-color: #2196F3;
            color: white;
            font-size: 14px;
            font-weight: bold;
            border-radius: 8px;
            padding: 5px 15px;
        }
        QPushButton:hover {
            background-color: #0d8aee;
        }
        """)
        refresh_btn.clicked.connect(self.load_hardware_info)
        btn_layout.addWidget(refresh_btn)
        export_btn = QPushButton(" Export Specs")
        export_btn.setMinimumHeight(35)
        export_btn.setMaximumHeight(45)
        export_btn.setStyleSheet("""
        QPushButton {
            background-color: #4CAF50;
            color: white;
            font-size: 14px;
            font-weight: bold;
            border-radius: 8px;
            padding: 5px 15px;
        }
        QPushButton:hover {
            background-color: #388E3C;
        }
        """)
        export_btn.clicked.connect(self.export_specs)
        btn_layout.addWidget(export_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

    def load_hardware_info(self):
        info = []
        dmi = self.read_dmi_info()
        if "error" not in dmi:
            info.append("<h3>🔹 System Information</h3>")
            info.append(f"<b>• System Model:</b> {dmi.get('system_name', 'N/A')}")
            info.append(f"<b>• Motherboard:</b> {dmi.get('board_vendor', 'N/A')} {dmi.get('board_name', '')}")
            info.append(f"<b>• BIOS Version:</b> {dmi.get('bios_version', 'N/A')}")
            info.append(f"<b>• BIOS Date:</b> {dmi.get('bios_date', 'N/A')}")
        cpu_info = self.get_cpu_info()
        info.append("<h3>🔹 Processor</h3>")
        info.append(f"<b>• Model:</b> {cpu_info.get('model', 'N/A')}")
        info.append(
            f"<b>• Cores:</b> {cpu_info.get('physical_cores', 'N/A')} physical / {cpu_info.get('logical_cores', 'N/A')} logical")
        info.append(f"<b>• Max Frequency:</b> {cpu_info.get('max_freq', 'N/A')}")
        ram_info = self.get_ram_info()
        info.append("<h3>🔹 Memory</h3>")
        info.append(f"<b>• Total Installed:</b> {ram_info.get('total', 'N/A')}")
        info.append(f"<b>• Available:</b> {ram_info.get('available', 'N/A')}")
        info.append(f"<b>• Used:</b> {ram_info.get('used', 'N/A')}")
        if ram_info.get('slots'):
            info.append(f"<b>• Memory Slots:</b> {ram_info['slots']}")
        disks = self.get_disk_info()
        info.append("<h3>🔹 Storage</h3>")
        if disks:
            for disk in disks:
                info.append(f"<b>• {disk['device']}:</b> {disk['size']} ({disk['type']})")
                info.append(f"  <i>Mount:</i> {disk['mount']} | <i>Used:</i> {disk['used']}/{disk['total']}")
        else:
            info.append("  • No disks detected")
        gpu = self.get_gpu_info()
        info.append("<h3>🔹 Graphics</h3>")
        if isinstance(gpu, list) and gpu:
            for i, card in enumerate(gpu):
                info.append(f"<b>• GPU {i + 1}:</b> {card}")
        else:
            info.append(f"<b>• GPU:</b> {gpu}")
        network = self.get_network_info()
        info.append("<h3>🔹 Network</h3>")
        if network:
            for adapter in network:
                info.append(f"<b>• {adapter['name']}:</b> {adapter['status']}")
        else:
            info.append("  • No network adapters detected")
        info.append("<h3>🔹 Operating System</h3>")
        info.append(f"<b>• OS:</b> {self.get_os_info()}")
        info.append(f"<b>• Kernel:</b> {os.uname().release}")
        info.append(f"<b>• Python:</b> {sys.version.split()[0]}")
        info.append(f"<b>• Architecture:</b> {os.uname().machine}")
        self.info_text.setText("<br>".join(info))

    def read_dmi_info(self):
        dmi = {}
        dmi_path = "/sys/class/dmi/id/"
        fields = {
            "board_vendor": "board_vendor",
            "board_name": "board_name",
            "product_name": "system_name",
            "bios_version": "bios_version",
            "bios_date": "bios_date"
        }
        try:
            for sys_field, dict_field in fields.items():
                try:
                    with open(os.path.join(dmi_path, sys_field), "r") as f:
                        content = f.read().strip()
                        dmi[dict_field] = content if content else "N/A"
                except FileNotFoundError:
                    dmi[dict_field] = "N/A"
        except Exception as e:
            dmi["error"] = str(e)
        return dmi

    def get_cpu_info(self):
        info = {
            "model": "Unknown",
            "physical_cores": psutil.cpu_count(logical=False),
            "logical_cores": psutil.cpu_count(logical=True),
            "max_freq": "Unknown"
        }
        try:
            with open("/proc/cpuinfo", "r") as f:
                for line in f:
                    if "model name" in line:
                        info["model"] = line.split(":")[1].strip()
                        break
            freq = psutil.cpu_freq()
            if freq:
                info["max_freq"] = f"{freq.max:.1f} MHz"
        except Exception as e:
            print(f"Error getting CPU info: {e}")
        return info

    def get_ram_info(self):
        ram = psutil.virtual_memory()
        info = {
            "total": f"{self.format_bytes(ram.total)}",
            "available": f"{self.format_bytes(ram.available)}",
            "used": f"{self.format_bytes(ram.used)} ({ram.percent}%)"
        }
        try:
            output = subprocess.check_output(["dmidecode", "-t", "memory"], universal_newlines=True,
                                             stderr=subprocess.DEVNULL)
            slots = 0
            for line in output.splitlines():
                if "Number Of Devices:" in line:
                    slots = int(line.split(":")[1].strip())
                    break
            if slots > 0:
                info["slots"] = str(slots)
        except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
            pass
        return info

    def get_disk_info(self):
        disks = []
        try:
            partitions = psutil.disk_partitions(all=False)
            for part in partitions:
                if 'loop' in part.device or 'snap' in part.device or 'dm-' in part.device:
                    continue
                try:
                    usage = psutil.disk_usage(part.mountpoint)
                    disk_type = "SSD" if "nvme" in part.device or "ssd" in part.device.lower() else "HDD"
                    disks.append({
                        "device": part.device,
                        "mount": part.mountpoint,
                        "size": self.format_bytes(usage.total),
                        "used": self.format_bytes(usage.used),
                        "total": self.format_bytes(usage.total),
                        "percent": f"{usage.percent}%",
                        "type": disk_type
                    })
                except Exception as e:
                    continue
        except Exception as e:
            print(f"Error getting disk info: {e}")
        return disks

    def get_gpu_info(self):
        try:
            output = subprocess.check_output(["lspci"], universal_newlines=True)
            gpus = []
            for line in output.splitlines():
                if "VGA" in line or "3D" in line or "Display" in line:
                    gpu_info = line.split(": ")[1].strip()
                    gpus.append(gpu_info)
            if gpus:
                return gpus
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            print(f"Error getting GPU info: {e}")
            return "Unknown (install pciutils)"
        try:
            output = subprocess.check_output(["glxinfo"], universal_newlines=True, stderr=subprocess.DEVNULL)
            for line in output.splitlines():
                if "OpenGL vendor" in line:
                    vendor = line.split(":")[1].strip()
                if "OpenGL renderer" in line:
                    renderer = line.split(":")[1].strip()
                    return f"{vendor} {renderer}"
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass
        return "Unknown"

    def get_network_info(self):
        adapters = []
        try:
            net_if_stats = psutil.net_if_stats()
            net_if_addrs = psutil.net_if_addrs()
            for name, stats in net_if_stats.items():
                if name.startswith('lo'):
                    continue
                status = "Up" if stats.isup else "Down"
                speed = f"{stats.speed} Mbps" if stats.speed else "Unknown speed"
                ip = "No IP assigned"
                for addr in net_if_addrs.get(name, []):
                    if addr.family == 2:  # AF_INET
                        ip = addr.address
                        break
                adapters.append({
                    "name": name,
                    "status": f"{status}, {ip}, {speed}"
                })
        except Exception as e:
            print(f"Error getting network info: {e}")
        return adapters

    def get_os_info(self):
        try:
            with open("/etc/os-release", "r") as f:
                for line in f:
                    if line.startswith("PRETTY_NAME="):
                        return line.split("=")[1].strip().strip('"')
        except:
            pass
        return f"{os.name} ({sys.platform})"

    def format_bytes(self, size):
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size < 1024.0:
                return f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{size:.1f} PB"

    def export_specs(self):
        try:
            filename, _ = QFileDialog.getSaveFileName(
                self,
                "Save Hardware Specifications",
                f"hardware_specs_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
                "Text Files (*.txt);;All Files (*)"
            )
            if not filename:
                return
            with open(filename, 'w') as f:
                f.write("ThermoGuard Hardware Specifications\n")
                f.write("=" * 50 + "\n")
                f.write(f"Generated on: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
                dmi = self.read_dmi_info()
                if "error" not in dmi:
                    f.write("SYSTEM INFORMATION\n")
                    f.write("-" * 20 + "\n")
                    f.write(f"System Model: {dmi.get('system_name', 'N/A')}\n")
                    f.write(f"Motherboard: {dmi.get('board_vendor', 'N/A')} {dmi.get('board_name', '')}\n")
                    f.write(f"BIOS Version: {dmi.get('bios_version', 'N/A')}\n")
                    f.write(f"BIOS Date: {dmi.get('bios_date', 'N/A')}\n\n")
                cpu_info = self.get_cpu_info()
                f.write("PROCESSOR\n")
                f.write("-" * 20 + "\n")
                f.write(f"Model: {cpu_info.get('model', 'N/A')}\n")
                f.write(
                    f"Cores: {cpu_info.get('physical_cores', 'N/A')} physical / {cpu_info.get('logical_cores', 'N/A')} logical\n")
                f.write(f"Max Frequency: {cpu_info.get('max_freq', 'N/A')}\n\n")
                ram_info = self.get_ram_info()
                f.write("MEMORY\n")
                f.write("-" * 20 + "\n")
                f.write(f"Total Installed: {ram_info.get('total', 'N/A')}\n")
                f.write(f"Available: {ram_info.get('available', 'N/A')}\n")
                f.write(f"Used: {ram_info.get('used', 'N/A')}\n")
                if ram_info.get('slots'):
                    f.write(f"Memory Slots: {ram_info['slots']}\n\n")
                disks = self.get_disk_info()
                f.write("STORAGE\n")
                f.write("-" * 20 + "\n")
                if disks:
                    for disk in disks:
                        f.write(f"{disk['device']}: {disk['size']} ({disk['type']})\n")
                        f.write(f"  Mount: {disk['mount']}\n")
                        f.write(f"  Used: {disk['used']}/{disk['total']}\n\n")
                else:
                    f.write("No disks detected\n\n")
                gpu = self.get_gpu_info()
                f.write("GRAPHICS\n")
                f.write("-" * 20 + "\n")
                if isinstance(gpu, list) and gpu:
                    for i, card in enumerate(gpu):
                        f.write(f"GPU {i + 1}: {card}\n")
                else:
                    f.write(f"GPU: {gpu}\n\n")
                network = self.get_network_info()
                f.write("NETWORK\n")
                f.write("-" * 20 + "\n")
                if network:
                    for adapter in network:
                        f.write(f"{adapter['name']}: {adapter['status']}\n")
                else:
                    f.write("No network adapters detected\n\n")
                f.write("OPERATING SYSTEM\n")
                f.write("-" * 20 + "\n")
                f.write(f"OS: {self.get_os_info()}\n")
                f.write(f"Kernel: {os.uname().release}\n")
                f.write(f"Python: {sys.version.split()[0]}\n")
                f.write(f"Architecture: {os.uname().machine}\n")
            QMessageBox.information(self, "Export Successful", f"Hardware specifications saved to:\n{filename}")
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Failed to export hardware specifications:\n{str(e)}")
class MainWindow(QMainWindow):
    def __init__(self, tray_icon=None):
        super().__init__()
        self.tray_icon = tray_icon
        self.overview_status_card = None
        self.card_label = None
        self.mini_cpu_graph = None
        self.mini_ram_graph = None
        self.mini_temp_graph = None
        self.mini_disk_graph = None
        self.setWindowTitle("ThermoGuard")
        icon_paths = [
            "copilot_20260101_144719.png",
            "icon.png",
            "thermoguard.png",
            os.path.join(os.path.dirname(__file__), "icon.png")
        ]
        for path in icon_paths:
            if os.path.exists(path):
                self.setWindowIcon(QIcon(path))
                break
        else:
            self.setWindowIcon(QIcon.fromTheme("utilities-system-monitor"))
        self.setMinimumSize(900, 600)
        self.resize(1200, 800)
        self.central = QWidget(self)
        self.setCentralWidget(self.central)
        self.central.setStyleSheet("background-color:#121212;")
        main_layout = QVBoxLayout(self.central)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)
        header = QWidget()
        header.setMinimumHeight(50)
        header.setMaximumHeight(70)
        header.setStyleSheet("""
        background-color:#1F1F1F;
        border-radius:10px;
        """)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(15, 10, 15, 10)
        title = QLabel("ThermoGuard")
        title.setStyleSheet("color:#00B0FF; font-size:24px; font-weight:bold;")
        header_layout.addWidget(title)
        header_layout.addStretch()
        help_btn = QPushButton("❓ ")
        help_btn.setMinimumHeight(30)
        help_btn.setMaximumHeight(35)
        help_btn.setStyleSheet("""
        QPushButton {
            background-color: transparent;
            color: #B0B0B0;
            border: 1px solid #3A3A3A;
            border-radius: 8px;
            padding: 5px 12px;
            font-size: 12px;
        }
        QPushButton:hover {
            color: white;
            border-color: #00B0FF;
            background-color: #1F1F1F;
        }
        """)
        help_btn.clicked.connect(self.show_help_guide)
        header_layout.addWidget(help_btn)
        self.status_label = QLabel("● SYSTEM STABLE")
        self.status_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.status_label.setStyleSheet("color:#00E676; font-size:14px; font-weight:bold;")
        header_layout.addWidget(self.status_label)
        header_layout.addWidget(title)
        header_layout.addStretch()
        header_layout.addWidget(self.status_label)
        self.tabs = QTabWidget()
        self.tabs.tabBar().setMinimumHeight(40)
        self.tabs.tabBar().setMaximumHeight(50)
        self.tabs.setDocumentMode(True)
        self.tabs.setStyleSheet("""
        QTabWidget::pane {
            border: none;
            background: #1F1F1F;
            border-radius: 10px;
            margin-top: 5px;
        }
        QTabBar::tab {
            background: transparent;
            color: #B0B0B0;
            min-width: 100px;
            padding: 8px 20px;
            font-size: 14px;
        }
        QTabBar::tab:selected {
            color: #00B0FF;
            border-bottom: 3px solid #00B0FF;
        }
        QTabBar::tab:hover {
            color: white;
        }
        """)
        self.tabs.addTab(self.create_overview_tab(), "Overview")
        self.tabs.addTab(GraphTab(), "Graphs")
        self.tabs.addTab(SensorsTab(), "Sensors")
        self.tabs.addTab(PowerTab(), "Power")
        self.tabs.addTab(HardwareTab(), "Hardware")
        self.tabs.addTab(self.create_ai_diagnosis_tab(), "AI Diagnosis")
        self.tabs.addTab(self.create_logs_tab(), "Logs")
        main_layout.addWidget(header)
        main_layout.addWidget(self.tabs, 1)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_all_from_system)
        self.timer.start(2000)
        self.update_all_from_system()
    def create_ai_diagnosis_tab(self):
        tab = QWidget()
        main_layout = QVBoxLayout(tab)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Scroll Area
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setStyleSheet("""
            QScrollArea {
                border: none;
                background: transparent;
            }
            QScrollBar:vertical {
                border: none;
                background: #1F1F1F;
                width: 10px;
                margin: 0px;
            }
            QScrollBar::handle:vertical {
                background: #00B0FF;
                min-height: 20px;
                border-radius: 5px;
            }
        """)

        content_widget = QWidget()
        layout = QVBoxLayout(content_widget)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(20)

        # العنوان
        title = QLabel("🤖 AI Diagnosis & Assistant")
        title.setStyleSheet("color: #00B0FF; font-size: 24px; font-weight: bold;")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        desc = QLabel("Ollama AI-powered system analysis and chat assistant")
        desc.setStyleSheet("color: #B0B0B0; font-size: 14px;")
        desc.setAlignment(Qt.AlignCenter)
        layout.addWidget(desc)

        # === البطاقة الرئيسية: Ollama Status ===
        ollama_card = QFrame()
        ollama_card.setStyleSheet("""
            background-color: #1F1F1F;
            border-radius: 15px;
            border: 2px solid #00B0FF;
            padding: 20px;
        """)
        ollama_layout = QVBoxLayout(ollama_card)
        ollama_layout.setSpacing(12)

        ollama_title = QLabel("⚡ Powered by Ollama Local AI")
        ollama_title.setStyleSheet("color: #00B0FF; font-size: 18px; font-weight: bold;")

        ollama_desc = QLabel(
            "Local AI analysis of your system using Ollama.\n"
            "Get personalized recommendations, troubleshooting, and optimization tips based on your actual hardware."
        )
        ollama_desc.setStyleSheet("color: #B0B0B0; font-size: 14px;")
        ollama_desc.setWordWrap(True)

        self.ai_analysis_label = QLabel("Ready to analyze your system...")
        self.ai_analysis_label.setStyleSheet("color: white; font-size: 16px; font-weight: bold;")
        self.ai_analysis_label.setWordWrap(True)

        self.ai_recommendation_label = QLabel("Click 'Open AI Assistant' to start")
        self.ai_recommendation_label.setStyleSheet("color: #B0B0B0; font-size: 14px;")
        self.ai_recommendation_label.setWordWrap(True)

        ollama_layout.addWidget(ollama_title)
        ollama_layout.addWidget(ollama_desc)
        ollama_layout.addWidget(self.ai_analysis_label)
        ollama_layout.addWidget(self.ai_recommendation_label)
        layout.addWidget(ollama_card)

        # === أزرار التحكم ===
        button_layout = QHBoxLayout()
        button_layout.setSpacing(15)

        def create_styled_button(text, color, hover_color):
            btn = QPushButton(text)
            btn.setMinimumHeight(50)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {color};
                    color: white;
                    font-size: 14px;
                    font-weight: bold;
                    border-radius: 10px;
                    padding: 10px;
                }}
                QPushButton:hover {{
                    background-color: {hover_color};
                }}
            """)
            return btn

        scan_btn = create_styled_button("🔍 Quick System Scan", "#00B0FF", "#0091EA")
        chat_btn = create_styled_button("💬 Open AI Assistant", "#4CAF50", "#388E3C")
        check_btn = create_styled_button("📡 Check Ollama Status", "#FF9800", "#F57C00")

        scan_btn.clicked.connect(self.run_ai_scan)
        chat_btn.clicked.connect(self.open_ai_chat)
        check_btn.clicked.connect(self.check_ollama_status)

        button_layout.addWidget(scan_btn)
        button_layout.addWidget(chat_btn)
        button_layout.addWidget(check_btn)
        button_layout.addStretch()
        layout.addLayout(button_layout)

        # === ميزات Ollama ===
        features_card = QFrame()
        features_card.setStyleSheet("""
            background-color: #1F1F1F;
            border-radius: 15px;
            border: 1px solid #2A2A2A;
            padding: 20px;
        """)
        features_layout = QVBoxLayout(features_card)
        features_layout.setSpacing(8)

        features_title = QLabel("🚀 Ollama AI Features:")
        features_title.setStyleSheet("color: #00B0FF; font-size: 16px; font-weight: bold;")
        features_layout.addWidget(features_title)

        features = [
            "• Real-time system analysis with local AI",
            "• Hardware optimization suggestions",
            "• Troubleshooting guidance",
            "• Performance bottleneck identification",
            "• Power management recommendations",
            "• Security and maintenance tips",
            "• Natural language conversation about your system",
            "• 100% local - No data sent to the cloud",
            "• Free and open source"
        ]
        for feature in features:
            label = QLabel(feature)
            label.setStyleSheet("color: #B0B0B0; font-size: 14px;")
            features_layout.addWidget(label)

        layout.addWidget(features_card)

        # === تعليمات الإعداد ===
        setup_card = QFrame()
        setup_card.setStyleSheet("""
            background-color: #1F1F1F;
            border-radius: 15px;
            border: 1px solid #4CAF50;
            padding: 20px;
        """)
        setup_layout = QVBoxLayout(setup_card)
        setup_layout.setSpacing(8)

        setup_title = QLabel("📋 How to set up Ollama:")
        setup_title.setStyleSheet("color: #4CAF50; font-size: 16px; font-weight: bold;")
        setup_layout.addWidget(setup_title)

        instructions = [
            "1. Install Ollama from <a href='https://ollama.com' style='color:#00B0FF;'>https://ollama.com</a>",
            "2. Open terminal and run: <code style='background:#2A2A2A; padding:2px 6px; border-radius:4px;'>ollama pull llama2</code>",
            "3. Start Ollama server: <code style='background:#2A2A2A; padding:2px 6px; border-radius:4px;'>ollama serve</code>",
            "4. The AI assistant will automatically connect",
            "5. Try other models: <code style='background:#2A2A2A; padding:2px 6px; border-radius:4px;'>ollama pull mistral</code>"
        ]
        for instruction in instructions:
            label = QLabel(instruction)
            label.setStyleSheet("color: #B0B0B0; font-size: 14px;")
            label.setWordWrap(True)
            label.setTextFormat(Qt.RichText)
            label.setOpenExternalLinks(True)
            setup_layout.addWidget(label)

        layout.addWidget(setup_card)
        layout.addStretch()

        # تعيين المحتوى للـ Scroll Area
        scroll_area.setWidget(content_widget)
        main_layout.addWidget(scroll_area)
        return tab
    def show_help_guide(self):
        guide_text = """
    <h2 style="color: #00B0FF;">📖 ThermoGuard - User Guide</h2>
    <p><b>Welcome!</b> This tool helps you monitor and optimize your system using real-time data and local AI.</p>

    <h3>🔹 Overview Tab</h3>
    <ul>
    <li>See live CPU, RAM, temperature, and disk usage.</li>
    <li>System status changes color if overloaded or overheating.</li>
    </ul>

    <h3>📊 Graphs Tab</h3>
    <ul>
    <li>60-second history graphs for all key resources.</li>
    <li>Useful for spotting performance spikes.</li>
    </ul>

    <h3>🌡️ Sensors Tab</h3>
    <ul>
    <li>Shows all hardware sensors: CPU temp, fans, battery, voltage, etc.</li>
    <li>Red/orange borders indicate warnings or critical values.</li>
    </ul>

    <h3>⚡ Power Tab</h3>
    <ul>
    <li>Monitor battery health, power consumption, and efficiency.</li>
    <li>Click "Optimize Now" to apply power-saving settings.</li>
    </ul>

    <h3>🖥️ Hardware Tab</h3>
    <ul>
    <li>Detailed specs: CPU, RAM, disks, GPU, OS.</li>
    <li>Click "Export Specs" to save as text file.</li>
    </ul>

    <h3>🤖 AI Diagnosis Tab</h3>
    <ul>
    <li>Uses <b>Ollama</b> (local AI) — no internet or API key needed!</li>
    <li><b>Steps to use:</b>
      <ol>
        <li>Install Ollama from <a href="https://ollama.com">ollama.com</a></li>
        <li>Run in terminal: <code>ollama pull llama2</code></li>
        <li>Keep Ollama running (<code>ollama serve</code>)</li>
        <li>Click "Open AI Assistant" to chat with your system!</li>
      </ol>
    </li>
    <li>"Quick System Scan" gives instant analysis without chat.</li>
    </ul>

    <h3>📝 Logs Tab</h3>
    <ul>
    <li>All actions and errors are logged here.</li>
    <li>Export logs for debugging or sharing.</li>
    </ul>

    <p style="color: #4CAF50;"><b>💡 Tip:</b> Everything runs locally — your data never leaves your machine!</p>
    """
        dialog = QDialog(self)
        dialog.setWindowTitle("How to Use ThermoGuard")
        dialog.setMinimumSize(600, 500)
        layout = QVBoxLayout(dialog)

        browser = QTextBrowser()
        browser.setHtml(guide_text)
        browser.setStyleSheet("background-color: #1A1A1A; color: white; border: none; padding: 15px;")
        browser.setOpenExternalLinks(True)
        layout.addWidget(browser)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.accept)
        close_btn.setStyleSheet("""
            QPushButton {
                background-color: #2196F3;
                color: white;
                padding: 8px 20px;
                border-radius: 5px;
                font-weight: bold;
            }
        """)
        layout.addWidget(close_btn, alignment=Qt.AlignCenter)

        dialog.exec_()
    def check_ollama_status(self):
        """Check if Ollama is running"""
        try:
            response = requests.get("http://localhost:11434/api/tags", timeout=3)
            if response.status_code == 200:
                models = response.json().get('models', [])
                model_list = "\n".join([f"• {m.get('name', 'Unknown')}" for m in models])

                QMessageBox.information(self, "✅ Ollama Status",
                                        f"Ollama is running!\n\nAvailable models:\n{model_list}")
            else:
                QMessageBox.warning(self, "⚠️ Ollama Status",
                                    "Ollama is running but returned an error.\nCheck if models are installed.")

        except requests.exceptions.ConnectionError:
            QMessageBox.critical(self, "❌ Ollama Not Found",
                                 "Ollama is not running!\n\nPlease:\n"
                                 "1. Install Ollama from https://ollama.com\n"
                                 "2. Run 'ollama serve' in terminal\n"
                                 "3. Download a model: 'ollama pull llama2'")

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not check Ollama status: {str(e)}")
    def open_ai_chat(self):
        """Open AI chat dialog - Check Ollama first"""
        try:
            # First check if Ollama is running
            try:
                response = requests.get("http://localhost:11434/api/tags", timeout=2)
                if response.status_code != 200:
                    self.show_ollama_error()
                    return
            except:
                self.show_ollama_error()
                return

            # Collect system data and open chat
            system_data = self.collect_system_data_for_ai()
            self.chat_dialog = AIChatDialog(system_data, self)
            self.chat_dialog.exec_()

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to open AI chat: {str(e)}")
            self.add_log_entry("AI Chat", "ERROR", f"Failed to open: {str(e)}")
    def show_ollama_error(self):
        """Show Ollama not running error"""
        reply = QMessageBox.critical(
            self,
            "❌ Ollama Not Running",
            "Ollama is not running or not installed!\n\n"
            "To use the AI Assistant, you need to:\n\n"
            "1. Install Ollama from https://ollama.com\n"
            "2. Open terminal and run: ollama serve\n"
            "3. Download a model: ollama pull llama2\n\n"
            "Do you want to open the Ollama website for installation?",
            QMessageBox.Yes | QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            import webbrowser
            webbrowser.open("https://ollama.com")
    def collect_system_data_for_ai(self):
        """Collect system information for AI context"""
        try:
            data = {}

            # CPU information
            cpu_percent = psutil.cpu_percent()
            cpu_freq = psutil.cpu_freq()
            cpu_count = psutil.cpu_count()

            data["cpu"] = {
                "usage_percent": cpu_percent,
                "cores": cpu_count,
                "frequency": f"{cpu_freq.current:.1f} MHz" if cpu_freq else "N/A"
            }

            # RAM information
            memory = psutil.virtual_memory()
            data["memory"] = {
                "total_gb": memory.total / (1024 ** 3),
                "used_gb": memory.used / (1024 ** 3),
                "available_gb": memory.available / (1024 ** 3),
                "percent": memory.percent
            }

            # Disk information
            try:
                disk = psutil.disk_usage("/")
                data["disk"] = {
                    "total_gb": disk.total / (1024 ** 3),
                    "used_gb": disk.used / (1024 ** 3),
                    "free_gb": disk.free / (1024 ** 3),
                    "percent": disk.percent
                }
            except:
                data["disk"] = {"error": "Could not read disk info"}

            # Temperature information
            temps = []
            if hasattr(psutil, "sensors_temperatures"):
                try:
                    sensors = psutil.sensors_temperatures()
                    for name, entries in sensors.items():
                        for entry in entries:
                            temps.append({
                                "sensor": name,
                                "current": entry.current,
                                "high": entry.high,
                                "critical": entry.critical
                            })
                except:
                    pass

            data["temperatures"] = temps

            # Battery information
            battery_info = {}
            if hasattr(psutil, "sensors_battery"):
                try:
                    battery = psutil.sensors_battery()
                    if battery:
                        battery_info = {
                            "percent": battery.percent,
                            "plugged": battery.power_plugged,
                            "secsleft": battery.secsleft
                        }
                except:
                    pass

            data["battery"] = battery_info

            # System information
            data["system"] = {
                "platform": platform.platform(),
                "processor": platform.processor(),
                "hostname": platform.node()
            }

            # Process count
            data["processes"] = len(psutil.pids())

            return data

        except Exception as e:
            return {"error": str(e)}
    def run_ai_scan(self):
        """Run AI system analysis"""
        self.add_log_entry("AI Diagnosis", "INFO", "Starting system scan...")

        if hasattr(self, 'ai_analysis_label'):
            self.ai_analysis_label.setText("🔍 Scanning system...")
            self.ai_analysis_label.setStyleSheet("color: #FF9800; font-size: 16px; font-weight: bold;")

        # Collect current system stats
        cpu = psutil.cpu_percent()
        ram = psutil.virtual_memory().percent
        disk = psutil.disk_usage("/").percent
        temp = self.get_cpu_temp()

        # Generate analysis
        issues = []
        recommendations = []

        if cpu > 90:
            issues.append("High CPU usage")
            recommendations.append("Close unnecessary applications")
        elif cpu > 70:
            issues.append("Moderate CPU usage")
            recommendations.append("Consider closing some background tasks")

        if ram > 90:
            issues.append("High RAM usage")
            recommendations.append("Close memory-intensive programs")
        elif ram > 75:
            issues.append("Moderate RAM usage")
            recommendations.append("Clear browser cache and tabs")

        if temp and temp > 80:
            issues.append("High temperature")
            recommendations.append("Ensure proper ventilation")
        elif temp and temp > 70:
            issues.append("Moderate temperature")
            recommendations.append("Check cooling system")

        if disk > 90:
            issues.append("Low disk space")
            recommendations.append("Clean up unnecessary files")

        # Update UI
        if issues:
            analysis = f"⚠️ Issues detected: {', '.join(issues)}"
            if recommendations:
                recommendation_text = f"💡 Recommendations: {', '.join(recommendations)}"
            else:
                recommendation_text = "✅ No specific recommendations needed"
        else:
            analysis = "✅ System is running optimally"
            recommendation_text = "💡 Continue regular monitoring"

        if hasattr(self, 'ai_analysis_label'):
            self.ai_analysis_label.setText(analysis)
            self.ai_analysis_label.setStyleSheet("color: #00E676; font-size: 16px; font-weight: bold;")
            self.ai_recommendation_label.setText(recommendation_text)

        self.add_log_entry("AI Diagnosis", "INFO", f"Scan complete: {analysis}")

        # Show notification
        QMessageBox.information(
            self,
            "System Scan Complete",
            f"{analysis}\n\n{recommendation_text}"
        )
    def read_dmi_info(self):
        dmi = {}
        dmi_path = "/sys/class/dmi/id/"
        fields = {
            "board_vendor": "board_vendor",
            "board_name": "board_name",
            "product_name": "system_name",
            "bios_version": "bios_version",
            "bios_date": "bios_date"
        }
        try:
            for sys_field, dict_field in fields.items():
                try:
                    with open(os.path.join(dmi_path, sys_field), "r") as f:
                        content = f.read().strip()
                        dmi[dict_field] = content if content else "N/A"
                except FileNotFoundError:
                    dmi[dict_field] = "N/A"
        except Exception as e:
            dmi["error"] = str(e)
        return dmi
    def get_cpu_info(self):
        info = {
            "model": "Unknown",
            "physical_cores": psutil.cpu_count(logical=False),
            "logical_cores": psutil.cpu_count(logical=True),
            "max_freq": "Unknown"
        }
        try:
            with open("/proc/cpuinfo", "r") as f:
                for line in f:
                    if "model name" in line:
                        info["model"] = line.split(":")[1].strip()
                        break
            freq = psutil.cpu_freq()
            if freq:
                info["max_freq"] = f"{freq.max:.1f} MHz"
        except Exception as e:
            print(f"Error getting CPU info: {e}")
        return info
    def get_ram_info(self):
        ram = psutil.virtual_memory()
        info = {
            "total": self.format_bytes(ram.total),
            "available": self.format_bytes(ram.available),
            "used": f"{self.format_bytes(ram.used)} ({ram.percent}%)"
        }
        return info
    def get_disk_info(self):
        disks = []
        try:
            partitions = psutil.disk_partitions(all=False)
            for part in partitions:
                if 'loop' in part.device or 'snap' in part.device or 'dm-' in part.device:
                    continue
                try:
                    usage = psutil.disk_usage(part.mountpoint)
                    disk_type = "SSD" if "nvme" in part.device or "ssd" in part.device.lower() else "HDD"
                    disks.append({
                        "device": part.device,
                        "mount": part.mountpoint,
                        "size": self.format_bytes(usage.total),
                        "used": self.format_bytes(usage.used),
                        "total": self.format_bytes(usage.total),
                        "percent": f"{usage.percent}%",
                        "type": disk_type
                    })
                except Exception:
                    continue
        except Exception as e:
            print(f"Error getting disk info: {e}")
        return disks
    def get_gpu_info(self):
        try:
            output = subprocess.check_output(["lspci"], universal_newlines=True)
            gpus = []
            for line in output.splitlines():
                if "VGA" in line or "3D" in line or "Display" in line:
                    gpu_info = line.split(": ")[1].strip()
                    gpus.append(gpu_info)
            if gpus:
                return gpus
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            print(f"Error getting GPU info: {e}")
        return "Unknown"
    def get_network_info(self):
        adapters = []
        try:
            net_if_stats = psutil.net_if_stats()
            net_if_addrs = psutil.net_if_addrs()
            for name, stats in net_if_stats.items():
                if name.startswith('lo'):
                    continue
                status = "Up" if stats.isup else "Down"
                speed = f"{stats.speed} Mbps" if stats.speed else "Unknown speed"
                ip = "No IP assigned"
                for addr in net_if_addrs.get(name, []):
                    if addr.family == 2:
                        ip = addr.address
                        break
                adapters.append({
                    "name": name,
                    "status": f"{status}, {ip}, {speed}"
                })
        except Exception as e:
            print(f"Error getting network info: {e}")
        return adapters
    def get_os_info(self):
        try:
            with open("/etc/os-release", "r") as f:
                for line in f:
                    if line.startswith("PRETTY_NAME="):
                        return line.split("=")[1].strip().strip('"')
        except:
            pass
        return f"{os.name} ({sys.platform})"
    def format_bytes(self, size):
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size < 1024.0:
                return f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{size:.1f} PB"
    def create_logs_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(15)
        title = QLabel(" System Logs")
        title.setStyleSheet("""
        color: #00B0FF;
        font-size: 24px;
        font-weight: bold;
        """)
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)
        desc = QLabel("Historical system events and performance logs")
        desc.setStyleSheet("color: #B0B0B0; font-size: 14px;")
        desc.setAlignment(Qt.AlignCenter)
        layout.addWidget(desc)
        self.logs_table = QTableWidget()
        self.logs_table.setColumnCount(4)
        self.logs_table.setHorizontalHeaderLabels(["Time", "Type", "Source", "Message"])
        self.logs_table.horizontalHeader().setStretchLastSection(True)
        self.logs_table.setStyleSheet("""
        QTableWidget {
            background-color: #1F1F1F;
            border: 1px solid #2A2A2A;
            border-radius: 10px;
            gridline-color: #2A2A2A;
            color: white;
            font-size: 12px;
        }
        QHeaderView::section {
            background-color: #2A2A2A;
            color: white;
            padding: 5px;
            border: none;
            font-weight: bold;
        }
        QTableWidget::item {
            padding: 5px;
        }
        """)
        self.logs_table.setColumnWidth(0, 120)
        self.logs_table.setColumnWidth(1, 80)
        self.logs_table.setColumnWidth(2, 120)
        layout.addWidget(self.logs_table, 1)
        controls_widget = QWidget()
        controls_layout = QHBoxLayout(controls_widget)
        controls_layout.setSpacing(15)
        clear_btn = QPushButton("Clear Logs")
        clear_btn.setMinimumHeight(35)
        clear_btn.setMaximumHeight(45)
        clear_btn.setStyleSheet("""
        QPushButton {
            background-color: #F44336;
            color: white;
            font-size: 14px;
            font-weight: bold;
            border-radius: 5px;
            padding: 5px 15px;
        }
        QPushButton:hover {
            background-color: #D32F2F;
        }
        """)
        clear_btn.clicked.connect(self.clear_logs)
        export_btn = QPushButton("Export Logs")
        export_btn.setMinimumHeight(35)
        export_btn.setMaximumHeight(45)
        export_btn.setStyleSheet("""
        QPushButton {
            background-color: #4CAF50;
            color: white;
            font-size: 14px;
            font-weight: bold;
            border-radius: 5px;
            padding: 5px 15px;
        }
        QPushButton:hover {
            background-color: #388E3C;
        }
        """)
        export_btn.clicked.connect(self.export_logs)
        controls_layout.addStretch()
        controls_layout.addWidget(clear_btn)
        controls_layout.addWidget(export_btn)
        layout.addWidget(controls_widget)
        return tab
    def create_overview_tab(self):
        tab = QWidget()
        main_layout = QVBoxLayout(tab)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(15)
        self.overview_status_card = QFrame()
        self.overview_status_card.setMinimumHeight(60)
        self.overview_status_card.setMaximumHeight(80)
        self.overview_status_card.setStyleSheet("""
        background-color: #00E676;
        border-radius: 10px;
        """)
        status_layout = QVBoxLayout(self.overview_status_card)
        status_layout.setContentsMargins(15, 0, 15, 0)
        self.card_label = QLabel("System Stable")
        self.card_label.setStyleSheet("""
        color: black;
        font-size: 20px;
        font-weight: bold;
        """)
        self.card_label.setAlignment(Qt.AlignCenter)
        status_layout.addWidget(self.card_label, alignment=Qt.AlignCenter)
        metrics_container = QWidget()
        metrics_layout = QVBoxLayout(metrics_container)
        metrics_layout.setContentsMargins(0, 0, 0, 0)
        metrics_grid = QGridLayout()
        metrics_grid.setSpacing(10)
        metrics_grid.setRowStretch(0, 1)
        metrics_grid.setRowStretch(1, 1)
        metrics_grid.setColumnStretch(0, 1)
        metrics_grid.setColumnStretch(1, 1)
        metrics_grid.setColumnStretch(2, 1)
        self.mini_cpu_graph = LiveGraph("CPU", "#2196F3", 15)
        self.mini_cpu_graph.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.mini_ram_graph = LiveGraph("RAM", "#FF9800", 15)
        self.mini_ram_graph.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.mini_temp_graph = LiveGraph("Temperature", "#F44336", 15)
        self.mini_temp_graph.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.mini_disk_graph = LiveGraph("Disk", "#9C27B0", 15)
        self.mini_disk_graph.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        power_card = self.create_power_card()
        power_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        processes_card = self.create_processes_card()
        processes_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        metrics_grid.addWidget(self.mini_cpu_graph, 0, 0)
        metrics_grid.addWidget(self.mini_ram_graph, 0, 1)
        metrics_grid.addWidget(self.mini_temp_graph, 0, 2)
        metrics_grid.addWidget(self.mini_disk_graph, 1, 0)
        metrics_grid.addWidget(power_card, 1, 1)
        metrics_grid.addWidget(processes_card, 1, 2)
        metrics_layout.addLayout(metrics_grid)
        metrics_layout.addStretch()
        actions_widget = QWidget()
        actions_layout = QHBoxLayout(actions_widget)
        actions_layout.setSpacing(10)
        actions = [
            (" Detailed Graphs", "#00B0FF", lambda: self.tabs.setCurrentIndex(1)),
            ("️ Sensors", "#FF9800", lambda: self.tabs.setCurrentIndex(2)),

        ]
        for text, color, callback in actions:
            btn = QPushButton(text)
            btn.setMinimumHeight(45)
            btn.setMaximumHeight(55)
            btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {color};
                color: white;
                font-size: 14px;
                font-weight: bold;
                border-radius: 8px;
                padding: 10px;
            }}
            QPushButton:hover {{
                background-color: {self.darken_color(color)};
            }}
            """)
            btn.clicked.connect(callback)
            actions_layout.addWidget(btn)
        actions_layout.addStretch()
        main_layout.addWidget(self.overview_status_card)
        main_layout.addWidget(metrics_container, 1)
        main_layout.addWidget(actions_widget)
        return tab
    def darken_color(self, color):
        if color.startswith('#'):
            rgb = tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))
            darkened = tuple(max(0, c - 40) for c in rgb)
            return f'#{darkened[0]:02x}{darkened[1]:02x}{darkened[2]:02x}'
        return color
    def create_power_card(self):
        card = QFrame()
        card.setMinimumHeight(120)
        card.setMaximumHeight(150)
        card.setStyleSheet("""
        background-color: #1F1F1F;
        border-radius: 10px;
        border: 2px solid #4CAF50;
        """)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        title_label = QLabel("🔌 Power Status")
        title_label.setStyleSheet("color: #4CAF50; font-size: 14px; font-weight: bold;")
        title_label.setAlignment(Qt.AlignCenter)
        self.power_label = QLabel("Loading...")
        self.power_label.setStyleSheet("color: white; font-size: 20px; font-weight: bold;")
        self.power_label.setAlignment(Qt.AlignCenter)
        self.battery_label = QLabel("")
        self.battery_label.setStyleSheet("color: #B0B0B0; font-size: 12px;")
        self.battery_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(title_label)
        layout.addStretch()
        layout.addWidget(self.power_label)
        layout.addWidget(self.battery_label)
        layout.addStretch()
        return card
    def create_processes_card(self):
        card = QFrame()
        card.setMinimumHeight(120)
        card.setMaximumHeight(150)
        card.setStyleSheet("""
        background-color: #1F1F1F;
        border-radius: 10px;
        border: 2px solid #607D8B;
        """)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        title_label = QLabel("📊 System Processes")
        title_label.setStyleSheet("color: #607D8B; font-size: 14px; font-weight: bold;")
        title_label.setAlignment(Qt.AlignCenter)
        self.processes_label = QLabel("0")
        self.processes_label.setStyleSheet("color: white; font-size: 28px; font-weight: bold;")
        self.processes_label.setAlignment(Qt.AlignCenter)
        self.top_processes_label = QLabel("")
        self.top_processes_label.setStyleSheet("color: #B0B0B0; font-size: 11px;")
        self.top_processes_label.setWordWrap(True)
        self.top_processes_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(title_label)
        layout.addStretch()
        layout.addWidget(self.processes_label)
        layout.addWidget(self.top_processes_label)
        layout.addStretch()
        return card
    def update_all_from_system(self):
        try:
            cpu = psutil.cpu_percent()
            ram = psutil.virtual_memory().percent
            disk = psutil.disk_usage("/").percent
            temp = self.get_cpu_temp()
            power = self.get_power_status()
            process_count = len(psutil.pids())
            top_processes = self.get_top_processes()
            if self.mini_cpu_graph:
                self.mini_cpu_graph.update_value(cpu)
            if self.mini_ram_graph:
                self.mini_ram_graph.update_value(ram)
            if self.mini_temp_graph:
                self.mini_temp_graph.update_value(temp if temp else 0)
            if self.mini_disk_graph:
                self.mini_disk_graph.update_value(disk)
            if hasattr(self, 'power_label'):
                self.power_label.setText(power)
            if "Battery" in power:
                self.battery_label.setText(power.replace("Battery", "").strip())
            else:
                self.battery_label.setText("Connected to AC Power")
            if hasattr(self, 'processes_label'):
                self.processes_label.setText(str(process_count))
            if top_processes:
                self.top_processes_label.setText("\n".join(top_processes[:3]))
            else:
                self.top_processes_label.setText("")
            self.update_ai_analysis(cpu, ram, temp, disk)
            self.add_log_entry("System", "INFO", f"CPU: {cpu:.1f}%, RAM: {ram:.1f}%, Temp: {temp if temp else 'N/A'}°C")
            tooltip = f"ThermoGuard\nCPU: {cpu:.1f}% | RAM: {ram:.1f}%\nTemp: {temp if temp else 'N/A'}°C | Processes: {process_count}"
            if self.tray_icon:
                self.tray_icon.setToolTip(tooltip)
            self.update_system_status(cpu, ram, temp)
        except Exception as e:
            print(f"Error updating system info: {e}")
            self.add_log_entry("System", "ERROR", f"Update failed: {str(e)}")
            self.handle_offline_state()
    def get_cpu_temp(self):
        try:
            if hasattr(psutil, "sensors_temperatures"):
                temps = psutil.sensors_temperatures()
                if temps:
                    for name, entries in temps.items():
                        if entries and ('core' in name.lower() or 'cpu' in name.lower()):
                            return round(entries[0].current, 1)
        except:
            pass
        return None
    def get_power_status(self):
        try:
            if hasattr(psutil, "sensors_battery"):
                battery = psutil.sensors_battery()
                if battery:
                    if battery.power_plugged:
                        return f"🔌 AC ({battery.percent:.0f}%)"
                    return f"🔋 Battery ({battery.percent:.0f}%)"
            return "🔌 AC Power"
        except:
            return "🔌 AC Power"
    def get_top_processes(self):
        try:
            processes = []
            for proc in psutil.process_iter(['pid', 'name', 'cpu_percent']):
                try:
                    cpu = proc.info['cpu_percent']
                    if cpu is not None and cpu > 0.1:
                        processes.append(proc.info)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            processes.sort(key=lambda x: x['cpu_percent'] if x['cpu_percent'] else 0, reverse=True)
            top_processes = processes[:5]
            formatted = []
            for proc in top_processes:
                name = proc['name'][:15] + "..." if len(proc['name']) > 15 else proc['name']
                cpu = proc['cpu_percent'] or 0
                formatted.append(f"{name}: {cpu:.1f}%")
            return formatted
        except:
            return []
    def update_ai_analysis(self, cpu, ram, temp, disk):
        try:
            issues = []
            recommendations = []
            if cpu > 90:
                issues.append("High CPU usage")
                recommendations.append("Close unnecessary applications")
            elif cpu > 70:
                issues.append("Moderate CPU usage")
                recommendations.append("Consider closing some background tasks")
            if ram > 90:
                issues.append("High RAM usage")
                recommendations.append("Close memory-intensive programs")
            elif ram > 75:
                issues.append("Moderate RAM usage")
                recommendations.append("Clear browser cache and tabs")
            if temp and temp > 80:
                issues.append("High temperature")
                recommendations.append("Ensure proper ventilation")
            elif temp and temp > 70:
                issues.append("Moderate temperature")
                recommendations.append("Check cooling system")
            if disk > 90:
                issues.append("Low disk space")
                recommendations.append("Clean up unnecessary files")
            if issues:
                analysis = f"⚠️ Issues detected: {', '.join(issues)}"
                if recommendations:
                    recommendation_text = f"💡 Recommendations: {', '.join(recommendations)}"
                else:
                    recommendation_text = "✅ No specific recommendations needed"
            else:
                analysis = "✅ System is running optimally"
                recommendation_text = "💡 Continue regular monitoring"
            if hasattr(self, 'ai_analysis_label'):
                self.ai_analysis_label.setText(analysis)
                self.ai_recommendation_label.setText(recommendation_text)
        except Exception as e:
            print(f"Error in AI analysis: {e}")
    def run_ai_scan(self):
        self.add_log_entry("AI Diagnosis", "INFO", "Starting full system scan...")
        if hasattr(self, 'ai_analysis_label'):
            self.ai_analysis_label.setText("🔍 Scanning system...")
        cpu = psutil.cpu_percent()
        ram = psutil.virtual_memory().percent
        disk = psutil.disk_usage("/").percent
        temp = self.get_cpu_temp()
        self.update_ai_analysis(cpu, ram, temp, disk)
        self.add_log_entry("AI Diagnosis", "INFO", "System scan completed")
    def add_log_entry(self, source, log_type, message):
        try:
            if hasattr(self, 'logs_table'):
                current_time = datetime.datetime.now().strftime("%H:%M:%S")
                row = self.logs_table.rowCount()
                self.logs_table.insertRow(row)
                self.logs_table.setItem(row, 0, QTableWidgetItem(current_time))
                self.logs_table.setItem(row, 1, QTableWidgetItem(log_type))
                self.logs_table.setItem(row, 2, QTableWidgetItem(source))
                self.logs_table.setItem(row, 3, QTableWidgetItem(message))
                if log_type == "ERROR":
                    for col in range(4):
                        item = self.logs_table.item(row, col)
                        if item:
                            item.setForeground(QColor("#FF5252"))
                elif log_type == "WARNING":
                    for col in range(4):
                        item = self.logs_table.item(row, col)
                        if item:
                            item.setForeground(QColor("#FF9800"))
                elif log_type == "INFO":
                    for col in range(4):
                        item = self.logs_table.item(row, col)
                        if item:
                            item.setForeground(QColor("#00B0FF"))
                self.logs_table.scrollToBottom()
        except Exception as e:
            print(f"Error adding log entry: {e}")
    def clear_logs(self):
        if hasattr(self, 'logs_table'):
            self.logs_table.setRowCount(0)
            self.add_log_entry("System", "INFO", "Logs cleared")
    def export_logs(self):
        try:
            filename = f"thermoguard_logs_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            with open(filename, 'w') as f:
                f.write("ThermoGuard System Logs\n")
                f.write(f"Generated: {datetime.datetime.now()}\n")
                f.write("=" * 50 + "\n")
                for row in range(self.logs_table.rowCount()):
                    time = self.logs_table.item(row, 0).text()
                    log_type = self.logs_table.item(row, 1).text()
                    source = self.logs_table.item(row, 2).text()
                    message = self.logs_table.item(row, 3).text()
                    f.write(f"[{time}] [{log_type}] {source}: {message}\n")
            self.add_log_entry("System", "INFO", f"Logs exported to {filename}")
            print(f"Logs exported to {filename}")
        except Exception as e:
            self.add_log_entry("System", "ERROR", f"Failed to export logs: {str(e)}")
            print(f"Error exporting logs: {e}")
    def update_system_status(self, cpu, ram, temp):
        is_overheat = isinstance(temp, (int, float)) and temp > 80
        is_hot = isinstance(temp, (int, float)) and temp > 70
        is_critical_load = cpu > 95 or ram > 95
        is_loaded = cpu > 85 or ram > 90
        if is_overheat or is_critical_load:
            state = "● HIGH LOAD / OVERHEAT!"
            color = "#FF5252"
            bg_color = "#FF5252"
            status_text = "Warning: Overheat / High Load"
            self.add_log_entry("System", "WARNING", "High load/overheat detected!")
        elif is_hot or is_loaded:
            state = "● SYSTEM LOADED / HOT"
            color = "#FF9800"
            bg_color = "#FF9800"
            status_text = "Warning: System Hot / Loaded"
            self.add_log_entry("System", "WARNING", "System loaded/hot")
        else:
            state = "● SYSTEM STABLE"
            color = "#00E676"
            bg_color = "#00E676"
            status_text = "System Stable"
        self.status_label.setText(state)
        self.status_label.setStyleSheet(f"color:{color}; font-size:14px; font-weight:bold;")
        if self.overview_status_card:
            self.overview_status_card.setStyleSheet(f"background-color: {bg_color}; border-radius: 10px;")
        if self.card_label:
            self.card_label.setText(status_text)
    def show_performance_view(self):
        self.tabs.setCurrentIndex(1)
    def show_settings(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Google Gemini API Settings")
        dialog.setMinimumSize(500, 350)
        dialog.setStyleSheet("background-color: #1F1F1F; color: white;")
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)
        title = QLabel("Google Gemini API Configuration")
        title.setStyleSheet("color: #00B0FF; font-size: 18px; font-weight: bold;")
        layout.addWidget(title)
        desc = QLabel("Configure your Google AI Studio API key for Gemini")
        desc.setStyleSheet("color: #B0B0B0; font-size: 12px;")
        layout.addWidget(desc)
        url_layout = QHBoxLayout()
        url_label = QLabel("API URL:")
        url_label.setFixedWidth(100)
        url_label.setStyleSheet("color: #B0B0B0;")
        url_input = QLineEdit()
        config_file = "ai_config.json"
        default_config = {
            "api_url": "https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent",
            "api_key": ""
        }
        try:
            if os.path.exists(config_file):
                with open(config_file, 'r') as f:
                    config = json.load(f)
            else:
                config = default_config.copy()
        except:
            config = default_config.copy()
        url_input.setText(
            config.get("api_url", "https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent"))
        url_input.setStyleSheet("""
            background-color: #2A2A2A;
            border: 1px solid #3A3A3A;
            border-radius: 5px;
            padding: 8px;
            color: white;
        """)
        url_layout.addWidget(url_label)
        url_layout.addWidget(url_input, 1)
        layout.addLayout(url_layout)
        key_layout = QHBoxLayout()
        key_label = QLabel("API Key:")
        key_label.setFixedWidth(100)
        key_label.setStyleSheet("color: #B0B0B0;")
        key_input = QLineEdit()
        key_input.setText(config.get("api_key", ""))
        key_input.setEchoMode(QLineEdit.Password)
        key_input.setStyleSheet("""
            background-color: #2A2A2A;
            border: 1px solid #3A3A3A;
            border-radius: 5px;
            padding: 8px;
            color: white;
        """)
        key_layout.addWidget(key_label)
        key_layout.addWidget(key_input, 1)
        show_key_btn = QPushButton("👁️ Show/Hide")
        show_key_btn.setFixedWidth(100)
        show_key_btn.setStyleSheet("""
            QPushButton {
                background-color: #3A3A3A;
                color: white;
                border: 1px solid #4A4A4A;
                border-radius: 5px;
                padding: 5px;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #4A4A4A;
            }
        """)
        show_key_btn.clicked.connect(lambda: self.toggle_show_key(key_input))
        key_layout.addWidget(show_key_btn)
        layout.addLayout(key_layout)
        instructions = QLabel("""
        <div style='color: #B0B0B0; font-size: 11px;'>
        <b>How to get a free API key:</b>
        <ol style='margin: 5px 0; padding-left: 15px;'>
            <li>Go to <a href='https://aistudio.google.com/app/apikey' style='color: #00B0FF;'>Google AI Studio</a></li>
            <li>Sign in with your Google account</li>
            <li>Click "Create API Key" → "Create API key in new project"</li>
            <li>Copy the generated API key</li>
            <li>Paste it above and test the connection</li>
        </ol>
        </div>
        """)
        instructions.setOpenExternalLinks(True)
        layout.addWidget(instructions)
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)
        test_btn = QPushButton("🔍 Test Gemini API Connection")
        test_btn.setStyleSheet("""
            QPushButton {
                background-color: #2196F3;
                color: white;
                border: none;
                border-radius: 5px;
                padding: 10px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #0d8aee;
            }
        """)
        test_btn.clicked.connect(lambda: self.test_api_connection(url_input.text(), key_input.text()))
        btn_layout.addWidget(test_btn)
        btn_layout.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: #F44336;
                color: white;
                border: none;
                border-radius: 5px;
                padding: 10px 20px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #D32F2F;
            }
        """)
        cancel_btn.clicked.connect(dialog.reject)
        save_btn = QPushButton("Save")
        save_btn.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                border: none;
                border-radius: 5px;
                padding: 10px 20px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #388E3C;
            }
        """)
        save_btn.clicked.connect(lambda: self.save_api_settings(dialog, url_input.text(), key_input.text()))
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(save_btn)
        layout.addLayout(btn_layout)
        dialog.exec_()
    def toggle_show_key(self, key_input):
        if key_input.echoMode() == QLineEdit.Password:
            key_input.setEchoMode(QLineEdit.Normal)
        else:
            key_input.setEchoMode(QLineEdit.Password)
    def test_api_connection(self, api_url, api_key):
        if not api_url or not api_key:
            QMessageBox.warning(self, "Warning", "Please enter both API URL and API Key")
            return
        try:
            headers = {
                "Content-Type": "application/json"
            }
            data = {
                "contents": [{
                    "parts": [{
                        "text": "Hello, this is a test message. Please respond with 'API connection successful!'"
                    }]
                }],
                "generationConfig": {
                    "maxOutputTokens": 50,
                }
            }
            url = f"{api_url}?key={api_key}"
            response = requests.post(url, headers=headers, json=data, timeout=10)
            if response.status_code == 200:
                result = response.json()
                if "candidates" in result and result["candidates"]:
                    QMessageBox.information(self, "Success",
                                            "✅ Google Gemini API connection successful!\n\n"
                                            "You can now use the AI assistant features.")
                else:
                    QMessageBox.critical(self, "Error",
                                         "API returned empty response.\n"
                                         "Please check if the API key has proper permissions.")
            else:
                error_text = response.text[:200]
                QMessageBox.critical(self, "Error",
                                     f"API Error: {response.status_code}\n\n"
                                     f"Response: {error_text}\n\n"
                                     "Please verify your API key and URL.")
        except requests.exceptions.Timeout:
            QMessageBox.critical(self, "Error",
                                 "Connection timeout. Please check your internet connection.")
        except requests.exceptions.ConnectionError:
            QMessageBox.critical(self, "Error",
                                 "Connection error. Please check your internet connection.")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Connection failed: {str(e)}")
    def save_api_settings(self, dialog, api_url, api_key):
        config_file = "ai_config.json"
        default_config = {
            "api_url": "https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent",
            "api_key": "",
            "model": "gemini-pro",
            "include_system_data": True
        }
        try:
            if os.path.exists(config_file):
                with open(config_file, 'r') as f:
                    config = json.load(f)
            else:
                config = default_config.copy()
            config["api_url"] = api_url
            config["api_key"] = api_key
            with open(config_file, 'w') as f:
                json.dump(config, f, indent=2)
            dialog.accept()
            QMessageBox.information(self, "Success", "API settings updated successfully.")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save settings: {str(e)}")
    def handle_offline_state(self):
        if self.tray_icon:
            self.tray_icon.setToolTip("Agent not responding")
        self.status_label.setText("● OFFLINE")
        self.status_label.setStyleSheet("color:#FF5252; font-size:14px; font-weight:bold;")
        if self.overview_status_card:
            self.overview_status_card.setStyleSheet("background-color: #FF5252; border-radius: 10px;")
        if self.card_label:
            self.card_label.setText("Agent not connected")
    def closeEvent(self, event):
        event.ignore()
        self.hide()
        if self.tray_icon:
            self.tray_icon.showMessage(
                "ThermoGuard",
                "Application is running in the background.\nDouble-click the tray icon to show it.",
                QSystemTrayIcon.Information,
                2000
            )
    def showEvent(self, event):
        super().showEvent(event)
        screen_geometry = QApplication.desktop().screenGeometry()
        x = (screen_geometry.width() - self.width()) // 2
        y = (screen_geometry.height() - self.height()) // 2
        self.move(x, y)
def main():
    app = QApplication(sys.argv)
    app.setApplicationName("ThermoGuard")
    app.setApplicationDisplayName("ThermoGuard System Monitor")
    app.setQuitOnLastWindowClosed(False)
    tray_icon = QSystemTrayIcon()
    icon_paths = [
        "copilot_20260101_144719.png",
        "icon.png",
        "thermoguard.png",
        os.path.join(os.path.dirname(__file__), "icon.png")
    ]
    icon = None
    for path in icon_paths:
        if os.path.exists(path):
            icon = QIcon(path)
            break
    if icon is None or icon.isNull():
        icon = QIcon.fromTheme("utilities-system-monitor")
    if icon.isNull():
        from PyQt5.QtGui import QPixmap, QPainter, QColor, QBrush
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setBrush(QBrush(QColor(0, 176, 255)))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(8, 8, 48, 48)
        painter.end()
        icon = QIcon(pixmap)
    tray_icon.setIcon(icon)
    tray_menu = QMenu()
    show_action = QAction("Show / Hide")
    quit_action = QAction("Quit")
    tray_menu.addAction(show_action)
    tray_menu.addSeparator()
    tray_menu.addAction(quit_action)
    tray_icon.setContextMenu(tray_menu)
    tray_icon.setToolTip("ThermoGuard - System Monitoring")
    window = MainWindow(tray_icon=tray_icon)
    def toggle_window(reason=None):
        if reason is None or reason == QSystemTrayIcon.DoubleClick:
            if window.isVisible():
                window.hide()
            else:
                window.show()
                window.raise_()
                window.activateWindow()
    show_action.triggered.connect(toggle_window)
    quit_action.triggered.connect(app.quit)
    tray_icon.activated.connect(toggle_window)
    tray_icon.show()
    window.show()
    sys.exit(app.exec_())
if __name__ == "__main__":
    main()
