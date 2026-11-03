"""One event catalogue and occurrence list shared by the sidebar and inventory."""
from collections import Counter
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QComboBox,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QSizePolicy)
from .exploration import ExplorationCatalog


def floor_label(event):
    ordinary='、'.join(map(str,sorted(event.get('floors',[])))) or '—'
    conditional='、'.join(map(str,sorted(event.get('conditional_floors',[]))))
    return ordinary + ('；条件：'+conditional if conditional else '')


def populate_events(combo,session,catalog,query=''):
    context=((session.current or {}).get('floor'),session.exploration.get('entrance_floor'))
    combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    if combo.minimumContentsLength()<1:combo.setMinimumContentsLength(12)
    combo.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Fixed)
    combo.view().setMinimumWidth(430);combo.setMaxVisibleItems(16)
    previous=combo.currentData() if getattr(combo,'_event_floor',None)==context else None
    combo._event_floor=context;combo.blockSignals(True);combo.clear()
    options=catalog.event_options((session.current or {}).get('floor'),session.exploration['events'],session.exploration.get('entrance_floor'))
    seen=Counter(e['id'] for e in session.exploration['events'])
    group=None
    for event in options:
        if query and query not in event['name']:continue
        category='当层可遇' if event['current'] else '当层条件事件' if event['conditional'] else '其他楼层'
        if category!=group:
            combo.addItem('── '+category+' ──',None)
            combo.model().item(combo.count()-1).setEnabled(False)
            group=category
        label=event['name']+'  · 可遇 '+floor_label(event)+' 层'
        if seen[event['id']]:label+=f" · 已遇{seen[event['id']]}次"
        combo.addItem(label,event['id'])
        combo.setItemData(combo.count()-1,label,Qt.ItemDataRole.ToolTipRole)
        if not event['current']:combo.setItemData(combo.count()-1,QColor('#8a989f'),Qt.ItemDataRole.ForegroundRole)
    index=combo.findData(previous) if previous else -1
    if index<0:index=next((i for i in range(combo.count()) if combo.itemData(i)), -1)
    combo.setCurrentIndex(index);combo.setToolTip(combo.currentText());combo.blockSignals(False)


class EventListPanel(QWidget):
    changed=Signal()
    def __init__(self,session,catalog=None):
        super().__init__();self.session=session;self.catalog=catalog or ExplorationCatalog.shared()
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0)
        row=QHBoxLayout();self.search=QLineEdit();self.search.setPlaceholderText('搜索待补录事件')
        self.events=QComboBox();self.events.setMinimumContentsLength(22)
        row.addWidget(self.search,1);row.addWidget(self.events,3)
        button=QPushButton('添加已遇事件');button.clicked.connect(self.add);row.addWidget(button)
        layout.addLayout(row)
        self.table=QTableWidget(0,4);self.table.setHorizontalHeaderLabels(['事件','记录楼层','可遇楼层','记录方式'])
        self.table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.Stretch)
        for col in range(1,4):self.table.horizontalHeader().setSectionResizeMode(col,QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers);self.table.verticalHeader().hide()
        layout.addWidget(self.table,1)
        self.search.textChanged.connect(self.refresh_options)
        self.refresh()

    def refresh_options(self,*_):
        populate_events(self.events,self.session,self.catalog,self.search.text().strip())

    def refresh(self):
        self.refresh_options();rows=self.session.exploration['events'];self.table.setRowCount(len(rows))
        for i,event in enumerate(rows):
            info=self.catalog.event_info(event['id']);manual=(event.get('source') or {}).get('method')=='manual'
            values=[event['name'],event.get('floor') or '未读取',floor_label(info),'手动' if manual else '截图']
            for j,value in enumerate(values):
                cell=QTableWidgetItem(str(value));cell.setData(Qt.ItemDataRole.UserRole,event.get('occurrence_id'))
                self.table.setItem(i,j,cell)

    def add(self):
        identity=self.events.currentData()
        if not identity:return
        info=self.catalog.event_info(identity)
        if self.session.record_event(dict(event_id=identity,event_name=info['name'],repeatable=info.get('repeatable',False),source={'method':'manual'})):
            self.refresh();self.changed.emit()

    def remove(self):
        row=self.table.currentRow()
        if row<0:return
        identity=self.table.item(row,0).data(Qt.ItemDataRole.UserRole)
        if self.session.remove_event(identity):self.refresh();self.changed.emit()
