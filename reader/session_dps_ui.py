"""Guided local-session DPH/DPS page. No examples masquerade as player data."""
from copy import deepcopy
import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QSpinBox, QDoubleSpinBox, QCheckBox, QFormLayout, QGridLayout, QGroupBox, QScrollArea, QSplitter,
    QTreeWidget, QTreeWidgetItem, QPlainTextEdit, QFileDialog, QTabWidget,QSizePolicy,QAbstractScrollArea)

from .catalog import ROOT
from .calculation_api import parse_json
from .effect_rules import RuleCatalog
from .session_calculation import field
from .session_dps import calculate_session_dps, selected_skill
from .skill_timing import skill_profile, source_text


def value_text(value):
    return '—' if value is None else f'{value:.10g}' if isinstance(value, (int, float)) else str(value)


class SessionDpsPanel(QWidget):
    def __init__(self, snapshot_provider=None, catalog=None):
        super().__init__()
        self.snapshot_provider = snapshot_provider
        self.catalog = catalog or RuleCatalog()
        self.snapshot = {}; self.request_context = {}
        self.last_request = self.last_response = None
        self._loading = True
        layout = QVBoxLayout(self)
        caption = QLabel('本局伤害计算 · 固定单目标 0 防御 / 0 法抗\n'
            '① 读取清单　→　② 选择干员、技能并核对练度　→　③ 计算 DPH 与技能期间平均 DPS')
        caption.setWordWrap(True); layout.addWidget(caption)
        top = QHBoxLayout()
        for text, callback in [('读取Reader当前清单', self.refresh_snapshot), ('导入本局存档/计算文件', self.import_snapshot)]:
            b = QPushButton(text); b.clicked.connect(callback); top.addWidget(b)
        layout.addLayout(top)
        self.snapshot_status = QLabel(); self.snapshot_status.setWordWrap(True); layout.addWidget(self.snapshot_status)
        split = QSplitter(); layout.addWidget(split, 1)
        left = QWidget(); form = QFormLayout(left)
        self.operator = QComboBox(); form.addRow('干员', self.operator)
        self.skill = QComboBox(); form.addRow('使用技能', self.skill)
        self.rank = QComboBox(); self.rank.addItem('请选择技能等级', None)
        for level in range(1, 8): self.rank.addItem(f'技能{level}级', (level, 0))
        for mastery in range(1, 4): self.rank.addItem(f'技能7级 · 专精{mastery}', (7, mastery))
        form.addRow('技能等级 / 专精', self.rank)
        self.skill_note = QLabel(); self.skill_note.setWordWrap(True); form.addRow(self.skill_note)
        note = QLabel('下方优先使用读取值；可直接补录。修改仅用于本次计算，不改写Reader清单。')
        note.setWordWrap(True); form.addRow(note)
        self.fields = {}
        for name, label, hi in [('elite','精英阶段',2),('level','等级',90),('trust','信赖',200),
                                ('potential','潜能',6),('module_level','模组等级',3)]:
            spin = QSpinBox(); spin.setRange(-1, hi); spin.setSpecialValueText('未读取'); spin.setValue(-1)
            self.fields[name] = spin; form.addRow(label, spin)
        self.module = QComboBox(); form.addRow('装备模组', self.module)
        self.kind = QComboBox(); self.kind.addItem('请选择伤害类型', None)
        for label, key in [('物理','physical'),('法术','arts'),('真实','true'),('元素扣血','element')]: self.kind.addItem(label,key)
        form.addRow('本次伤害类型', self.kind)
        self.attack_mode = QComboBox()
        self.attack_mode.addItem('近战攻击','melee');self.attack_mode.addItem('远程攻击','ranged')
        form.addRow('攻击方式', self.attack_mode)
        timing_group = QGroupBox('时序假设与补录（默认首击0秒、理论间隔）')
        timing_group.setCheckable(True); timing_group.setChecked(False)
        timing_outer = QVBoxLayout(timing_group); timing_body = QWidget(); tf = QFormLayout(timing_body)
        timing_outer.addWidget(timing_body); timing_body.hide(); timing_group.toggled.connect(timing_body.setVisible)
        self.duration = self.optional_seconds(36000); tf.addRow('技能持续时间 / 秒', self.duration)
        self.interval = self.optional_seconds(1000); tf.addRow('基础攻击间隔 / 秒', self.interval)
        self.first_hit = QDoubleSpinBox();self.first_hit.setRange(0,36000);self.first_hit.setDecimals(6)
        tf.addRow('首次命中时间 / 秒', self.first_hit)
        self.segments = QSpinBox();self.segments.setRange(0,32);self.segments.setSpecialValueText('按技能资料')
        tf.addRow('每次攻击命中段数', self.segments)
        self.frames = QCheckBox('按30帧四舍五入近似（并非所有技能的精确动画）'); tf.addRow(self.frames)
        self.custom_interval = QCheckBox('覆盖技能的间隔修正'); tf.addRow(self.custom_interval)
        self.interval_add = QDoubleSpinBox();self.interval_add.setRange(-1000,1000);self.interval_add.setDecimals(6)
        self.interval_factor = QDoubleSpinBox();self.interval_factor.setRange(.000001,1000);self.interval_factor.setDecimals(6);self.interval_factor.setValue(1)
        tf.addRow('间隔加算 / 秒',self.interval_add);tf.addRow('间隔倍率',self.interval_factor)
        form.addRow(timing_group)
        panel_group = QGroupBox('使用手工补充的起始面板'); panel_group.setCheckable(True);panel_group.setChecked(False)
        panel_outer=QVBoxLayout(panel_group);panel_body=QWidget();pf=QFormLayout(panel_body);panel_outer.addWidget(panel_body)
        panel_body.hide();panel_group.toggled.connect(panel_body.setVisible);self.manual_panel_group=panel_group
        warning=QLabel('填养成面板，不含本局藏品和技能加成；明确哪些养成平值已包含，避免重复计算。')
        warning.setWordWrap(True);pf.addRow(warning)
        self.panel_stats={}
        for key,label in [('atk','起始攻击力'),('attack_speed','起始攻击速度（默认100）'),('def','起始防御'),('max_hp','起始生命'),('res','起始法抗')]:
            spin=QDoubleSpinBox();spin.setRange(-1,1e9);spin.setDecimals(5);spin.setSpecialValueText('不提供')
            spin.setValue(100 if key=='attack_speed' else -1);self.panel_stats[key]=spin;pf.addRow(label,spin)
        self.included={}
        for key,label in [('trust','上述面板已含信赖平值'),('potential','已含潜能平值'),('module','已含模组平值')]:
            check=QCheckBox(label);self.included[key]=check;pf.addRow(check)
        form.addRow(panel_group)
        extra=QGroupBox('分队与额外条件（未选时不计入）');extra.setCheckable(True);extra.setChecked(False)
        ef_outer=QVBoxLayout(extra);extra_body=QWidget();ef=QFormLayout(extra_body);ef_outer.addWidget(extra_body)
        extra_body.hide();extra.toggled.connect(extra_body.setVisible)
        self.squad=QComboBox();self.squad.addItem('本次不使用分队加成',None)
        for e in sorted((e for e in self.catalog.entries.values() if e['category']=='路标分队'),key=lambda e:e['name']):
            self.squad.addItem(e['name']+' · '+e['variant'],e['id'])
        self.difficulty=QSpinBox();self.difficulty.setRange(0,99);ef.addRow('额外难度修正（0不附加）',self.difficulty);ef.addRow('分队',self.squad)
        form.addRow(extra)
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(left);scroll.setMinimumWidth(360);split.addWidget(scroll)
        right=QWidget();box=QVBoxLayout(right)
        self.status=QLabel('读取本局清单后选择干员与技能');self.status.setObjectName('review');self.status.setWordWrap(True);box.addWidget(self.status)
        cards=QHBoxLayout();self.dph_card=QLabel('DPH · 单段命中伤害\n—');self.dps_card=QLabel('技能期间平均 DPS\n—')
        for card in (self.dph_card,self.dps_card):
            card.setStyleSheet('font-size:19px;padding:14px;border:1px solid #47796e;');card.setWordWrap(True);cards.addWidget(card)
        box.addLayout(cards)
        self.summary=QLabel('结果只在点击计算后生成；读取或修改输入会清除旧结果。');self.summary.setWordWrap(True);box.addWidget(self.summary)
        self.partial=QCheckBox('显示已支持效果的部分结果（明确标为不完整）');box.addWidget(self.partial)
        actions=QHBoxLayout();calculate=QPushButton('计算 DPH / DPS');calculate.clicked.connect(self.run);actions.addWidget(calculate)
        export=QPushButton('导出本局计算');export.clicked.connect(self.export_bundle);actions.addWidget(export);box.addLayout(actions)
        self.details=QTabWidget();box.addWidget(self.details,1)
        self.trace=QTreeWidget();self.trace.setHeaderLabels(['计算步骤 / 来源','算式','结果']);self.trace.setColumnWidth(0,230)
        self.details.addTab(self.trace,'计算过程与来源')
        self.audit_tree=QTreeWidget();self.audit_tree.setHeaderLabels(['效果或缺项','说明']);self.audit_tree.setColumnWidth(0,270)
        self.details.addTab(self.audit_tree,'效果与缺项')
        advanced=QWidget();advanced_layout=QVBoxLayout(advanced)
        self.json_mode=QCheckBox('使用高级JSON（启用后覆盖左侧表单）');advanced_layout.addWidget(self.json_mode)
        self.json_mode.toggled.connect(left.setDisabled)
        self.selection_json=QPlainTextEdit();advanced_layout.addWidget(self.selection_json)
        sync=QPushButton('从表单生成高级输入');sync.clicked.connect(self.sync_selection);advanced_layout.addWidget(sync)
        self.output=QPlainTextEdit();self.output.setReadOnly(True);advanced_layout.addWidget(self.output)
        self.details.addTab(advanced,'高级输入输出');split.addWidget(right);split.setSizes([410,800])
        self.operator.currentIndexChanged.connect(self.operator_changed)
        self.skill.currentIndexChanged.connect(self.skill_changed)
        widgets=[self.rank,self.module,self.kind,self.attack_mode,self.squad,*self.fields.values(),
                 self.duration,self.interval,self.first_hit,self.segments,self.interval_add,self.interval_factor,
                 *self.panel_stats.values(),self.difficulty]
        for w in widgets:
            (w.currentIndexChanged if isinstance(w,QComboBox) else w.valueChanged).connect(self.invalidate)
        for w in [self.frames,self.custom_interval,self.manual_panel_group,self.partial,self.json_mode,*self.included.values()]:w.toggled.connect(self.invalidate)
        self.rank.currentIndexChanged.connect(self.update_skill_note)
        self.selection_json.textChanged.connect(self.invalidate)
        self._loading=False;self.refresh_snapshot()
        self._compact_layout(layout,split,left,form,right,box)

    def _compact_layout(self,layout,split,old_left,old_form,right,result_layout):
        groups=[]
        while old_form.rowCount():
            row=old_form.takeRow(0)
            if row.fieldItem and isinstance(row.fieldItem.widget(),QGroupBox):groups.append(row.fieldItem.widget())
            if row.labelItem and row.labelItem.widget():row.labelItem.widget().deleteLater()
        host=QWidget();outer=QVBoxLayout(host);outer.setContentsMargins(0,0,0,0)
        grid=QGridLayout();outer.addLayout(grid)
        for label,widget,column,span in [('干员',self.operator,0,3),('使用技能',self.skill,3,3),('等级／专精',self.rank,6,4)]:
            grid.addWidget(QLabel(label),0,column);grid.addWidget(widget,0,column+1,1,span-1)
        for i,(key,label) in enumerate([('elite','精英'),('level','等级'),('trust','信赖'),('potential','潜能'),('module_level','模组等级')]):
            grid.addWidget(QLabel(label),1,i*2);grid.addWidget(self.fields[key],1,i*2+1)
        for label,widget,column,span in [('装备模组',self.module,0,4),('伤害类型',self.kind,4,3),('攻击方式',self.attack_mode,7,3)]:
            grid.addWidget(QLabel(label),2,column);grid.addWidget(widget,2,column+1,1,span-1)
        outer.addWidget(self.skill_note)
        extras=QWidget();extra_layout=QHBoxLayout(extras);extra_layout.setContentsMargins(0,0,0,0)
        for group in groups:extra_layout.addWidget(group,1)
        area=QScrollArea();area.setWidgetResizable(True);area.setWidget(extras);area.setMaximumHeight(240)
        area.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustToContents)
        area.setSizePolicy(QSizePolicy.Policy.Preferred,QSizePolicy.Policy.Maximum)
        area.setMinimumHeight(45);outer.addWidget(area)
        self.json_mode.toggled.disconnect(old_left.setDisabled);self.json_mode.toggled.connect(host.setDisabled)
        host.setDisabled(self.json_mode.isChecked())
        result_layout.removeWidget(self.details)
        self.details_toggle=QGroupBox('展开算式、来源与缺项');self.details_toggle.setCheckable(True);self.details_toggle.setChecked(False)
        self.details_toggle.setSizePolicy(QSizePolicy.Policy.Preferred,QSizePolicy.Policy.Maximum)
        detail_layout=QVBoxLayout(self.details_toggle);detail_layout.addWidget(self.details)
        self.details.hide();self.details_toggle.toggled.connect(self.details.setVisible)
        result_layout.addWidget(self.details_toggle);result_layout.addStretch(1)
        self.status.setObjectName('muted')
        layout.removeWidget(split);right.setParent(self)
        layout.addWidget(host);layout.addWidget(right,1);split.hide();split.setParent(None);split.deleteLater()

    @staticmethod
    def optional_seconds(maximum):
        spin=QDoubleSpinBox();spin.setRange(0,maximum);spin.setDecimals(6);spin.setSpecialValueText('自动读取')
        return spin

    def invalidate(self,*_):
        if self._loading:return
        self.last_request=self.last_response=None
        self.dph_card.setText('DPH · 单段命中伤害\n—');self.dps_card.setText('技能期间平均 DPS\n—')
        self.dph_card.setToolTip('');self.dps_card.setToolTip('');self.trace.clear();self.audit_tree.clear();self.output.clear()
        self.summary.setText('输入已更新，点击“计算 DPH / DPS”。');self.status.setText('等待计算，不沿用旧结果')

    def refresh_snapshot(self):
        try:
            data=self.snapshot_provider() if self.snapshot_provider else parse_json((ROOT/'data/sessions/current.json').read_text(encoding='utf-8-sig'))
            self.set_snapshot(data)
        except (ValueError,OSError) as error:self.invalidate();self.status.setText('读取失败：'+str(error))

    def set_snapshot(self,data):
        if not isinstance(data,dict) or not isinstance(data.get('inventories'),dict):raise ValueError('本局文件缺少库存与干员清单')
        self._loading=True;old=self.operator.currentData();self.snapshot=deepcopy(data);self.request_context={}
        self.operator.clear();self.operator.addItem('请选择已确认干员',None)
        for item in data['inventories'].get('operators',{}).get('items',[]):
            if item.get('confirmed') is True and not item.get('excluded'):self.operator.addItem(item.get('name',item['id']),item['id'])
        self.operator.setCurrentIndex(max(1 if self.operator.count()>1 else 0,self.operator.findData(old)))
        summaries=[]
        for kind,label in [('operators','干员'),('relics','藏品'),('parts','零件')]:
            bucket=data['inventories'].get(kind,{})
            count=sum(i.get('confirmed') is True and not i.get('excluded') for i in bucket.get('items',[]))
            summaries.append(f'{label} {count} 项'+(' · 已录全' if bucket.get('complete') else ' · 未确认录全'))
        self.snapshot_status.setText('当前本局：'+'；'.join(summaries)+'。清单未完整时请在Reader清单页核对。')
        self.json_mode.setChecked(False);self.partial.setChecked(False);self._loading=False;self.operator_changed();self.invalidate()

    def current_operator(self):
        return next((i for i in self.snapshot.get('inventories',{}).get('operators',{}).get('items',[]) if i.get('id')==self.operator.currentData()),{'fields':{}})

    def operator_changed(self,*_):
        if self._loading:return
        self._loading=True;o=self.current_operator();oid=self.operator.currentData()
        for name,spin in self.fields.items():spin.setValue(field(o,name) if field(o,name) is not None else -1)
        self.module.clear();self.module.addItem('未知，请核对',None);self.module.addItem('未装备','none')
        detail=self.catalog.operators.get(oid,{})
        for component in detail.get('components',{}).get('modules',[]):
            f=component.get('fields',{});identity=f.get('类型') or f.get('名称')
            if identity:self.module.addItem(f.get('名称',identity)+' · '+identity,identity)
        self.module.setCurrentIndex(max(0,self.module.findData(field(o,'module_id'))))
        self.skill.clear();self.skill.addItem('请选择技能',None)
        for index,component in enumerate(detail.get('components',{}).get('skills',[]),1):
            self.skill.addItem(f'{index} · '+component.get('fields',{}).get('技能名',f'技能{index}'),index)
        self.skill.setCurrentIndex(max(0,self.skill.findData(field(o,'skill_index'))))
        profile=self.catalog.profiles.get(oid,{})
        trait=(self.catalog.operator_trait(oid) or {}).get('text','')
        kind='arts' if '法术伤害' in trait else 'physical' if profile.get('data-profession') not in {'医疗','辅助'} else None
        self.kind.setCurrentIndex(max(0,self.kind.findData(kind)))
        self.attack_mode.setCurrentIndex(self.attack_mode.findData('ranged' if profile.get('data-position')=='远程位' else 'melee'))
        self.duration.setValue(0);self.interval.setValue(0);self.first_hit.setValue(0);self.segments.setValue(0)
        self.frames.setChecked(False);self.custom_interval.setChecked(False);self.manual_panel_group.setChecked(False)
        for name,spin in self.panel_stats.items():spin.setValue(100 if name=='attack_speed' else -1)
        for check in self.included.values():check.setChecked(False)
        self.json_mode.setChecked(False);self._loading=False;self.skill_changed();self.invalidate()

    def skill_changed(self,*_):
        if self._loading:return
        self._loading=True;o=self.current_operator();index=self.skill.currentData()
        mastery=field(o,f'skill_{index}_mastery')
        if mastery is None and field(o,'skill_index')==index:mastery=field(o,'mastery')
        rank=(field(o,'skill_level'),mastery) if None not in (field(o,'skill_level'),mastery) else None
        self.rank.setCurrentIndex(next((i for i in range(self.rank.count()) if self.rank.itemData(i)==rank),0))
        self.duration.setValue(0);self.segments.setValue(0);self.custom_interval.setChecked(False)
        self._loading=False;self.update_skill_note();self.invalidate()

    def update_skill_note(self,*_):
        o=deepcopy(self.current_operator());index=self.skill.currentData();rank=self.rank.currentData()
        if index and rank:
            for key,value in [('skill_index',index),('skill_level',rank[0]),('mastery',rank[1]),(f'skill_{index}_mastery',rank[1])]:o.setdefault('fields',{})[key]={'value':value}
            entry=selected_skill(self.catalog,o)
            if entry:
                p=skill_profile(entry)
                self.skill_note.setText(source_text(entry['raw_effect'])+'\n资料时长：'+(f"{p['duration']:g} 秒" if p['duration'] else '无明确有限时长，暂不输出技能期间DPS'))
                if p['damage_type'] and not self._loading:self.kind.setCurrentIndex(self.kind.findData(p['damage_type']))
                return
        self.skill_note.setText('请选择技能及等级。等级/信赖/潜能/模组缺失可在本页补录，无需编辑JSON。')

    def selection_data(self):
        overrides={name:spin.value() if spin.value()>=0 else None for name,spin in self.fields.items()}
        overrides['module_id']=self.module.currentData();overrides['skill_index']=self.skill.currentData()
        rank=self.rank.currentData();overrides['skill_level']=rank[0] if rank else None;overrides['mastery']=rank[1] if rank else None
        if self.skill.currentData():overrides[f'skill_{self.skill.currentData()}_mastery']=rank[1] if rank else None
        model={'first_hit':self.first_hit.value(),'frame_align':self.frames.isChecked()}
        if self.duration.value()>0:model['duration']=self.duration.value()
        if self.interval.value()>0:model['base_interval']=self.interval.value()
        if self.segments.value()>0:model['hits_per_attack']=self.segments.value()
        if self.custom_interval.isChecked():model.update(interval_add=self.interval_add.value(),interval_factor=self.interval_factor.value())
        result={'operator_id':self.operator.currentData(),'operator_fields':overrides,'model':model,
            'skill_active':True,'damage_type':self.kind.currentData(),'attack_mode':self.attack_mode.currentData(),
            'difficulty':self.difficulty.value(),'squad_id':self.squad.currentData()}
        if result['operator_id'] is None:result.pop('operator_id')
        if result['damage_type'] is None:result.pop('damage_type')
        if self.manual_panel_group.isChecked():
            result['operator_panel']={'stats':{key:spin.value() for key,spin in self.panel_stats.items() if spin.value()>=0},
                'included':[key for key,check in self.included.items() if check.isChecked()]}
        return result

    def sync_selection(self):
        self.selection_json.setPlainText(json.dumps(self.selection_data(),ensure_ascii=False,indent=2))

    def run(self):
        try:
            self.invalidate()
            selection=parse_json(self.selection_json.toPlainText()) if self.json_mode.isChecked() else self.selection_data()
            envelope={'schema_version':'1.2','operation':'session_dps','session':deepcopy(self.snapshot),
                      'selection':selection,'allow_partial':self.partial.isChecked(),'context':deepcopy(self.request_context)}
            response=calculate_session_dps(envelope,self.catalog);self.last_request=envelope;self.last_response=response
            self.output.setPlainText(json.dumps(response,ensure_ascii=False,indent=2))
            dph=response['dph_result'];window=response['window_result']
            self.dph_card.setText('DPH · 单段命中伤害\n'+value_text(response['result']['dph']))
            self.dps_card.setText('技能期间平均 DPS\n'+value_text(response['result']['skill_average_dps']))
            audit=response['audit'];missing=len(audit['missing'])+len(audit['unsupported'])
            self.details_toggle.setTitle(f'展开算式、来源与缺项 · 待补 {missing+len(response["timing_missing"])} 项')
            if not dph:self.details_toggle.setChecked(True)
            state='计算完成' if response['status']=='complete_for_supported_scope' else '请补录参数或查看“效果与缺项”'
            self.status.setText(state+f'\n固定0防0抗单目标；效果缺项 {missing}；时序缺项 {len(response["timing_missing"])}')
            if window:
                self.summary.setText(f"攻击白值 {value_text(dph['white_values']['attacker']['atk'])}；攻速 {window['attack_speed']:g}；实际间隔 {window['actual_interval']:.8g}s\n"
                    f"技能 {window['duration']:g}s，攻击 {window['attack_count']} 次，每次 {window['hits_per_attack']} 段。DPS = {window['skill_total_damage']:.10g} ÷ {window['duration']:g}\n"
                    f"首击 {window['first_hit']:g}s；{'30帧近似' if window['frame_align'] else '理论间隔、无帧对齐'}；不含技能外充能时间。")
                self.dps_card.setToolTip('\n'.join(r['formula'] for r in window['trace']))
            else:self.summary.setText('DPH 与 DPS 分别判断。'+('；'.join(response['timing_missing']) or '基础效果尚有缺项。'))
            if dph:
                self.dph_card.setToolTip('\n'.join(r.get('formula','') for r in dph['trace']))
                for side,stats in dph['attributes'].items():
                    for stat,data in stats.items():
                        label={'atk':'攻击力','attack_speed':'攻击速度','def':'防御力','res':'法术抗性','max_hp':'生命值','element_res':'元素抗性','ep_resistance':'损伤抗性'}.get(stat,stat)
                        parent=QTreeWidgetItem(self.trace,[('我方' if side=='attacker' else '目标')+' '+label,'',value_text(data['value'])]);self.add_trace(parent,data['trace'])
                self.add_trace(self.trace,dph['trace'])
            if window:self.add_trace(self.trace,window['trace'])
            for r in audit['rules']:
                item=QTreeWidgetItem(self.audit_tree,[r['source']['name'],('生效：' if r['applied'] else '未生效：')+(r.get('reason') or r['stage'])+f" {r['value']:g}"])
                item.setToolTip(0,json.dumps(r,ensure_ascii=False,indent=2))
            for key,title in [('missing','需要补录'),('unsupported','未支持的效果'),('excluded','未计入本次输出')]:
                parent=QTreeWidgetItem(self.audit_tree,[title,str(len(audit[key]))])
                for r in audit[key]:QTreeWidgetItem(parent,[r.get('name',r.get('code',r.get('stat',''))),r.get('detail',r.get('reason',''))])
            parent=QTreeWidgetItem(self.audit_tree,['DPS时序缺项',str(len(response['timing_missing']))])
            for text in response['timing_missing']:QTreeWidgetItem(parent,['待确认',text])
            self.trace.expandToDepth(0);self.audit_tree.expandAll()
            self.details.setCurrentIndex(0 if dph else 1)
            return True
        except (ValueError,OSError,TypeError) as error:
            self.invalidate();self.status.setText('输入错误：'+str(error));return False

    @staticmethod
    def add_trace(parent,rows):
        for row in rows:
            item=QTreeWidgetItem(parent,[row.get('title',row.get('step','计算')),row.get('formula',''),value_text(row.get('after',row.get('value')))])
            item.setToolTip(2,json.dumps(row,ensure_ascii=False,indent=2))
            for operand in row.get('contributors',[]):
                source=operand.get('source',{})
                if source:QTreeWidgetItem(item,['来源：'+source.get('name',''),source.get('detail',''),value_text(operand.get('value'))])
                elif 'title' in operand:SessionDpsPanel.add_trace(item,[operand])

    def import_snapshot(self):
        path,_=QFileDialog.getOpenFileName(self,'导入本局清单或计算文件','','JSON (*.json)')
        if path:
            try:self.load_document(parse_json(Path(path).read_text(encoding='utf-8-sig')))
            except (ValueError,OSError) as error:self.invalidate();self.status.setText('导入失败：'+str(error))

    def load_document(self,data):
        if not isinstance(data,dict):raise ValueError('需要JSON对象')
        data=data.get('request',data)
        if data.get('operation'):
            if data['operation'] not in ('session_dps','session_damage'):raise ValueError('本页导入本局计算；基础手工请求请使用高级页')
            self.set_snapshot(data.get('session'));selection=deepcopy(data.get('selection',{}))
            if data['operation']=='session_damage':
                for key in ('stage_id','enemy_id','enemy_level','target_mode','hits'):selection.pop(key,None)
            self.selection_json.setPlainText(json.dumps(selection,ensure_ascii=False,indent=2));self.json_mode.setChecked(True)
            self.partial.setChecked(data.get('allow_partial',False));self.request_context=deepcopy(data.get('context',{}))
            self.details.setCurrentIndex(2);self.status.setText('已导入计算请求；高级JSON输入已启用，覆盖左侧表单。当前固定0防0抗。')
            self.details_toggle.setChecked(True)
        else:self.set_snapshot(data)

    def export_bundle(self):
        if not self.run():return
        path,_=QFileDialog.getSaveFileName(self,'导出本局DPH与DPS','session_dps.json','JSON (*.json)')
        if path:Path(path).write_text(json.dumps({'request':self.last_request,'response':self.last_response},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
