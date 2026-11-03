"""设置中心：热键 / 界面 / 识别与环境 / 数据与更新。

原来是一个两页签的模态对话框（每次关掉就丢上下文），现在变成主窗里的一个页面：
左边导航“设置”进来，四个分组各管一摊，数据组还能直接跑更新并看日志。
"""
from __future__ import annotations

from datetime import datetime
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys

from PySide6.QtCore import QProcess, Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QFrame, QGroupBox,
                               QHBoxLayout, QHeaderView, QKeySequenceEdit, QLabel, QPlainTextEdit,
                               QProgressBar, QPushButton, QTableWidget, QTableWidgetItem,
                               QTabWidget, QVBoxLayout, QWidget)

from .catalog import ROOT


def _table(headers):
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.horizontalHeader().setStretchLastSection(True)
    widget.verticalHeader().hide()
    widget.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    widget.setShowGrid(False)
    return widget


def _stat(path):
    path = Path(path)
    if not path.exists():
        return "缺失", "—"
    stamp = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    size = path.stat().st_size
    text = f"{size / 1024 / 1024:.1f} MB" if size >= 1024 * 1024 else f"{size / 1024:.0f} KB"
    return text, stamp


class SettingsPanel(QWidget):
    """四个分组共用一块工作区，进入页面时重新读一遍状态。"""

    dataUpdated = Signal()

    def __init__(self, window):
        super().__init__()
        self.window = window
        self.process = None
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 10, 14, 10)
        root.setSpacing(8)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_hotkeys(), "热键与界面")
        self.tabs.addTab(self._build_diagnostics(), "识别与环境")
        self.tabs.addTab(self._build_data(), "数据与更新")
        root.addWidget(self.tabs, 1)
        self.refresh()

    # -- 热键 ------------------------------------------------------------
    def _build_hotkeys(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)
        lay.addWidget(self._note("全局热键在游戏或其他窗口里也能用。改完点“应用热键”，冲突的组合会在这里报出来。"))

        card = QFrame()
        card.setObjectName("panel")
        form = QFormLayout(card)
        form.setContentsMargins(14, 12, 14, 12)
        form.setSpacing(8)
        self.hotkey_edits = {}
        titles = (("capture", "截取并识别"), ("toggle", "显示 / 隐藏窗口"), ("undo", "撤销最近一次更新"),
                  ("relics", "识别已有藏品"), ("parts", "识别零件箱"), ("operators", "识别已招募干员"))
        for name, title in titles:
            edit = QKeySequenceEdit(QKeySequence(self.window.config[name]))
            self.hotkey_edits[name] = edit
            form.addRow(title, edit)
        lay.addWidget(card)

        row = QHBoxLayout()
        self.hotkey_status = QLabel("")
        self.hotkey_status.setObjectName("muted")
        row.addWidget(self.hotkey_status, 1)
        revert = QPushButton("恢复默认")
        revert.clicked.connect(self._reset_hotkeys)
        row.addWidget(revert)
        apply = QPushButton("应用热键")
        apply.setObjectName("primary")
        apply.clicked.connect(self._apply_hotkeys)
        row.addWidget(apply)
        lay.addLayout(row)

        self._ui_section(lay)
        lay.addStretch()
        return page

    def _reset_hotkeys(self):
        from .hotkeys import DEFAULT_HOTKEYS
        for name, edit in self.hotkey_edits.items():
            edit.setKeySequence(QKeySequence(DEFAULT_HOTKEYS[name]))
        self.hotkey_status.setText("已填入默认组合，点“应用热键”生效。")

    def _apply_hotkeys(self):
        config = {name: edit.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
                  for name, edit in self.hotkey_edits.items()}
        try:
            errors = self.window.hotkeys.register(config)
        except ValueError as exc:
            self.hotkey_status.setText(str(exc))
            return
        self.window.config = config
        self.window.save_settings()
        self.window.hotkey_label.setText(self.window.key_help())
        self.hotkey_status.setText("；".join(errors) if errors else "热键已更新。")
        self.window.status.setText("热键已更新。" if not errors else "；".join(errors))

    # -- 界面 ------------------------------------------------------------
    def _ui_section(self, lay):
        lay.addWidget(self._note("界面设置只影响本机这一份，保存在 data/settings.json。"))
        card = QFrame()
        card.setObjectName("panel")
        form = QFormLayout(card)
        form.setContentsMargins(14, 12, 14, 12)
        form.setSpacing(8)
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("长按显示（按住 Tab 轮播，松开停止）", "hold")
        self.mode_combo.addItem("按键切换（按一次开始，再按一次停止）", "toggle")
        self.show_check = QCheckBox("读取完成后显示结果")
        form.addRow("前台 Tab 操作", self.mode_combo)
        form.addRow("", self.show_check)
        hint = QLabel("长按适合边看地图边查概率；切换适合单手。停止后叠加层保留 3 秒。")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        form.addRow(hint)
        lay.addWidget(card)

        row = QHBoxLayout()
        self.ui_status = QLabel("")
        self.ui_status.setObjectName("muted")
        row.addWidget(self.ui_status, 1)
        save = QPushButton("保存界面设置")
        save.setObjectName("primary")
        save.clicked.connect(self._apply_ui)
        row.addWidget(save)
        lay.addLayout(row)

    def _apply_ui(self):
        mode = self.mode_combo.currentData()
        index = self.window.tab_mode.findData(mode)
        if index >= 0:
            self.window.tab_mode.setCurrentIndex(index)
        self.window.auto_show.setChecked(self.show_check.isChecked())
        self.window.save_settings()
        self.window.status.setText("界面设置已保存。")
        self.ui_status.setText("已保存 · Tab " + ("长按显示" if mode == "hold" else "按键切换"))

    # -- 识别与环境 ------------------------------------------------------
    def _build_diagnostics(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)
        lay.addWidget(self._note("检查运行环境、数据库完整性和 OCR 模型。打包版换机后先跑一遍，缺什么会写在这里。"))
        self.diag_table = _table(["检查项", "结果"])
        self.diag_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.diag_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        lay.addWidget(self.diag_table, 1)
        self._placeholder_diagnostics()
        row = QHBoxLayout()
        self.diag_status = QLabel("尚未运行")
        self.diag_status.setObjectName("muted")
        row.addWidget(self.diag_status, 1)
        run = QPushButton("运行自检")
        run.clicked.connect(lambda: self._run_diagnostics(False))
        row.addWidget(run)
        ocr = QPushButton("加载 OCR 模型")
        ocr.clicked.connect(lambda: self._run_diagnostics(True))
        row.addWidget(ocr)
        lay.addLayout(row)
        return page

    def _placeholder_diagnostics(self):
        checks = ["PySide6", "numpy", "cv2", "PIL", "mss", "rapidocr", "onnxruntime", "windows_capture",
                  "reader.app", "reader.calculation_app", "reader.effects_ui",
                  "reader.calculation_api", "数据库完整性", "效果条目"]
        self.diag_table.setRowCount(len(checks))
        for index, name in enumerate(checks):
            self.diag_table.setItem(index, 0, QTableWidgetItem(name))
            self.diag_table.setItem(index, 1, QTableWidgetItem("未检查"))

    def _run_diagnostics(self, with_ocr):
        from .launcher import diagnostic_report
        self.diag_status.setText("正在自检…")
        if with_ocr:
            self.diag_status.setText("正在加载 OCR 模型，可能要几秒…")
        from PySide6.QtWidgets import QApplication
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            report = diagnostic_report(with_ocr=with_ocr)
        finally:
            QApplication.restoreOverrideCursor()
        rows = []
        for name, value in report["checks"].items():
            rows.append((name, "ok" if value == "ok" else str(value)))
        if "database_integrity" in report:
            rows.append(("数据库完整性", report["database_integrity"]))
        if "effect_entries" in report:
            rows.append(("效果条目", f"{report['effect_entries']} 条"))
        if "ocr_models" in report:
            rows.append(("OCR 模型", report["ocr_models"]))
        rows.append(("Python", f"{report['python']} · {report['executable']}"))
        self.diag_table.setRowCount(len(rows))
        for index, (name, value) in enumerate(rows):
            bad = value not in {"ok"} and not value.startswith(("3.", "2.")) and name not in ("效果条目",)
            cell = QTableWidgetItem(name)
            self.diag_table.setItem(index, 0, cell)
            result = QTableWidgetItem(value)
            if bad:
                result.setForeground(Qt.GlobalColor.yellow)
            self.diag_table.setItem(index, 1, result)
        self.diag_table.resizeColumnToContents(0)
        self.diag_status.setText("自检完成 · " + ("通过" if report["status"] == "ok" else "有问题，见上表"))

    # -- 数据与更新 ------------------------------------------------------
    def _build_data(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)
        lay.addWidget(self._note("更新会先抓来源快照，成功后才重建数据库；失败时保留旧库。更新期间不要关窗口。"))

        card = QFrame()
        card.setObjectName("panel")
        body = QVBoxLayout(card)
        body.setContentsMargins(14, 12, 14, 12)
        body.setSpacing(8)
        title = QLabel("当前数据")
        title.setObjectName("section")
        body.addWidget(title)
        self.data_table = _table(["数据", "体积", "最近修改"])
        self.data_table.setMinimumHeight(150)
        body.addWidget(self.data_table)
        self.version_label = QLabel("")
        self.version_label.setObjectName("muted")
        self.version_label.setWordWrap(True)
        body.addWidget(self.version_label)
        lay.addWidget(card)

        row = QHBoxLayout()
        self.update_progress = QProgressBar()
        self.update_progress.setRange(0, 1)
        self.update_progress.setValue(0)
        self.update_progress.setTextVisible(False)
        self.update_progress.setFixedWidth(180)
        row.addWidget(self.update_progress)
        self.update_status = QLabel("未运行")
        self.update_status.setObjectName("muted")
        row.addWidget(self.update_status, 1)
        self.check_button = QPushButton("检查更新")
        self.check_button.clicked.connect(lambda: self._start_update(check_only=True))
        row.addWidget(self.check_button)
        self.update_source=QComboBox()
        for text,value in [('全部资料','all'),('路标与PRTS资料','both'),('关卡、事件与藏品池','exploration')]:self.update_source.addItem(text,value)
        row.addWidget(self.update_source)
        self.update_button = QPushButton("立即更新")
        self.update_button.setObjectName("primary")
        self.update_button.clicked.connect(lambda: self._start_update(check_only=False))
        row.addWidget(self.update_button)
        lay.addLayout(row)

        self.update_log = QPlainTextEdit()
        self.update_log.setReadOnly(True)
        self.update_log.setPlaceholderText("更新日志会出现在这里。")
        lay.addWidget(self.update_log, 1)

        self.restart_row = QWidget()
        restart_layout = QHBoxLayout(self.restart_row)
        restart_layout.setContentsMargins(0, 0, 0, 0)
        tip = QLabel("更新已完成。已加载的数据会在下次启动时换成新库。")
        tip.setObjectName("muted")
        restart_layout.addWidget(tip, 1)
        restart = QPushButton("重启程序")
        restart.clicked.connect(self._restart)
        restart_layout.addWidget(restart)
        self.restart_row.hide()
        lay.addWidget(self.restart_row)
        return page

    @staticmethod
    def _note(text):
        label = QLabel(text)
        label.setObjectName("muted")
        label.setWordWrap(True)
        return label

    def _data_rows(self):
        rows = [("地图与节点库", ROOT / "data/reader.sqlite3"),
                ("效果目录", ROOT / "data/effects/catalog.json"),
                ("探索知识库", ROOT / "data/exploration/knowledge.sqlite3"),
                ("本局存档", ROOT / "data/sessions/current.json"),
                ("更新报告", ROOT / "reports/update_status.json")]
        return [(name, *_stat(path)) for name, path in rows]

    def refresh(self):
        self.mode_combo.blockSignals(True);self.show_check.blockSignals(True)
        self.mode_combo.setCurrentIndex(max(0, self.mode_combo.findData(self.window.tab_mode.currentData())))
        self.show_check.setChecked(self.window.auto_show.isChecked())
        self.mode_combo.blockSignals(False);self.show_check.blockSignals(False)
        rows = self._data_rows()
        self.data_table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            for column, value in enumerate(row):
                self.data_table.setItem(index, column, QTableWidgetItem(str(value)))
        self.data_table.setColumnWidth(0, 200)
        self.version_label.setText(self._version_text())

    def _version_text(self):
        parts = []
        try:
            catalog = self.window.recognizer.catalog
            with closing(sqlite3.connect(catalog.database.resolve().as_uri() + "?mode=ro", uri=True)) as db:
                entries = db.execute("select count(*) from effect_entries").fetchone()[0]
                floors = db.execute("select count(*) from active_map_templates").fetchone()[0]
            parts.append(f"地图模板 {floors} 套")
            parts.append(f"效果档案 {entries} 条")
        except Exception as error:
            parts.append(f"读取数据库失败：{error}")
        try:
            from .exploration import ExplorationCatalog
            exploration = ExplorationCatalog.shared()
            stages = len(exploration.stage_index())
            parts.append(f"关卡 {stages} 个")
        except Exception:
            pass
        report = ROOT / "reports/update_status.json"
        if report.exists():
            try:
                data = json.loads(report.read_text(encoding="utf-8"))
                checked = str(data.get("checked_at", ""))[:19].replace("T", " ")
                parts.append(f"上次检查 {checked}")
            except Exception:
                pass
        return " · ".join(parts)

    # -- 更新 ------------------------------------------------------------
    def _start_update(self, check_only):
        if self.process is not None:
            return
        script = ROOT / "scripts" / "update_data.py"
        if not script.exists():
            self.update_status.setText("找不到 scripts/update_data.py。")
            return
        self.update_log.clear()
        self.update_status.setText("检查更新中…" if check_only else "更新中，请勿关闭窗口…")
        self.update_progress.setRange(0, 0)
        self.check_button.setEnabled(False)
        self.update_button.setEnabled(False)
        self.restart_row.hide()
        if not check_only:
            self.window.live_monitor.stop()
            self.window.live_button.blockSignals(True);self.window.live_button.setChecked(False);self.window.live_button.blockSignals(False)
            self.window.live_button.setText('开始实时跟踪')
            self.window.live_feedback('更新资料，实时跟踪已停止')

        process = QProcess(self)
        process.setWorkingDirectory(str(ROOT))
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(self._read_output)
        process.finished.connect(lambda code, status: self._update_finished(code, check_only,process))
        process.errorOccurred.connect(self._update_failed)
        args = [str(script),'--source',self.update_source.currentData()] + (["--check-only"] if check_only else ['--force'])
        self.process = process
        executable=Path(sys.executable)
        if executable.name.lower()=='pythonw.exe':executable=executable.with_name('python.exe')
        if getattr(sys,'frozen',False):executable=ROOT/'runtime/python.exe'
        self.window.data_updating=not check_only
        process.start(str(executable), args)

    def _read_output(self):
        if self.process is None:return
        chunk = bytes(self.process.readAllStandardOutput()).decode("utf-8", "replace")
        if chunk:
            self.update_log.insertPlainText(chunk)
            bar = self.update_log.verticalScrollBar()
            bar.setValue(bar.maximum())

    def _update_failed(self, error):
        self.update_status.setText(f"启动失败：{error}")
        self._finish_progress()
        if error==QProcess.ProcessError.FailedToStart:self.process=None

    def _update_finished(self, code, check_only,process=None):
        if process is not None and process is not self.process:return
        self._finish_progress()
        if code == 0:
            self.update_status.setText("检查完成。" if check_only else "更新完成，正在重载数据…")
            self.refresh()
            if not check_only:
                self.dataUpdated.emit()
                if getattr(self.window,'data_reload_error',None):
                    self.restart_row.show();self.update_status.setText('更新已保存，重载失败：'+self.window.data_reload_error)
                else:self.update_status.setText("更新完成，数据已重载。")
        else:
            self.update_status.setText(f"更新失败（退出码 {code}），旧库保持原样。")
        self.process = None

    def _finish_progress(self):
        self.window.data_updating=False
        self.update_progress.setRange(0, 1)
        self.update_progress.setValue(1)
        self.check_button.setEnabled(True)
        self.update_button.setEnabled(True)

    def _restart(self):
        if getattr(sys, "frozen", False):
            self.update_status.setText("打包版请手动重启程序。")
            return
        main = ROOT / "launch_reader.py"
        if not main.exists():
            self.update_status.setText("找不到 launch_reader.py。")
            return
        # Let the single-instance server close before the replacement connects.
        import subprocess
        command=[sys.executable,'-c',
            'import subprocess,time; time.sleep(1); subprocess.Popen('+repr([sys.executable,str(main),'--tool','reader'])+',creationflags=0x08000000)',]
        subprocess.Popen(command,cwd=str(ROOT),creationflags=0x08000000)
        self.window.close()
        from PySide6.QtWidgets import QApplication
        QApplication.instance().quit()
