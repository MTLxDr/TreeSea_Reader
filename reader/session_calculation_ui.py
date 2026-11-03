"""Read-only bridge between Reader snapshots and the independent calculator."""
from copy import deepcopy
import json
from pathlib import Path

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QPushButton, QComboBox, QSpinBox, QCheckBox, QPlainTextEdit, QFileDialog, QSplitter,
    QTreeWidget, QTreeWidgetItem)
from PySide6.QtCore import Qt

from .calculation_api import parse_json
from .effect_rules import RuleCatalog, ROOT
from .session_calculation import calculate_session


class SessionCalculationPanel(QWidget):
    def __init__(self, manual_panel, show_manual, snapshot_provider=None, database=None):
        super().__init__()
        self.manual_panel, self.show_manual = manual_panel, show_manual
        self.snapshot_provider = snapshot_provider
        self.catalog = RuleCatalog(database) if database else RuleCatalog()
        self.snapshot, self.last_request, self.last_response = {}, None, None
        self.request_context = {}
        layout = QVBoxLayout(self)
        note = QLabel('本局接入读取已确认的库存与干员。难度、分队、关卡和敌人档位需明确选择；原文未编译、条件未知或库存不完整时列出缺项。此处不改写Reader存档。')
        note.setWordWrap(True); layout.addWidget(note)
        row = QHBoxLayout()
        for label, fn in [('读取Reader当前快照', self.refresh_snapshot), ('导入本局存档/请求', self.import_snapshot)]:
            b = QPushButton(label); b.clicked.connect(fn); row.addWidget(b)
        self.snapshot_status = QLabel(); row.addWidget(self.snapshot_status, 1); layout.addLayout(row)
        split = QSplitter(); layout.addWidget(split, 1)
        left = QWidget(); form = QFormLayout(left); split.addWidget(left)
        self.operator = QComboBox(); form.addRow('已确认干员', self.operator)
        self.stage = QComboBox(); self.stage.addItem('请选择关卡', None)
        for e in sorted((e for e in self.catalog.entries.values() if e['category'] == '关卡环境与规则'), key=lambda e: e['subject_id']):
            self.stage.addItem((self.catalog.stage_names.get(e['subject_id']) or e['name']) + ' · ' + e['subject_id'], e['subject_id'])
        form.addRow('具体关卡', self.stage)
        self.enemy = QComboBox(); form.addRow('敌人及档位', self.enemy)
        self.difficulty = QSpinBox(); self.difficulty.setRange(-1, 99); self.difficulty.setSpecialValueText('未知'); self.difficulty.setValue(-1)
        form.addRow('难度（0为不加难度符文）', self.difficulty)
        self.squad = QComboBox(); self.squad.addItem('未知', 'unknown'); self.squad.addItem('本次不使用分队加成', None)
        for e in sorted((e for e in self.catalog.entries.values() if e['category'] == '路标分队'), key=lambda e: e['name']):
            self.squad.addItem(e['name'] + ' · ' + e['variant'], e['id'])
        form.addRow('分队', self.squad)
        self.skill = QComboBox(); self.skill.addItem('未知', None); self.skill.addItem('技能未生效', False); self.skill.addItem('所选技能已生效', True)
        form.addRow('技能状态', self.skill)
        self.kind = QComboBox(); self.kind.addItem('请选择伤害类型', None)
        for label, value in [('物理', 'physical'), ('法术', 'arts'), ('真实', 'true'), ('元素扣血', 'element')]: self.kind.addItem(label, value)
        form.addRow('本次伤害类型', self.kind)
        self.attack_mode=QComboBox();self.attack_mode.addItem('未知',None)
        self.attack_mode.addItem('近战攻击','melee');self.attack_mode.addItem('远程攻击','ranged')
        form.addRow('本次攻击方式',self.attack_mode)
        self.hits = QSpinBox(); self.hits.setRange(1, 10000); form.addRow('相同伤害段数', self.hits)
        self.partial = QCheckBox('允许部分演算（明确标为不完整）'); form.addRow(self.partial)
        self.json_mode = QCheckBox('使用下方JSON作为选择与面板输入'); form.addRow(self.json_mode)
        self.selection_json = QPlainTextEdit(); self.selection_json.setPlaceholderText('可补入operator_panel；面板已含信赖/潜能/模组时在included中明确列出。')
        self.selection_json.setMinimumHeight(170); form.addRow('选择与面板JSON', self.selection_json)
        sync = QPushButton('从上方选择生成JSON'); sync.clicked.connect(self.sync_selection); form.addRow(sync)
        right = QWidget(); box = QVBoxLayout(right); split.addWidget(right)
        self.status = QLabel('等待组装'); self.status.setObjectName('review'); self.status.setWordWrap(True); box.addWidget(self.status)
        self.audit_tree = QTreeWidget(); self.audit_tree.setHeaderLabels(['规则 / 缺项', '阶段 / 原因', '数值'])
        self.audit_tree.setColumnWidth(0, 310); self.audit_tree.setColumnWidth(1, 260); box.addWidget(self.audit_tree, 1)
        self.output = QPlainTextEdit(); self.output.setReadOnly(True); box.addWidget(self.output, 1)
        actions = QHBoxLayout()
        for label, fn in [('组装并核对', self.run), ('查看逐步算式', self.view_trace), ('导出本局请求与结果', self.export_bundle)]:
            b = QPushButton(label); b.clicked.connect(fn); actions.addWidget(b)
        layout.addLayout(actions)
        for combo in (self.operator,self.stage,self.enemy,self.squad,self.skill,self.kind,self.attack_mode):
            combo.setMinimumContentsLength(20)
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        split.setSizes([520,920])
        self.stage.currentIndexChanged.connect(self.change_stage)
        self.change_stage(); self.refresh_snapshot()

    def refresh_snapshot(self):
        try:
            if self.snapshot_provider: snapshot = self.snapshot_provider()
            else: snapshot = parse_json((ROOT / 'data/sessions/current.json').read_text(encoding='utf-8-sig'))
            self.set_snapshot(snapshot)
        except (ValueError, OSError) as error: self.snapshot_status.setText(str(error))

    def set_snapshot(self, snapshot):
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get('inventories'), dict): raise ValueError('本局存档缺少inventories')
        old = self.operator.currentData(); self.snapshot = deepcopy(snapshot)
        self.request_context = {}
        self.operator.clear(); self.operator.addItem('请选择本局干员', None)
        for item in snapshot['inventories'].get('operators', {}).get('items', []):
            if item.get('confirmed') is True and not item.get('excluded'):
                self.operator.addItem(item.get('name', item['id']), item['id'])
        self.operator.setCurrentIndex(max(0, self.operator.findData(old)))
        self.snapshot_status.setText(f'已读取快照 · {self.operator.count() - 1}名已确认干员')
        self.sync_selection()

    def import_snapshot(self):
        path, _ = QFileDialog.getOpenFileName(self, '导入Reader本局存档或计算请求', '', 'JSON (*.json)')
        if path:
            try: self.load_document(parse_json(Path(path).read_text(encoding='utf-8-sig')))
            except (ValueError, OSError) as error: self.snapshot_status.setText(str(error))

    def load_document(self, data):
        if not isinstance(data, dict): raise ValueError('本局文件必须是JSON对象')
        if 'request' in data: data = data['request']
        if not isinstance(data, dict): raise ValueError('request必须是对象')
        if 'operation' in data:
            if data['operation'] != 'session_damage' or data.get('schema_version','1.2') != '1.2':
                raise ValueError('此页接收1.2版session_damage请求；基础请求请在手工页导入')
            if not isinstance(data.get('selection'), dict) or not isinstance(data.get('allow_partial',False), bool):
                raise ValueError('无效的本局选择或部分演算标志')
            self.set_snapshot(data.get('session'))
            self.selection_json.setPlainText(json.dumps(data['selection'],ensure_ascii=False,indent=2))
            self.json_mode.setChecked(True);self.partial.setChecked(data.get('allow_partial',False))
            self.request_context=deepcopy(data.get('context',{}))
        else:self.set_snapshot(data)

    def change_stage(self):
        self.enemy.clear(); self.enemy.addItem('请选择实际敌人档位', None)
        stage = self.stage.currentData()
        for e in sorted((e for e in self.catalog.entries.values() if e['category'] == '敌人关卡参数'
                         and e['subject_id'].startswith(str(stage) + ':')), key=lambda e: (e['name'], e['variant'])):
            data = e['source_parameters']
            self.enemy.addItem(e['name'] + ' · ' + e['variant'], data['id'] + ':' + str(data['level']))

    def sync_selection(self):
        self.selection_json.setPlainText(json.dumps(self.selection_data(), ensure_ascii=False, indent=2))

    def selection_data(self):
        data = {'operator_id': self.operator.currentData(), 'stage_id': self.stage.currentData(),
                'damage_type': self.kind.currentData(), 'skill_active': self.skill.currentData(), 'hits': self.hits.value(),
                'attack_mode':self.attack_mode.currentData()}
        if self.enemy.currentData():
            data['enemy_id'], level = self.enemy.currentData().rsplit(':', 1)
            data['enemy_level'] = int(level)
        if self.difficulty.value() >= 0: data['difficulty'] = self.difficulty.value()
        if self.squad.currentData() != 'unknown': data['squad_id'] = self.squad.currentData()
        # Missing choices stay absent so the compiler can report useful gaps.
        data = {k: v for k, v in data.items() if v is not None or k == 'squad_id'}
        return data

    def run(self):
        try:
            selection = parse_json(self.selection_json.toPlainText()) if self.json_mode.isChecked() else self.selection_data()
            envelope = {'schema_version': '1.2', 'operation': 'session_damage', 'session': deepcopy(self.snapshot),
                        'selection': selection, 'allow_partial': self.partial.isChecked(),'context':deepcopy(self.request_context)}
            response = calculate_session(envelope, self.catalog)
            self.last_request, self.last_response = envelope, response
            self.output.setPlainText(json.dumps(response, ensure_ascii=False, indent=2))
            audit = response['audit']; self.audit_tree.clear()
            for r in audit['rules']:
                stages = {'relic_add':'藏品符文平值','relic_mul_delta':'藏品符文加成率',
                    'direct_add':'直接加算','direct_mul_delta':'直接乘算加成率','base_add':'起始面板平值补充',
                    'final_add':'最终加算','attack_scale_groups':'攻击力比例（防抗前）'}
                value = f"{r['value']*100:+.8g}%" if r['unit']=='ratio_delta' else f"{r['value']:.8g}"
                item = QTreeWidgetItem(self.audit_tree, [r['source']['name'] + ('（生效）' if r['applied'] else '（未生效）'),
                    r['reason'] or stages.get(r['stage'],r['stage']), value])
                item.setToolTip(0, json.dumps(r, ensure_ascii=False, indent=2))
            for group, title in [('missing', '缺项'), ('unsupported', '未支持'), ('excluded', '未选中')]:
                parent = QTreeWidgetItem(self.audit_tree, [title, str(len(audit[group])), ''])
                for row in audit[group]:
                    item = QTreeWidgetItem(parent, [row.get('name', row.get('code', row.get('effect_id', row.get('id', '')))),
                        row.get('detail', row.get('reason', '')), ''])
                    item.setToolTip(0, json.dumps(row, ensure_ascii=False, indent=2))
            self.audit_tree.expandAll()
            result = response['result']
            state = '完整覆盖本次支持范围' if response['status'] == 'complete_for_supported_scope' else '不完整 · 请检查缺项/未支持清单'
            amount = f" · 已知贡献演算扣血 {result['hp_damage']:.10g}" if result else ' · 暂不输出伤害结果'
            self.status.setText(state + amount)
            return True
        except (ValueError, OSError) as error:
            self.last_request = self.last_response = None
            self.output.clear(); self.audit_tree.clear(); self.status.setText('输入错误：' + str(error)); return False

    def view_trace(self):
        if not self.run(): return
        if not self.last_response['result']:
            self.status.setText('请先组装；存在缺项时，需显式选择允许部分演算才显示已知贡献。'); return
        data = deepcopy(self.last_response['compiled_request'])
        data['context'] = {'session_status': self.last_response['status'], 'audit': self.last_response['audit']}
        self.manual_panel.input_json.setPlainText(json.dumps(data, ensure_ascii=False, indent=2))
        self.manual_panel.json_mode.setChecked(True)
        if self.manual_panel.run():
            self.manual_panel.status.setText('本局来源演算 · ' + self.status.text() + '。完整审计保留在本局接入页和导出文件中。')
            self.show_manual()

    def export_bundle(self):
        if not self.run(): return
        path, _ = QFileDialog.getSaveFileName(self, '导出本局计算', 'session_calculation.json', 'JSON (*.json)')
        if path:
            try: Path(path).write_text(json.dumps({'request': self.last_request, 'response': self.last_response}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            except OSError as error: self.status.setText(str(error))
