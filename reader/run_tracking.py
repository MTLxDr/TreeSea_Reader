"""New-run evidence is independent of user-edited resource fields."""
import json


def frame_observation(result):
    source=result.get('source') or {}
    if isinstance(source,dict):
        window=source.get('window') or source
        if isinstance(window,dict) and window.get('captured_at'):
            return json.dumps([window.get('pid'),window.get('hwnd'),window['captured_at']])
    return result.get('captured_at')


class RunTracker:
    def __init__(self):
        self.key = None
        self.count = 0
        self.observation = None

    def assess(self, previous, incoming):
        before, after = (previous or {}).get('floor'), incoming.get('floor')
        regression = isinstance(before, int) and before > 1 and after == 1
        if not regression:
            self.key = None
            self.count = 0
            return 'continue', ''
        if incoming.get('floor_hint_used') or incoming.get('template_hint_used'):
            return 'wait', '楼层由手动指定；开始新局请点击“新建本局”'
        source = incoming.get('source') or {}
        if isinstance(source, dict) and source.get('method') in {'file_import', 'clipboard'}:
            return 'wait', '导入的一层地图不清空本局；开始新局请点击“新建本局”'
        if not incoming.get('at_start') or incoming.get('current_slot') != incoming.get('start_slot'):
            return 'wait', '读到一层地图，等待出生点确认；当前本局记录已保留'
        key = json.dumps([incoming.get('template_id'), incoming.get('start_slot'),
                          sorted(n['slot'] for n in incoming.get('nodes', []))], sort_keys=True)
        observation = frame_observation(incoming)
        if key != self.key:
            self.key, self.count, self.observation = key, 0, None
        if observation is None or observation != self.observation:
            self.count += 1
            self.observation = observation
        if incoming.get('_stable_observations', 0) >= 2 or self.count >= 2:
            return 'new', '连续确认一层出生点，已开始新局；可撤销恢复'
        return 'wait', '读到一层出生点，等待下一次截图确认新局'
