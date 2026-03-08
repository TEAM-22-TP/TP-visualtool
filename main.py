#!/usr/bin/env python3

# main.py

import json
import sys
from itertools import count
from pathlib import Path
from datetime import datetime, timezone
from functools import partial
from PyQt5 import QtCore, QtGui, QtWidgets

from graph import build_graph_tab, graph_update_from_frame

sofname = "potato-fe"
version = "2026.03.08"

# ./feed.json
DB_FEED_PATH = Path(__file__).with_name("feed.json")

# signal metadata for each telemetry point
# template
signal_profiles = [
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
signal_lookup = {profile["signal"]: profile for profile in signal_profiles}


# config the JSON feed path and its sequence generator
def create_feed(path: Path) -> dict:
    return {"path": path, "sequence_source": count(1)}


# reads the latest packets and validate the JSON structure
def read_latest_packets(feed: dict) -> list[dict]:
    path = feed["path"]
    try:
        with path.open("r", encoding="utf-8") as source:
            payload = json.load(source)
        if isinstance(payload, list):
            return payload
        raise ValueError("database JSON must be a list of packets")
    except FileNotFoundError:
        return []
    except json.JSONDecodeError as exc:
        print(f"[ee] JSON parse error: {exc}")
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
    for packet in packets:
        clone = dict(packet)
        clone.setdefault("ts", now)
        clone["seq"] = next(feed["sequence_source"])
        frame.append(clone)
    return frame


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
            if packet.get("quality") != "GOOD":
                item.setBackground(QtGui.QColor(255, 205, 210))
            table.setItem(row, col, item)


# builds the graphical process flow diagram and return item references
def build_process_scene(scene):
    process_items = {}
    scene.setSceneRect(0, 0, 1500, 420)
    primary_chain = ["Intake Hopper", "Washer/Peeler", "Optical Sorter", "Steamer", "Fryer", "Dryer", "Seasoner", "Packer"]
    utility_nodes = [("Energy Center", 180, 280), ("Ambient Node", 420, 280)]
    x_offset = 40
    y_level = 90
    for name in primary_chain:
        rect = QtCore.QRectF(x_offset, y_level, 150, 90)
        item = QtWidgets.QGraphicsRectItem(rect)
        pen_color = QtGui.QColor("#90A4AE")
        brush_color = QtGui.QColor("#455A64")
        item.setPen(QtGui.QPen(pen_color, 1.5))
        item.setBrush(QtGui.QBrush(brush_color))
        item.setFlag(QtWidgets.QGraphicsItem.ItemIsSelectable, True)
        scene.addItem(item)

        label = scene.addSimpleText(name)
        label.setBrush(QtGui.QBrush(QtGui.QColor("#ECEFF1")))
        label.setPos(x_offset + 10, y_level + 35)

        process_items[name] = {
            "item": item,
            "default_pen_color": pen_color,
            "default_brush_color": brush_color,
        }
        x_offset += 170

    for name, x_pos, y_pos in utility_nodes:
        rect = QtCore.QRectF(x_pos, y_pos, 150, 90)
        item = QtWidgets.QGraphicsRectItem(rect)
        pen_color = QtGui.QColor("#B0BEC5")
        brush_color = QtGui.QColor("#37474F")
        item.setPen(QtGui.QPen(pen_color, 1.2))
        item.setBrush(QtGui.QBrush(brush_color))
        item.setFlag(QtWidgets.QGraphicsItem.ItemIsSelectable, True)
        scene.addItem(item)

        label = scene.addSimpleText(name)
        label.setBrush(QtGui.QBrush(QtGui.QColor("#ECEFF1")))
        label.setPos(x_pos + 10, y_pos + 35)

        process_items[name] = {
            "item": item,
            "default_pen_color": pen_color,
            "default_brush_color": brush_color,
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
    info["item"].setPen(QtGui.QPen(pen_color, 1.8 if quality in {"WARN", "ERR"} else 1.5))


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
def handle_stream_tick(feed, table, log_panel, process_items, signal_index, kpi_labels, kpi_cache, timer, stream_state, db_path):
    frame = next_frame(feed)
    if not frame:
        if not stream_state["waiting_for_data"]:
            log_panel.appendPlainText(f"[ww] no packets found in {db_path.name}, waiting")
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
def toggle_stream(active, timer, status_label, source_combo, stream_state):
    stream_state["waiting_for_data"] = False
    if active:
        timer.start(1000)
        status_label.setText(f"Datastream running: {source_combo.currentText()}")
    else:
        timer.stop()
        status_label.setText("stream idle")


# entry point
def main():
    app = QtWidgets.QApplication(sys.argv)
    QtCore.QCoreApplication.setOrganizationName(f"{sofname}-{version}")

    main_window = QtWidgets.QMainWindow()
    main_window.setWindowTitle(f"{sofname}-{version}")
    main_window.resize(1400, 860)

    central_widget = QtWidgets.QWidget()
    root_layout = QtWidgets.QVBoxLayout(central_widget)

    control_layout = QtWidgets.QHBoxLayout()
    ingest_source_combo = QtWidgets.QComboBox()
    ingest_source_combo.addItems(["JSON"])
    ingest_button = QtWidgets.QPushButton("Start")
    ingest_button.setCheckable(True)
    ingest_status_label = QtWidgets.QLabel("Idle")
    ingest_status_label.setStyleSheet("color: #90CAF9;")
    edit_checkbox = QtWidgets.QCheckBox("Edit mode")

    control_layout.addWidget(QtWidgets.QLabel("Ingest source:"))
    control_layout.addWidget(ingest_source_combo)
    control_layout.addWidget(ingest_button)
    control_layout.addWidget(ingest_status_label)
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
    process_items = build_process_scene(graphics_scene)

    process_layout.addWidget(process_label)
    process_layout.addWidget(graphics_view)
    splitter.addWidget(process_container)
    splitter.setSizes([700, 700])

    main_tab = QtWidgets.QWidget()
    main_tab_layout = QtWidgets.QVBoxLayout(main_tab)
    main_tab_layout.addWidget(kpi_box)
    main_tab_layout.addWidget(splitter, 1)

    # graph tab
    graph_tab, graph_state = build_graph_tab(signal_profiles)

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

    feed = create_feed(DB_FEED_PATH)
    stream_state = {"waiting_for_data": False}
    kpi_cache = {}

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
            timer=stream_timer,
            stream_state=stream_state,
            db_path=DB_FEED_PATH,
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
        )
    )

    edit_checkbox.stateChanged.connect(
        partial(toggle_edit_mode, process_items=process_items, graphics_view=graphics_view)
    )

    main_window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
