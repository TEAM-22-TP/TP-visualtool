#!/usr/bin/env python3

# main.py

import argparse
import json
import sys
import threading
from collections import deque
from itertools import count
from pathlib import Path
from datetime import datetime, timezone
from functools import partial
import urllib.error
import urllib.request

from PyQt5 import QtCore, QtGui, QtWidgets

from graph import build_graph_tab, graph_update_from_frame

try:
    import paho.mqtt.client as mqtt
except ImportError:
    mqtt = None

sofname = "potato-fe"
version = "2026.04.06"

# ./feed.json
DB_FEED_PATH = Path(__file__).with_name("feed.json")

# ./simple.json - we get this from translation layer. does not have keys. XXX: useless at this point in the project
DB_SIMPLE_PATH = Path(__file__).with_name("simple.json")

# ./scene.json
DB_SCENE_PATH = Path(__file__).with_name("scene.json")

# fallback ./config.json
DEFAULT_CONFIG_PATH = Path(__file__).with_name("config.json")

# signal metadata for each telemetry point
DEFAULT_SIGNAL_PROFILES = [
    {"signal": "feed_temp", "asset": "Intake Hopper", "unit": "°C", "min": 5, "max": 18, "desc": "Raw potato intake temperature"},
    {"signal": "peeler_load", "asset": "Washer/Peeler", "unit": "t/h", "min": 4, "max": 12, "desc": "Peeler throughput"},
    {"signal": "washer_turbidity", "asset": "Washer/Peeler", "unit": "NTU", "min": 10, "max": 160, "desc": "Wash water turbidity"},
    {"signal": "optical_yield", "asset": "Optical Sorter", "unit": "%", "min": 92, "max": 99.5, "desc": "Sorter good product yield"},
    {"signal": "steamer_temp", "asset": "Steamer/Blancher", "unit": "°C", "min": 80, "max": 105, "desc": "Blancher steam temperature"},
    {"signal": "dryer_humidity", "asset": "Dryer", "unit": "%", "min": 3, "max": 12, "desc": "Outlet moisture"},
    {"signal": "dryer_out_temp", "asset": "Dryer", "unit": "°C", "min": 70, "max": 95, "desc": "Dryer discharge temperature"},
    {"signal": "seasoner_salt_flow", "asset": "Seasoner", "unit": "g/kg", "min": 15, "max": 32, "desc": "Salt dosing rate"},
    {"signal": "packager_speed", "asset": "Packer", "unit": "bags/min", "min": 80, "max": 140, "desc": "Packaging throughput"},
    {"signal": "energy_kwh", "asset": "Energy Center", "unit": "kWh", "min": 250, "max": 420, "desc": "Hourly energy draw"},
    {"signal": "ambient_temp", "asset": "Ambient Node", "unit": "°C", "min": 18, "max": 32, "desc": "Hall ambient temperature"},
]

# simple mode has one synthetic signal as taken from the translation layer
SIMPLE_SIGNAL_PROFILES = [
    {
        "signal": "DEMO",
        "asset": "DEMO",
        "unit": "DEMO",
        "min": -1.0,
        "max": 1.0,
        "auto_range": True,
        "desc": "DEMO",
    }
]


def _safe_int_reason_code(reason_code) -> int:
    try:
        return int(reason_code)
    except Exception:
        try:
            return int(reason_code.value)
        except Exception:
            return -1


def _utc_iso_from_ms(ts_ms):
    if isinstance(ts_ms, (int, float)):
        return datetime.fromtimestamp(float(ts_ms) / 1000.0, tz=timezone.utc).isoformat(timespec="milliseconds")
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _enqueue_mqtt_log(feed: dict, message: str):
    runtime = feed.get("mqtt_runtime")
    if not runtime:
        return
    stamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    line = f"[{stamp}] {message}"
    with runtime["log_lock"]:
        runtime["log_queue"].append(line)
    runtime["log_sem"].release()


def _enqueue_mqtt_payload(feed: dict, payload_obj):
    runtime = feed.get("mqtt_runtime")
    if not runtime:
        return
    with runtime["in_lock"]:
        runtime["in_queue"].append(payload_obj)
    runtime["in_sem"].release()


def _drain_mqtt_logs(feed: dict, log_panel, max_lines: int = 300):
    runtime = feed.get("mqtt_runtime")
    if not runtime:
        return
    lines = []
    for _ in range(max_lines):
        if not runtime["log_sem"].acquire(blocking=False):
            break
        with runtime["log_lock"]:
            if runtime["log_queue"]:
                lines.append(runtime["log_queue"].popleft())
    for line in lines:
        log_panel.appendPlainText(line)


def _drain_mqtt_payloads(feed: dict, max_items: int = 500):
    runtime = feed.get("mqtt_runtime")
    if not runtime:
        return []
    out = []
    for _ in range(max_items):
        if not runtime["in_sem"].acquire(blocking=False):
            break
        with runtime["in_lock"]:
            if runtime["in_queue"]:
                out.append(runtime["in_queue"].popleft())
    return out


def _normalize_mqtt_payload_to_packet(payload: dict) -> dict:
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}

    ts = _utc_iso_from_ms(payload.get("timestamp_ms"))
    signal = payload.get("signal")
    if not isinstance(signal, str) or not signal.strip():
        signal = payload.get("mqtt_topic", "mqtt_value")

    asset_id = source.get("browse_path") or source.get("endpoint") or payload.get("mqtt_topic") or "mqtt"

    value = payload.get("value")
    if not isinstance(value, (int, float)):
        raise ValueError("MQTT payload missing numeric 'value'")

    packet = {
        "ts": ts,
        "asset_id": str(asset_id),
        "signal": str(signal),
        "value": float(value),
        "unit": str(payload.get("unit", "")),
        "quality": str(payload.get("quality", "GOOD")),
        "batch_id": str(payload.get("batch_id", "")),
        "seq": payload.get("timestamp_ms", ""),
    }
    return packet


def _mqtt_on_connect(client, userdata, flags, reason_code, properties=None):
    feed = userdata["feed"]
    runtime = feed["mqtt_runtime"]
    rc = _safe_int_reason_code(reason_code)
    if rc == 0:
        topic_sub = feed["source"].get("topic_sub", "")
        qos = int(feed["source"].get("qos", 0))
        client.subscribe(topic_sub, qos=qos)
        runtime["connected_event"].set()
        _enqueue_mqtt_log(feed, f"[ii] MQTT connected and subscribed to '{topic_sub}' (qos={qos})")
    else:
        _enqueue_mqtt_log(feed, f"[ee] MQTT connect failed, rc={rc}")


def _mqtt_on_disconnect(client, userdata, reason_code, properties=None):
    feed = userdata["feed"]
    runtime = feed["mqtt_runtime"]
    rc = _safe_int_reason_code(reason_code)
    runtime["connected_event"].clear()
    if runtime["stop_event"].is_set():
        _enqueue_mqtt_log(feed, "[ii] MQTT disconnected")
    else:
        _enqueue_mqtt_log(feed, f"[ww] MQTT unexpected disconnect, rc={rc}")


def _mqtt_on_message(client, userdata, msg):
    feed = userdata["feed"]
    runtime = feed["mqtt_runtime"]

    if runtime["stop_event"].is_set():
        return

    try:
        text = msg.payload.decode("utf-8", errors="replace")
        obj = json.loads(text)

        # Accept single object or list of objects
        if isinstance(obj, list):
            count_ok = 0
            for row in obj:
                if isinstance(row, dict):
                    _enqueue_mqtt_payload(feed, row)
                    count_ok += 1
            _enqueue_mqtt_log(feed, f"[ii] MQTT message on '{msg.topic}' with {count_ok} packet(s)")
        elif isinstance(obj, dict):
            _enqueue_mqtt_payload(feed, obj)
            _enqueue_mqtt_log(feed, f"[ii] MQTT message on '{msg.topic}'")
        else:
            _enqueue_mqtt_log(feed, f"[ee] MQTT payload is not object/list on topic '{msg.topic}'")
    except json.JSONDecodeError as exc:
        _enqueue_mqtt_log(feed, f"[ee] MQTT JSON parse error on topic '{msg.topic}': {exc}")
    except Exception as exc:
        _enqueue_mqtt_log(feed, f"[ee] MQTT message handling failed: {exc}")


def mqtt_start(feed: dict):
    if feed.get("source_type") != "mqtt":
        return

    if mqtt is None:
        _enqueue_mqtt_log(feed, "[ii] can't find paho-mqtt. MQTT is therefore disabled.")
        return

    runtime = feed["mqtt_runtime"]
    if runtime["started"]:
        return

    src = feed["source"]
    client_id = src.get("client_id") or f"{sofname}-{version}".replace("/", "-")
    broker = src["broker"]
    port = int(src.get("port", 1883))
    keepalive = int(src.get("keepalive", 60))
    username = src.get("username")
    password = src.get("password")

    client = mqtt.Client(client_id=client_id, clean_session=True)
    if username:
        client.username_pw_set(username, password=password)

    client.user_data_set({"feed": feed})
    client.on_connect = _mqtt_on_connect
    client.on_message = _mqtt_on_message
    client.on_disconnect = _mqtt_on_disconnect

    runtime["stop_event"].clear()
    runtime["client"] = client
    runtime["started"] = True

    try:
        client.connect_async(broker, port=port, keepalive=keepalive)
        client.loop_start()
        _enqueue_mqtt_log(feed, f"[ii] MQTT connecting to {broker}:{port} as '{client_id}'")
    except Exception as exc:
        runtime["started"] = False
        runtime["client"] = None
        _enqueue_mqtt_log(feed, f"[ee] MQTT start failed: {exc}")


def mqtt_stop(feed: dict):
    if feed.get("source_type") != "mqtt":
        return
    runtime = feed.get("mqtt_runtime")
    if not runtime or not runtime["started"]:
        return

    runtime["stop_event"].set()
    runtime["connected_event"].clear()
    client = runtime.get("client")
    runtime["started"] = False
    runtime["client"] = None

    if client is not None:
        try:
            client.disconnect()
        except Exception:
            pass
        try:
            client.loop_stop()
        except Exception:
            pass

    _enqueue_mqtt_log(feed, "[ii] MQTT stream stopped")


def mqtt_publish_dummy_control(feed: dict):
    if feed.get("source_type") != "mqtt":
        return False, "current source is not MQTT"

    runtime = feed.get("mqtt_runtime", {})
    client = runtime.get("client")

    if client is None:
        return False, "MQTT client not started"

    src = feed["source"]
    topic_pub = src.get("topic_pub", "control/potato-fe")
    qos = int(src.get("qos", 0))

    payload = {
        "type": "control",
        "timestamp_ms": int(datetime.now(timezone.utc).timestamp() * 1000),
        "source": sofname,
        "message": "hello from the frontend!",
        "target": "potato-line",
    }

    try:
        info = client.publish(topic_pub, json.dumps(payload), qos=qos, retain=False)
        if info.rc == mqtt.MQTT_ERR_SUCCESS:
            return True, f"published control to '{topic_pub}'"
        return False, f"publish failed rc={info.rc}"
    except Exception as exc:
        return False, f"publish failed: {exc}"


def _validate_and_normalize_config(raw_config: dict, config_path: Path, default_source_path: Path) -> dict:
    fallback = {
        "polling": 1.0,
        "source": {"type": "file", "location": str(default_source_path.resolve())},
    }

    if not isinstance(raw_config, dict):
        raise ValueError("config must be a JSON object")

    polling = raw_config.get("polling", fallback["polling"])
    if not isinstance(polling, (int, float)) or polling <= 0:
        raise ValueError("config.polling must be a positive number (seconds)")
    polling = float(polling)

    source = raw_config.get("source")
    if not isinstance(source, dict):
        raise ValueError("config.source must be a JSON object")

    source_type = source.get("type")
    if source_type not in {"file", "network", "mqtt"}:
        raise ValueError("config.source.type must be 'file', 'network', or 'mqtt'")

    if source_type in {"file", "network"}:
        location = source.get("location")
        if not isinstance(location, str) or not location.strip():
            raise ValueError("config.source.location must be a non-empty string")

        if source_type == "file":
            p = Path(location).expanduser()
            if not p.is_absolute():
                p = (config_path.parent / p).resolve()
            source_location = str(p)
            display_location = location
        else:
            source_location = location.strip()
            display_location = source_location

        return {
            "polling": polling,
            "source": {
                "type": source_type,
                "location": source_location,
                "display_location": display_location,
            },
        }

    # MQTT config
    broker = source.get("broker")
    if not isinstance(broker, str) or not broker.strip():
        raise ValueError("config.source.broker must be a non-empty string for mqtt")

    port = source.get("port", 1883)
    if not isinstance(port, int) or not (1 <= port <= 65535):
        raise ValueError("config.source.port must be int in [1, 65535]")

    keepalive = source.get("keepalive", 60)
    if not isinstance(keepalive, int) or keepalive <= 0:
        raise ValueError("config.source.keepalive must be a positive int")

    topic_sub = source.get("topic_sub")
    if not isinstance(topic_sub, str) or not topic_sub.strip():
        raise ValueError("config.source.topic_sub must be a non-empty string for mqtt")

    topic_pub = source.get("topic_pub", "control/potato-fe")
    if not isinstance(topic_pub, str) or not topic_pub.strip():
        raise ValueError("config.source.topic_pub must be a non-empty string")

    qos = source.get("qos", 0)
    if not isinstance(qos, int) or qos not in {0, 1, 2}:
        raise ValueError("config.source.qos must be 0, 1, or 2")

    username = source.get("username")
    password = source.get("password")
    client_id = source.get("client_id", f"{sofname}-{version}".replace("/", "-"))

    if username is not None and not isinstance(username, str):
        raise ValueError("config.source.username must be a string")
    if password is not None and not isinstance(password, str):
        raise ValueError("config.source.password must be a string")
    if not isinstance(client_id, str) or not client_id.strip():
        raise ValueError("config.source.client_id must be a non-empty string")

    display_location = f"mqtt://{broker}:{port} sub={topic_sub}"

    return {
        "polling": polling,
        "source": {
            "type": "mqtt",
            "broker": broker.strip(),
            "port": port,
            "keepalive": keepalive,
            "topic_sub": topic_sub.strip(),
            "topic_pub": topic_pub.strip(),
            "qos": qos,
            "username": username,
            "password": password,
            "client_id": client_id.strip(),
            "display_location": display_location,
            "location": display_location,  # compatibility
        },
    }


def load_runtime_config(config_path: Path, default_source_path: Path) -> dict:
    fallback = {
        "polling": 1.0,
        "source": {"type": "file", "location": str(default_source_path.resolve()), "display_location": str(default_source_path)},
    }

    try:
        with config_path.open("r", encoding="utf-8") as fh:
            raw = json.load(fh)
        return _validate_and_normalize_config(raw, config_path, default_source_path)
    except FileNotFoundError:
        print(f"[ww] config not found: {config_path} - using fallback.")
    except json.JSONDecodeError as exc:
        print(f"[ee] config JSON parse error in {config_path}: {exc}.")
    except ValueError as exc:
        print(f"[ee] invalid config in {config_path}: {exc}.")
    return fallback


# config the JSON feed source and its sequence generator
def create_feed(config: dict, simple: bool = False) -> dict:
    src = config["source"]
    feed = {
        "source_type": src["type"],
        "source_location": src.get("location", ""),
        "source_display_location": src.get("display_location", src.get("location", "")),
        "sequence_source": count(1),
        "simple": simple,
        "source": src,
    }

    if src["type"] == "mqtt":
        feed["mqtt_runtime"] = {
            "client": None,
            "started": False,
            "connected_event": threading.Event(),
            "stop_event": threading.Event(),
            "in_queue": deque(),
            "in_lock": threading.Lock(),
            "in_sem": threading.Semaphore(0),
            "log_queue": deque(),
            "log_lock": threading.Lock(),
            "log_sem": threading.Semaphore(0),
        }

    return feed


# helper that reads JSON payload either from local disk or network
def read_payload(feed: dict):
    source_type = feed.get("source_type")
    source_location = feed.get("source_location")
    user_agent = f"{sofname}/{version}"

    if source_type == "file":
        with Path(source_location).open("r", encoding="utf-8") as source:
            return json.load(source)

    if source_type == "network":
        req = urllib.request.Request(
            source_location,
            headers={"User-Agent": user_agent},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw_bytes = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
            text = raw_bytes.decode(charset, errors="replace")
            return json.loads(text)

    raise ValueError(f"unsupported source type for read_payload: {source_type}")


# reads the latest packets and validate the JSON structure
def read_latest_packets(feed: dict) -> list[dict]:
    simple_mode = feed.get("simple", False)
    source_type = feed.get("source_type")
    source_location = feed.get("source_location")

    try:
        if source_type == "mqtt":
            incoming = _drain_mqtt_payloads(feed)
            if not incoming:
                return []

            if simple_mode:
                out = []
                for payload in incoming:
                    if not isinstance(payload, dict):
                        continue
                    value = payload.get("value")
                    if isinstance(value, (int, float)):
                        out.append({"value": float(value)})
                return out

            out = []
            for payload in incoming:
                if not isinstance(payload, dict):
                    continue
                try:
                    out.append(_normalize_mqtt_payload_to_packet(payload))
                except ValueError as exc:
                    _enqueue_mqtt_log(feed, f"[ee] invalid MQTT payload: {exc}")
            return out

        payload = read_payload(feed)

        if simple_mode:
            if not isinstance(payload, dict):
                raise ValueError("simple mode source must be a JSON object")
            value = payload.get("value")
            if not isinstance(value, (int, float)):
                raise ValueError("simple mode source must contain numeric field 'value'")
            # normalize to frame-like packet
            return [{"value": float(value)}]

        if isinstance(payload, list):
            return payload
        raise ValueError("database JSON must be a list of packets")
    except FileNotFoundError:
        return []
    except urllib.error.HTTPError as exc:
        print(f"[ee] HTTP error reading {source_location}: {exc.code} {exc.reason}")
    except urllib.error.URLError as exc:
        print(f"[ee] network error reading {source_location}: {exc.reason}")
    except TimeoutError:
        print(f"[ee] timeout while reading {source_location}")
    except json.JSONDecodeError as exc:
        where = source_location if source_type == "network" else Path(source_location).name
        print(f"[ee] JSON parse error in {where}: {exc}")
    except ValueError as exc:
        print(f"[ee] {exc}")
    return []


# helper, detect if the feed currently has any packets
def feed_empty(feed: dict) -> bool:
    return not read_latest_packets(feed)


# make a telemetry frame
#   clone stored packets; stamp metadata
def next_frame(feed: dict) -> list[dict]:
    packets = read_latest_packets(feed)
    if not packets:
        return []

    now = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    frame = []

    if feed.get("simple", False):
        for packet in packets:
            value = packet.get("value", 0.0)
            frame.append(
                {
                    "ts": "DEMO",
                    "asset_id": "DEMO",
                    "signal": "DEMO",
                    "value": float(value),
                    "unit": "DEMO",
                    "quality": "DEMO",
                    "batch_id": "DEMO",
                    "seq": "DEMO",
                }
            )
        return frame

    for packet in packets:
        clone = dict(packet)
        clone.setdefault("ts", now)
        clone["seq"] = next(feed["sequence_source"])
        frame.append(clone)
    return frame


# load the process scene definition from scene.json
def load_scene_definition(path: Path) -> dict:
    fallback = {"scene": {"width": 1500, "height": 420}, "nodes": []}
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError("scene.json must be a JSON object at the top level")
        return data
    except FileNotFoundError:
        print(f"[ee] scene definition not found: {path}")
    except json.JSONDecodeError as exc:
        print(f"[ee] scene JSON parse error: {exc}")
    except ValueError as exc:
        print(f"[ee] {exc}")
    return fallback


# config the telemetry QTableWidget
def configure_table(table):
    headers = ["Timestamp (UTC)", "Asset", "Signal", "Value", "Unit", "Quality", "Batch", "Seq"]
    table.setColumnCount(len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
    table.verticalHeader().setVisible(False)
    table.setAlternatingRowColors(True)
    table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
    table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)


# repopulate the table widget with the latest telemetry frame
def refresh_table_with_frame(table, frame):
    table.setRowCount(0)
    for packet in frame:
        row = table.rowCount()
        table.insertRow(row)
        values = [
            packet.get("ts", ""),
            packet.get("asset_id", ""),
            packet.get("signal", ""),
            f'{packet.get("value", 0):.3f}',
            packet.get("unit", ""),
            packet.get("quality", ""),
            packet.get("batch_id", ""),
            str(packet.get("seq", "")),
        ]
        for col, val in enumerate(values):
            item = QtWidgets.QTableWidgetItem(val)
            item.setTextAlignment(QtCore.Qt.AlignCenter)
            # only highlight known degraded qualities
            if packet.get("quality") in {"WARN", "ERR"}:
                item.setBackground(QtGui.QColor(255, 205, 210))
            table.setItem(row, col, item)


# builds the graphical process flow diagram from a scene definition dict
# and returns item references keyed by node label
def build_process_scene(scene, scene_def: dict) -> dict:
    process_items = {}

    scene_meta = scene_def.get("scene", {})
    scene_width = scene_meta.get("width", 1500)
    scene_height = scene_meta.get("height", 420)
    scene.setSceneRect(0, 0, scene_width, scene_height)

    for node in scene_def.get("nodes", []):
        x = float(node.get("x", 0))
        y = float(node.get("y", 0))
        w = float(node.get("w", 150))
        h = float(node.get("h", 90))
        label_text = node.get("label", "")
        pen_color = QtGui.QColor(node.get("pen_color", "#90A4AE"))
        brush_color = QtGui.QColor(node.get("brush_color", "#455A64"))
        pen_width = float(node.get("pen_width", 1.5))

        # rect item positioned at (x, y); local rect starts at (0, 0)
        item = QtWidgets.QGraphicsRectItem(0, 0, w, h)
        item.setPos(x, y)
        item.setPen(QtGui.QPen(pen_color, pen_width))
        item.setBrush(QtGui.QBrush(brush_color))
        item.setFlag(QtWidgets.QGraphicsItem.ItemIsSelectable, True)
        scene.addItem(item)

        # make label a child of the rect item so it moves together with it
        label = QtWidgets.QGraphicsSimpleTextItem(label_text, item)
        label.setBrush(QtGui.QBrush(QtGui.QColor("#ECEFF1")))
        label.setPos(10, 35)

        label.setAcceptedMouseButtons(QtCore.Qt.NoButton)

        process_items[label_text] = {
            "uuid": node.get("uuid", ""),
            "item": item,
            "label": label,
            "default_pen_color": pen_color,
            "default_brush_color": brush_color,
            "pen_width": pen_width,
        }

    return process_items


# edit mode
def toggle_edit_mode(state, process_items, graphics_view):
    enabled = state == QtCore.Qt.Checked
    for info in process_items.values():
        info["item"].setFlag(QtWidgets.QGraphicsItem.ItemIsMovable, enabled)
        info["item"].setOpacity(0.55 if enabled else 1.0)
    mode = QtWidgets.QGraphicsView.RubberBandDrag if enabled else QtWidgets.QGraphicsView.ScrollHandDrag
    graphics_view.setDragMode(mode)


# colors process nodes based on quality
def update_process_view(packet, process_items, signal_index):
    meta = signal_index.get(packet.get("signal"))
    if not meta:
        return
    asset = meta["asset"]
    info = process_items.get(asset)
    if not info:
        return
    quality = packet.get("quality", "GOOD")
    if quality == "ERR":
        # red
        brush_color = QtGui.QColor("#F44336")
        pen_color = QtGui.QColor("#B71C1C")
    elif quality == "WARN":
        # orange
        brush_color = QtGui.QColor("#FFC107")
        pen_color = QtGui.QColor("#FFAB00")
    else:
        # def
        brush_color = info["default_brush_color"]
        pen_color = info["default_pen_color"]
    info["item"].setBrush(QtGui.QBrush(brush_color))
    info["item"].setPen(QtGui.QPen(pen_color, 1.8 if quality in {"WARN", "ERR"} else info["pen_width"]))


# calculate + update toplevel KPI based on inbound signals
def update_kpis(packet, kpi_labels, kpi_cache):
    signal = packet.get("signal")
    value = packet.get("value")
    if signal == "packager_speed":
        throughput = value * 0.15 * 60
        kpi_cache["throughput"] = throughput
        kpi_labels["throughput"].setText(f"{throughput:,.0f}")
    elif signal == "energy_kwh":
        kpi_cache["energy_raw"] = value
        throughput = kpi_cache.get("throughput")
        if throughput and throughput > 0:
            tons_per_hour = throughput / 1000
            intensity = value / tons_per_hour
            kpi_labels["energy"].setText(f"{intensity:.1f}")
        else:
            kpi_labels["energy"].setText("--")
    elif signal == "dryer_humidity":
        deviation = value - 5.0
        kpi_labels["moisture"].setText(f"{deviation:+.1f}")
    elif signal == "optical_yield":
        kpi_labels["yield"].setText(f"{value:.2f}")


# exec a single refresh cycle:
#  -> load packets -> update UI -> log output -> repeat
def handle_stream_tick(feed, table, log_panel, process_items, signal_index, kpi_labels, kpi_cache, stream_state, source_label, graph_state):
    # drain async mqtt logs first
    _drain_mqtt_logs(feed, log_panel)

    frame = next_frame(feed)
    if not frame:
        if not stream_state["waiting_for_data"]:
            log_panel.appendPlainText(f"[ww] no packets found in {source_label}, waiting")
            stream_state["waiting_for_data"] = True
        return

    stream_state["waiting_for_data"] = False
    refresh_table_with_frame(table, frame)
    log_panel.appendPlainText(json.dumps(frame, indent=2))
    graph_update_from_frame(frame, graph_state)

    for packet in frame:
        update_process_view(packet, process_items, signal_index)
        update_kpis(packet, kpi_labels, kpi_cache)


# start-stop
def toggle_stream(active, timer, status_label, source_combo, stream_state, polling_seconds, feed, log_panel):
    stream_state["waiting_for_data"] = False

    if active:
        if feed["source_type"] == "mqtt":
            mqtt_start(feed)
            timer.start(120)  # UI-side drain cadence; event-driven ingest itself is callback-based
            status_label.setText(f"Datastream running: {source_combo.currentText()} (event-driven)")
            _drain_mqtt_logs(feed, log_panel)
        else:
            polling_ms = max(100, int(float(polling_seconds) * 1000))
            timer.start(polling_ms)
            status_label.setText(f"Datastream running: {source_combo.currentText()} ({polling_seconds:g}s)")
    else:
        timer.stop()
        if feed["source_type"] == "mqtt":
            mqtt_stop(feed)
            _drain_mqtt_logs(feed, log_panel)
        status_label.setText("stream idle")


def on_send_control_clicked(feed, log_panel):
    ok, msg = mqtt_publish_dummy_control(feed)
    prefix = "[ii]" if ok else "[ee]"
    log_panel.appendPlainText(f"{prefix} {msg}")


# entry point
def main():
    parser = argparse.ArgumentParser(description="potato-fe telemetry UI")
    parser.add_argument(
        "--simple",
        action="store_true",
        help="simple values",
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="path to config JSON file (default: ./config.json)",
    )
    args = parser.parse_args()

    app = QtWidgets.QApplication(sys.argv)
    QtCore.QCoreApplication.setOrganizationName(f"{sofname}-{version}")

    active_signal_profiles = SIMPLE_SIGNAL_PROFILES if args.simple else DEFAULT_SIGNAL_PROFILES
    signal_lookup = {profile["signal"]: profile for profile in active_signal_profiles}
    default_db_path = DB_SIMPLE_PATH if args.simple else DB_FEED_PATH

    runtime_config = load_runtime_config(Path(args.config).expanduser(), default_db_path)

    main_window = QtWidgets.QMainWindow()
    mode_suffix = " [DEMO]" if args.simple else ""
    main_window.setWindowTitle(f"{sofname}-{version}{mode_suffix}")
    main_window.resize(1400, 860)

    central_widget = QtWidgets.QWidget()
    root_layout = QtWidgets.QVBoxLayout(central_widget)

    control_layout = QtWidgets.QHBoxLayout()
    ingest_source_combo = QtWidgets.QComboBox()

    source_type = runtime_config["source"]["type"]
    source_display = runtime_config["source"]["display_location"]
    source_display_text = f"{source_type}:{source_display}"
    ingest_source_combo.addItems([source_display_text])

    ingest_button = QtWidgets.QPushButton("Start")
    ingest_button.setCheckable(True)
    ingest_status_label = QtWidgets.QLabel("Idle")
    ingest_status_label.setStyleSheet("color: #90CAF9;")
    edit_checkbox = QtWidgets.QCheckBox("Edit mode")
    control_pub_button = QtWidgets.QPushButton("Send dummy control")

    control_layout.addWidget(QtWidgets.QLabel("Ingest source:"))
    control_layout.addWidget(ingest_source_combo)
    control_layout.addWidget(ingest_button)
    control_layout.addWidget(ingest_status_label)
    control_layout.addWidget(control_pub_button)
    control_layout.addStretch(1)
    control_layout.addWidget(edit_checkbox)
    root_layout.addLayout(control_layout)

    tabs = QtWidgets.QTabWidget()
    root_layout.addWidget(tabs, 1)
    tabs.setTabPosition(QtWidgets.QTabWidget.South)

    # main tab
    main_tab = QtWidgets.QWidget()
    main_tab_layout = QtWidgets.QVBoxLayout(main_tab)

    kpi_box = QtWidgets.QGroupBox("Live KPIs")
    kpi_layout = QtWidgets.QGridLayout(kpi_box)
    kpi_titles = {
        "throughput": "Line Throughput (kg/h)",
        "energy": "Energy Intensity (kWh/t)",
        "moisture": "Moisture Deviation (%)",
        "yield": "Net Yield (%)",
    }
    kpi_labels = {}
    row = 0
    for key, title in kpi_titles.items():
        label_title = QtWidgets.QLabel(title)
        value_label = QtWidgets.QLabel("--")
        value_label.setStyleSheet("font-size: 20px; color: #FFC107;")
        kpi_layout.addWidget(label_title, row, 0)
        kpi_layout.addWidget(value_label, row, 1)
        kpi_labels[key] = value_label
        row += 1

    splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)

    table_container = QtWidgets.QWidget()
    table_layout = QtWidgets.QVBoxLayout(table_container)
    table_label = QtWidgets.QLabel("Telemetry snapshot")
    data_table = QtWidgets.QTableWidget()
    configure_table(data_table)
    table_layout.addWidget(table_label)
    table_layout.addWidget(data_table)
    splitter.addWidget(table_container)

    process_container = QtWidgets.QWidget()
    process_layout = QtWidgets.QVBoxLayout(process_container)
    process_label = QtWidgets.QLabel("Process flow")
    graphics_view = QtWidgets.QGraphicsView()
    graphics_scene = QtWidgets.QGraphicsScene()
    graphics_view.setScene(graphics_scene)
    graphics_view.setRenderHints(QtGui.QPainter.Antialiasing | QtGui.QPainter.TextAntialiasing)
    graphics_view.setDragMode(QtWidgets.QGraphicsView.ScrollHandDrag)

    scene_def = load_scene_definition(DB_SCENE_PATH)
    process_items = build_process_scene(graphics_scene, scene_def)

    process_layout.addWidget(process_label)
    process_layout.addWidget(graphics_view)
    splitter.addWidget(process_container)
    splitter.setSizes([700, 700])

    main_tab = QtWidgets.QWidget()
    main_tab_layout = QtWidgets.QVBoxLayout(main_tab)
    main_tab_layout.addWidget(kpi_box)
    main_tab_layout.addWidget(splitter, 1)

    # graph tab
    graph_tab, graph_state = build_graph_tab(active_signal_profiles)

    # log tab
    logs_tab = QtWidgets.QWidget()
    logs_layout = QtWidgets.QVBoxLayout(logs_tab)
    logs_layout.addWidget(QtWidgets.QLabel("Logs"))
    log_panel = QtWidgets.QPlainTextEdit()
    log_panel.setReadOnly(True)
    logs_layout.addWidget(log_panel, 1)

    tabs.addTab(main_tab, "Main")
    tabs.addTab(graph_tab, "Graph")
    tabs.addTab(logs_tab, "Logs")

    main_window.setCentralWidget(central_widget)

    feed = create_feed(runtime_config, simple=args.simple)
    stream_state = {"waiting_for_data": False}
    kpi_cache = {}

    # enable/disable control publish button based on source
    control_pub_button.setEnabled(feed["source_type"] == "mqtt")
    control_pub_button.clicked.connect(partial(on_send_control_clicked, feed=feed, log_panel=log_panel))

    stream_timer = QtCore.QTimer()
    stream_timer.timeout.connect(
        partial(
            handle_stream_tick,
            feed=feed,
            table=data_table,
            log_panel=log_panel,
            process_items=process_items,
            signal_index=signal_lookup,
            kpi_labels=kpi_labels,
            kpi_cache=kpi_cache,
            stream_state=stream_state,
            source_label=source_display_text,
            graph_state=graph_state,
        )
    )

    ingest_button.toggled.connect(
        partial(
            toggle_stream,
            timer=stream_timer,
            status_label=ingest_status_label,
            source_combo=ingest_source_combo,
            stream_state=stream_state,
            polling_seconds=runtime_config["polling"],
            feed=feed,
            log_panel=log_panel,
        )
    )

    edit_checkbox.stateChanged.connect(
        partial(toggle_edit_mode, process_items=process_items, graphics_view=graphics_view)
    )

    main_window.show()
    rc = app.exec_()

    # safety stop for mqtt background loop
    mqtt_stop(feed)
    sys.exit(rc)


if __name__ == "__main__":
    main()
