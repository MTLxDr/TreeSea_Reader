"""Independent, exportable calculator UI with named multiplier groups."""
from copy import deepcopy
from html import escape
import json
from pathlib import Path

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (QCheckBox,QComboBox,QDoubleSpinBox,QFileDialog,QFormLayout,QHBoxLayout,QHeaderView,
    QDialog,QDialogButtonBox,QLabel,QLineEdit,QMainWindow,QPlainTextEdit,QPushButton,QScrollArea,QSpinBox,QSplitter,QTableWidget,
    QTableWidgetItem,QTabWidget,QTreeWidget,QTreeWidgetItem,QVBoxLayout,QWidget)
from PySide6.QtCore import Qt

from .calculation import calculate,CalculationError,ALGORITHM_VERSION,SCHEMA_VERSION,LIST_FIELDS
from .calculation_api import parse_json,contract,start_server
from .calculation_trace import SOURCE_LABELS,unpack_operand,explain,fmt
from .theme import STYLE

FIELD_LABELS=[('base','原始面板'),('base_add','原始面板平值补充'),('stage_add','关卡符文加算'),('stage_factors','关卡符文倍率（逐项乘）'),
    ('relic_add','藏品符文平值加算'),('relic_mul_delta','藏品符文加成率（相加）'),('relic_post_factors','藏品符文后置倍率'),
    ('direct_add','直接加算'),('direct_mul_delta','直接乘算加成率（相加）'),('final_add','最终加算'),('final_factors','最终乘算倍率（逐项乘）'),
    ('minimum','属性下限'),('maximum','属性上限')]
STAT_LABELS={'atk':'攻击力','def':'防御力','res':'法术抗性','element_res':'元素抗性','ep_resistance':'损伤抵抗（损伤抗性）','max_hp':'生命上限','attack_speed':'攻击速度'}


def example_request():
    return {'schema_version':SCHEMA_VERSION,'operation':'damage','attacker':{'atk':{
        'base':{'value':833,'source':{'kind':'base','name':'银灰·ArkDPS X模固定配置','url':'https://ark-dps.com/','detail':'公开站点的对照配置，不是本局自动识别结果'}},
        'direct_mul_delta':[{'value':.25,'source':{'kind':'talent','name':'领袖（X模对应配置）','url':'https://ark-dps.com/'}},
                            {'value':2,'source':{'kind':'skill','name':'真银斩','url':'https://prts.wiki/w/银灰'}}]},'attack_speed':{'base':100}},
        'defender':{'def':{'base':0},'res':{'base':0}},'hit':{'type':'physical'},
        'context':{'purpose':'ArkDPS银灰3物理DPH对照：833×(1+0.25+2)=2707.25，站点显示2707；非本局自动套用'}}


def list_text(text):
    if text.strip().startswith('['):return parse_json(text)
    return [float(v.strip()) for v in text.replace('，',',').split(',') if v.strip()]


def tooltip(text):
    lines=text.splitlines()
    lines=[line if len(line)<=120 else line[:117]+'…' for line in lines]
    if len(lines)>40:lines=lines[:40]+['…完整过程与来源请展开行或查看导出结果。']
    return '<qt><pre>'+escape('\n'.join(lines))+'</pre></qt>'


def source_summary(sources):
    return '；'.join(SOURCE_LABELS.get(source['kind'],source['kind'])+' · '+source['name'] for source in sources)


class ContributionDialog(QDialog):
    def __init__(self,records,title,parent=None):
        super().__init__(parent);self.setWindowTitle(title+' · 数值与贡献来源');self.resize(1050,430)
        layout=QVBoxLayout(self);layout.addWidget(QLabel('数值仍使用接口单位：加成率+20%填0.2，实际倍率120%填1.2。未确认的效果不要仅凭文案指定乘区。'))
        self.table=QTableWidget(0,5);self.table.setHorizontalHeaderLabels(['数值','来源类型','贡献者名称','条件 / 说明','来源URL'])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch);layout.addWidget(self.table,1)
        for record in records:self.add_record(record)
        row=QHBoxLayout();add=QPushButton('添加');add.clicked.connect(lambda:self.add_record(0));row.addWidget(add)
        remove=QPushButton('删除选中');remove.clicked.connect(lambda:self.table.removeRow(self.table.currentRow()) if self.table.currentRow()>=0 else None);row.addWidget(remove);layout.addLayout(row)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('确定');buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject);layout.addWidget(buttons)

    def add_record(self,record):
        term=unpack_operand(record);source=term['source'];row=self.table.rowCount();self.table.insertRow(row)
        value=QTableWidgetItem(fmt(term['value']));value.setData(Qt.ItemDataRole.UserRole,source);self.table.setItem(row,0,value)
        kinds=QComboBox()
        for kind,name in SOURCE_LABELS.items():kinds.addItem(name,kind)
        kinds.setCurrentIndex(kinds.findData(source['kind']));self.table.setCellWidget(row,1,kinds)
        for col,key in [(2,'name'),(3,'detail'),(4,'url')]:self.table.setItem(row,col,QTableWidgetItem(source.get(key,'')))

    def data(self):
        records=[]
        for row in range(self.table.rowCount()):
            source=deepcopy(self.table.item(row,0).data(Qt.ItemDataRole.UserRole) or {})
            source.update(kind=self.table.cellWidget(row,1).currentData(),name=self.table.item(row,2).text().strip() or '未标注来源')
            for col,key in [(3,'detail'),(4,'url')]:
                value=self.table.item(row,col).text().strip()
                if value:source[key]=value
                else:source.pop(key,None)
            record={'value':float(self.table.item(row,0).text()),'source':source};unpack_operand(record);records.append(record)
        return records


class MultiplierGroupsEditor(QWidget):
    def __init__(self,title):
        super().__init__();layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0)
        row=QHBoxLayout();row.addWidget(QLabel(title),1)
        add=QPushButton('添加乘区');add.clicked.connect(lambda:self.add_group());row.addWidget(add)
        sources=QPushButton('编辑来源');sources.clicked.connect(self.edit_sources);row.addWidget(sources)
        remove=QPushButton('删除选中');remove.clicked.connect(self.remove_group);row.addWidget(remove);layout.addLayout(row)
        self.table=QTableWidget(0,4);self.table.setHorizontalHeaderLabels(['乘区ID（同名合并）','组内规则','加成率（+20%填0.2）','贡献来源（可点编辑来源）'])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setMinimumHeight(90);self.table.setMaximumHeight(180);layout.addWidget(self.table)

    def add_group(self,record=None):
        record=record or {'group_id':'custom.'+str(self.table.rowCount()+1),'deltas':[.2]}
        row=self.table.rowCount();self.table.insertRow(row)
        self.table.setItem(row,0,QTableWidgetItem(record['group_id']))
        combo=QComboBox()
        for label,value in [('加成率相加','add'),('取最高','max'),('取最低','min'),('倍率逐项乘','multiply')]:combo.addItem(label,value)
        combo.setCurrentIndex(combo.findData(record.get('stacking','multiply')));self.table.setCellWidget(row,1,combo)
        self.write_values(row,record['deltas'])

    def write_values(self,row,records):
        self.table.setItem(row,2,QTableWidgetItem(', '.join(fmt(unpack_operand(v)['value']) for v in records)))
        sources=[unpack_operand(v)['source'] for v in records]
        tagged=any(isinstance(v,dict) for v in records)
        item=QTableWidgetItem(source_summary(sources) if tagged else '未标注来源')
        item.setData(Qt.ItemDataRole.UserRole,sources if tagged else None)
        item.setFlags(item.flags()&~Qt.ItemFlag.ItemIsEditable)
        item.setToolTip(tooltip(explain({'contributors':[unpack_operand(v) for v in records]})));self.table.setItem(row,3,item)

    def edit_sources(self):
        row=self.table.currentRow()
        if row<0:return
        try:
            dialog=ContributionDialog(self.data()[row]['deltas'],'乘区 '+self.table.item(row,0).text(),self)
            if dialog.exec()==QDialog.DialogCode.Accepted:self.write_values(row,dialog.data())
        except ValueError as error:self.table.setToolTip(str(error))

    def remove_group(self):
        if self.table.currentRow()>=0:self.table.removeRow(self.table.currentRow())

    def set_data(self,records):
        self.table.setRowCount(0)
        for record in records:self.add_group(record)

    def data(self):
        result=[]
        for row in range(self.table.rowCount()):
            deltas=list_text(self.table.item(row,2).text());sources=self.table.item(row,3).data(Qt.ItemDataRole.UserRole)
            if sources is not None:
                if not isinstance(sources,list) or len(sources)!=len(deltas):raise CalculationError('贡献来源数量必须与加成率数量相同')
                deltas=[{'value':unpack_operand(v)['value'],'source':source} for v,source in zip(deltas,sources)]
            result.append({'group_id':self.table.item(row,0).text(),'stacking':self.table.cellWidget(row,1).currentData(),'deltas':deltas})
        return result


class AttributeEditor(QWidget):
    def __init__(self,specs):
        super().__init__();self.specs=deepcopy(specs);self.current=None
        layout=QVBoxLayout(self);self.stat=QComboBox()
        for key,label in STAT_LABELS.items():self.stat.addItem(label,key)
        layout.addWidget(self.stat)
        self.enabled=QCheckBox('计入此属性');layout.addWidget(self.enabled)
        self.table=QTableWidget(len(FIELD_LABELS),3);self.table.setHorizontalHeaderLabels(['结算阶段','数值 / 数列','贡献来源'])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide();self.table.setWordWrap(True)
        self.table.verticalHeader().setDefaultSectionSize(35)
        for index,(key,label) in enumerate(FIELD_LABELS):
            item=QTableWidgetItem(label);item.setFlags(item.flags()&~Qt.ItemFlag.ItemIsEditable);self.table.setItem(index,0,item)
        layout.addWidget(self.table,1)
        edit=QPushButton('编辑选中阶段的数值与贡献来源');edit.clicked.connect(self.edit_sources);layout.addWidget(edit)
        self.groups=MultiplierGroupsEditor('最终乘算：先同组结算，再将组间相乘');layout.addWidget(self.groups)
        self.info=QLabel('按PRTS：直接加算→直接乘算→最终加算→最终乘算。攻击力提升通常参与直接乘算；攻击力比例伤害填伤害页，不改白值。命名组仅按明确机制相加/取最高，不能凭名称推断。')
        self.info.setWordWrap(True);layout.addWidget(self.info)
        self.stat.currentIndexChanged.connect(self.switch);self.switch()
        self.table.cellDoubleClicked.connect(lambda row,col:self.edit_sources() if col==2 else None)

    def store(self):
        if self.current is None:return
        if not self.enabled.isChecked():self.specs.pop(self.current,None);return
        spec={}
        for index,(key,_) in enumerate(FIELD_LABELS):
            cell=self.table.item(index,1);text=cell.text().strip() if cell else ''
            saved=cell.data(Qt.ItemDataRole.UserRole) if cell else None
            if saved is not None:
                spec[key]=deepcopy(saved);continue
            if key in LIST_FIELDS:
                if text:spec[key]=list_text(text)
            elif text:spec[key]=parse_json(text) if text.startswith('{') else float(text)
        if 'base' not in spec:raise CalculationError('已启用属性必须填写原始面板')
        named=self.groups.data()
        if named:spec['final_groups']=named
        self.specs[self.current]=spec

    def switch(self,*_):
        try:self.store()
        except ValueError as error:
            self.info.setText(str(error));self.stat.blockSignals(True);self.stat.setCurrentIndex(self.stat.findData(self.current));self.stat.blockSignals(False);return
        self.current=self.stat.currentData();spec=self.specs.get(self.current,{})
        self.enabled.setChecked(self.current in self.specs)
        self.groups.set_data(spec.get('final_groups',[]))
        for index,(key,_) in enumerate(FIELD_LABELS):
            value=spec.get(key,0 if key=='base' else '')
            self.set_field(index,value)

    def set_field(self,row,value):
        tagged=isinstance(value,dict) or (isinstance(value,list) and any(isinstance(v,dict) for v in value))
        records=value if isinstance(value,list) else [value]
        if tagged:
            terms=[unpack_operand(v) for v in records]
            cell=QTableWidgetItem(', '.join(fmt(term['value']) for term in terms))
            cell.setData(Qt.ItemDataRole.UserRole,deepcopy(value));cell.setFlags(cell.flags()&~Qt.ItemFlag.ItemIsEditable)
            note=explain({'title':'此项带贡献来源，请点“编辑来源”修改数值。','contributors':terms})
            source=QTableWidgetItem(source_summary([term['source'] for term in terms]));source.setToolTip(tooltip(note));cell.setToolTip(tooltip(note))
        else:
            text=', '.join(str(v) for v in value) if isinstance(value,list) else str(value) if value is not None else ''
            cell=QTableWidgetItem(text);source=QTableWidgetItem('手工输入 / 未标注' if text else '—')
        source.setFlags(source.flags()&~Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(row,1,cell);self.table.setItem(row,2,source)

    def edit_sources(self):
        row=max(0,self.table.currentRow());key,label=FIELD_LABELS[row]
        if key in {'minimum','maximum'}:return
        try:
            self.store();spec=self.specs.get(self.current,{})
            raw=spec.get(key,[] if key in LIST_FIELDS else 0);records=raw if isinstance(raw,list) else [raw]
            dialog=ContributionDialog(records,label,self)
            if dialog.exec()!=QDialog.DialogCode.Accepted:return
            records=dialog.data()
            if key not in LIST_FIELDS and len(records)!=1:raise CalculationError('单值字段必须保留且仅保留一项')
            self.set_field(row,records if key in LIST_FIELDS else records[0])
        except ValueError as error:self.info.setText(str(error))

    def data(self):self.store();return deepcopy(self.specs)


class CalculationPanel(QWidget):
    def __init__(self):
        super().__init__();self.last_request=None;self.last_result=None;self.request=example_request()
        layout=QVBoxLayout(self)
        self.status=QLabel('算法 '+ALGORITHM_VERSION+' · 统一按PRTS。白值与伤害增益分开；数字悬浮可看算式，展开可看贡献来源。未规则化效果不自动计入。')
        self.status.setObjectName('review');self.status.setWordWrap(True);layout.addWidget(self.status)
        row=QHBoxLayout();row.addWidget(QLabel('计算项目'));self.operation=QComboBox()
        self.operation.addItem('扣生命值的伤害','damage');self.operation.addItem('元素损伤累积（独立通道）','elemental_buildup');row.addWidget(self.operation,1);layout.addLayout(row)
        self.tabs=QTabWidget();layout.addWidget(self.tabs,1)
        self.attack=AttributeEditor(self.request['attacker']);self.tabs.addTab(self.attack,'攻击方白值')
        self.defense=AttributeEditor(self.request['defender']);self.defense.stat.setCurrentIndex(self.defense.stat.findData('def'));self.tabs.addTab(self.defense,'承伤方白值')
        page=QWidget();form=QFormLayout(page);self.hit_fields={}
        self.damage_type=QComboBox()
        for label,value in [('物理','physical'),('法术','arts'),('真实','true'),('元素','element')]:self.damage_type.addItem(label,value)
        form.addRow('伤害类型',self.damage_type)
        specs=[('attack_scale','攻击力比例（防抗前，不改白值）',1,0,100000),('attack_flat_add','基本伤害附加值（防抗前）',0,-1e9,1e9),
            ('def_penetration_flat','固定防御贯穿',0,0,1e9),('def_penetration_ratio','百分比防御贯穿（0–1）',0,0,1),
            ('res_penetration_flat','固定无视法抗（封顶后）',0,0,1e9),('res_penetration_ratio','百分比无视法抗（0–1）',0,0,1),
            ('min_damage_ratio','伤害下限比例',.05,0,1),('shield','共享数值护盾',0,0,1e12)]
        for key,label,value,lo,hi in specs:
            spin=QDoubleSpinBox();spin.setRange(lo,hi);spin.setDecimals(6);spin.setValue(value);form.addRow(label,spin);self.hit_fields[key]=spin
        self.scale_source=QLineEdit();self.scale_source.setPlaceholderText('例如：银灰 · 强力击γ型');form.addRow('攻击力比例的技能/效果名',self.scale_source)
        self.scale_groups=MultiplierGroupsEditor('攻击力比例乘区（防抗前）');form.addRow(self.scale_groups)
        self.post_factors=QLineEdit();form.addRow('已解析的独立增伤/减伤倍率',self.post_factors)
        self.post_groups=MultiplierGroupsEditor('伤害增益（防抗后）');form.addRow(self.post_groups)
        for key,label,lo,value in [('hits','相同伤害段数',1,1),('barrier_charges','独立屏障次数',0,0)]:
            spin=QSpinBox();spin.setRange(lo,10000);spin.setValue(value);form.addRow(label,spin);self.hit_fields[key]=spin
        self.immune=QCheckBox('明确的本次伤害免疫');form.addRow(self.immune)
        self.timing=QCheckBox('估算理论DPS（不含帧/前后摇/技能循环）');form.addRow(self.timing)
        self.interval=QDoubleSpinBox();self.interval.setRange(.000001,100000);self.interval.setDecimals(6);self.interval.setValue(1.3);form.addRow('原始攻击间隔（秒）',self.interval)
        note=QLabel('法抗填点数，属性结算后封顶100，再处理无视法抗；普通降低法抗仍在属性阶段计算。元素伤害使用元素抗性，无贯穿；损伤抵抗只用于元素损伤累积。')
        note.setWordWrap(True);form.addRow(note)
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(page);self.tabs.addTab(scroll,'伤害增益与吸收')
        self.buildup_page=QWidget();buildup_form=QFormLayout(self.buildup_page)
        self.buildup_amount=QDoubleSpinBox();self.buildup_amount.setRange(0,1e12);self.buildup_amount.setDecimals(6);self.buildup_amount.setValue(1000)
        buildup_form.addRow('单次基本元素损伤值',self.buildup_amount)
        self.buildup_hits=QSpinBox();self.buildup_hits.setRange(1,10000);self.buildup_hits.setValue(1);buildup_form.addRow('相同段数',self.buildup_hits)
        self.buildup_groups=MultiplierGroupsEditor('元素损伤后置命名乘区');buildup_form.addRow(self.buildup_groups)
        tip=QLabel('本通道只计算损伤累积。到“承伤方属性”填写损伤抵抗；元素抗性不参与。没有贯穿、生命护盾或扣血结算，不自动触发损伤爆发。');tip.setWordWrap(True);buildup_form.addRow(tip)
        self.tabs.addTab(self.buildup_page,'元素损伤累积')
        page=QWidget();box=QVBoxLayout(page);self.json_mode=QCheckBox('使用此JSON作为权威输入（支持单属性演算）');box.addWidget(self.json_mode)
        self.input_json=QPlainTextEdit(json.dumps(self.request,ensure_ascii=False,indent=2));box.addWidget(self.input_json)
        sync=QPushButton('从表单生成JSON');sync.clicked.connect(self.sync_json);box.addWidget(sync);self.tabs.addTab(page,'输入JSON')
        page=QWidget();box=QVBoxLayout(page);self.summary=QLabel('等待演算');self.summary.setWordWrap(True);box.addWidget(self.summary)
        cards=QHBoxLayout();self.white_card=QLabel('最终白值 —');self.basic_card=QLabel('防抗前基本伤害 —');self.damage_card=QLabel('最终伤害 —')
        for card in (self.white_card,self.basic_card,self.damage_card):card.setStyleSheet('padding:10px;border:1px solid #36515e;');cards.addWidget(card)
        box.addLayout(cards)
        self.trace=QTreeWidget();self.trace.setHeaderLabels(['结算阶段','输入 / 参数','结果'])
        self.trace.header().setStretchLastSection(False)
        self.trace.header().setSectionResizeMode(0,QHeaderView.ResizeMode.Fixed);self.trace.setColumnWidth(0,365)
        self.trace.header().setSectionResizeMode(1,QHeaderView.ResizeMode.Stretch)
        self.trace.header().setSectionResizeMode(2,QHeaderView.ResizeMode.Fixed);self.trace.setColumnWidth(2,145)
        box.addWidget(self.trace);self.tabs.addTab(page,'逐步计算过程');self.result_page=page
        self.output_json=QPlainTextEdit();self.output_json.setReadOnly(True);self.tabs.addTab(self.output_json,'输出JSON')
        row=QHBoxLayout()
        for text,fn in [('计算并核对',self.run),('导入请求JSON',self.import_request),('导出请求与结果',self.export_bundle),('导出接口Schema',self.export_schema)]:
            button=QPushButton(text);button.clicked.connect(fn);row.addWidget(button)
        layout.addLayout(row)
        self.operation.currentIndexChanged.connect(self.change_operation)
        self.damage_type.currentIndexChanged.connect(self.change_damage_type)
        self.change_damage_type()

    def change_operation(self,*_):
        if self.operation.currentData()=='elemental_buildup':
            self.defense.stat.setCurrentIndex(self.defense.stat.findData('ep_resistance'))
            self.tabs.setCurrentWidget(self.buildup_page)

    def change_damage_type(self,*_):
        kind=self.damage_type.currentData()
        for key in ('def_penetration_flat','def_penetration_ratio','res_penetration_flat','res_penetration_ratio'):
            self.hit_fields[key].setEnabled((kind=='physical' and key.startswith('def_')) or (kind=='arts' and key.startswith('res_')))

    def form_request(self):
        if self.operation.currentData()=='elemental_buildup':
            defense=self.defense.data()
            return {'schema_version':SCHEMA_VERSION,'operation':'elemental_buildup','amount':self.buildup_amount.value(),
                'defender':{'ep_resistance':defense.get('ep_resistance',{'base':0})},'hits':self.buildup_hits.value(),
                'post_scale_groups':self.buildup_groups.data(),'context':{'purpose':'元素损伤累积'}}
        hit={key:control.value() for key,control in self.hit_fields.items()}
        for key in ('def_penetration_flat','def_penetration_ratio','res_penetration_flat','res_penetration_ratio'):
            if not self.hit_fields[key].isEnabled():hit.pop(key)
        hit.update(type=self.damage_type.currentData(),post_scale_factors=list_text(self.post_factors.text()),post_scale_groups=self.post_groups.data(),immune=self.immune.isChecked())
        hit['attack_scale_groups']=self.scale_groups.data()
        if self.scale_source.text().strip():hit['attack_scale']={'value':hit['attack_scale'],'source':{'kind':'skill','name':self.scale_source.text().strip()}}
        result={'schema_version':SCHEMA_VERSION,'operation':'damage','attacker':self.attack.data(),'defender':self.defense.data(),'hit':hit,
                'context':{'purpose':'人工核对计算流程'}}
        if self.timing.isChecked():result['timing']={'base_interval':self.interval.value()}
        return result

    def sync_json(self):
        try:self.input_json.setPlainText(json.dumps(self.form_request(),ensure_ascii=False,indent=2))
        except ValueError as error:self.status.setText('输入错误：'+str(error))

    def run(self):
        try:
            request=parse_json(self.input_json.toPlainText()) if self.json_mode.isChecked() else self.form_request()
            result=calculate(request)
            self.last_request,self.last_result=deepcopy(request),result
            self.output_json.setPlainText(json.dumps(result,ensure_ascii=False,indent=2))
            self.trace.clear()
            for card in (self.white_card,self.basic_card,self.damage_card):card.clear();card.setToolTip('')
            if result['operation']=='attribute':
                self.summary.setText(f"{result['stat']} = {result['value']:.10g}")
                self.add_trace(self.trace,result['trace'])
                self.white_card.setText(f"白值 {result['value']:.10g}");self.white_card.setToolTip(tooltip('\n\n'.join(explain(row) for row in result['trace'])))
            elif result['operation']=='elemental_buildup':
                self.summary.setText(f"单次元素损伤 {result['buildup_per_hit']:.10g}　总损伤累积 {result['total_buildup']:.10g}（独立通道）")
                parent=QTreeWidgetItem(self.trace,['承伤方 · 损伤抵抗','',''])
                self.add_trace(parent,result['attributes']['defender']['ep_resistance']['trace']);self.add_trace(self.trace,result['trace'])
                self.damage_card.setText(f"损伤累积 {result['total_buildup']:.10g}");self.damage_card.setToolTip(tooltip('\n\n'.join(explain(row) for row in result['trace'])))
            else:
                self.summary.setText(f"每段伤害（吸收前） {result['per_hit_before_absorption']:.10g}　总伤害（吸收前） {result['total_before_absorption']:.10g}\n护盾吸收 {result['shield_absorbed']:.10g}　最终扣血 {result['hp_damage']:.10g}")
                for side,stats in result['attributes'].items():
                    for stat,detail in stats.items():
                        parent=QTreeWidgetItem(self.trace,[('攻击方' if side=='attacker' else '承伤方')+' · '+STAT_LABELS.get(stat,stat),'',f"{detail['value']:.10g}"])
                        parent.setToolTip(2,tooltip('\n\n'.join(explain(row) for row in detail['trace'])))
                        self.add_trace(parent,detail['trace'])
                parent=QTreeWidgetItem(self.trace,['伤害结算','','']);self.add_trace(parent,result['trace'])
                attack=result['attributes']['attacker']['atk'];self.white_card.setText(f"攻击白值 {attack['value']:.10g}")
                self.white_card.setToolTip(tooltip('\n\n'.join(explain(row) for row in attack['trace'])))
                self.basic_card.setText(f"基本伤害 {result['basic_damage']:.10g}");self.basic_card.setToolTip(tooltip(explain(result['trace'][0])))
                self.damage_card.setText(f"最终扣血 {result['hp_damage']:.10g}");self.damage_card.setToolTip(tooltip('\n\n'.join(explain(row) for row in result['trace'])))
                if 'timing' in result:
                    timing=result['timing'];parent=QTreeWidgetItem(self.trace,['理论DPS（非完整技能循环）','',''])
                    self.add_trace(parent,[{'step':'attack_interval','title':'攻击间隔','formula':timing['interval_formula'],'contributors':timing['contributors'],'value':timing['actual_interval']},
                        {'step':'theoretical_dps','title':'吸收前理论DPS','formula':timing['dps_formula'],'value':timing['theoretical_dps_before_absorption']}])
            self.trace.expandAll();self.tabs.setCurrentWidget(self.result_page)
            self.status.setText('计算完成 · 算法 '+ALGORITHM_VERSION+'。光标停在数字上查看算式；展开行查看技能、天赋、藏品等贡献来源。')
            return True
        except (ValueError,TypeError) as error:self.status.setText('输入错误：'+str(error));return False

    @staticmethod
    def add_trace(parent,rows):
        for row in rows:
            value=row.get('after',row.get('value',''))
            detail=row.get('formula') or json.dumps(row.get('components',row.get('factor',{})),ensure_ascii=False)
            item=QTreeWidgetItem(parent,[row.get('title',row['step']),detail,f'{value:.10g}' if isinstance(value,(int,float)) else str(value)])
            item.setData(0,Qt.ItemDataRole.UserRole,row)
            for column in range(3):item.setToolTip(column,tooltip(explain(row)))
            for term in row.get('contributors',[]):
                source=term['source'];state='（未选中）' if term.get('applied') is False else ''
                field=term['field'];is_rate=field.endswith('delta') or field.endswith('deltas')
                role=dict(FIELD_LABELS).get(field,{'attack_scale':'攻击力比例（防抗前）','attack_flat_add':'基本伤害附加值','shield':'共享护盾','amount':'基本损伤'}.get(field,'加成率' if is_rate else '数值贡献'))
                value_text=f"{term['value']*100:+.10g}%" if is_rate else fmt(term['value'])
                leaf=QTreeWidgetItem(item,[SOURCE_LABELS.get(source['kind'],source['kind'])+' · '+source['name']+state,role,value_text])
                for column in range(3):leaf.setToolTip(column,tooltip(explain({'title':'数值贡献'+state,'contributors':[term]})))

    def import_request(self):
        path,_=QFileDialog.getOpenFileName(self,'导入计算请求','','JSON (*.json)')
        if not path:return
        try:
            text=Path(path).read_text(encoding='utf-8-sig');calculate(parse_json(text))
            self.input_json.setPlainText(text);self.json_mode.setChecked(True);self.tabs.setCurrentWidget(self.input_json.parentWidget())
        except (OSError,ValueError) as error:self.status.setText(str(error))

    def export_bundle(self):
        if not self.run():return
        path,_=QFileDialog.getSaveFileName(self,'导出完整计算','calculation.json','JSON (*.json)')
        if path:
            try:Path(path).write_text(json.dumps({'request':self.last_request,'response':self.last_result},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            except OSError as error:self.status.setText(str(error))

    def export_schema(self):
        path,_=QFileDialog.getSaveFileName(self,'导出接口定义','calculation.schema.json','JSON (*.json)')
        if path:
            try:Path(path).write_text(json.dumps(contract(),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            except OSError as error:self.status.setText(str(error))


class CalculationWindow(QMainWindow):
    def __init__(self,snapshot_provider=None,database=None):
        super().__init__()
        if 'Microsoft YaHei UI' not in QFontDatabase.families():QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc')
        self.setWindowTitle('树海数值计算器 · '+ALGORITHM_VERSION);self.setStyleSheet(STYLE);self.resize(1300,960);self.server=None
        widget=QWidget();layout=QVBoxLayout(widget);self.panel=CalculationPanel()
        from .session_calculation_ui import SessionCalculationPanel
        from .session_dps_ui import SessionDpsPanel
        self.pages=QTabWidget();self.advanced=QWidget();advanced_layout=QVBoxLayout(self.advanced)
        self.advanced_pages=QTabWidget();self.advanced_pages.addTab(self.panel,'手工参数计算')
        def show_manual():
            self.pages.setCurrentWidget(self.advanced);self.advanced_pages.setCurrentWidget(self.panel)
        self.session_panel=SessionCalculationPanel(self.panel,show_manual,snapshot_provider,database)
        self.advanced_pages.addTab(self.session_panel,'旧版按敌人计算');advanced_layout.addWidget(self.advanced_pages,1)
        self.dps_panel=SessionDpsPanel(snapshot_provider,self.session_panel.catalog)
        self.pages.addTab(self.dps_panel,'本局伤害计算');self.pages.addTab(self.advanced,'高级计算')
        layout.addWidget(self.pages,1)
        row=QHBoxLayout();self.api_status=QLabel('HTTP接口未启动');row.addWidget(self.api_status,1)
        self.port=QSpinBox();self.port.setRange(1,65535);self.port.setValue(8765);row.addWidget(self.port)
        self.api_button=QPushButton('启动本机HTTP接口');self.api_button.clicked.connect(self.toggle_api);row.addWidget(self.api_button);advanced_layout.addLayout(row)
        self.setCentralWidget(widget)

    def toggle_api(self):
        if self.server:
            self.server.shutdown();self.server.server_close();self.server=None;self.api_button.setText('启动本机HTTP接口');self.api_status.setText('HTTP接口已停止');return
        try:
            self.server,self.api_thread=start_server(self.port.value())
            self.api_status.setText(f'http://127.0.0.1:{self.port.value()}　POST /v1/calculate　GET /v1/schema')
            self.api_button.setText('停止HTTP接口')
        except OSError as error:self.api_status.setText('接口启动失败：'+str(error))

    def closeEvent(self,event):
        if self.server:self.server.shutdown();self.server.server_close();self.server=None
        super().closeEvent(event)
