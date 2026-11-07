"""Locate resident badges independently of map OCR and node types."""
from pathlib import Path
import cv2
import numpy as np

from .catalog import ROOT


class ResidentVision:
    def __init__(self, path=ROOT/'assets/nodes/mark-1.png'):
        raw = cv2.imdecode(np.fromfile(Path(path),np.uint8),cv2.IMREAD_COLOR)
        self.icon = cv2.cvtColor(raw,cv2.COLOR_BGR2GRAY) if raw is not None else None

    def detect(self, image):
        if self.icon is None:
            return []
        h,w = image.shape[:2]
        gray = cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)
        y0,y1 = round(h*.13),round(h*.88)
        region = gray[y0:y1]
        hits = []
        for size in range(max(12,round(w*.015)),max(15,round(w*.032))):
            template = cv2.resize(self.icon,(size,size))
            mask = np.zeros_like(template)
            cv2.circle(mask,(size//2,size//2),round(size*.34),255,-1)
            response = cv2.matchTemplate(region,template,cv2.TM_CCOEFF_NORMED,mask=mask)
            response[~np.isfinite(response)] = -1
            for _ in range(12):
                _,score,_,point = cv2.minMaxLoc(response)
                if score < .79:
                    break
                x,y = point
                hits.append(dict(center=[x+size/2,y+y0+size/2],size=size,score=float(score)))
                response[max(0,y-size):y+size,max(0,x-size):x+size] = -1
        selected = []
        for hit in sorted(hits,key=lambda row:-row['score']):
            if all(np.linalg.norm(np.subtract(hit['center'],old['center'])) > max(hit['size'],old['size'])*.65
                   for old in selected):
                selected.append(hit)
        return selected


def attach_residents(region, markers):
    if not region.get('nodes'):
        return
    spacing = region['grid']['spacing']
    for marker in markers:
        center = np.asarray(marker['center'])
        expected = center + np.array([-.22,.23])*spacing
        node = min(region['nodes'],key=lambda n:np.linalg.norm(np.subtract(n['pixel'],expected)))
        if np.linalg.norm(np.subtract(node['pixel'],expected)) <= spacing*.32:
            node.update(resident=True,resident_score=marker['score'],
                        resident_marker=marker,resident_evidence='右上角居民标记')
