"""Build the local event-art index from PRTS scene metadata."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import md5
from html.parser import HTMLParser
import json
from pathlib import Path
from urllib.request import Request, urlopen

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
PAGE = 'https://prts.wiki/w/%E6%B2%89%E6%B2%A6%E8%80%85%E7%9A%84%E9%BB%91%E6%B5%81%E6%A0%91%E6%B5%B7/%E4%BA%8B%E4%BB%B6%E4%B8%80%E8%A7%88'


class Scenes(HTMLParser):
    def __init__(self):
        super().__init__()
        self.art = {}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        name = a.get('data-name')
        image = a.get('data-image')
        if tag == 'div' and a.get('class') == 'scene' and name and image and image.startswith('Avg_pic_'):
            self.art.setdefault(image, set()).add(name)


def fetch(url):
    with urlopen(Request(url, headers={'User-Agent': 'TreeSeaReader/1.0 (local event asset index)'}), timeout=25) as response:
        return response.read()


def collect():
    folder = ROOT/'assets/events'
    folder.mkdir(parents=True, exist_ok=True)
    scenes = Scenes()
    scenes.feed(fetch(PAGE).decode('utf-8'))

    def download(row):
        stem, names = row
        filename = stem+'.png'
        digest = md5(filename.encode()).hexdigest()
        url = f'https://media.prts.wiki/{digest[0]}/{digest[:2]}/{filename}'
        image = cv2.imdecode(np.frombuffer(fetch(url), np.uint8), cv2.IMREAD_GRAYSCALE)
        if image is None:
            raise ValueError(url)
        scale = min(1., 768/max(image.shape))
        image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        path = folder/(stem+'.jpg')
        cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tofile(path)
        return dict(file=path.name, names=sorted(names), source_url=url)

    with ThreadPoolExecutor(max_workers=4) as pool:
        assets = list(pool.map(download, sorted(scenes.art.items())))
    if not assets:
        raise ValueError('No event artwork found')
    (folder/'manifest.json').write_text(json.dumps(dict(source=PAGE, assets=assets), ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'{len(assets)} event images')


if __name__ == '__main__':
    collect()
