from PySide6.QtCore import Qt,Signal
from PySide6.QtWidgets import (QCheckBox,QComboBox,QFrame,QHBoxLayout,QHeaderView,QLabel,QSpinBox,
    QTableWidget,QTableWidgetItem,QVBoxLayout,QWidget)

from .probability import rounded_percentages

METHODS={"quota_exact":"数量约束联合模型 · 精确枚举","quota_sampling":"数量约束联合模型 · 抽样估计",
         "distance_frequency":"路标基础距离频率"}


def card(title):
    frame=QFrame();frame.setObjectName("panel")
    layout=QVBoxLayout(frame);layout.setContentsMargins(12,10,12,10);layout.setSpacing(8)
    label=QLabel(title);label.setObjectName("section");layout.addWidget(label)
    return frame,layout


def table(headers,widths=None):
    widget=QTableWidget(0,len(headers));widget.setHorizontalHeaderLabels(headers)
    widget.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.Stretch)
    for column in range(1,len(headers)):
        if widths:
            widget.horizontalHeader().setSectionResizeMode(column,QHeaderView.ResizeMode.Fixed)
            widget.setColumnWidth(column,widths[column-1])
        else:
            widget.horizontalHeader().setSectionResizeMode(column,QHeaderView.ResizeMode.ResizeToContents)
    widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    widget.verticalHeader().hide();widget.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    widget.setShowGrid(False);widget.setAlternatingRowColors(False)
    return widget


class ProbabilityPanel(QWidget):
    model_changed=Signal()
    def __init__(self,catalog):
        super().__init__()
        self.catalog,self.context=catalog,None
        root=QVBoxLayout(self);root.setContentsMargins(14,10,14,10);root.setSpacing(10)

        head=QHBoxLayout();head.setSpacing(8)
        self.floor=QComboBox()
        for floor in ("1","2","3","4","追忆","5","6"):
            self.floor.addItem("第4层·追忆" if floor=="追忆" else "第"+floor+"层",floor)
        self.floor.setCurrentIndex(1)
        self.distance=QSpinBox();self.distance.setRange(0,40);self.distance.setValue(3)
        self.kind=QComboBox();self.kind.addItem("未知的凶戾","unknown-ferocity");self.kind.addItem("未知的诡秘","unknown-mystery")
        head.addWidget(self.floor)
        head.addWidget(QLabel("距起点"));head.addWidget(self.distance)
        head.addWidget(self.kind)
        self.use_ranges=QCheckBox('按观测数量范围估算');self.use_ranges.setChecked(getattr(catalog,'use_observed_ranges',True))
        self.use_ranges.setToolTip('数量范围来自路标样本，不代表游戏绝对生成限制；取消后使用距离频率。')
        self.use_ranges.toggled.connect(self.set_ranges)
        options=QHBoxLayout();options.addWidget(self.use_ranges)
        self.memory=QCheckBox('第4层追忆规则');self.memory.setChecked(getattr(catalog,'fourth_floor_memory',False))
        self.memory.setToolTip('按地图状态手动选择；第4层普通规则与追忆统计分开计算。')
        self.memory.toggled.connect(self.set_memory);options.addWidget(self.memory);options.addStretch()
        head.addStretch()
        mode=QLabel("独立查询：不联动本局")
        mode.setObjectName("muted")
        self.mode=mode
        head.addWidget(mode)
        root.addLayout(head)
        root.addLayout(options)

        row=QHBoxLayout();row.setSpacing(10)
        first,layout=card("节点类型概率")
        self.posterior=table(["类型","路标","约整","已确认/上限"],[46,46,80])
        layout.addWidget(self.posterior,1)
        row.addWidget(first,1)

        second,layout=card("节点距离分布")
        self.node_type=QComboBox()
        names=sorted({n for sources in catalog.distance_counts.values() for counts in sources.values() for n in counts})
        self.node_type.addItems(names);self.node_type.setCurrentText("作战")
        layout.addWidget(self.node_type)
        self.distribution=table(["距起点","观测","距离"],[46,46])
        layout.addWidget(self.distribution,1)
        self.distance_note=QLabel("按同一种节点在本层各距离的计数归一化，与左表分母不同。")
        self.distance_note.setWordWrap(True);self.distance_note.setObjectName("muted")
        layout.addWidget(self.distance_note)
        row.addWidget(second,1)

        third,layout=card("本层观测数量范围")
        self.limits=table(["节点","范围","已确认","剩余"],[62,58,52])
        layout.addWidget(self.limits,1)
        row.addWidget(third,1)
        root.addLayout(row,1)

        self.side=self._build_side()
        for control in (self.floor,self.kind):control.currentIndexChanged.connect(self.changed)
        self.distance.valueChanged.connect(self.changed);self.node_type.currentTextChanged.connect(self.refresh)
        self.refresh()

    def _build_side(self):
        host=QWidget()
        layout=QVBoxLayout(host);layout.setContentsMargins(12,10,12,10);layout.setSpacing(10)
        frame,inner=card("模型与样本")
        self.method=QLabel("—");self.method.setObjectName("section");self.method.setWordWrap(True)
        inner.addWidget(self.method)
        self.info=QLabel();self.info.setWordWrap(True)
        inner.addWidget(self.info)
        self.limitation=QLabel();self.limitation.setWordWrap(True);self.limitation.setObjectName("muted")
        inner.addWidget(self.limitation)
        self.scope=QLabel();self.scope.setWordWrap(True)
        inner.addWidget(self.scope)
        layout.addWidget(frame)

        frame,inner=card("读数说明")
        for text in ("界面约整到 1%，底层保留原始计数与精确值；整数分配合计 100%，极小非零项显示 <1%。",
                     "「路标计数」是该类型在本层路标池中的出现次数，用于估计基础频率，不是本局观测。",
                     "点击地图里已确认的未知节点，会联动本局已确认数量；独立查看时只有基础频率。"):
            line=QLabel("· "+text);line.setWordWrap(True);line.setObjectName("muted")
            inner.addWidget(line)
        layout.addWidget(frame)
        layout.addStretch()
        return host

    @staticmethod
    def fill(widget,rows,column_tips=None):
        widget.setRowCount(len(rows))
        for i,row in enumerate(rows):
            for j,value in enumerate(row):
                item=QTableWidgetItem(str(value));item.setFlags(item.flags()&~Qt.ItemFlag.ItemIsEditable)
                if column_tips and j in column_tips:
                    item.setToolTip(column_tips[j][i])
                widget.setItem(i,j,item)

    def set_memory(self,enabled):
        self.catalog.fourth_floor_memory=enabled
        self.set_ranges(self.use_ranges.isChecked())

    def set_ranges(self,enabled):
        self.catalog.use_observed_ranges=enabled
        if self.context:
            from .probability import refresh_map_probabilities
            refresh_map_probabilities(self.context[0],self.catalog)
        self.refresh()
        self.model_changed.emit()

    def changed(self,*_):
        self.context=None;self.refresh()

    def set_node(self,result,node):
        if node.get("region_id","main")!="main" or node["type"] not in {"unknown-mystery","unknown-ferocity"}:return
        for control in (self.floor,self.kind,self.distance):control.blockSignals(True)
        self.floor.setCurrentIndex(max(0,self.floor.findData(str(result["floor"]))))
        self.kind.setCurrentIndex(self.kind.findData(node["type"]));self.distance.setValue(node.get("distance") or 0)
        for control in (self.floor,self.kind,self.distance):control.blockSignals(False)
        self.context=(result,node);self.refresh()

    def refresh(self,*_):
        fid=self.floor.currentData()
        if fid=="4" and getattr(self.catalog,"fourth_floor_memory",False):fid="追忆"
        kind=self.kind.currentData();distance=self.distance.value()
        probability=self.catalog.probabilities(fid,distance,kind)
        counts={}
        if self.context:
            result,node=self.context
            probability=node.get("probabilities") if not result.get("needs_review") else None
            counts=result.get("floor_node_counts",{}).get("confirmed",{})
        if probability:
            estimates=probability["estimates"];units=rounded_percentages([r["probability"] for r in estimates])
            self.fill(self.posterior,[(r["name"],r["count"],"<1%" if r["probability"]>0 and u==0 else f"{u}%",
                f"{counts.get(r['name'],0) if self.context else '未联动'} / {self.catalog.count_limits.get(fid,{}).get(r['name'],{}).get('maximum','—')}") for r,u in zip(estimates,units)],
                {2:[f"基础频率 {r.get('prior_probability',r['probability']):.2%}" for r in estimates]})
            model=probability.get("model",{})
            method=METHODS.get(model.get("method"),"路标基础距离频率")
            extra=f"　·　有效联合样本 {model['accepted']}" if "accepted" in model else ""
            if model.get("lower_bounds_applied") is False:extra+="　·　存在未确认格点，本次只应用数量上限"
            self.method.setText(method)
            self.info.setText(f"源站样本 {probability['sample_count']}　·　资料 #{probability['document_id']}{extra}")
            self.limitation.setText(probability.get('limitation','') or "")
            self.limitation.setVisible(bool(probability.get('limitation')))
            self.mode.setText("已联动本局：点击的节点" if self.context else "独立查询：不联动本局")
        else:
            self.fill(self.posterior,[])
            self.method.setText("等待选择")
            self.info.setText((self.context[1].get("probability_error") if self.context else None) or "选择楼层与距离查看统计，或点击地图中的未知节点。")
            self.limitation.setText("")
            self.limitation.setVisible(False)
            self.mode.setText("独立查询：不联动本局")
        floor_text=self.floor.currentText();kind_text=self.kind.currentText()
        self.scope.setText(f"读取范围：{floor_text} · 距起点 {distance} · {kind_text}")
        distribution=self.catalog.distance_distribution(fid,self.node_type.currentText())
        rows=distribution["estimates"] if distribution else []
        units=rounded_percentages([r["probability"] for r in rows])
        self.fill(self.distribution,[(r["distance"],r["count"],"<1%" if r["probability"]>0 and u==0 else f"{u}%") for r,u in zip(rows,units)],
                  {2:[f"精确值 {r['probability']:.4%}" for r in rows]})
        limit_rows=[(name,f"{row['minimum']}–{row['maximum']}",counts.get(name,"未联动"),max(0,row["maximum"]-counts.get(name,0))) for name,row in self.catalog.count_limits.get(fid,{}).items()]
        limit_rows += [(name,'未统计',counts.get(name,'未联动'),'—') for name in getattr(self.catalog,'count_limit_gaps',{}).get(fid,[])]
        self.fill(self.limits,limit_rows)
