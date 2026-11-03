"""Browse source-backed combat parameters; presentation never replaces raw fields."""
import json
import re
from html import unescape

from PySide6.QtWidgets import QComboBox,QDialog,QLabel,QTabWidget,QTextBrowser,QVBoxLayout


def wiki_text(text):
    text=re.sub(r"<!--.*?-->","",str(text),flags=re.S)
    text=re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]",r"\2",text)
    text=re.sub(r"\[\[([^\]]+)\]\]",r"\1",text)
    def replace(match):
        parts=match[1].split("|")
        if parts[0] in {"修正","修正2"}:return parts[1] if len(parts)>1 else ""
        values=[p for p in parts[1:] if "=" not in p]
        return values[-1] if values else match[0]
    for _ in range(15):
        changed=re.sub(r"\{\{([^{}]*)\}\}",replace,text)
        if changed==text:break
        text=changed
    text=re.sub(r"<br\s*/?>","\n",text,flags=re.I)
    return unescape(re.sub(r"<[^>]*>","",text)).replace("'''","").replace("''","")


class OperatorDetailsDialog(QDialog):
    def __init__(self,catalog,operator_id=None,parent=None):
        super().__init__(parent)
        self.catalog=catalog
        self.setWindowTitle("干员技能、天赋、潜能与模组资料")
        self.resize(1100,780)
        layout=QVBoxLayout(self)
        self.names=QComboBox();self.names.setEditable(True);self.names.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for item in sorted(catalog.inventory["operators"],key=lambda r:r["name"]):self.names.addItem(item["name"],item["id"])
        layout.addWidget(self.names)
        self.source=QLabel();self.source.setWordWrap(True);layout.addWidget(self.source)
        self.tabs=QTabWidget();layout.addWidget(self.tabs,1)
        self.views={}
        for key,label in (("skills","技能各级"),("talents","天赋及变体"),("potentials","逐潜效果"),("modules","模组各级"),("attributes","面板与信赖"),("raw","原始参数")):
            view=QTextBrowser();self.views[key]=view;self.tabs.addTab(view,label)
        self.names.currentIndexChanged.connect(self.render)
        if operator_id:self.names.setCurrentIndex(max(0,self.names.findData(operator_id)))
        self.render()

    def render(self,*_):
        data=self.catalog.operator_detail(self.names.currentData())
        if not data:
            self.source.setText("尚无该干员的完整来源资料。")
            for view in self.views.values():view.clear()
            return
        freshness=f"PRTS修订 {data['revision_id']}" if data.get("revision_id") else "本地原文缓存，源站修订时间未知"
        self.source.setText(f"{freshness}　·　{data['source_url']}\n保留源站原始模板和备注；当前资料尚未全部转换为伤害计算规则。")
        components=data["components"]
        for kind,view in self.views.items():
            if kind=="raw":view.setPlainText(json.dumps(data,ensure_ascii=False,indent=2));continue
            lines=[]
            for index,record in enumerate(components.get(kind,[]),1):
                fields=record["fields"]
                if kind=="skills":
                    lines.append(f"技能{index}　{wiki_text(fields.get('技能名','未命名'))}")
                    lines.append(f"{fields.get('技能类型1','')} / {fields.get('技能类型2','')}")
                    for row in record["levels"]:
                        label=f"专精{row['mastery']}" if row["mastery"] else f"等级{row['level']}"
                        lines.append(f"\n{label}　初始技力 {row.get('initial_sp')} / 消耗 {row.get('sp_cost')} / 持续 {row.get('duration')}\n{wiki_text(row.get('description',''))}")
                else:
                    lines.append(f"第{index}组　{wiki_text(fields.get('名称',fields.get('天赋',record['template'])))}")
                    for key,value in fields.items():
                        if kind=="modules" and any(s in key for s in ("基础信息","材料消耗","任务")):continue
                        lines.append(f"{key}：{wiki_text(value)}")
                lines.append("\n"+"─"*40+"\n")
            section={"skills":"技能","talents":"天赋","potentials":"潜能提升","modules":"模组","attributes":"属性"}[kind]
            notes=data.get("sections",{}).get(section,"")
            if notes and (kind in {"skills","talents","modules","potentials"} or not lines):
                lines.append("源站原文与机制备注\n"+notes)
            view.setPlainText("\n".join(lines) or "该页面未收录此类模板；不能据此推断效果为零。")
