# graph.py

from functools import partial
from PyQt5 import QtCore, QtGui, QtWidgets

def build_graph_tab(signal_profiles):
    profile_lookup = {row["signal"]: row for row in signal_profiles}

    tab = QtWidgets.QWidget()
    root = QtWidgets.QVBoxLayout(tab)

    controls = QtWidgets.QHBoxLayout()
    signal_combo = QtWidgets.QComboBox()
    for row in signal_profiles:
        label = f'{row["signal"]}  ({row["unit"]})'
        signal_combo.addItem(label, row["signal"])

    unit_label = QtWidgets.QLabel("Unit: --")
    range_label = QtWidgets.QLabel("Range: --")
    current_label = QtWidgets.QLabel("Current: --")
    current_label.setStyleSheet("font-size: 18px; color: #80DEEA;")

    controls.addWidget(QtWidgets.QLabel("Signal:"))
    controls.addWidget(signal_combo, 1)
    controls.addWidget(unit_label)
    controls.addWidget(range_label)
    controls.addWidget(current_label)
    root.addLayout(controls)

    graph_view = QtWidgets.QGraphicsView()
    graph_view.setRenderHints(QtGui.QPainter.Antialiasing | QtGui.QPainter.TextAntialiasing)
    graph_view.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
    graph_view.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
    graph_view.setFrameShape(QtWidgets.QFrame.NoFrame)
    graph_view.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)

    scene = QtWidgets.QGraphicsScene()
    graph_view.setScene(scene)
    root.addWidget(graph_view, 1)

    state = {
        "tab": tab,
        "scene": scene,
        "view": graph_view,
        "signal_combo": signal_combo,
        "profile_lookup": profile_lookup,
        "selected_signal": signal_profiles[0]["signal"] if signal_profiles else None,
        "history": [],
        "max_points": 240,
        "unit_label": unit_label,
        "range_label": range_label,
        "current_label": current_label,
    }

    signal_combo.currentIndexChanged.connect(partial(_on_signal_changed, state=state))

    original_resize = graph_view.resizeEvent

    def _resize_event(event):
        original_resize(event)
        redraw_graph(state)

    graph_view.resizeEvent = _resize_event

    _refresh_meta_labels(state)
    redraw_graph(state)
    return tab, state


def _on_signal_changed(_index, state):
    signal = state["signal_combo"].currentData()
    state["selected_signal"] = signal
    state["history"] = []
    _refresh_meta_labels(state)
    redraw_graph(state)


def _refresh_meta_labels(state):
    signal = state["selected_signal"]
    meta = state["profile_lookup"].get(signal, {})
    if not meta:
        state["unit_label"].setText("Unit: --")
        state["range_label"].setText("Range: --")
        state["current_label"].setText("Current: --")
        return

    unit = meta.get("unit", "")
    lo = meta.get("min", 0)
    hi = meta.get("max", 1)
    state["unit_label"].setText(f"Unit: {unit}")
    state["range_label"].setText(f"Range: {lo} .. {hi}")
    state["current_label"].setText("Current: --")


def graph_update_from_frame(frame, state):
    signal = state.get("selected_signal")
    if not signal:
        return

    changed = False
    for packet in frame:
        if packet.get("signal") != signal:
            continue
        value = packet.get("value")
        if not isinstance(value, (int, float)):
            continue
        state["history"].append(float(value))
        if len(state["history"]) > state["max_points"]:
            state["history"] = state["history"][-state["max_points"] :]
        changed = True

    if changed:
        meta = state["profile_lookup"].get(signal, {})
        unit = meta.get("unit", "")
        latest = state["history"][-1]
        state["current_label"].setText(f"Current: {latest:.3f} {unit}")
        redraw_graph(state)


def redraw_graph(state):
    scene = state["scene"]
    view = state["view"]
    scene.clear()

    w = max(320, view.viewport().width())
    h = max(220, view.viewport().height())
    scene.setSceneRect(0, 0, w, h)

    selected = state.get("selected_signal")
    meta = state["profile_lookup"].get(selected)
    if not meta:
        return

    lo = float(meta.get("min", 0.0))
    hi = float(meta.get("max", 1.0))
    if hi <= lo:
        hi = lo + 1.0

    left = 72
    right = 24
    top = 28
    bottom = 52

    pw = max(20, w - left - right)
    ph = max(20, h - top - bottom)

    bg = QtWidgets.QGraphicsRectItem(left, top, pw, ph)
    bg.setPen(QtGui.QPen(QtGui.QColor("#37474F"), 1))
    bg.setBrush(QtGui.QBrush(QtGui.QColor("#1E272E")))
    scene.addItem(bg)

    axis_pen = QtGui.QPen(QtGui.QColor("#90A4AE"), 1.2)
    scene.addLine(left, top + ph, left + pw, top + ph, axis_pen)
    scene.addLine(left, top, left, top + ph, axis_pen)

    grid_pen = QtGui.QPen(QtGui.QColor("#455A64"), 1, QtCore.Qt.DashLine)
    ticks = 5
    for i in range(ticks + 1):
        y = top + (ph * i / ticks)
        scene.addLine(left, y, left + pw, y, grid_pen)
        v = hi - (hi - lo) * (i / ticks)
        t = scene.addSimpleText(f"{v:.2f}")
        t.setBrush(QtGui.QBrush(QtGui.QColor("#CFD8DC")))
        t.setPos(6, y - 9)

    min_line_pen = QtGui.QPen(QtGui.QColor("#42A5F5"), 1)
    max_line_pen = QtGui.QPen(QtGui.QColor("#EF5350"), 1)
    scene.addLine(left, top + ph, left + pw, top + ph, min_line_pen)
    scene.addLine(left, top, left + pw, top, max_line_pen)

    title = scene.addSimpleText(f'{meta.get("signal")} [{meta.get("unit", "")}]')
    title.setBrush(QtGui.QBrush(QtGui.QColor("#ECEFF1")))
    title.setPos(left, 4)

    history = state["history"]
    if len(history) < 2:
        empty = scene.addSimpleText("Waiting for data...")
        empty.setBrush(QtGui.QBrush(QtGui.QColor("#B0BEC5")))
        empty.setPos(left + 8, top + 8)
        return

    path = QtGui.QPainterPath()
    n = len(history)
    den = max(1, n - 1)

    for i, value in enumerate(history):
        x = left + (pw * i / den)
        norm = (value - lo) / (hi - lo)
        norm = 0.0 if norm < 0 else 1.0 if norm > 1 else norm
        y = top + ph * (1.0 - norm)
        if i == 0:
            path.moveTo(x, y)
        else:
            path.lineTo(x, y)

    line_item = QtWidgets.QGraphicsPathItem(path)
    line_item.setPen(QtGui.QPen(QtGui.QColor("#26C6DA"), 2))
    scene.addItem(line_item)
