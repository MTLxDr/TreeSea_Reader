"""Review OCR proposals and edit the per-run catalog without touching the game."""
from copy import deepcopy

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QFrame, QHBoxLayout, QHeaderView, QLabel, QPushButton, QScrollArea, QSpinBox,
    QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .inventory import KINDS
from .events_ui import EventListPanel
DISPLAY_KINDS={**KINDS,"events":"事件"}
from .operator_ui import OperatorDetailsDialog

FIELDS = {"quantity": ("数量", 1, 999), "level": ("等级", 1, 90), "elite": ("精英阶段", 0, 2),
          "potential": ("潜能", 1, 6), "skill_level": ("技能等级", 1, 7), "mastery": ("专精", 0, 3)}
FIELDS["skill_index"] = ("技能编号（用于专精）", 1, 3)
FIELDS.update(valuation=("当前估价",0,99999),uses=("可用次数",0,99))
FIELDS.update(trust=("信赖",0,200),module_level=("模组等级",0,3),
              skill_1_mastery=("技能1专精",0,3),skill_2_mastery=("技能2专精",0,3),skill_3_mastery=("技能3专精",0,3))
COLUMNS = {"relics": ["名称", "数量"], "parts": ["名称", "数量", "当前估价", "可用次数"],
           "operators": ["名称", "等级", "精英阶段", "技能等级", "专精"]}
KEYS = {"relics": ["quantity"], "parts": ["quantity", "valuation", "uses"],
        "operators": ["level", "elite", "skill_level", "mastery"]}


def card(title):
    frame = QFrame()
    frame.setObjectName("panel")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(12, 10, 12, 10)
    layout.setSpacing(8)
    label = QLabel(title)
    label.setObjectName("section")
    layout.addWidget(label)
    return frame, layout


class InventoryPanel(QWidget):
    changed = Signal()

    def __init__(self, session, catalog):
        super().__init__()
        self.session, self.catalog, self.scan = session, catalog, None
        self.kind = list(KINDS)[0]
        self.cands = []
        self.edit_buttons=[]
        self.tables, self.completes = {}, {}
        self.body = self._build_body()
        self.side = self._build_side()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.body)
        self._rebuild_candidates()
        self.refresh()

    # -- 工作区 ----------------------------------------------------------
    def _build_body(self):
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(14, 10, 14, 8)
        layout.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(10)
        seg = QFrame()
        seg.setObjectName("seg")
        seg_layout = QHBoxLayout(seg)
        seg_layout.setContentsMargins(2, 2, 2, 2)
        seg_layout.setSpacing(2)
        self.kind_buttons = {}
        for kind, label in DISPLAY_KINDS.items():
            button = QPushButton(label)
            button.setObjectName("segItem")
            button.setCheckable(True)
            button.clicked.connect(lambda _=False, k=kind: self.show_kind(k))
            seg_layout.addWidget(button)
            self.kind_buttons[kind] = button
        seg.setFixedWidth(72 * len(DISPLAY_KINDS) + 6)
        head.addWidget(seg)
        self.status = QLabel()
        self.status.setObjectName("muted")
        head.addWidget(self.status)
        head.addStretch()
        layout.addLayout(head)

        self.stack = QStackedWidget()
        widths = {"relics": [110], "parts": [110], "operators": [70, 88, 88, 58]}
        for kind in KINDS:
            widget = QTableWidget(0, len(COLUMNS[kind]))
            widget.setHorizontalHeaderLabels(COLUMNS[kind])
            widget.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            for column, width in enumerate(widths[kind], 1):
                widget.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
                widget.setColumnWidth(column, width)
            widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            widget.verticalHeader().hide()
            widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            widget.itemChanged.connect(lambda item, k=kind: self.edit_cell(k, item))
            widget.itemSelectionChanged.connect(self._refresh_selected)
            self.tables[kind] = widget
            self.stack.addWidget(widget)
        self.event_panel=EventListPanel(self.session)
        self.event_panel.changed.connect(self._events_changed)
        self.stack.addWidget(self.event_panel)
        layout.addWidget(self.stack, 1)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        edit = QPushButton("补录 / 修正")
        edit.clicked.connect(lambda: self.edit_dialog(self.kind))
        self.edit_buttons.append(edit)
        actions.addWidget(edit)
        remove = QPushButton("移除所选")
        remove.clicked.connect(lambda: self.remove(self.kind))
        actions.addWidget(remove)
        self.details = QPushButton("技能 / 天赋 / 模组资料")
        self.details.clicked.connect(lambda: OperatorDetailsDialog(self.catalog, self.selected("operators"), self).exec())
        actions.addWidget(self.details)
        actions.addStretch()
        layout.addLayout(actions)

        self.help = QLabel("打开已有藏品、零件箱或已招募干员页面。实时跟踪会同步可见条目，翻页后继续读取；也可使用截图热键。")
        self.help.setWordWrap(True)
        self.help.setObjectName("muted")
        layout.addWidget(self.help)
        return host

    # -- 检查器 ----------------------------------------------------------
    def _build_side(self):
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)

        frame, inner = card("本页候选")
        self.candidate_card=frame
        self.cand_scroll = QScrollArea()
        self.cand_scroll.setWidgetResizable(True)
        self.cand_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.cand_scroll.setMaximumHeight(320)
        self.cand_host = QWidget()
        self.cand_layout = QVBoxLayout(self.cand_host)
        self.cand_layout.setContentsMargins(0, 0, 0, 0)
        self.cand_layout.setSpacing(6)
        self.cand_layout.addStretch()
        self.cand_scroll.setWidget(self.cand_host)
        inner.addWidget(self.cand_scroll)
        self.empty_label = QLabel("尚无待合并条目。读取藏品 / 零件 / 干员页面后，候选会出现在这里。")
        self.empty_label.setWordWrap(True)
        self.empty_label.setObjectName("muted")
        inner.addWidget(self.empty_label)
        self.accept = QPushButton("合并本页")
        self.accept.setObjectName("primary")
        self.accept.setEnabled(False)
        self.accept.clicked.connect(self.merge)
        inner.addWidget(self.accept)
        layout.addWidget(frame)

        frame, inner = card("选中条目")
        self.selected_name = QLabel("未选择")
        self.selected_name.setWordWrap(True)
        inner.addWidget(self.selected_name)
        self.selected_hint = QLabel("在左侧表格中点一行，可补录、修正或移除。")
        self.selected_hint.setWordWrap(True)
        self.selected_hint.setObjectName("muted")
        inner.addWidget(self.selected_hint)
        row = QHBoxLayout()
        row.setSpacing(6)
        edit = QPushButton("补录 / 修正")
        edit.clicked.connect(lambda: self.edit_dialog(self.kind))
        self.edit_buttons.append(edit)
        row.addWidget(edit)
        remove = QPushButton("移除")
        remove.clicked.connect(lambda: self.remove(self.kind))
        row.addWidget(remove)
        inner.addLayout(row)
        self.completes = {}
        for kind in KINDS:
            check = QCheckBox("已扫描全部页面")
            check.toggled.connect(lambda checked, k=kind: self.mark_complete(k, checked))
            check.hide()
            self.completes[kind] = check
            inner.addWidget(check)
        layout.addWidget(frame)
        layout.addStretch()
        return host

    # -- 状态 ------------------------------------------------------------
    def show_kind(self, kind):
        self.kind = kind
        for key, button in self.kind_buttons.items():
            button.blockSignals(True)
            button.setChecked(key == kind)
            button.blockSignals(False)
        self.candidate_card.setVisible(kind!='events')
        for button in self.edit_buttons:button.setText('添加候选' if kind=='events' else '补录 / 修正')
        self.cand_scroll.setVisible(kind!='events')
        self.accept.setVisible(kind!='events')
        self.empty_label.setVisible(kind!='events' and not self.cands)
        if kind=='events':
            self.stack.setCurrentWidget(self.event_panel)
            self.status.setText(f"事件　已记录 {len(self.session.exploration['events'])} 次")
            self.help.setText('记录已遇事件；移除所选后恢复事件池，撤销可以恢复删除。')
            for box in self.completes.values():box.hide()
            self.details.hide();self._refresh_selected();return
        self.stack.setCurrentWidget(self.tables[kind])
        bucket = self.session.inventories[kind]
        rows = [r for r in bucket["items"] if not r.get("excluded")]
        self.status.setText(f"{KINDS[kind]}　已记录 {len(rows)} 种 / {len(bucket['pages'])} 页　·　"
                            + ("已录全" if bucket["complete"] else "可继续翻页同步"))
        if kind == "parts":
            total = bucket.get("page_fields", {}).get("total_valuation", {}).get("value")
            if total is not None:
                self.status.setText(self.status.text() + f"　箱内总估价 {total}")
        check = self.completes[kind]
        check.blockSignals(True)
        check.setChecked(bucket["complete"])
        check.blockSignals(False)
        for key, box in self.completes.items():
            box.setVisible(key == kind)
        self._refresh_selected()

    def _events_changed(self):
        self.refresh();self.changed.emit()

    def _refresh_selected(self):
        if self.kind=='events':
            self.selected_name.setText('已遇事件')
            self.selected_hint.setText('候选按当前楼层优先排列；可重复事件可以多次记录。')
            self.details.hide();return
        identity = self.selected(self.kind)
        self.details.setVisible(self.kind == "operators")
        if not identity:
            self.selected_name.setText("未选择")
            self.selected_hint.setText("在左侧表格中点一行，可补录、修正或移除。")
            return
        row = next((r for r in self.session.inventories[self.kind]["items"] if r["id"] == identity), None)
        if row is None:
            self.selected_name.setText("未选择")
            return
        self.selected_name.setText(row["name"])
        fields = "　".join(f"{FIELDS[key][0]} {data['value']}" for key, data in row.get("fields", {}).items()
                           if key in FIELDS and data.get("value") is not None)
        if self.kind == "parts":
            copies = [card for card in self.session.inventories["parts"].get("visible_cards", [])
                      if card["id"] == identity]
            if len(copies) > 1:
                lines = []
                for index, card in enumerate(copies, 1):
                    details = "，".join(f"{FIELDS[key][0]} {card['fields'][key]['value']}"
                                        for key in ("valuation", "uses") if key in card["fields"])
                    lines.append(f"本页第 {index} 张：{details or '字段待读取'}")
                fields = "\n".join(lines)
        self.selected_hint.setText(fields or "字段未知，可补录。")

    # -- 扫描结果 --------------------------------------------------------
    def set_scan(self, result, follow=True):
        self.scan = result
        if follow:self.show_kind(result["inventory_kind"])
        coverage = result.get("coverage")
        if coverage:
            self.help.setText(f"本页识别 {coverage['recognized']} / {coverage['visible']} 张卡。其余卡片可补录。")
        else:
            self.help.setText("\n".join(result["notices"]))
        self._rebuild_candidates()

    def _rebuild_candidates(self):
        while self.cand_layout.count():
            item = self.cand_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.cands = []
        entries = (self.scan or {}).get("entries") or []
        for index, row in enumerate(entries):
            host = QWidget()
            line = QVBoxLayout(host)
            line.setContentsMargins(0, 0, 0, 0)
            line.setSpacing(2)
            top = QHBoxLayout()
            top.setSpacing(6)
            check = QCheckBox()
            check.setChecked(not (row["needs_review"] or self.scan["ownership_risk"]))
            check.setToolTip("取消勾选可跳过这一条")
            top.addWidget(check)
            combo = QComboBox()
            for item in row["candidates"]:
                combo.addItem(item["name"], item["id"])
            top.addWidget(combo, 1)
            line.addLayout(top)
            fields = " · ".join(f"{FIELDS[k][0]} {v['value']}" for k, v in row["fields"].items()) or "字段未知"
            detail = QLabel(f"{fields}\n{row['raw']} · {row['confidence']:.0%}")
            detail.setWordWrap(True)
            detail.setObjectName("muted")
            line.addWidget(detail)
            self.cand_layout.addWidget(host)
            self.cands.append({"check": check, "combo": combo, "index": index})
        self.cand_layout.addStretch()
        self.empty_label.setVisible(not entries)
        self.cand_scroll.setVisible(bool(entries))
        self.accept.setEnabled(bool(entries))
        self.accept.setText(f"合并本页 {len(entries)} 条" if entries else "合并本页")

    def clear_pending(self):
        self.scan = None
        self._rebuild_candidates()
        self.accept.setEnabled(False)

    def merge(self):
        if self.scan is None:
            return
        entries = []
        for cand in self.cands:
            if not cand["check"].isChecked():
                continue
            row = self.scan["entries"][cand["index"]]
            entry = deepcopy(row)
            identity = cand["combo"].currentData()
            match = next(item for item in self.catalog.inventory[self.scan["inventory_kind"]] if item["id"] == identity)
            entry.update(id=match["id"], name=match["name"], needs_review=False,
                         document_id=match["document_id"], source_url=match["source_url"])
            entries.append(entry)
        if self.session.merge_inventory(self.scan, entries):
            self.help.setText("本页已合并。请在游戏中翻到下一页，再按截图热键；重复条目会更新观察记录。")
            self.accept.setEnabled(False)
            self.refresh()
            self.changed.emit()

    def refresh(self):
        self.event_panel.refresh()
        for kind, table in self.tables.items():
            bucket = self.session.inventories[kind]
            rows = [r for r in bucket["items"] if not r.get("excluded")]
            table.blockSignals(True)
            table.setRowCount(len(rows))
            keys = KEYS[kind]
            for index, row in enumerate(rows):
                name = QTableWidgetItem(row["name"])
                name.setData(Qt.ItemDataRole.UserRole, row["id"])
                name.setFlags(name.flags() & ~Qt.ItemFlag.ItemIsEditable)
                name.setToolTip(f"来源：{row.get('source_url', '人工补录')}\nID：{row['id']}")
                table.setItem(index, 0, name)
                for col, key in enumerate(keys, 1):
                    data = row.get("fields", {}).get(key, {})
                    value = data.get("value")
                    cell = QTableWidgetItem(str(value) if value is not None else "—")
                    cell.setToolTip("手动锁定" if data.get("manual") else "OCR观察；空白表示未知")
                    table.setItem(index, col, cell)
            table.blockSignals(False)
        self.show_kind(self.kind)

    def selected(self, kind):
        if kind=="events":return None
        table = self.tables[kind]
        index = table.currentRow()
        return table.item(index, 0).data(Qt.ItemDataRole.UserRole) if index >= 0 else None

    def edit_cell(self, kind, cell):
        if cell.column() == 0:
            return
        table = self.tables[kind]
        identity = table.item(cell.row(), 0).data(Qt.ItemDataRole.UserRole)
        item = next(r for r in self.catalog.inventory[kind] if r["id"] == identity)
        key = KEYS[kind][cell.column() - 1]
        try:
            value = None if cell.text().strip() in {"", "—"} else int(cell.text())
            if value is not None and not FIELDS[key][1] <= value <= FIELDS[key][2]:
                raise ValueError
        except ValueError:
            self.help.setText(f"{FIELDS[key][0]}须为{FIELDS[key][1]}至{FIELDS[key][2]}，留空可标为未知。")
            self.refresh()
            return
        self.session.manual_inventory(kind, item, {key: value})
        self.refresh()
        self.changed.emit()

    def edit_dialog(self, kind):
        if kind=="events":self.event_panel.add();return
        dialog = QDialog(self)
        dialog.setWindowTitle("补录 / 修正" + KINDS[kind])
        form = QFormLayout(dialog)
        names = QComboBox()
        names.setEditable(True)
        names.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for item in sorted(self.catalog.inventory[kind], key=lambda r: r["name"]):
            names.addItem(item["name"], item["id"])
        chosen = self.selected(kind)
        if chosen:
            names.setCurrentIndex(names.findData(chosen))
        form.addRow("名称（可搜索）", names)
        spins = {}
        keys = [key for key in FIELDS if key not in {"quantity", "valuation", "uses"}] if kind == "operators" else KEYS[kind]
        for key in keys:
            label, lo, hi = FIELDS[key]
            spin = QSpinBox()
            spin.setRange(-1, hi)
            spin.setSpecialValueText("未知")
            spin.setValue(-1)
            spins[key] = spin
            form.addRow(label, spin)
        module=None
        if kind=="operators":
            module=QComboBox();form.addRow("装备模组",module)
        def load_fields():
            row = next((r for r in self.session.inventories[kind]["items"] if r["id"] == names.currentData()), {})
            for key, spin in spins.items():
                value = row.get("fields", {}).get(key, {}).get("value")
                spin.setValue(value if value is not None else -1)
            if module is not None:
                module.clear();module.addItem("未知",None);module.addItem("未装备","none")
                data=self.catalog.operator_detail(names.currentData()) or {}
                for component in data.get("components",{}).get("modules",[]):
                    fields=component["fields"]
                    identity=fields.get("类型") or fields.get("名称")
                    if identity:module.addItem(fields.get("名称",identity)+" · "+identity,identity)
                module.setCurrentIndex(max(0,module.findData(row.get("fields",{}).get("module_id",{}).get("value"))))
        names.currentIndexChanged.connect(load_fields)
        load_fields()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        item = next((i for i in self.catalog.inventory[kind] if i["name"] == names.currentText() and i["id"] == names.currentData()), None)
        if item is None:
            self.help.setText("请选择目录中的完整名称；目录之外的名称暂不自动猜测。")
            return
        fields = {key: spin.value() if spin.value() >= FIELDS[key][1] else None for key, spin in spins.items()}
        if module is not None:fields["module_id"]=module.currentData()
        self.session.manual_inventory(kind, item, fields)
        self.refresh()
        self.changed.emit()

    def remove(self, kind):
        if kind=="events":self.event_panel.remove();return
        identity = self.selected(kind)
        if identity:
            item = next(i for i in self.catalog.inventory[kind] if i["id"] == identity)
            self.session.manual_inventory(kind, item, excluded=True)
            self.refresh()
            self.changed.emit()

    def mark_complete(self, kind, checked):
        self.session.complete_inventory(kind, checked)
        self.refresh()
        self.changed.emit()
