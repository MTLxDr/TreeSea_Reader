"""Independent stage query, event sidebar and remaining-pool entry."""
import html
import json
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor,QPen,QBrush,QPolygonF,QFont,QDesktopServices
from .events_ui import populate_events
from PySide6.QtCore import QUrl,QPointF
from PySide6.QtWidgets import (QWidget,QDialog,QVBoxLayout,QHBoxLayout,QLabel,QComboBox,QLineEdit,
    QPushButton,QTextBrowser,QTableWidget,QTableWidgetItem,QHeaderView,QGraphicsView,QGraphicsScene,QCheckBox,QSplitter)
from .exploration import ExplorationCatalog
from .stage_routes import stage_events,placed_units,route_points,route_segments,portal_links
from .stage_tiles import describe_tile,tile_at
from .stage_paths import estimate_path
from .probability import rounded_percentages
from .skill_timing import source_text


class StageGraphicsView(QGraphicsView):
    def __init__(self,*args):
        super().__init__(*args)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setToolTip('滚轮缩放；拖动平移；双击适应视图')
    def wheelEvent(self,event):
        factor=1.15 if event.angleDelta().y()>0 else 1/1.15
        if .05<self.transform().m11()*factor<20:self.scale(factor,factor)
        event.accept()
    def mouseDoubleClickEvent(self,event):
        if self.scene():self.fitInView(self.scene().itemsBoundingRect(),Qt.AspectRatioMode.KeepAspectRatio)
        event.accept()
    def resizeEvent(self,event):
        super().resizeEvent(event)
        if self.scene() and self.scene().items():self.fitInView(self.scene().itemsBoundingRect(),Qt.AspectRatioMode.KeepAspectRatio)


class EventPoolPanel(QWidget):
    def __init__(self,session,changed,catalog=None):
        super().__init__();self.session=session;self.changed=changed;self.catalog=catalog or ExplorationCatalog.shared()
        layout=QVBoxLayout(self);self.title=QLabel('当前不期而遇池');self.title.setWordWrap(True);layout.addWidget(self.title)
        self.content=QTextBrowser();self.content.setMinimumHeight(96);self.content.setMaximumHeight(180);layout.addWidget(self.content,1)
        self.entrance=QComboBox();self.entrance.addItem('选择树洞入口楼层',None)
        for floor in (3,4,5):self.entrance.addItem(f'第{floor}层进入树洞',floor)
        self.entrance.currentIndexChanged.connect(self.set_entrance);layout.addWidget(self.entrance)
        self.events=QComboBox();layout.addWidget(self.events)
        button=QPushButton('记录已遇事件');button.clicked.connect(self.record);layout.addWidget(button)
        self.refresh()
    def refresh(self):
        current=self.session.current or {};floor=current.get('floor')
        self.entrance.setVisible(floor=='treehole')
        self.entrance.blockSignals(True);self.entrance.setCurrentIndex(max(0,self.entrance.findData(self.session.exploration.get('entrance_floor'))));self.entrance.blockSignals(False)
        result=self.catalog.eligible_events(floor,self.session.exploration['events'],self.session.exploration.get('entrance_floor'))
        floor_name='树洞' if floor=='treehole' else f'第{floor or "—"}层'
        self.title.setText('当前不期而遇池 · '+floor_name+'\n'+result['method'])
        units=rounded_percentages([e['probability'] for e in result['events']])
        rows=[f"{html.escape(e['name'])}　{u}%"+(' · 可重复' if e.get('repeatable') else '') for e,u in zip(result['events'],units)]
        if result['excluded']:rows.append('<br>已遇：'+'、'.join(html.escape(e['name']) for e in result['excluded']))
        if result.get('conditional'):rows.append('<br>另有条件事件，见楼层资料')
        self.content.setHtml('<br>'.join(rows) or '读取地图后显示本层事件池')
        self.content.setToolTip(json.dumps(result,ensure_ascii=False,indent=2))
        populate_events(self.events,self.session,self.catalog)
    def record(self):
        identity=self.events.currentData()
        if identity:
            info=self.catalog.event_info(identity)
            self.session.record_event({'event_id':identity,'event_name':info['name'],'repeatable':info.get('repeatable',False),'source':{'method':'manual'}})
            self.changed();self.refresh()
    def set_entrance(self,*_):
        self.session.set_entrance_floor(self.entrance.currentData());self.changed();self.refresh()


class RelicPoolsDialog(QDialog):
    def __init__(self,session,inventory_catalog,parent=None,catalog=None):
        super().__init__(parent);self.setWindowTitle('藏品池与剩余藏品');self.resize(920,680)
        self.session=session;self.catalog=catalog or ExplorationCatalog.shared()
        self.items={i['id']:i for rows in inventory_catalog.inventory.values() for i in rows}
        layout=QVBoxLayout(self);controls=QHBoxLayout();layout.addLayout(controls)
        self.version=QComboBox()
        for v in self.catalog.versions:self.version.addItem(v,v)
        self.version.setCurrentIndex(max(0,self.version.findData(self.catalog.default_version)));controls.addWidget(self.version)
        self.pool=QComboBox();controls.addWidget(self.pool,1)
        self.search=QLineEdit();self.search.setPlaceholderText('查找藏品或零件');controls.addWidget(self.search)
        self.only_remaining=QCheckBox('只看剩余');self.only_remaining.setChecked(True);controls.addWidget(self.only_remaining)
        self.summary=QLabel();self.summary.setWordWrap(True);layout.addWidget(self.summary)
        self.table=QTableWidget(0,3);self.table.setHorizontalHeaderLabels(['名称','状态','效果'])
        self.table.horizontalHeader().setSectionResizeMode(2,QHeaderView.ResizeMode.Stretch);layout.addWidget(self.table,1)
        self.version.currentIndexChanged.connect(self.load_pools);self.pool.currentIndexChanged.connect(self.refresh)
        self.search.textChanged.connect(self.refresh);self.only_remaining.toggled.connect(self.refresh);self.load_pools()
    def load_pools(self,*_):
        self.pool.blockSignals(True);self.pool.clear()
        for p in self.catalog.pools(self.version.currentData()):self.pool.addItem(p['name']+' · '+p.get('poolKind',''),p['id'])
        self.pool.blockSignals(False);self.refresh()
    def refresh(self,*_):
        result=self.catalog.remaining_items(self.pool.currentData(),self.session.inventories,self.version.currentData())
        if not result:self.table.setRowCount(0);return
        self.summary.setText(f"剩余 {len(result['remaining'])} / {len(result['pool']['collectibles'])} · 已持有 {len(result['owned'])}；按本局已记录库存筛选")
        self.summary.setToolTip(json.dumps(result['pool'],ensure_ascii=False,indent=2))
        rows=[]
        for identity in result['pool']['collectibles']:
            owned=identity in result['owned'];item=self.items.get(identity,{'name':identity,'effect':''})
            if self.only_remaining.isChecked() and owned:continue
            if self.search.text() not in item['name']:continue
            rows.append((item['name'],'已持有' if owned else '剩余',item.get('effect','')))
        self.table.setRowCount(len(rows))
        for i,row in enumerate(rows):
            for j,value in enumerate(row):self.table.setItem(i,j,QTableWidgetItem(str(value)))
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)


class StageMapPanel(QWidget):
    def __init__(self,catalog=None):
        super().__init__();self.catalog=catalog or ExplorationCatalog.shared();self.stage=None;self.rows=[]
        self.index=self.catalog.stage_index();layout=QVBoxLayout(self)
        title=QLabel('关卡地图 · 出怪路线与特殊条件');title.setStyleSheet('font-size:18px;');layout.addWidget(title)
        controls=QHBoxLayout();layout.addLayout(controls);self.search=QLineEdit();self.search.setPlaceholderText('输入关卡名或ID，独立查询')
        controls.addWidget(self.search);self.selector=QComboBox();controls.addWidget(self.selector,1)
        source=QPushButton('在PRTS.Map查看');source.clicked.connect(self.open_source);controls.addWidget(source)
        self.info=QLabel();self.info.setWordWrap(True);layout.addWidget(self.info)
        self.legend=QLabel('<span style="color:#83958b">■ 地面</span>　<span style="color:#d6c48c">■ 高台</span>　'
            '<span style="color:#9c8c78">▦ 墙体／禁入区</span>　▰ 地面阻挡　▣ 空洞　'
            '╱ 斜线：不可部署　<span style="color:#d9a5ee">◎ 传送入／出</span>　'
            '<span style="color:#ff6666">△ 侵入点</span>　<span style="color:#65b9ff">△ 保护目标</span>')
        self.legend.setWordWrap(True);layout.addWidget(self.legend)
        split=QSplitter(Qt.Orientation.Vertical);layout.addWidget(split,1)
        self.scene=QGraphicsScene();self.view=StageGraphicsView(self.scene);self.view.setMinimumHeight(250);split.addWidget(self.view)
        bottom=QWidget();bl=QVBoxLayout(bottom);split.addWidget(bottom)
        filters=QHBoxLayout();bl.addLayout(filters);self.proto=QCheckBox('额外猎犬proto');filters.addWidget(self.proto)
        self.group=QComboBox();filters.addWidget(self.group,1)
        self.path_mode=QCheckBox('沿通行地块显示（估算）');self.path_mode.setChecked(True);filters.addWidget(self.path_mode)
        self.table=QTableWidget(0,5);self.table.setHorizontalHeaderLabels(['出怪时机','单位','数量 / 间隔','条件组','路线'])
        self.table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeMode.Stretch);bl.addWidget(self.table,1)
        self.units=QLabel();self.units.setWordWrap(True);bl.addWidget(self.units)
        self.search.textChanged.connect(self.filter_stages);self.selector.currentIndexChanged.connect(self.load)
        self.proto.toggled.connect(self.filter_events);self.group.currentIndexChanged.connect(self.filter_events)
        self.path_mode.toggled.connect(lambda:self.draw(self.shown[self.table.currentRow()] if self.table.currentRow()>=0 else None))
        self.table.currentCellChanged.connect(self.route_selected);self.filter_stages()
    def filter_stages(self,*_):
        self.selector.blockSignals(True);self.selector.clear();query=self.search.text().strip()
        for s in self.index:
            if query and query not in str(s.get('name','')) and query not in s['id']:continue
            label=(s.get('name') or s['id'])+(' · 紧急' if s['id'].startswith('ro6_e_') else '')+' · '+s['id']
            self.selector.addItem(label,s['id'])
        self.selector.blockSignals(False);self.load()
    def load(self,*_):
        self.stage=self.catalog.read('stage',self.selector.currentData()) if self.selector.currentData() else None
        self.scene.clear()
        if not self.stage:
            self.table.setRowCount(0);self.info.setText('输入关卡名查询');self.units.clear();self.shown=[];return
        self.rows=stage_events(self.stage,self.catalog.names);self.placed=placed_units(self.stage,self.catalog.names)
        self.info.setText('时机从对应波次片段或条件分支开始计时；橙色为猎犬proto，紫色虚线为传送。勾选下方选项按静态地形估算路径，取消后显示源移动点连线。\n'+source_text(self.stage.get('description','')))
        self.group.blockSignals(True);self.group.clear();self.group.addItem('全部条件组',None)
        for group in sorted({r['hidden_group'] for r in self.rows if r['hidden_group']}):
            label=('额外猎犬 · ' if group.startswith('shsdgo') else '探险者 · ' if group.startswith('shadv') else '')+group
            self.group.addItem(label,group)
        self.group.blockSignals(False)
        npcs=[u for u in self.placed if u['npc']]
        arrivals=[r for r in self.rows if r['npc']]
        npc_text=[f"{u['name']}：列{u['position']['col']} 行{u['position']['row']} [{','.join(str(c.get('hidden_group') or '开场') for c in u['conditions'])}]" for u in npcs if u.get('position')]
        self.units.setText('探险者位置：'+('；'.join(npc_text) or '本关未预放置探险者')+f'；预放置单位与机关 {len(self.placed)} 处')
        self.units.setToolTip(json.dumps(self.placed,ensure_ascii=False,indent=2));self.filter_events()
    def filter_events(self,*_):
        if not self.stage:return
        self.shown=[r for r in self.rows if (not self.proto.isChecked() or r['proto']) and (not self.group.currentData() or r['hidden_group']==self.group.currentData())]
        self.table.setRowCount(len(self.shown))
        for i,r in enumerate(self.shown):
            condition=r['hidden_group'] or '常规'
            if r.get('random_group'):condition+=f" · 随机组 {r['random_group']} 权重{r['weight']}"
            for j,value in enumerate((r['time'],r['name'],f"{r['count']} / {r['interval']:g}s",condition,str(r['route']))):
                item=QTableWidgetItem(str(value));item.setToolTip(json.dumps(r,ensure_ascii=False,indent=2))
                if r['proto']:item.setForeground(QColor('#ffb366'))
                self.table.setItem(i,j,item)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        if self.shown:self.table.setCurrentCell(0,0)
        self.draw(self.shown[0] if self.shown else None)
    def route_selected(self,row,*_):
        if 0<=row<len(getattr(self,'shown',[])):self.draw(self.shown[row])
    def draw(self,spawn=None):
        if not self.stage:return
        self.scene.clear();level=self.stage['level'];data=level['mapData'];grid=data['map'];size=42
        h=len(grid)
        links=portal_links(level)
        for row,tiles in enumerate(grid):
            for col,index in enumerate(tiles):
                tile=data['tiles'][index];spec=describe_tile(tile);x,y=col*size,row*size
                rect=self.scene.addRect(x,y,size,size,QPen(QColor(spec['border'])),QColor(spec['fill']))
                rect.setData(0,spec)
                passage='地面可通行' if spec['ground_passable'] else '地面不可通行' if spec['ground_passable'] is False else '通行属性未提供'
                text=f"列{col} 行{h-1-row}\n{spec['label']} · {spec['height']}\n{passage}；{spec['deployment']}"
                if spec['special']:text+='\n'+spec['special']
                if spec['category']=='hole':text+='（可坠落）'
                rect.setToolTip(text+'\n'+json.dumps(tile,ensure_ascii=False))
                if spec['undeployable']:
                    hatch=self.scene.addRect(x+2,y+2,size-4,size-4,QPen(Qt.PenStyle.NoPen),QBrush(QColor('#b3b7a4'),Qt.BrushStyle.BDiagPattern));hatch.setOpacity(.32);hatch.setToolTip(rect.toolTip())
                if spec['category']=='highland':
                    self.scene.addLine(x+2,y+size-4,x+size-2,y+size-4,QPen(QColor('#e6d69e'),3))
                elif spec['category']=='wall':
                    for offset in (14,28):self.scene.addLine(x+3,y+offset,x+size-3,y+offset,QPen(QColor('#786b5b')))
                    self.scene.addLine(x+size/2,y+3,x+size/2,y+size-3,QPen(QColor('#786b5b')))
                elif spec['category']=='obstacle':
                    self.scene.addRect(x+5,y+5,size-10,size-10,QPen(QColor('#e0d6af'),3))
                    self.scene.addLine(x+10,y+size/2,x+size-10,y+size/2,QPen(QColor('#e0d6af'),4))
                elif spec['category']=='hole':
                    self.scene.addRect(x+6,y+6,size-12,size-12,QPen(QColor('#ad9e7d'),2),QColor('#090c0b'))
                if spec['spawn'] or spec['goal']:
                    color=QColor('#65b9ff' if spec['goal'] else '#ff6666')
                    self.scene.addPolygon(QPolygonF([QPointF(x+size/2,y+5),QPointF(x+5,y+size-6),QPointF(x+size-5,y+size-6)]),QPen(color,2),QBrush(QColor('#19201e')))
                    label=self.scene.addText('守' if spec['goal'] else '敌');label.setDefaultTextColor(color);label.setPos(x+10,y+10)
                if spec['portal_role']:
                    self.scene.addEllipse(x+5,y+5,size-10,size-10,QPen(QColor('#e0aff5'),3),QBrush(QColor('#44354f')))
                    label=self.scene.addText('入' if spec['portal_role']=='in' else '出');label.setDefaultTextColor(QColor('#f0dafa'));label.setPos(x+10,y+5)
                    ids=[str(link['id']) for link in links if (link['entry'] if spec['portal_role']=='in' else link['exit'])=={'row':h-1-row,'col':col}]
                    if ids:
                        number=self.scene.addText('/'.join(ids));number.setDefaultTextColor(QColor('#f6dcff'));number.setFont(QFont('Microsoft YaHei UI',7));number.setPos(x+5,y+23)
        for u in self.placed:
            if not u.get('position'):continue
            selected=self.group.currentData()
            if u['npc'] and selected and selected.startswith('shadv') and selected not in {r.get('hidden_group') for r in u['conditions']}:continue
            p=u['position'];x=p['col']*size;y=(h-1-p['row'])*size
            marker=self.scene.addEllipse(x+14,y+14,14,14,QPen(QColor('#e6cf78')),QColor('#e6cf78' if u['npc'] else '#6e857c'))
            marker.setToolTip(u['name']+'\n'+str(u['position'])+'\n'+json.dumps(u['conditions'],ensure_ascii=False))
            if u['npc']:
                text=self.scene.addText(u['name']);text.setDefaultTextColor(QColor('#f6e69a'));text.setPos(x,y-18)
        if spawn and spawn['action_type']=='SPAWN':
            routes=level.get('extraRoutes',[]) if spawn['scope'].startswith('触发分支') else level.get('routes',[])
            if 0<=spawn['route']<len(routes):
                route=routes[spawn['route']];points=route_points(route);color=QColor('#ff993d' if spawn['proto'] else '#6de7cc')
                for segment in route_segments(route):
                    a,b=segment['start'],segment['end'];pen=QPen(QColor('#dba6f0') if segment['kind']=='teleport' else color,3)
                    if segment['kind']!='move':pen.setStyle(Qt.PenStyle.DashLine)
                    path=estimate_path(level,route,a,b) if segment['kind']=='move' and self.path_mode.isChecked() else [a,b]
                    if path is None:path=[a,b];pen.setStyle(Qt.PenStyle.DotLine)
                    for start,end in zip(path,path[1:]):
                        line=self.scene.addLine((start['col']+.5)*size,(h-start['row']-.5)*size,(end['col']+.5)*size,(h-end['row']-.5)*size,pen)
                        line.setToolTip(('静态地形路径估算\n' if self.path_mode.isChecked() else '源移动点\n')+json.dumps(route,ensure_ascii=False,indent=2))
                for i,p in enumerate(points):
                    terrain=tile_at(level,p) or {}
                    if terrain.get('tileKey') in {'tile_telin','tile_telout'}:continue
                    dot=self.scene.addEllipse((p['col']+.5)*size-2,(h-p['row']-.5)*size-2,4,4,QPen(color),QBrush(color))
                    dot.setToolTip(f'路线移动点 {i}：列{p["col"]} 行{p["row"]}')
        self.view.fitInView(self.scene.itemsBoundingRect(),Qt.AspectRatioMode.KeepAspectRatio)
    def open_source(self):
        if self.stage:QDesktopServices.openUrl(QUrl('https://map.ark-nights.com/map/'+self.stage['id']))
