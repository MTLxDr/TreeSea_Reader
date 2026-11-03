"""Foreground-only TAB hold/toggle; one node type per prediction frame."""
from __future__ import annotations

from PySide6.QtCore import QElapsedTimer, QEvent, QObject, QTimer, Qt
from PySide6.QtGui import QColor, QFont, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from .catalog import NODE_NAMES
from .probability import rounded_percentages

HOLD_MS = 1000
TYPE_MS = 2000
FLASH_MS = 300
RETAIN_MS = 3000


def normalized_name(name):
    return "居民据点" if name == "“居民”据点" else name


def prediction_frames(result):
    """Percentages are per node, never normalized across map positions."""
    if not result or result.get("needs_review") or result.get("needs_node_review"):
        return []
    groups = {}
    families = {}
    for node in result.get("nodes", []):
        if node.get("type") not in {"unknown-mystery", "unknown-ferocity"}:
            continue
        rows = (node.get("probabilities") or {}).get("estimates", [])
        units = rounded_percentages([r["probability"] for r in rows])
        combined = {}
        for row, unit in zip(rows, units):
            if row["probability"] <= 0:
                continue
            name = normalized_name(row["name"])
            if name in {"未知的诡秘", "未知的凶戾", "unknown-mystery", "unknown-ferocity", "未识别"}:
                continue
            entry = combined.setdefault(name, {"probability": 0., "percent": 0})
            entry["probability"] += row["probability"]
            entry["percent"] += unit
        for name, values in combined.items():
            family = 0 if node["type"] == "unknown-mystery" else 1
            families[name] = min(families.get(name, family), family)
            groups.setdefault(name, []).append({"node": node, **values,
                "display": f"{values['percent']}%" if values["percent"] else "<1%"})
    order = {normalized_name(name): i for i, name in enumerate(NODE_NAMES.values())}
    return [{"name": name, "family": "mystery" if families[name] == 0 else "ferocity",
             "locations": groups[name]} for name in
            sorted(groups, key=lambda name: (families[name], order.get(name, 999), name))]


class ProbabilityOverlay(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.held = False
        self.active = False
        self.retaining = False
        self.frames = []
        self.index = 0
        self.items = []
        self.hidden_items = []
        self.dim = False
        self.elapsed = QElapsedTimer()
        self.hold_timer = QTimer(self)
        self.hold_timer.setSingleShot(True)
        self.hold_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.hold_timer.setInterval(HOLD_MS)
        self.hold_timer.timeout.connect(self.begin)
        self.type_timer = QTimer(self)
        self.type_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.type_timer.setInterval(TYPE_MS)
        self.type_timer.timeout.connect(self.next_type)
        self.flash_timer = QTimer(self)
        self.flash_timer.setInterval(FLASH_MS)
        self.flash_timer.timeout.connect(self.flash)
        self.release_timer = QTimer(self)
        self.release_timer.setSingleShot(True)
        self.release_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.release_timer.setInterval(RETAIN_MS)
        self.release_timer.timeout.connect(self.cancel)
        QApplication.instance().installEventFilter(self)

    def eligible(self):
        return (QApplication.activeWindow() is self.window and self.window.isActiveWindow()
                and self.window.isVisible() and not self.window.isMinimized()
                and not self.window.busy
                and self.window.tabs.currentWidget() in (self.window.map_view, self.window.screenshot_view)
                and not (self.window.tabs.currentWidget() is self.window.screenshot_view
                         and ((self.window.last_scan or {}).get("page_type") == "inventory"
                              or self.window.image is None)))

    def mode(self):
        return self.window.ui_options.get("tab_mode", "hold")

    def running(self):
        return self.active and not self.retaining and (self.mode() == "toggle" or self.held)

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind == QEvent.Type.ApplicationDeactivate or (
                watched is self.window and kind in (QEvent.Type.WindowDeactivate, QEvent.Type.Hide,
                                                     QEvent.Type.Close)):
            self.cancel()
        if kind not in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            return False
        if event.key() != Qt.Key.Key_Tab:
            return False
        belongs = isinstance(watched, QWidget) and watched.window() is self.window
        if kind == QEvent.Type.KeyRelease and self.held:
            if event.isAutoRepeat():
                return True
            if self.mode() == "toggle":
                self.held = False
                return True
            short = not self.active and self.elapsed.elapsed() < HOLD_MS
            if self.active and self.eligible():
                self.retain_current()
            else:
                self.cancel()
            if short and self.eligible():
                self.window.focusNextChild()
            return True
        if not belongs or not self.eligible() or event.modifiers() != Qt.KeyboardModifier.NoModifier:
            return False
        if kind == QEvent.Type.ShortcutOverride:
            event.accept()
            return True
        if kind == QEvent.Type.KeyPress:
            if not self.held and not event.isAutoRepeat():
                if self.mode() == "toggle" and self.running():
                    self.retain_current()
                    self.held = True  # Ignore repeat key-downs until the real release.
                else:
                    self.cancel()
                    self.held = True
                    self.elapsed.start()
                    if self.mode() == "toggle":
                        self.begin(immediate=True)
                    else:
                        self.hold_timer.start(HOLD_MS)
            return True
        return False

    def begin(self, immediate=False):
        if not self.held or not self.eligible():
            self.cancel()
            return
        # Precise timers must not make a sub-second press activate the overlay.
        if not immediate and self.elapsed.elapsed() < HOLD_MS:
            self.hold_timer.start(HOLD_MS - self.elapsed.elapsed())
            return
        self.frames = prediction_frames(self.window.result)
        if not self.frames:
            self.window.status.setText("当前地图没有可展示的节点概率：请确认模板、节点类型及识别冲突。")
            return
        self.active = True
        self.index = 0
        self.show_frame()
        self.type_timer.start()
        self.flash_timer.start()

    def clear_items(self):
        for scene, item in self.items:
            scene.removeItem(item)
        self.items.clear()
        for item in self.hidden_items:
            item.setVisible(True)
        self.hidden_items.clear()

    def retain_current(self):
        self.held = False
        self.retaining = True
        self.hold_timer.stop()
        self.type_timer.stop()
        self.flash_timer.stop()
        self.dim = False
        self.release_timer.start(RETAIN_MS)
        self.show_frame()

    def cancel(self, *_):
        self.hold_timer.stop()
        self.type_timer.stop()
        self.flash_timer.stop()
        self.release_timer.stop()
        self.held = self.active = self.retaining = False
        self.clear_items()
        self.frames = []
        self.window.prediction_label.hide()

    def refresh(self):
        if not self.active:
            return
        if not self.eligible():
            self.cancel()
            return
        previous = self.frames[self.index]["name"] if self.frames else None
        self.frames = prediction_frames(self.window.result)
        if not self.frames:
            self.cancel()
            return
        selected = next((i for i, f in enumerate(self.frames) if f["name"] == previous), None)
        if self.retaining and selected is None:
            self.cancel()
            return
        self.index = selected if selected is not None else 0
        self.show_frame()

    def next_type(self):
        if self.retaining:
            return
        if not self.eligible() or not self.running():
            self.cancel()
            return
        self.index = (self.index + 1) % len(self.frames)
        self.show_frame()

    def flash(self):
        if self.retaining:
            return
        if not self.eligible() or not self.running():
            self.cancel()
            return
        self.dim = not self.dim
        for _, item in self.items:
            if item.data(2):
                item.setOpacity(.50 if self.dim else 1.)

    def add_item(self, view, item, flash=True):
        item.setZValue(20)
        item.setData(0, "probability_prediction")
        item.setData(1, self.frames[self.index]["name"])
        item.setData(2, flash)
        item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.items.append((view.scene, item))

    def show_frame(self):
        self.clear_items()
        self.dim = False
        frame = self.frames[self.index]
        for view in (self.window.map_view, self.window.screenshot_view):
            # The inventory screenshot is not the current map screenshot.
            if view.screenshot and ((self.window.last_scan or {}).get("page_type") == "inventory"
                                    or self.window.image is None):
                continue
            spacing = (self.window.result.get("grid", {}).get("spacing", 100)
                       if view.screenshot else 130)
            scale = max(abs(view.transform().m11()), .01)
            radius = max(spacing * .23, 13 / scale)
            for row in frame["locations"]:
                node = row["node"]
                center = node.get("pixel") if view.screenshot else [node["slot"][0]*130+80,
                                                                   node["slot"][1]*130+60]
                if center is None:
                    continue
                x, y = center
                for item in view.node_visuals.get(("main", tuple(node["slot"])), []):
                    if item.isVisible():
                        self.hidden_items.append(item)
                        item.setVisible(False)
                # Screenshot labels are baked into the background image. Keep
                # this opaque mask steady while the prediction icon pulses.
                if view.screenshot:
                    mask = view.scene.addRect(x-spacing*.46, y-spacing*.40, spacing*.92, spacing*.98,
                        QPen(Qt.PenStyle.NoPen), QColor("#102029"))
                    mask.setData(3, "background_mask")
                    self.add_item(view, mask, flash=False)
                    mask.setZValue(19)
                circle = view.scene.addEllipse(x-radius, y-radius, radius*2, radius*2,
                    QPen(QColor("#fff08a"), max(2., spacing*.025)), QColor(20, 43, 48, 240))
                self.add_item(view, circle)
                icon_path = view.prediction_icon_paths.get(frame["name"])
                if icon_path:
                    pixmap = QPixmap(str(icon_path))
                    pixmap = pixmap.scaled(round(radius*2.5), round(radius*2.5),
                        Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                    icon = view.scene.addPixmap(pixmap)
                    icon.setPos(x-pixmap.width()/2, y-pixmap.height()/2)
                    icon.setData(3, "icon")
                    icon.setData(4, str(icon_path))
                    self.add_item(view, icon)
                label_font = QFont("Microsoft YaHei UI")
                label_font.setPointSizeF(9 / scale)
                label = view.scene.addText(frame["name"], label_font)
                label.setDefaultTextColor(QColor("#fff5b5"))
                label.setPos(x-label.boundingRect().width()/2, y+radius)
                label.setData(3, "type_label")
                label_rect = label.boundingRect().translated(label.pos())
                if view.screenshot:
                    label_rect.setLeft(min(label_rect.left(), x-spacing*.46))
                    label_rect.setRight(max(label_rect.right(), x+spacing*.46))
                label_background = view.scene.addRect(label_rect, QPen(Qt.PenStyle.NoPen), QColor("#102029"))
                self.add_item(view, label_background, flash=False)
                self.add_item(view, label, flash=False)
                label.setZValue(22)
                label_background.setZValue(21)
                font = QFont("Microsoft YaHei UI")
                font.setPointSizeF(11 / scale)
                font.setWeight(QFont.Weight.Bold)
                text = view.scene.addText(row["display"], font)
                text.setDefaultTextColor(QColor("#fff5b5"))
                text.setPos(x+radius*.8, y-radius*1.6)
                text.setToolTip(f"{frame['name']}：{row['probability']:.4%}；本节点的经验条件概率")
                rect = text.boundingRect().translated(text.pos()).adjusted(-3/scale, -1/scale, 3/scale, 1/scale)
                background = view.scene.addRect(rect, QPen(QColor("#a99959")), QColor("#142b30"))
                self.add_item(view, background, flash=False)
                self.add_item(view, text, flash=False)
                text.setZValue(22)
                background.setZValue(21)
        missing = sum(n.get("type") in {"unobserved", "unknown-mystery", "unknown-ferocity"}
                      and not n.get("probabilities") for n in self.window.result.get("nodes", []))
        extra = f" · {missing} 处暂无统计" if missing else ""
        if self.window.result.get("side_map", {}).get("nodes"):
            extra += " · 右侧小图暂无适用统计"
        seconds = RETAIN_MS // 1000
        if self.retaining:
            ending = f"已停止，{seconds}秒后隐藏"
        elif self.mode() == "toggle":
            ending = f"再按 TAB 停止并保留{seconds}秒"
        else:
            ending = f"松开 TAB 后保留{seconds}秒"
        self.window.prediction_label.setText(f"{frame['name']} · {len(frame['locations'])} 处 · "
            f"{self.index+1}/{len(self.frames)} · 经验条件概率{extra} · {ending}")
        self.window.prediction_label.show()
