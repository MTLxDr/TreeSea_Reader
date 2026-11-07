"""Map-aligned ink, temporary mouse tools and observed movement overlays."""
from copy import deepcopy
import math

from PySide6.QtCore import QObject, QEvent, QPointF, QSize, QTimer, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QHBoxLayout, QToolButton, QWidget


def tool_icon(kind):
    pixmap=QPixmap(30,30)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter=QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor('#dce5df'),2))
    if kind=='pen':
        painter.drawLine(6,24,23,7)
        painter.drawLine(9,26,26,9)
        painter.drawLine(6,24,9,26)
        painter.drawLine(6,24,4,28)
    elif kind=='erase':
        painter.drawPolygon(QPolygonF([QPointF(4,19),QPointF(17,5),QPointF(27,15),QPointF(16,27),QPointF(11,27)]))
        painter.drawLine(11,14,21,23)
        painter.drawLine(5,28,27,28)
    else:
        painter.drawLine(16,3,12,13)
        painter.drawPolygon(QPolygonF([QPointF(9,12),QPointF(20,16),QPointF(25,25),QPointF(5,21)]))
        painter.drawLine(12,15,9,22)
        painter.drawLine(16,16,14,23)
    painter.end()
    return QIcon(pixmap)


class MapAnnotations(QObject):
    def __init__(self,view):
        super().__init__(view)
        self.view=view
        self.viewport=view.viewport()
        self.session=None
        self.result=None
        self.key=None
        self.mode=None
        self.temporary=None
        self.pending=None
        self.points=[]
        self.ink_region=None
        self.before=None
        self.display_items=[]
        self.timer=QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(300)
        self.timer.timeout.connect(self.activate_temporary)
        self.toolbar=QWidget(view.viewport())
        self.toolbar.setStyleSheet('QWidget {background:#20282c;border-radius:6px} QToolButton {padding:4px;color:#dce5df} QToolButton:checked {background:#45665f}')
        layout=QHBoxLayout(self.toolbar)
        layout.setContentsMargins(5,3,5,3)
        self.buttons={}
        for kind,label in [('pen','画笔'),('erase','橡皮'),('clear','清空手绘')]:
            button=QToolButton()
            button.setIcon(tool_icon(kind))
            button.setIconSize(QSize(26,26))
            button.setToolTip(label)
            button.setCheckable(kind!='clear')
            button.clicked.connect(lambda checked=False,k=kind:self.select(k))
            layout.addWidget(button)
            self.buttons[kind]=button
        undo=QToolButton()
        undo.setText('撤销')
        undo.clicked.connect(self.undo)
        layout.addWidget(undo)
        self.routes={}
        for key,label in [('player','行动'),('residents','居民')]:
            button=QToolButton()
            button.setText(label)
            button.setCheckable(True)
            button.setChecked(True)
            button.clicked.connect(self.redraw)
            layout.addWidget(button)
            self.routes[key]=button
        self.toolbar.adjustSize()
        view.viewport().installEventFilter(self)
        view.installEventFilter(self)
        self.position_toolbar()

    def bind(self,session):
        self.session=session

    def position_toolbar(self):
        from shiboken6 import isValid
        if not isValid(self.toolbar):
            return
        self.toolbar.move(12,12)
        self.toolbar.raise_()

    def set_result(self,result):
        new_key=None
        if result and result.get('template_id') and self.session:
            new_key=f"{self.session.run['id']}:{self.session.exploration['zone_serial']}:{result.get('floor')}:{result['template_id']}"
        if new_key!=self.key:
            self.finish()
            self.select(None)
        self.result=result
        self.key=new_key
        self.toolbar.setVisible(bool(new_key))
        self.display_items=[]
        self.position_toolbar()

    def strokes(self):
        if not self.key or not self.session:
            return []
        return self.session.exploration.get('annotations',{}).get(self.key,[])

    def save(self,strokes):
        if self.session and self.key:
            self.session.set_annotations(self.key,strokes)

    def select(self,kind):
        self.finish()
        if kind=='clear':
            self.save([])
            self.redraw()
            return
        self.mode=None if self.mode==kind else kind
        for name,button in self.buttons.items():
            if name!='clear':
                button.setChecked(name==self.mode)
        cursor=Qt.CursorShape.CrossCursor if self.mode else Qt.CursorShape.ArrowCursor
        self.view.viewport().setCursor(cursor)

    def regions(self):
        if not self.result:
            return []
        side=self.result.get('side_map',{})
        rows=[self.result]
        if side and (not self.view.screenshot or side.get('observed_in_frame',True)):
            rows.append(side)
        return [r for r in rows if r.get('nodes')]

    def region(self,identity):
        return next((r for r in self.regions() if r.get('region_id','main')==identity),None)

    def mapping(self,region):
        if self.view.screenshot:
            return region['grid']['origin'],region['grid']['spacing']
        return [80,60],130

    def scene_point(self,point,region):
        origin,spacing=self.mapping(region)
        return QPointF(origin[0]+point[0]*spacing,origin[1]+point[1]*spacing)

    def start(self,position):
        if not self.key:
            return
        point=self.view.mapToScene(position)
        regions=self.regions()
        if not regions:
            return
        target=min(regions,key=lambda r:min(math.dist((point.x(),point.y()),
                    (self.scene_point(n['slot'],r).x(),self.scene_point(n['slot'],r).y())) for n in r['nodes']))
        self.ink_region=target.get('region_id','main')
        self.before=deepcopy(self.strokes())
        self.points=[]
        self.add_point(position)

    def add_point(self,position):
        region=self.region(self.ink_region)
        if region is None:
            return
        p=self.view.mapToScene(position)
        origin,spacing=self.mapping(region)
        point=[(p.x()-origin[0])/spacing,(p.y()-origin[1])/spacing]
        tool=self.temporary or self.mode
        if tool=='pen':
            if len(self.points)<5000 and (not self.points or math.dist(self.points[-1],point)>.015):
                self.points.append(point)
        elif tool=='erase':
            radius=12/max(.01,self.view.transform().m11())
            kept=[]
            for stroke in self.strokes():
                owner=self.region(stroke['region'])
                if owner is None:
                    kept.append(stroke)
                    continue
                path=self.path(stroke['points'],owner)
                from PySide6.QtGui import QPainterPathStroker
                outline=QPainterPathStroker()
                outline.setWidth(radius*2)
                if not outline.createStroke(path).contains(p):
                    kept.append(stroke)
            # Keep erasing in memory until release, then make one undo record.
            self.session.exploration.setdefault('annotations',{})[self.key]=kept
        self.redraw()

    def activate_temporary(self):
        if self.pending:
            self.temporary='pen' if self.pending[0]==Qt.MouseButton.RightButton else 'erase'
            self.start(self.pending[1])

    def finish(self):
        self.timer.stop()
        if self.before is not None and self.session and self.key:
            strokes=deepcopy(self.strokes())
            if self.points:
                if len(self.points)==1:
                    self.points.append([self.points[0][0]+.006,self.points[0][1]+.006])
                strokes.append(dict(region=self.ink_region,points=self.points))
                strokes=strokes[-200:]
            self.session.exploration.setdefault('annotations',{})[self.key]=self.before
            self.save(strokes)
        self.before=None
        self.points=[]
        self.pending=None
        self.temporary=None

    def undo(self):
        if self.session and self.session.undo_annotation(self.key):
            self.redraw()

    def eventFilter(self,obj,event):
        from shiboken6 import isValid
        if not isValid(self.view) or not isValid(self.viewport):
            return False
        kind=event.type()
        if kind==QEvent.Type.Resize:
            self.position_toolbar()
        if kind in {QEvent.Type.FocusOut,QEvent.Type.Hide}:
            self.finish()
            self.select(None)
        if obj is not self.viewport or not self.key:
            return False
        if kind==QEvent.Type.MouseButtonPress:
            button=event.button()
            if button in {Qt.MouseButton.RightButton,Qt.MouseButton.MiddleButton}:
                if self.pending or self.before is not None:
                    self.finish()
                self.pending=(button,event.position().toPoint())
                self.timer.start()
                return True
            if button==Qt.MouseButton.LeftButton and self.mode:
                self.start(event.position().toPoint())
                return True
        if kind==QEvent.Type.MouseMove:
            if self.pending:
                self.pending=(self.pending[0],event.position().toPoint())
            if self.before is not None:
                self.add_point(event.position().toPoint())
                return True
            if self.pending:
                return True
        if kind==QEvent.Type.MouseButtonRelease:
            if self.pending or self.before is not None:
                expected=self.pending[0] if self.pending else Qt.MouseButton.LeftButton
                if event.button()!=expected:
                    return True
                self.finish()
                self.redraw()
                return True
        return False

    def path(self,points,region):
        path=QPainterPath()
        if points:
            path.moveTo(self.scene_point(points[0],region))
            for point in points[1:]:
                path.lineTo(self.scene_point(point,region))
        return path

    def add_path(self,points,region,color,dashed=False):
        pen=QPen(QColor(color),2.5)
        pen.setCosmetic(True)
        if dashed:
            pen.setStyle(Qt.PenStyle.DashLine)
        item=self.view.scene.addPath(self.path(points,region),pen)
        item.setZValue(8)
        item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.display_items.append(item)

    def route(self,a,b,region,color,observed,tip):
        self.add_path([a,b],region,color,not observed)
        self.display_items[-1].setToolTip(tip)
        p,q=self.scene_point(a,region),self.scene_point(b,region)
        angle=math.atan2(q.y()-p.y(),q.x()-p.x())
        size=8/max(.01,self.view.transform().m11())
        triangle=QPolygonF([q,QPointF(q.x()-size*math.cos(angle-.45),q.y()-size*math.sin(angle-.45)),
                           QPointF(q.x()-size*math.cos(angle+.45),q.y()-size*math.sin(angle+.45))])
        arrow=self.view.scene.addPolygon(triangle,QPen(Qt.PenStyle.NoPen),QColor(color))
        arrow.setZValue(9)
        arrow.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.display_items.append(arrow)

    def redraw(self,*_):
        for item in self.display_items:
            try:
                if item.scene() is self.view.scene:
                    self.view.scene.removeItem(item)
            except RuntimeError:
                pass
        self.display_items=[]
        for stroke in self.strokes():
            region=self.region(stroke['region'])
            if region:
                self.add_path(stroke['points'],region,'#f3dc91')
        if self.points:
            region=self.region(self.ink_region)
            if region:
                self.add_path(self.points,region,'#f3dc91')
        if not self.session or not self.result:
            return
        region=self.result
        zone=self.session.exploration['zone_serial']
        if self.routes['player'].isChecked():
            edges={frozenset(map(tuple,e)) for e in region.get('edges',[])}
            for action in self.session.exploration.get('actions',[]):
                if (action.get('zone')==zone and action['type']=='enter_node'
                        and action.get('region_id','main')==region.get('region_id','main')):
                    a,b=action['from_slot'],action['slot']
                    adjacent=frozenset((tuple(a),tuple(b))) in edges
                    self.route(a,b,region,'#efb666',adjacent,
                               '已观察到的相邻移动' if adjacent else '两次观察位置，中间经过未记录')
        if self.routes['residents'].isChecked():
            for step in self.session.exploration.get('resident_routes',[]):
                if step['zone']==zone and step.get('region_id','main')==region.get('region_id','main'):
                    self.route(step['from'],step['to'],region,'#79e6ce',True,'居民相邻移动，两帧唯一对应')
