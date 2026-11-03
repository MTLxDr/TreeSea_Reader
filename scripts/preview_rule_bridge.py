"""Render the independent window with a labelled example; never save a session."""
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from reader.calculation_ui import CalculationWindow


def main():
    app = QApplication.instance() or QApplication([])
    example = json.loads((ROOT / 'docs/examples/session_strong_strike.request.json').read_text(encoding='utf-8'))
    window = CalculationWindow(snapshot_provider=lambda: example['session'])
    window.resize(1500, 1040); panel = window.session_panel
    window.pages.setCurrentWidget(panel)
    for combo,value in [(panel.operator,'char_172_svrash'),(panel.stage,'ro6_b_1'),
                        (panel.enemy,'enemy_1140_merhd:0'),(panel.squad,None),
                        (panel.skill,True),(panel.kind,'physical'),(panel.attack_mode,'melee')]:
        combo.setCurrentIndex(combo.findData(value))
    panel.difficulty.setValue(0); panel.sync_selection()
    panel.snapshot_status.setText('手算验证样例 · 非用户当前库存')
    if not panel.run(): raise AssertionError(panel.status.text())
    window.show(); app.processEvents()
    folder = ROOT / 'reports/calculation/rule_bridge'
    window.grab().save(str(folder / 'session_preview.png'))
    panel.view_trace(); app.processEvents()
    for i in range(window.panel.trace.topLevelItemCount()):
        item=window.panel.trace.topLevelItem(i)
        item.setExpanded(item.text(0) in ('攻击方 · 攻击力','伤害结算'))
    window.grab().save(str(folder / 'trace_preview.png'))
    window.close(); app.processEvents()
    print('UI previews saved to reports/calculation/rule_bridge/')


if __name__ == '__main__': main()
