import json
from urllib.parse import unquote
from PySide6.QtCore import Qt,QTimer
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (QComboBox,QHBoxLayout,QHeaderView,QLabel,QLineEdit,QMainWindow,
    QPlainTextEdit,QPushButton,QSpinBox,QSplitter,QTableWidget,QTableWidgetItem,QVBoxLayout,QWidget)

from .catalog import Catalog
from .calculation_ui import STYLE


class EffectsWindow(QMainWindow):
    def __init__(self,catalog=None):
        super().__init__()
        if 'Microsoft YaHei UI' not in QFontDatabase.families():QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc')
        self.catalog=catalog or Catalog();self.setWindowTitle('黑流树海效果库 · 原文与生效规则');self.resize(1500,960);self.setStyleSheet(STYLE)
        root=QWidget();layout=QVBoxLayout(root);self.setCentralWidget(root)
        summary=self.catalog.effect_summary()
        note=QLabel(f"来源分档记录 {summary['entry_count']:,} 条；已标注运算类型 {summary.get('review_status',{}).get('source_annotated',0)} 条。原文已收录与规则可自动执行分别记录，内部变体缺少独立页时保留待核对状态。")
        note.setWordWrap(True);note.setObjectName('muted');layout.addWidget(note)
        row=QHBoxLayout();self.search=QLineEdit();self.search.setPlaceholderText('名称 / 效果 / 变体');row.addWidget(self.search,1)
        self.category=QComboBox();self.category.addItem('全部类别',None)
        for category in sorted(summary.get('actual_by_category',{})):self.category.addItem(category,category)
        row.addWidget(self.category)
        self.status=QComboBox()
        for label,value in [('全部状态',None),('来源标注了运算类型','source_annotated'),('有底层参数','source_parameters'),('尚未规则化','not_compiled'),('非数值说明','non_numeric')]:self.status.addItem(label,value)
        row.addWidget(self.status);self.page=QSpinBox();self.page.setRange(1,1000);self.page.setPrefix('页 ');row.addWidget(self.page)
        button=QPushButton('查询');button.clicked.connect(self.refresh);row.addWidget(button);layout.addLayout(row)
        self.summary=QLabel();layout.addWidget(self.summary)
        splitter=QSplitter(Qt.Orientation.Horizontal);layout.addWidget(splitter,1)
        self.table=QTableWidget(0,6);self.table.setHorizontalHeaderLabels(['名称','类别','分档 / 变体','作用方','运算标注','执行状态']);self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2,QHeaderView.ResizeMode.Stretch);self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows);splitter.addWidget(self.table)
        self.details=QPlainTextEdit();self.details.setReadOnly(True);splitter.addWidget(self.details);splitter.setSizes([850,600])
        self.table.currentCellChanged.connect(self.selected);self.search.returnPressed.connect(self.refresh)
        self.refresh()

    def refresh(self,*_):
        result=self.catalog.search_effects(self.search.text().strip(),self.category.currentData(),status=self.status.currentData(),offset=(self.page.value()-1)*200)
        self.rows=result['entries'];self.table.setRowCount(len(self.rows));self.summary.setText(f"匹配 {result['total']:,} 条，当前显示 {len(self.rows)} 条；每页200条。相同藏品的来源、版本及不同档位分别保留，不能相加当作多件藏品。")
        for index,row in enumerate(self.rows):
            reviewed={'source_annotated':'来源有运算标注','source_parameters':'有底层参数','catalogued':'已收录原文'}.get(row['review_status'],row['review_status'])
            execution={'not_compiled':'待规则化','non_numeric':'非数值说明'}.get(row['execution_status'],row['execution_status'])
            values=[row['name'],row['category'],row['variant'],{'friendly':'我方','enemy':'敌方','both':'双方','unspecified':'待定/资源'}.get(row['side'],row['side']),reviewed,execution]
            for col,value in enumerate(values):
                cell=QTableWidgetItem(value);cell.setFlags(cell.flags()&~Qt.ItemFlag.ItemIsEditable);self.table.setItem(index,col,cell)
        has_variant=any(row['variant'] for row in self.rows)
        self.table.setColumnHidden(2,not has_variant)
        if self.rows:self.table.setCurrentCell(0,0);self.selected(0)
        else:self.details.setPlainText('没有匹配的条目。')

    def selected(self,row,*_):
        if not 0<=row<len(self.rows):return
        data=self.catalog.effect_detail(self.rows[row]['id'])
        if not data:return
        side={'friendly':'我方','enemy':'敌方','both':'双方','unspecified':'待定/资源'}.get(data['side'],data['side'])
        head=f"{data['name']}　{data['variant']}" if data['variant'] else data['name']
        blocks=[head,
                f"来源：{unquote(data['source']['url'])}",
                f"修订：{data['source'].get('revision_id')}　作用方：{side}"]
        sections=[('效果原文',data['raw_effect']),
                  ('生效条件（原文线索）','\n'.join(data['conditions_raw'])),
                  ('叠加/上限（原文）','\n'.join(data['stack_limits_raw'])),
                  ('备注与机制',data['raw_notes']),
                  ('作用对象（原文线索）','\n'.join(data.get('targets_raw',[]))),
                  ('来源运算标注',json.dumps(data['modifiers'],ensure_ascii=False,indent=2)),
                  ('当前规则编译（不等于已在本局生效）',json.dumps(data.get('compilation',{'status':'inactive_revision'}),ensure_ascii=False,indent=2)),
                  ('分档、选择条件及原始参数',json.dumps({'context':data['context'],'parameters':data['source_parameters']},ensure_ascii=False,indent=2))]
        for title,body in sections:
            if not str(body).strip() or str(body).strip() in ('[]','{}','null'):
                continue
            blocks.append(f"{title}\n{body}")
        self.details.setPlainText('\n\n'.join(blocks))


def main():
    import argparse,sys
    from pathlib import Path
    from PySide6.QtWidgets import QApplication
    parser=argparse.ArgumentParser(description='独立查看黑流树海效果来源与规则')
    parser.add_argument('--preview',type=Path);args=parser.parse_args()
    app=QApplication(sys.argv[:1]);window=EffectsWindow();window.show()
    from .startup import gui_ready
    gui_ready(app,window,'effects')
    if args.preview:
        def save():
            args.preview.parent.mkdir(parents=True,exist_ok=True);window.grab().save(str(args.preview))
        QTimer.singleShot(300,save)
    return app.exec()


if __name__=='__main__':raise SystemExit(main())
