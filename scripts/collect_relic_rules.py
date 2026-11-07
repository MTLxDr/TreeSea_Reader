"""Save per-relic counter and one-use markers from the PRTS catalogue."""
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from reader.catalog import Catalog
from reader.exploration import ExplorationCatalog

URL = 'https://prts.wiki/w/%E6%B2%89%E6%B2%A6%E8%80%85%E7%9A%84%E9%BB%91%E6%B5%81%E6%A0%91%E6%B5%B7/%E6%8B%9F%E9%80%A0%E7%89%A9%E8%B4%A8%E7%BC%96%E7%9B%AE'


class Tables(HTMLParser):
    def __init__(self):
        super().__init__()
        self.depth = 0
        self.parts = []
        self.tables = []

    def handle_starttag(self,tag,attrs):
        if tag == 'table':
            if self.depth == 0:
                self.parts = []
            self.depth += 1
        if self.depth:
            values = dict(attrs)
            for key in ('title','alt'):
                if values.get(key):
                    self.parts.append(values[key])

    def handle_data(self,data):
        if self.depth:
            self.parts.append(data.strip())

    def handle_endtag(self,tag):
        if tag == 'table' and self.depth:
            self.depth -= 1
            if self.depth == 0:
                self.tables.append(' '.join(self.parts))


def main():
    request = Request(URL,headers={'User-Agent':'TreeSeaReader/1.0 relic metadata'})
    with urlopen(request,timeout=30) as response:
        html = response.read().decode('utf-8')
    parser = Tables()
    parser.feed(html)
    catalog = Catalog()
    catalog.include_exploration_items()
    rules = {}
    source_items={item['itemId']:item for item in ExplorationCatalog().archive['classifier']['items']
                  if item.get('topicId')=='rogue_6' and item.get('dataType')=='藏品'}
    for item in catalog.inventory['relics']:
        candidates = [table for table in parser.tables if re.search(r'No\.?\s*\d+',table)
                      and item['name'] in table.split('效果')[0]]
        if len(candidates) != 1:
            continue
        text = candidates[0]
        counted = '可计数叠加层数' in text
        one_use = '一次性使用' in text
        maximum = re.search(r'最多叠加\s*(\d+)\s*层',text)
        rules[item['id']] = dict(name=item['name'],mode='counter' if counted else 'one_use' if one_use else 'continuous',
                                counter=counted,one_use=one_use,
                                maximum=int(maximum[1]) if counted and maximum else None,
                                source_url=URL,source_excerpt=text[:5000])
        runes=source_items.get(item['id'],{}).get('topicBuffs',[])
        if runes and all(r['key'] in {'immediate_reward','rand_gain_char_buff'} for r in runes) and not counted:
            rules[item['id']].update(mode='on_acquire',one_use=True,trigger='acquire',source_runes=runes)
    if len(rules) < 200:
        raise ValueError(f'Unexpected PRTS coverage: {len(rules)}')
    output=ROOT/'assets/relics/rules.json'
    output.write_text(json.dumps(dict(retrieved_at=datetime.now(timezone.utc).isoformat(),
                                      source_url=URL,rules=rules),ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'{len(rules)} relic rules')


if __name__ == '__main__':
    main()
