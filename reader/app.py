from __future__ import annotations

import argparse
import ctypes
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
import traceback

import cv2
import numpy as np
from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtGui import QColor, QFontDatabase, QImage, QKeySequence, QPainter, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QFileDialog, QFormLayout, QFrame, QGraphicsEllipseItem, QGraphicsScene,
    QGraphicsView, QHBoxLayout, QHeaderView, QKeySequenceEdit, QLabel, QListWidget, QMainWindow,
    QMenu, QMessageBox, QPushButton, QScrollArea, QSpinBox, QSplitter, QStackedWidget,
    QTableWidget, QTableWidgetItem, QTabWidget, QTextBrowser, QToolButton, QVBoxLayout, QWidget)

from .capture import CaptureError, capture_window, enable_dpi_awareness, foreground_window, list_windows, persist_capture
from .catalog import NODE_NAMES, ROOT
from .hotkeys import DEFAULT_HOTKEYS, GlobalHotkeys
from .recognition import Recognizer, load_image, save_result
from .state import Session, map_signature
from .inventory_ui import InventoryPanel
from .probability_ui import ProbabilityPanel
from .probability import rounded_percentages
from .probability_overlay import ProbabilityOverlay, normalized_name
from .live_monitor import LiveMonitor
from .exploration_ui import EventPoolPanel, RelicPoolsDialog, StageMapPanel
from .exploration import ExplorationCatalog
from .settings_ui import SettingsPanel
from .theme import STYLE
from .version import DISPLAY_VERSION

COLORS = {"unknown-ferocity": "#bb8bdf", "unknown-mystery": "#55cec7", "combat": "#c6a4ee",
          "emergency-combat": "#ec7484", "danger-end": "#d4db76", "danger-path": "#e8c779",
          "rogue-trader": "#42d4b6", "secret-trader": "#e6bf67", "empty": "#8a9ea8", "unobserved": "#4f636e"}
FIELD_NAMES = {"floor": "当前层数", "life": "目标生命", "life_max": "生命上限", "shield": "护盾",
               "action_points": "行动力", "relic_count": "藏品数量", "ingots": "源石锭",
               "parts_count": "零件数量", "parts_capacity": "零件箱容量"}

class TaskSignals(QObject):
    ready = Signal(object, object)
    failed = Signal(str)


class RecognitionTask(QRunnable):
    def __init__(self, recognizer, *, image=None, hwnd=None, source=None, floor=None, template=None,
                 page="auto", side_mode="auto", side_template=None):
        super().__init__()
        self.recognizer, self.image, self.hwnd = recognizer, image, hwnd
        self.source, self.floor, self.template = source, floor, template
        self.page, self.side_mode, self.side_template = page, side_mode, side_template
        self.signals = TaskSignals()

    def run(self):
        try:
            image = self.image
            source = self.source
            if image is None:
                image, metadata = capture_window(self.hwnd)
                path = persist_capture(image, metadata)
                source = {"path": str(path), "window": metadata}
            result = self.recognizer.analyze(image, floor_hint=self.floor, template_hint=self.template, source=source,
                page_hint=self.page, side_mode=self.side_mode, side_template_hint=self.side_template)
            if isinstance(source, dict) and source.get("path"):
                result_path = Path(source["path"]).with_suffix(".json")
                # Imported source pictures stay unchanged; results belong to this project.
                if not result_path.resolve().is_relative_to((ROOT / "data/captures").resolve()):
                    result_path = ROOT / "data/captures" / (Path(source["path"]).stem + ".result.json")
                save_result(result, result_path)
            self.signals.ready.emit(result, image)
        except Exception as exc:
            log = ROOT / "data/logs/last_error.txt"
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(traceback.format_exc(), encoding="utf-8")
            self.signals.failed.emit(str(exc))


class NodeItem(QGraphicsEllipseItem):
    def __init__(self, node, center, radius, callback):
        super().__init__(center[0] - radius, center[1] - radius, radius * 2, radius * 2)
        self.node, self.callback = node, callback
        color = QColor("#ff916e" if node.get("observation_conflict") else COLORS.get(node["type"], "#7ec0cd"))
        self.setBrush(QColor(color.red(), color.green(), color.blue(), 65))
        pen = QPen(color, max(2.0, radius * .10))
        if node["evidence"] == "template_only":
            pen.setStyle(Qt.PenStyle.DashLine)
        self.setPen(pen)
        self.setAcceptHoverEvents(True)
        self.setToolTip(f"{tuple(node['slot'])} {node['label']}\n{node['evidence']} · {node['confidence']:.0%}")
        if node.get('resident'):self.setToolTip(self.toolTip()+'\n流窜居民：'+node.get('resident_evidence','已观察'))
        if node.get('settlement_candidate'):self.setToolTip(self.toolTip()+'\n居民据点候选：初始居民沿实际连线5步内')
        if node.get("observation_conflict"):
            conflict = node["observation_conflict"]
            self.setToolTip(self.toolTip() + f"\n识别冲突：此前 {conflict['previous']} / 本次 {conflict['incoming']}，请手动确认")

    def mousePressEvent(self, event):
        self.callback(self.node)
        event.accept()


class MapView(QGraphicsView):
    def __init__(self, callback, screenshot=False):
        super().__init__()
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.callback, self.screenshot = callback, screenshot
        manifest = ROOT / "assets/nodes/manifest.json"
        assets = json.loads(manifest.read_text(encoding="utf-8"))["assets"] if manifest.exists() else []
        self.icon_paths = {item["type"]: ROOT / item["path"] for item in assets}
        self.prediction_icon_paths = {normalized_name(item["name"]): ROOT / item["path"]
                                      for item in assets if item.get("kind") == "node"}
        self.node_visuals = {}
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)

    def show_map(self, result, image=None, overlay=True):
        self.node_visuals = {}
        self.scene.clear()
        if self.screenshot and image is not None:
            rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            height, width = rgb.shape[:2]
            qimage = QImage(rgb.data, width, height, rgb.strides[0], QImage.Format.Format_RGB888).copy()
            pixmap = self.scene.addPixmap(QPixmap.fromImage(qimage))
            pixmap.setZValue(-10)
            self.scene.setSceneRect(0, 0, width, height)
        if not result or (self.screenshot and not overlay):
            self.fit()
            return
        self.draw_region(result)
        if self.screenshot and result.get("side_map", {}).get("observed_in_frame", True):
            self.draw_region(result.get("side_map", {}))
        else:
            self.scene.setSceneRect(self.scene.itemsBoundingRect().adjusted(-30, -30, 30, 30))
        self.fit()

    def draw_region(self, result):
        nodes = result.get("nodes", [])
        if not nodes:
            return
        def point(node):
            return node["pixel"] if self.screenshot else [node["slot"][0] * 130 + 80, node["slot"][1] * 130 + 60]
        by_slot = {tuple(n["slot"]): point(n) for n in nodes}
        spacing = result.get("grid", {}).get("spacing", 100) if self.screenshot else 130
        radius = spacing * (.20 if self.screenshot else .20)
        for a, b in result.get("edges", []):
            if tuple(a) not in by_slot or tuple(b) not in by_slot:
                continue
            p, q = by_slot[tuple(a)], by_slot[tuple(b)]
            pen = QPen(QColor("#7ec6b7"), 2.5 if self.screenshot else 3)
            pen.setColor(QColor(126, 198, 183, 150))
            line = self.scene.addLine(p[0], p[1], q[0], q[1], pen)
            line.setZValue(-1)
        for node in nodes:
            center = point(node)
            visuals = self.node_visuals.setdefault((node.get("region_id", "main"), tuple(node["slot"])), [])
            if not self.screenshot and node["type"] in self.icon_paths:
                pixmap = QPixmap(str(self.icon_paths[node["type"]]))
                if not pixmap.isNull():
                    pixmap = pixmap.scaled(round(radius * 3.7), round(radius * 3.7), Qt.AspectRatioMode.KeepAspectRatio,
                                           Qt.TransformationMode.SmoothTransformation)
                    icon = self.scene.addPixmap(pixmap)
                    icon.setPos(center[0] - pixmap.width() / 2, center[1] - pixmap.height() / 2)
                    icon.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
                    visuals.append(icon)
            self.scene.addItem(NodeItem(node, center, radius, self.callback))
            if not self.screenshot:
                name = node["label"]
                if node.get("fixed_by_template"):
                    name += " · 固定"
                if node.get("manual"):
                    name += " · 手动"
                text = self.scene.addText(name)
                text.setDefaultTextColor(QColor("#e0ebed"))
                text.setPos(center[0] - text.boundingRect().width() / 2, center[1] + radius + 3)
                visuals.append(text)
                if node["slot"] == result.get("start_slot"):
                    label = self.scene.addText("起点")
                    label.setDefaultTextColor(QColor("#e6c77b"))
                    label.setPos(center[0] - 16, center[1] - radius - 27)
            if node['slot']==result.get('current_slot'):
                ring=self.scene.addEllipse(center[0]-radius*1.25,center[1]-radius*1.25,radius*2.5,radius*2.5,QPen(QColor('#fff18f'),3))
                ring.setToolTip('当前位置 · '+result.get('position_evidence',''))
                label=self.scene.addText('当前位置');label.setDefaultTextColor(QColor('#fff18f'));label.setPos(center[0]-26,center[1]-radius-43)
            if node.get('resident'):
                mark=QPixmap(str(self.icon_paths.get('mark-1','')))
                if not mark.isNull():
                    mark=mark.scaled(round(radius*1.5),round(radius*1.5),Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation)
                    item=self.scene.addPixmap(mark);item.setPos(center[0]+radius*.45,center[1]-radius*1.8);item.setToolTip('流窜居民')
            if not self.screenshot:
                badges=[]
                if node.get('settlement_candidate'):badges.append('据点候选')
                badges.extend(('秘境' if r['name']=='秘境行商' else '奇境')+f" {r['probability']:.0%}" for r in node.get('high_value',[]))
                if badges:
                    badge=self.scene.addText(' / '.join(badges));badge.setDefaultTextColor(QColor('#e9cf81'))
                    badge.setPos(center[0]-badge.boundingRect().width()/2,center[1]+radius+22);visuals.append(badge)

    def fit(self):
        rect = self.scene.sceneRect()
        if not rect.isEmpty():
            self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit()

    def wheelEvent(self, event):
        self.scale(1.15 if event.angleDelta().y() > 0 else 1 / 1.15,
                   1.15 if event.angleDelta().y() > 0 else 1 / 1.15)


def panel(title):
    frame = QFrame()
    frame.setObjectName("panel")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(14, 14, 14, 14)
    label = QLabel(title)
    label.setObjectName("section")
    layout.addWidget(label)
    return frame, layout


class ReaderWindow(QMainWindow):
    def __init__(self, session_path=None, no_hotkeys=False, settings_path=None):
        super().__init__()
        # The offscreen Qt platform has no system font discovery on Windows.
        # Load the installed font explicitly when needed for previews/tests.
        if "Microsoft YaHei UI" not in QFontDatabase.families():
            QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")
        self.setWindowTitle("树海 Reader · 地图跟踪与本局查询")
        self.resize(1400, 900)
        self.setMinimumSize(1050, 720)
        self.recognizer = Recognizer()
        self.session = Session(session_path) if session_path else Session()
        self.result = self.session.current
        self.image = None
        self.map_image = None
        self.last_scan = None
        self.busy = False
        self.preview_path = None
        self.selected_slot = None
        self.selected_region = "main"
        self.tasks = QThreadPool(self)
        self.tasks.setMaxThreadCount(1)
        self.config_path = Path(settings_path) if settings_path else ROOT / "data/settings.json"
        self.config = dict(DEFAULT_HOTKEYS)
        self.ui_options = {"show_after_capture": True, "tab_mode": "hold"}
        self.hotkey_errors = []
        if self.config_path.exists():
            try:
                saved = json.loads(self.config_path.read_text(encoding="utf-8"))
                self.config.update(saved.get("hotkeys", {}))
                self.ui_options.update(saved.get("ui", {}))
            except (OSError, ValueError):
                pass
        # Legacy preferences must never re-enable permanent topmost windows.
        self.ui_options.pop("always_on_top", None)
        if self.ui_options.get("tab_mode") not in {"hold", "toggle"}:
            self.ui_options["tab_mode"] = "hold"
        self.recognizer.catalog.use_observed_ranges=self.ui_options.get("use_observed_ranges",True)
        self.recognizer.catalog.fourth_floor_memory=self.ui_options.get("fourth_floor_memory",False)
        self.build_ui()
        self.probability_panel.model_changed.connect(self.probability_changed)
        self.live_monitor=LiveMonitor(self.recognizer,self.tasks,guard=lambda:self.busy,
            hints=lambda:{'floor_hint':self.floor_hint.currentData(),'side_mode':self.side_mode.currentData()},
            normalize=self.live_context,parent=self)
        self.live_monitor.changed.connect(self.live_ready)
        self.live_monitor.preview.connect(self.live_preview)
        self.live_monitor.status.connect(self.live_feedback)
        self.hotkeys = GlobalHotkeys(QApplication.instance(), {"capture": self.capture_hotkey, "toggle": self.toggle, "undo": self.undo,
            "relics": lambda: self.capture_kind("relics"), "parts": lambda: self.capture_kind("parts"),
            "operators": lambda: self.capture_kind("operators")})
        if not no_hotkeys:
            self.hotkey_errors = self.hotkeys.register(self.config)
            if self.hotkey_errors:
                self.status.setText("；".join(self.hotkey_errors))
        self.hotkey_label.setText(self.key_help())
        self.refresh_windows()
        if self.result:
            self.restore_source_image()
            self.recognizer.refresh_probabilities(self.result)
            self.selected_slot = next((n["slot"] for n in self.result.get("nodes",[]) if n.get("probabilities")), None)
            if not self.hotkey_errors:self.status.setText('已恢复本局地图与清单；选择游戏窗口后可继续实时跟踪。')
        self.render()
        self.probability_overlay = ProbabilityOverlay(self)
        QShortcut(QKeySequence.StandardKey.Paste, self, self.paste_image)
        QShortcut(QKeySequence.StandardKey.Open, self, self.open_image)

    def key_help(self):
        text = f"识别  {self.config['capture']}\n显隐  {self.config['toggle']}\n撤销  {self.config['undo']}"
        text += f"\n藏品  {self.config['relics']}\n零件  {self.config['parts']}\n干员  {self.config['operators']}"
        text += ("\n前台地图：TAB按一次开启，再按一次停止" if self.ui_options["tab_mode"] == "toggle"
                 else "\n前台地图：按住 TAB 1秒，轮播节点概率")
        if self.hotkey_errors:
            text += "\n热键未生效：" + "；".join(self.hotkey_errors)
        return text

    def save_settings(self, *_):
        self.ui_options["show_after_capture"] = self.auto_show.isChecked()
        self.ui_options["tab_mode"] = self.tab_mode.currentData()
        save_result({"hotkeys": self.config, "ui": self.ui_options}, self.config_path)

    def tab_mode_changed(self, *_):
        if hasattr(self, "probability_overlay"):
            self.probability_overlay.cancel()
        self.save_settings()
        self.hotkey_label.setText(self.key_help())

    def present_results(self):
        """Present a normal window once; never keep it above other programs."""
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, False)
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()
        if sys.platform == "win32" and QApplication.platformName() != "offscreen":
            from ctypes import wintypes
            user = ctypes.WinDLL("user32", use_last_error=True)
            user.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
            user.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                          ctypes.c_int, ctypes.c_int, wintypes.UINT]
            user.SetForegroundWindow.argtypes = [wintypes.HWND]
            hwnd = int(self.winId())
            user.ShowWindow(hwnd, 9)
            # HWND_NOTOPMOST explicitly clears a native topmost flag, including
            # one applied outside Qt. HWND_TOP alone does not do this.
            user.SetWindowPos(hwnd, wintypes.HWND(-2), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040)
            user.SetForegroundWindow(hwnd)

    def build_ui(self):
        self._build_prefs()
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_topbar())
        root.addWidget(self._build_hud())
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._build_nav())
        body.addWidget(self._build_workspace(), 1)
        self.inspector = self._build_inspector()
        self.right_scroll = self.inspector
        self.inspector_stack = self.inspector
        body.addWidget(self.inspector)
        root.addLayout(body, 1)
        root.addWidget(self._build_statusline())
        self.tabs.currentChanged.connect(self.tab_changed)
        self.tab_changed()

    # -- 顶栏 ------------------------------------------------------------
    def _build_topbar(self):
        bar = QFrame()
        bar.setObjectName("topbar")
        bar.setFixedHeight(54)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 0, 12, 0)
        lay.setSpacing(8)

        title = QLabel("树海 READER")
        title.setObjectName("heading")
        lay.addWidget(title)
        lay.addSpacing(10)

        self.capture_button = QPushButton("截取并识别")
        self.capture_button.setObjectName("primary")
        self.capture_button.clicked.connect(self.capture_selected)
        lay.addWidget(self.capture_button)

        self.live_button = QPushButton("实时跟踪")
        self.live_button.setCheckable(True)
        self.live_button.toggled.connect(self.toggle_live)
        lay.addWidget(self.live_button)

        self.window_combo = QComboBox()
        self.window_combo.setMinimumContentsLength(14)
        self.window_combo.setMinimumWidth(230)
        lay.addWidget(self.window_combo)
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self.refresh_windows)
        lay.addWidget(refresh)

        self.page_hint = QComboBox()
        for label, value in (("自动识别页面", "auto"), ("地图", "map"), ("已有藏品", "relics"),
                             ("零件箱库存", "parts"), ("已招募干员 / 详情", "operators")):
            self.page_hint.addItem(label, value)
        lay.addWidget(self.page_hint)

        # Live tracking has its own status channel; it lands in the status line, so
        # keep a label around for the monitor signal even though it is never shown here.
        self.live_status = QLabel("")
        self.live_status.hide()
        lay.addWidget(self.live_status)

        lay.addStretch()

        self.state_dot = QLabel("●")
        self.state_dot.setObjectName("stateDot")
        lay.addWidget(self.state_dot)
        self.busy_label = QLabel("就绪")
        lay.addWidget(self.busy_label)
        lay.addSpacing(8)

        more = QToolButton()
        more.setText("更多")
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(more)
        menu.addAction("导入图片…", self.open_image)
        menu.addAction("粘贴剪贴板图片", self.paste_image)
        menu.addAction("3 秒后截取前台窗口", self.delayed_capture)
        menu.addSeparator()
        menu.addAction("打开藏品池", self.open_relic_pools)
        menu.addAction("导出识别 JSON", self.export)
        more.setMenu(menu)
        lay.addWidget(more)
        gear = QPushButton("设置")
        gear.clicked.connect(lambda: self.settings("ui"))
        lay.addWidget(gear)
        return bar

    # -- 资源条 ----------------------------------------------------------
    def _build_hud(self):
        bar = QFrame()
        bar.setObjectName("hud")
        bar.setFixedHeight(38)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 0, 12, 0)
        lay.setSpacing(6)
        self.hud_chips = {}
        for name in ("floor", "life", "shield", "action_points", "relic_count", "ingots", "parts"):
            chip = QPushButton("—")
            chip.setObjectName("hudChip")
            chip.setToolTip("点击修改本局资源")
            chip.clicked.connect(self.edit_resources)
            self.hud_chips[name] = chip
            lay.addWidget(chip)
        lay.addStretch()
        undo = QPushButton("撤销")
        undo.setObjectName("ghost")
        undo.clicked.connect(self.undo)
        lay.addWidget(undo)
        clear_manual=QPushButton("清除手动修改")
        clear_manual.setObjectName("ghost");clear_manual.setToolTip("移除手动补录，解除资源、节点、练度和地图提示的手动修改；保留自动读取记录，可撤销")
        clear_manual.clicked.connect(self.clear_manual)
        lay.addWidget(clear_manual)
        new_run = QPushButton("新建本局")
        new_run.setObjectName("ghost")
        new_run.clicked.connect(self.new_run)
        lay.addWidget(new_run)
        return bar

    # -- 左侧导航 --------------------------------------------------------
    def _build_nav(self):
        nav = QFrame()
        nav.setObjectName("nav")
        nav.setFixedWidth(200)
        lay = QVBoxLayout(nav)
        lay.setContentsMargins(8, 10, 8, 10)
        lay.setSpacing(2)
        self.nav_buttons = []
        self.nav_group = QButtonGroup(nav)
        self.nav_group.setExclusive(True)
        for text in ("地图", "概率", "清单", "关卡"):
            button = QPushButton(text)
            button.setObjectName("navItem")
            button.setCheckable(True)
            button.clicked.connect(lambda _=False, label=text: self.go_page(label))
            self.nav_group.addButton(button)
            lay.addWidget(button)
            self.nav_buttons.append(button)
        section = QLabel("工具")
        section.setObjectName("navSection")
        lay.addWidget(section)
        for text, slot in (("藏品池", self.open_relic_pools), ("效果库", self.open_effects),
                           ("计算器", self.open_calculator)):
            button = QPushButton(text)
            button.setObjectName("navItem")
            button.clicked.connect(lambda _=False, call=slot: call())
            lay.addWidget(button)
        settings = QPushButton("设置")
        settings.setObjectName("navItem")
        settings.setCheckable(True)
        settings.clicked.connect(lambda _=False: self.go_page("设置"))
        self.nav_group.addButton(settings)
        lay.addWidget(settings)
        self.nav_buttons.append(settings)
        lay.addStretch()
        version = QLabel(f"{DISPLAY_VERSION} · 算法 0.3-prts")
        version.setObjectName("navSection")
        lay.addWidget(version)
        return nav

    def go_page(self, label):
        pages = {"地图": self.map_view, "概率": self.probability_panel,
                 "清单": self.inventory_panel, "关卡": self.stage_panel,
                 "设置": self.settings_panel}
        widget = pages.get(label)
        if widget is not None:
            self.tabs.setCurrentWidget(widget)

    # -- 工作区 ----------------------------------------------------------
    def _build_workspace(self):
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(14, 10, 14, 8)
        lay.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(8)
        self.summary = QLabel("等待截图")
        self.summary.setObjectName("section")
        head.addWidget(self.summary)
        head.addStretch()
        self.map_options = QWidget()
        options = QHBoxLayout(self.map_options)
        options.setContentsMargins(0, 0, 0, 0)
        options.setSpacing(8)
        self.overlay = QCheckBox("识别叠加")
        self.overlay.setChecked(True)
        self.overlay.toggled.connect(self.render)
        options.addWidget(self.overlay)
        reset_view = QPushButton("适应视图")
        reset_view.clicked.connect(lambda: (self.map_view.fit(), self.side_view.fit(), self.screenshot_view.fit()))
        options.addWidget(reset_view)
        export = QPushButton("导出 JSON")
        export.clicked.connect(self.export)
        options.addWidget(export)
        head.addWidget(self.map_options)
        lay.addLayout(head)

        self.view_seg = QFrame()
        self.view_seg.setObjectName("seg")
        seg = QHBoxLayout(self.view_seg)
        seg.setContentsMargins(2, 2, 2, 2)
        seg.setSpacing(2)
        self.view_buttons = []
        for index, text in enumerate(("主图", "右侧小图", "原图叠加")):
            button = QPushButton(text)
            button.setObjectName("segItem")
            button.setCheckable(True)
            button.clicked.connect(lambda _=False, i=index: self.show_view(i))
            seg.addWidget(button)
            self.view_buttons.append(button)
        self.view_seg.setFixedWidth(240)
        lay.addWidget(self.view_seg)

        slot = QWidget()
        slot.setFixedHeight(28)
        slot_layout = QHBoxLayout(slot)
        slot_layout.setContentsMargins(0, 0, 0, 0)
        self.prediction_label = QLabel()
        self.prediction_label.setObjectName("prediction")
        self.prediction_label.hide()
        slot_layout.addWidget(self.prediction_label)
        slot_layout.addStretch()
        lay.addWidget(slot)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("pages")
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().hide()
        self.map_view = MapView(self.select_node)
        self.probability_panel = ProbabilityPanel(self.recognizer.catalog)
        self.side_view = MapView(self.select_node)
        self.screenshot_view = MapView(self.select_node, screenshot=True)
        self.inventory_panel = InventoryPanel(self.session, self.recognizer.catalog)
        self.stage_panel = StageMapPanel()
        self.settings_panel = SettingsPanel(self)
        self.settings_panel.dataUpdated.connect(self.reload_data)
        for widget in (self.map_view, self.probability_panel, self.side_view,
                       self.screenshot_view, self.inventory_panel, self.stage_panel,
                       self.settings_panel):
            self.tabs.addTab(widget, "")
        lay.addWidget(self.tabs, 1)

        self.notes = QLabel("滚轮缩放，拖动平移，点击节点查看或修正。")
        self.notes.setWordWrap(True)
        self.notes.setObjectName("muted")
        lay.addWidget(self.notes)
        return box

    def show_view(self, index):
        target = (self.map_view, self.side_view, self.screenshot_view)[index]
        self.tabs.setCurrentWidget(target)

    # -- 检查器 ----------------------------------------------------------
    def _build_inspector(self):
        stack = QStackedWidget()
        stack.setFixedWidth(330)
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(10)

        node_panel, layout = panel("选中节点")
        self.node_label = QLabel("点击地图中的节点")
        self.node_label.setWordWrap(True)
        layout.addWidget(self.node_label)
        self.node_combo = QComboBox()
        for kind, name in self.recognizer.names.items():
            self.node_combo.addItem(name, kind)
        layout.addWidget(self.node_combo)
        correct = QPushButton("设为此类型并锁定")
        correct.clicked.connect(self.correct_node)
        layout.addWidget(correct)
        row = QHBoxLayout()
        row.setSpacing(6)
        unlock = QPushButton("解除锁定")
        unlock.clicked.connect(self.unlock_node)
        row.addWidget(unlock)
        position = QPushButton("设为当前位置")
        position.clicked.connect(self.correct_position)
        row.addWidget(position)
        layout.addLayout(row)
        self.probabilities = QTextBrowser()
        self.probabilities.setMinimumHeight(96)
        layout.addWidget(self.probabilities, 1)
        open_probability = QPushButton("完整概率与距离分布")
        open_probability.clicked.connect(lambda: self.tabs.setCurrentWidget(self.probability_panel))
        layout.addWidget(open_probability)
        lay.addWidget(node_panel)

        event_panel, event_layout = panel("事件池 · 不期而遇")
        self.event_pool = EventPoolPanel(self.session, self.render)
        self.event_pool.setMaximumHeight(320)
        event_layout.addWidget(self.event_pool)
        lay.addWidget(event_panel)

        template_panel, layout = panel("地图构型")
        self.floor_hint = QComboBox()
        self.floor_hint.addItem("楼层：自动识别", None)
        for floor in range(1, 7):
            self.floor_hint.addItem(f"手动指定第 {floor} 层", floor)
        self.floor_hint.addItem("手动指定树洞层", "treehole")
        layout.addWidget(self.floor_hint)
        self.side_mode = QComboBox()
        self.side_mode.addItem("右侧小图：自动分区", "auto")
        self.side_mode.addItem("右侧小图：存在", "present")
        self.side_mode.addItem("右侧小图：不存在", "absent")
        layout.addWidget(self.side_mode)
        rerun = QPushButton("按所选楼层重新识别")
        rerun.clicked.connect(self.rerun)
        layout.addWidget(rerun)
        self.candidates = QListWidget()
        self.candidates.setMinimumHeight(96)
        layout.addWidget(self.candidates)
        confirm = QPushButton("确认选中的模板")
        confirm.clicked.connect(self.confirm_template)
        layout.addWidget(confirm)
        self.side_candidates = QComboBox()
        layout.addWidget(self.side_candidates)
        side_confirm = QPushButton("确认右侧小图模板")
        side_confirm.clicked.connect(self.confirm_side_template)
        layout.addWidget(side_confirm)
        explanation = QLabel("选择地图构型后可确认；虚线表示尚未读到类型的节点。")
        explanation.setWordWrap(True)
        explanation.setObjectName("muted")
        layout.addWidget(explanation)
        lay.addWidget(template_panel)
        lay.addStretch()

        for page in (host, self.probability_panel.side, self.inventory_panel.side):
            stack.addWidget(self._scroll_page(page))
        return stack

    @staticmethod
    def _scroll_page(widget):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(widget)
        return scroll

    # -- 状态线 ----------------------------------------------------------
    def _build_statusline(self):
        bar = QFrame()
        bar.setObjectName("statusline")
        bar.setFixedHeight(28)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 0, 12, 0)
        self.status = QLabel("导入地图截图，或切到游戏窗口后按识别热键。")
        self.status.setObjectName("statuslineText")
        lay.addWidget(self.status, 1)
        self.clock_label = QLabel("")
        self.clock_label.setObjectName("muted")
        lay.addWidget(self.clock_label)
        timer = QTimer(self)
        timer.setInterval(30000)
        timer.timeout.connect(self._tick_clock)
        timer.start()
        self._tick_clock()
        self.hotkey_label = QLabel()
        self.hotkey_label.hide()
        return bar

    def _tick_clock(self):
        self.clock_label.setText(datetime.now().strftime("%H:%M"))

    def set_active_nav(self, index):
        for position, button in enumerate(self.nav_buttons):
            button.blockSignals(True)
            button.setChecked(position == index)
            button.blockSignals(False)

    def tab_changed(self, *_):
        if hasattr(self, "probability_overlay"):
            self.probability_overlay.cancel()
        current = self.tabs.currentWidget()
        is_map = current in (self.map_view, self.side_view, self.screenshot_view)
        self.map_options.setVisible(is_map)
        self.view_seg.setVisible(is_map)
        self.summary.setVisible(is_map)
        self.notes.setVisible(is_map)
        if is_map:
            self.inspector_stack.setCurrentIndex(0)
            self.inspector.setVisible(True)
            current.fit()
        elif current is self.probability_panel:
            self.inspector_stack.setCurrentIndex(1)
            self.inspector.setVisible(True)
        elif current is self.inventory_panel:
            self.inspector_stack.setCurrentIndex(2)
            self.inspector.setVisible(True)
        else:
            self.inspector.setVisible(False)
            if current is self.settings_panel:
                self.settings_panel.refresh()
        if is_map:
            index = (self.map_view, self.side_view, self.screenshot_view).index(current)
            for position, button in enumerate(self.view_buttons):
                button.blockSignals(True)
                button.setChecked(position == index)
                button.blockSignals(False)
            self.set_active_nav(0)
        elif current is self.probability_panel:
            self.set_active_nav(1)
        elif current is self.inventory_panel:
            self.set_active_nav(2)
        elif current is self.settings_panel:
            self.set_active_nav(4)
        else:
            self.set_active_nav(3)

    def edit_resources(self):
        if not self.editable():
            self.status.setText("确认地图构型后才能修改本局资源。")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("本局资源")
        form = QFormLayout(dialog)
        spins = {}
        for name, title in FIELD_NAMES.items():
            current = ((self.result or {}).get("fields", {}).get(name) or {}).get("value")
            spin = QSpinBox()
            spin.setRange(-1, 99999)
            spin.setSpecialValueText("未读取")
            spin.setValue(current if isinstance(current, int) else -1)
            spins[name] = spin
            form.addRow(title, spin)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            for name, spin in spins.items():
                if spin.value() >= 0:
                    self.session.edit_field(name, spin.value())
            self.result = deepcopy(self.session.current)
            self.render()
            self.status.setText("本局资源已更新。")


    def update_hud(self, values):
        def text(name):
            return (values.get(name) or {}).get("text", "—")
        labels = {
            "floor": f"第 {text('floor')} 层",
            "life": f"生命 {text('life')} / {text('life_max')}",
            "shield": f"护盾 {text('shield')}",
            "action_points": f"行动力 {text('action_points')}",
            "relic_count": f"藏品 {text('relic_count')}",
            "ingots": f"源石锭 {text('ingots')}",
            "parts": f"零件 {text('parts_count')} / {text('parts_capacity')}",
        }
        for key, label in labels.items():
            chip = self.hud_chips.get(key)
            if chip is None:
                continue
            chip.setText(label)
            info = values.get(key) or {}
            source = "手动锁定" if info.get("manual") else f"OCR {info.get('confidence', 0):.0%}"
            chip.setToolTip(f"{source} · 点击修改本局资源")
            if info.get("manual"):
                chip.setStyleSheet("color:#79dbff;")
            elif info.get("text", "—") != "—" and info.get("confidence", 0) < 0.80:
                chip.setStyleSheet("color:#e6bd70;")
            else:
                chip.setStyleSheet("")

    def _build_prefs(self):
        self.tab_mode = QComboBox()
        self.tab_mode.addItem("TAB操作：长按显示", "hold")
        self.tab_mode.addItem("TAB操作：按键切换", "toggle")
        self.tab_mode.setCurrentIndex(max(0, self.tab_mode.findData(self.ui_options["tab_mode"])))
        self.tab_mode.currentIndexChanged.connect(self.tab_mode_changed)
        self.tab_mode.hide()
        self.auto_show = QCheckBox("读取完成后显示结果")
        self.auto_show.setChecked(self.ui_options["show_after_capture"])
        self.auto_show.toggled.connect(self.save_settings)
        self.auto_show.hide()

    def refresh_windows(self):
        selected = self.window_combo.currentData()
        self.window_combo.clear()
        self.window_combo.addItem("自动：热键时截取前台窗口", None)
        try:
            for item in list_windows():
                self.window_combo.addItem(item["title"][:65], item["hwnd"])
        except CaptureError:
            pass
        index = self.window_combo.findData(selected)
        self.window_combo.setCurrentIndex(max(0, index))

    def open_effects(self):
        from .effects_ui import EffectsWindow
        if not getattr(self,'effects_window',None):self.effects_window=EffectsWindow(self.recognizer.catalog)
        self.effects_window.show();self.effects_window.raise_();self.effects_window.activateWindow()

    def open_relic_pools(self):
        self.recognizer.catalog.include_exploration_items()
        RelicPoolsDialog(self.session,self.recognizer.catalog,self).exec()

    def toggle_live(self,enabled):
        if not hasattr(self,'live_monitor'):return
        if enabled and getattr(self,'data_updating',False):
            self.live_button.blockSignals(True);self.live_button.setChecked(False);self.live_button.blockSignals(False)
            self.status.setText('资料更新中，完成后可开始实时跟踪。');return
        if enabled:
            hwnd=self.window_combo.currentData()
            if not hwnd:
                self.live_button.blockSignals(True);self.live_button.setChecked(False);self.live_button.blockSignals(False)
                self.live_feedback('先在窗口列表中选择游戏窗口');return
            try:self.live_monitor.start(hwnd);self.live_button.setText('停止实时跟踪')
            except (ValueError,RuntimeError,OSError) as e:
                self.live_monitor.stop();self.live_button.setChecked(False);self.live_feedback(str(e))
        else:
            self.live_monitor.stop();self.live_button.setText('开始实时跟踪');self.live_feedback('实时跟踪已停止')

    def live_feedback(self,message):
        self.live_status.setText(message)
        if self.busy:return
        self.busy_label.setText('实时跟踪' if self.live_monitor.running else '就绪')
        self.busy_label.setToolTip(message);self.status.setText(message)
        waiting=any(word in message for word in ('等待','最小化','选择','关闭','失败','更换'))
        self.state_dot.setStyleSheet('color:'+('#e4bd75' if waiting else '#39d98a'))

    def live_ready(self,result,image):
        # This path never changes focus, visibility or the user's current tab.
        if self.busy:return
        self.last_scan=result;self.image=image;page=result.get('page_type');changed=False
        if page=='map':
            changed=self.session.apply(result)
            if changed:
                self.map_committed()
                self.recognizer.refresh_probabilities(self.session.current);self.session.persist()
                self.result=deepcopy(self.session.current);self.map_image=image
        elif page=='inventory':
            changed=self.session.auto_inventory(result);self.inventory_panel.set_scan(result,follow=False)
            self.inventory_panel.help.setText('可见清单已同步；翻页后继续读取。下表可补录或修正。' if changed else '本页条目待确认；勾选后合并本页。')
            if changed and getattr(self,'calculation_window',None):self.calculation_window.dps_panel.refresh_snapshot()
        elif page=='event':changed=self.session.record_event(result)
        if changed:self.status.setText(self.session.transition_message if page=='map' and self.session.transition_message else {'map':'地图已更新','inventory':'本局清单已同步','event':'已遇事件已记录'}[page])
        elif page=='map' and self.session.transition_message:self.status.setText(self.session.transition_message)
        self.render()

    def live_context(self,result):
        previous=self.session.current
        if result.get('page_type')=='map':
            old_floor=(previous or {}).get('floor')
            result['_new_run_candidate']=isinstance(old_floor,int) and old_floor>1 and result.get('floor')==1 and result.get('at_start')
        if result.get('page_type')=='map' and result.get('needs_review') and previous and not previous.get('needs_review'):
            if map_signature(previous) is not None and map_signature(previous)==map_signature(result) and not result.get('region_detection',{}).get('needs_review'):
                result['needs_review']=False;result['template_confirmation_inherited']=True
                from .resident_tracking import infer_position,update_resident_constraints
                infer_position(result,result.get('current_slot'));update_resident_constraints(result)
        return result

    def live_preview(self,result,image):
        if self.busy:return
        self.result=result;self.image=image;self.map_image=image;self.last_scan=result;self.render()

    def correct_position(self):
        if self.selected_region!='main' or self.selected_slot is None:return
        self.session.set_position(self.selected_slot);self.result=deepcopy(self.session.current)
        self.recognizer.refresh_probabilities(self.result);self.session.current=deepcopy(self.result);self.session.persist();self.render()

    def open_calculator(self):
        from .calculation_ui import CalculationWindow
        if not getattr(self,'calculation_window',None):
            self.calculation_window=CalculationWindow(snapshot_provider=lambda:{'schema_version':2,
                'current':deepcopy(self.session.current),'inventories':deepcopy(self.session.inventories)},
                database=self.recognizer.catalog.database)
        else:
            self.calculation_window.session_panel.refresh_snapshot()
            self.calculation_window.dps_panel.refresh_snapshot()
        self.calculation_window.pages.setCurrentWidget(self.calculation_window.dps_panel)
        self.calculation_window.show();self.calculation_window.raise_();self.calculation_window.activateWindow()

    def start_task(self, **kwargs):
        self.probability_overlay.cancel()
        if getattr(self,'data_updating',False):
            self.status.setText('资料更新中，完成后可继续识别。');return
        if self.busy:
            self.status.setText("上一张截图正在识别，请稍候。")
            return
        self.busy = True
        self.capture_button.setEnabled(False)
        self.busy_label.setText("识别中…")
        self.status.setText("正在处理截图，首次加载 OCR 模型可能稍慢。")
        kwargs.setdefault("page", self.page_hint.currentData())
        kwargs.setdefault("side_mode", self.side_mode.currentData())
        task = RecognitionTask(self.recognizer, floor=self.floor_hint.currentData(), **kwargs)
        task.signals.ready.connect(self.ready)
        task.signals.failed.connect(self.failed)
        self._task = task  # Keep signals alive until the worker completes.
        self._restore_on_error = self.isVisible()
        self._capture_aux=[]
        self.tasks.start(task)

    def capture_selected(self):
        hwnd = self.window_combo.currentData()
        if hwnd is None:
            self.delayed_capture()
        else:
            self.start_task(hwnd=hwnd)

    def capture_hotkey(self, page=None):
        if self.busy:
            return
        try:
            hwnd = self.window_combo.currentData()
            if hwnd is None:
                hwnd = foreground_window()["hwnd"]
            self.start_task(hwnd=hwnd, **({"page": page} if page else {}))
        except CaptureError as exc:
            self.status.setText(str(exc))

    def capture_kind(self, kind):
        if self.busy:
            return
        self.capture_hotkey(page=kind)

    def delayed_capture(self):
        if self.busy:
            return
        self.status.setText("请在3秒内切换到游戏窗口…")
        QTimer.singleShot(3000, self.capture_frontmost)

    def capture_frontmost(self):
        try:
            self.start_task(hwnd=foreground_window()["hwnd"])
        except CaptureError as exc:
            self.status.setText(str(exc))

    def open_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择游戏截图", str(ROOT), "图片 (*.png *.jpg *.jpeg *.bmp *.webp)")
        if path:
            self.import_image(path)

    def import_image(self, path):
        try:
            image = load_image(path)
            self.start_task(image=image, source={"path": str(Path(path).resolve()), "method": "file_import"})
        except (OSError, ValueError) as exc:
            self.status.setText(str(exc))

    def paste_image(self):
        image = QApplication.clipboard().image()
        if image.isNull():
            self.status.setText("剪贴板没有图片。")
            return
        image = image.convertToFormat(QImage.Format.Format_RGBA8888)
        pixels = np.frombuffer(image.bits(), np.uint8).reshape(image.height(), image.bytesPerLine())[:, :image.width() * 4]
        bgr = cv2.cvtColor(pixels.reshape(image.height(), image.width(), 4), cv2.COLOR_RGBA2BGR).copy()
        path = persist_capture(bgr)
        self.start_task(image=bgr, source={"path": str(path), "method": "clipboard"})

    def ready(self, result, image):
        self.restore_capture_aux()
        self.busy = False
        self.capture_button.setEnabled(True)
        self.busy_label.setText(f"耗时 {result['elapsed_seconds']:.1f} 秒")
        self.image = image
        self.last_scan = result
        if result.get('page_type')=='event':
            self.session.record_event(result);self.status.setText('已遇事件：'+result['event_name']);self.render()
            if self.auto_show.isChecked():self.present_results()
            return
        if result.get("page_type") == "inventory":
            self.inventory_panel.set_scan(result)
            self.status.setText(f"读到 {len(result['entries'])} 个条目候选，请核对后合并本页。")
            self.render()
            self.tabs.setCurrentWidget(self.inventory_panel)
            if self.auto_show.isChecked():
                self.present_results()
            if self.preview_path:
                QTimer.singleShot(150, self.save_preview)
            return
        self.map_image = image
        committed = self.session.apply(result)
        if committed:
            self.map_committed()
            self.recognizer.refresh_probabilities(self.session.current)
            self.session.persist()
        self.result = deepcopy(self.session.current) if committed or (self.session.transition_message and self.session.current) else result
        if self.result.get("nodes") and self.selected_slot is None:
            node = next((n for n in self.result["nodes"] if n.get("probabilities")), self.result["nodes"][0])
            self.selected_slot = node["slot"]
        self.status.setText("地图已更新" if committed else
                            "请选择并确认地图构型" if result["page_type"] == "map" else "等待地图、清单或事件页面")
        if committed and self.result.get("needs_node_review"):
            self.status.setText("连续识别的节点类型存在冲突，已暂停概率计算。请点击对应节点手动确认。")
        elif committed and self.result.get("map_continuity", {}).get("same_map"):
            self.status.setText("已合并同一张地图，保留此前可靠识别和人工修正。" +
                ("概率输入未变，已复用计算结果。" if self.result.get("probability_cache", {}).get("hit") else
                 "节点观察变化，概率已重新计算。"))
        if self.session.transition_message:self.status.setText(self.session.transition_message)
        self.render()
        if self.auto_show.isChecked():
            self.present_results()
        if self.preview_path:
            QTimer.singleShot(150, self.save_preview)

    def save_preview(self):
        if self.preview_path:
            self.preview_path.parent.mkdir(parents=True, exist_ok=True)
            self.grab().save(str(self.preview_path))

    def failed(self, message):
        self.restore_capture_aux()
        self.busy = False
        self.capture_button.setEnabled(True)
        self.busy_label.setText("处理失败")
        self.status.setText(message)
        if self.auto_show.isChecked() or getattr(self, "_restore_on_error", False):
            self.present_results()

    def restore_capture_aux(self):
        for window in getattr(self,'_capture_aux',[]):window.show()
        self._capture_aux=[]

    def render(self, *_):
        if hasattr(self, "probability_overlay"):
            self.probability_overlay.clear_items()
        result = self.result
        self.map_view.show_map(result)
        self.side_view.show_map((result or {}).get("side_map"))
        is_map_scan = (self.last_scan or {}).get("page_type") == "map"
        self.screenshot_view.show_map(result if is_map_scan else None, self.image, self.overlay.isChecked())
        self.inventory_panel.refresh()
        self.event_pool.refresh()
        values = {}
        for name in FIELD_NAMES:
            value = (result or {}).get("fields", {}).get(name, {})
            observed_floor = value.get('value') if name == 'floor' else None
            if name == 'floor' and result and result.get('floor') is not None:
                value = {'value': result['floor']}
            text_value = str(value["value"]) if "value" in value else "—"
            values[name] = {"text": text_value, "manual": bool(value.get("manual")),
                            "confidence": value.get("confidence", 0), "observed": observed_floor}
        self.update_hud(values)
        self.candidates.clear()
        for row in (result or {}).get("candidates", []):
            self.candidates.addItem(f"{row['template_id']}    {row['score']:.3f}")
        if self.candidates.count():
            self.candidates.setCurrentRow(0)
        self.side_candidates.clear()
        side = (result or {}).get("side_map", {})
        for row in side.get("candidates", []):
            self.side_candidates.addItem(f"{row['template_id']}  {row['score']:.3f}", row["template_id"])
        if result and result.get("template_id"):
            review = " · 待确认" if result.get("needs_review") else ""
            side_status = (" + 右侧小图待核对" if side.get("needs_review") else " + 右侧小图") if side.get("nodes") else ""
            self.summary.setText(f"第 {result['floor']} 层 · {result['template_id']} · {len(result['nodes'])} 节点{review}{side_status}")
        else:
            self.summary.setText("等待完整地图截图")
        if self.tabs.currentWidget() == self.inventory_panel:
            self.summary.setText("本局藏品、零件与干员 · 待核对条目 / 已确认清单")
        elif self.tabs.currentWidget()==self.probability_panel:
            self.summary.setText("节点概率 · 路标经验频率与本层数量约束")
        position=(result or {}).get('current_slot');residents=(result or {}).get('resident_constraints',{})
        self.notes.setText(f"当前位置：{tuple(position) if position is not None else '待定位'}　居民：{len(residents.get('visible',[]))}　据点候选：{len(residents.get('settlement_candidates',[]))}\n滚轮缩放，拖动平移，点击节点可修正或设为当前位置。")
        self.notes.setToolTip('\n'.join((result or {}).get('notices',[])))
        if self.selected_slot and result:
            region = result if self.selected_region == "main" else side
            node = next((n for n in region.get("nodes", []) if n["slot"] == self.selected_slot), None)
            if node:
                self.select_node(node)
        if hasattr(self, "probability_overlay"):
            self.probability_overlay.refresh()

    def select_node(self, node):
        self.selected_slot = node["slot"]
        self.selected_region = node.get("region_id", "main")
        sources = {"ocr_label": "文字识别", "small_marker_candidate": "小圆点候选", "template_only": "仅模板位置", "manual": "手动修正", "template_fixed_exit":"构型确认后的固定出口", "template_fixed_node":"构型确认后的固定节点"}
        region = "主图" if self.selected_region == "main" else "熄日余径"
        distance = f"距模板起点 {node.get('distance')} 步" if self.selected_region == "main" else "独立小图，不套用主图距离统计"
        self.node_label.setText(f"{region} 格点 {tuple(node['slot'])} · {node['label']}\n{sources.get(node['evidence'], node['evidence'])} · 分数 {node['confidence']:.0%}\n{distance}")
        if node.get("observation_conflict"):
            conflict = node["observation_conflict"]
            self.node_label.setText(self.node_label.text() +
                f"\n识别冲突：此前 {conflict['previous']} / 本次 {conflict['incoming']}，请手动确认。")
        index = self.node_combo.findData(node["type"])
        self.node_combo.setCurrentIndex(max(0, index))
        probability = node.get("probabilities") if not (self.result or {}).get("needs_review") else None
        if probability:
            lines = [f"<b>节点概率 · 源站样本 {probability['sample_count']}</b><br>"]
            units=rounded_percentages([r["probability"] for r in probability["estimates"]])
            for row,unit in zip(probability["estimates"],units):
                value="<1%" if row["probability"]>0 and unit==0 else f"{unit}%"
                lines.append(f"{row['name']}　{value}<br>")
            lines.append("<br>依据："+probability["conditions"])
            self.probabilities.setHtml("".join(lines))
            self.probabilities.setToolTip(probability.get('limitation',''))
        else:
            self.probabilities.setPlainText(node.get("probability_error") or "已揭示节点可直接查看类型；未知节点显示候选概率。")
        if self.result:self.probability_panel.set_node(self.result,node)

    def editable(self):
        return self.result is not None and self.result.get("page_type") == "map" and not self.result.get("needs_review")

    def correct_node(self):
        if not self.selected_slot or not self.editable() or (self.selected_region == "sunset" and self.result.get("side_map", {}).get("needs_review")):
            self.status.setText("请先确认地图模板并选中一个节点。")
            return
        kind = self.node_combo.currentData()
        region = self.result if self.selected_region == "main" else self.result.get("side_map", {})
        node = next(n for n in region["nodes"] if n["slot"] == self.selected_slot)
        probability = self.recognizer.catalog.probabilities(self.result["floor"], node.get("distance"), kind) if self.selected_region == "main" else None
        self.session.edit_node(self.selected_slot, kind, self.node_combo.currentText(), probability, region=self.selected_region)
        self.recognizer.refresh_probabilities(self.session.current)
        self.session.persist()
        self.result = deepcopy(self.session.current)
        self.render()

    def unlock_node(self):
        if not self.selected_slot or not self.editable():
            return
        self.session._push()
        region = self.session.current if self.selected_region == "main" else self.session.current.get("side_map", {})
        for node in region.get("nodes", []):
            if node["slot"] == self.selected_slot:
                node["manual"] = False
        self.session.persist()
        self.rerun()

    def rerun(self):
        if self.image is not None:
            self.start_task(image=self.image.copy(), source=(self.result or {}).get("source"))

    def confirm_template(self):
        index = self.candidates.currentRow()
        if self.map_image is not None and self.result and index >= 0:
            template = self.result["candidates"][index]["template_id"]
            self.start_task(image=self.map_image.copy(), source=self.result.get("source"), template=template, page="map")

    def confirm_side_template(self):
        template = self.side_candidates.currentData()
        if template and self.map_image is not None and self.result:
            self.start_task(image=self.map_image.copy(), source=self.result.get("source"),
                template=self.result.get("template_id"), side_template=template, side_mode="present", page="map")

    def undo(self):
        if self.busy:
            return
        if self.session.undo():
            self.result = deepcopy(self.session.current)
            self.selected_slot = None
            self.last_scan = None
            self.restore_source_image()
            self.render()
            self.status.setText("已撤销最近一次更新。")

    def restore_source_image(self):
        source = (self.result or {}).get("source")
        path = source.get("path") if isinstance(source, dict) else source
        try:
            self.image = load_image(path) if path else None
            self.map_image = self.image
        except (OSError, ValueError, TypeError):
            self.image = None

    def probability_changed(self):
        self.ui_options['use_observed_ranges']=self.recognizer.catalog.use_observed_ranges
        self.ui_options['fourth_floor_memory']=self.recognizer.catalog.fourth_floor_memory
        self.save_settings()
        if self.session.current:
            self.recognizer.refresh_probabilities(self.session.current);self.session.persist()
            self.result=deepcopy(self.session.current);self.render()

    def map_committed(self):
        if self.session.just_started:
            self.live_monitor.reset_observations(self.session.current)
            self.inventory_panel.clear_pending()
            self.reset_map_hints()
            self.selected_slot=None
        recent=(self.session.current or {}).get('recent_relics',[])
        if recent:
            self.inventory_panel.set_scan({'page_type':'inventory','inventory_kind':'relics','entries':recent,
                'captured_at':self.session.current.get('captured_at'),'page_fingerprint':recent[0]['page_fingerprint'],
                'source':self.session.current.get('source'),'ownership_risk':False,'context_detected':'recent_relics',
                'notices':['底部近期获得记录：明确匹配已补入清单，其他图标可在本页确认；数量在清单表中补齐。']},follow=False)
        if getattr(self,'calculation_window',None):self.calculation_window.dps_panel.refresh_snapshot()

    def reset_map_hints(self):
        self.floor_hint.setCurrentIndex(0)
        self.side_mode.setCurrentIndex(0)
        self.probability_panel.memory.setChecked(False)

    def clear_manual(self):
        if self.busy:return
        self.reset_map_hints()
        count=self.session.clear_manual()
        self.live_monitor.reset_observations()
        if self.session.current:self.recognizer.refresh_probabilities(self.session.current);self.session.persist()
        self.result=deepcopy(self.session.current);self.inventory_panel.clear_pending();self.render()
        self.status.setText(f'已清除 {count} 项手动修改，可撤销恢复')

    def new_run(self):
        if self.busy:
            return
        self.session.reset()
        self.reset_map_hints()
        self.live_monitor.reset_observations()
        self.result, self.image, self.selected_slot = None, None, None
        self.map_image, self.last_scan = None, None
        self.inventory_panel.clear_pending()
        self.render()
        self.status.setText("已新建本局，可撤销恢复上一状态。")

    def toggle(self):
        if self.isVisible() and not self.isMinimized() and self.isActiveWindow():
            self.hide()
        else:
            self.present_results()

    def export(self):
        if self.result is None and not any(b["items"] for b in self.session.inventories.values()):
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出识别结果", str(ROOT / "data/recognition.json"), "JSON (*.json)")
        if path:
            save_result({"schema_version": 2, "run": self.session.run, "map": self.result, "inventories": self.session.inventories,'exploration':self.session.exploration,
                         "latest_scan": self.last_scan}, path)
            self.status.setText(f"已导出：{path}")

    def settings(self, section="hotkeys"):
        self.go_page("设置")
        self.settings_panel.tabs.setCurrentIndex(0)
        self.settings_panel.refresh()

    def reload_data(self):
        self.data_reload_error=None
        try:
            exploration=ExplorationCatalog.shared();exploration.reload()
            self.recognizer.catalog.reload()
            self.recognizer.catalog.include_exploration_items()
            self.recognizer.recent_relics.templates=None
            self.recognizer.recent_relics.cache.clear()
            if self.session.current:
                self.recognizer.refresh_probabilities(self.session.current)
                self.result=deepcopy(self.session.current);self.session.persist()
            self.stage_panel.index=exploration.stage_index();self.stage_panel.filter_stages()
            if getattr(self,'effects_window',None):self.effects_window.refresh()
            if getattr(self,'calculation_window',None):
                from .effect_rules import RuleCatalog
                rules=RuleCatalog();self.calculation_window.dps_panel.catalog=rules
                self.calculation_window.session_panel.catalog=rules
                self.calculation_window.dps_panel.refresh_snapshot()
        except Exception as exc:  # a half-written database must not kill the window
            self.data_reload_error=str(exc)
            self.status.setText(f"数据重载失败：{exc}")
            return False
        self.probability_panel.refresh()
        self.inventory_panel.refresh()
        self.event_pool.refresh()
        self.render()
        self.status.setText("数据已重新载入，可以继续识别。")
        return True

    def closeEvent(self, event):
        panel=getattr(self,'settings_panel',None)
        if panel is not None and panel.process is not None:
            self.status.setText('数据更新进行中，完成后可关闭窗口。');event.ignore();return
        self.live_monitor.stop()
        self.hotkeys.close()
        super().closeEvent(event)


def main():
    parser = argparse.ArgumentParser(description="树海 Reader 截图与地图识别")
    parser.add_argument("--image", type=Path)
    parser.add_argument("--session", type=Path)
    parser.add_argument("--no-hotkeys", action="store_true")
    parser.add_argument("--preview", type=Path, help="Save a rendered QA preview after recognition")
    parser.add_argument("--tab",choices=["map","probabilities","inventory"])
    args = parser.parse_args()
    enable_dpi_awareness()
    app = QApplication(sys.argv[:1])
    app.setApplicationName("树海 Reader")
    app.setStyleSheet(STYLE)
    # A second launch should reveal the existing UI, not compete for hotkeys.
    identifier = str(ROOT.resolve()) + (str(args.session.resolve()) if args.session else "")
    server_name = "TreeSeaReader-" + hashlib.sha256(identifier.encode()).hexdigest()[:16]
    client = QLocalSocket()
    client.connectToServer(server_name)
    if client.waitForConnected(250):
        request = {"image": str(args.image.resolve()) if args.image else None,"tab":args.tab}
        client.write((json.dumps(request) + "\n").encode())
        client.flush()
        client.waitForBytesWritten(500)
        return 0
    server = QLocalServer()
    if not server.listen(server_name):
        QLocalServer.removeServer(server_name)
        if not server.listen(server_name):
            raise RuntimeError("无法建立 Reader 本地单实例通信。")
    window = ReaderWindow(args.session, args.no_hotkeys)
    window.preview_path = args.preview
    pending = []
    def incoming():
        while server.hasPendingConnections():
            socket = server.nextPendingConnection()
            pending.append(socket)
            def receive(connection=socket):
                if not connection.canReadLine():
                    return
                try:
                    request = json.loads(bytes(connection.readLine(65536)).decode())
                    window.present_results()
                    target={"map":window.map_view,"probabilities":window.probability_panel,"inventory":window.inventory_panel}.get(request.get("tab"))
                    if target is not None:window.tabs.setCurrentWidget(target)
                    if request.get("image"):
                        window.import_image(request["image"])
                except (ValueError, TypeError):
                    pass
                connection.disconnectFromServer()
                if connection in pending:
                    pending.remove(connection)
            socket.readyRead.connect(receive)
            receive()
    server.newConnection.connect(incoming)
    screen = app.primaryScreen().availableGeometry()
    window.resize(min(1400, screen.width() - 50), min(940, screen.height() - 70))
    window.show()
    from .startup import gui_ready
    gui_ready(app,window,'reader')
    QTimer.singleShot(100, window.present_results)
    if args.tab:
        QTimer.singleShot(120,lambda:window.tabs.setCurrentWidget({"map":window.map_view,"probabilities":window.probability_panel,"inventory":window.inventory_panel}[args.tab]))
    if args.image:
        QTimer.singleShot(150, lambda: window.import_image(args.image))
    elif args.preview and window.result:
        QTimer.singleShot(250, window.save_preview)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
