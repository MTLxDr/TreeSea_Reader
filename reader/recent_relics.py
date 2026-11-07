"""Recognize acquisition-strip icons with a coarse shortlist and masked colour match."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import cv2
import numpy as np
from .catalog import ROOT


class RecentRelicRecognizer:
    def __init__(self,catalog,folder=ROOT/'assets/relics'):
        self.catalog=catalog;self.folder=Path(folder);self.templates=None;self.cache={}

    def load(self):
        self.templates=[]
        manifest=self.folder/'manifest.json'
        if not manifest.exists():return
        rows={r['id']:r for r in self.catalog.inventory['relics']}
        for asset in json.loads(manifest.read_text(encoding='utf-8'))['assets']:
            if asset['id'] not in rows:continue
            path=ROOT/asset['path']
            if not path.exists():continue
            image=cv2.imdecode(np.fromfile(path,np.uint8),cv2.IMREAD_UNCHANGED)
            if image is None or image.ndim!=3 or image.shape[2]!=4:continue
            ys,xs=np.where(image[:,:,3]>80)
            if not len(xs):continue
            image=image[ys.min():ys.max()+1,xs.min():xs.max()+1]
            variants=[]
            for size in (56,64,72,80):
                scale=size/max(image.shape[:2])
                sample=cv2.resize(image,(max(4,round(image.shape[1]*scale)),max(4,round(image.shape[0]*scale))))
                mask=(sample[:,:,3]>100).astype(np.uint8)
                grey=cv2.cvtColor(sample[:,:,:3],cv2.COLOR_BGR2GRAY);grey[mask==0]=0
                if min(sample.shape[:2])<10:continue
                variants.append((sample[:,:,:3].copy(),mask,grey,size))
            self.templates.append((rows[asset['id']],asset,variants))

    def match_patch(self, patch):
        if self.templates is None:self.load()
        if not self.templates or min(patch.shape[:2]) < 80:return None
        patch_grey=cv2.cvtColor(patch,cv2.COLOR_BGR2GRAY);coarse=[]
        for index,(_,_,variants) in enumerate(self.templates):
            score=-1.
            for colour,mask,template,size in variants:
                if size not in (64,72):continue
                response=cv2.matchTemplate(patch_grey,template,cv2.TM_CCOEFF_NORMED)
                score=max(score,float(cv2.minMaxLoc(response)[1]))
            coarse.append((score,index))
        ranked=[]
        for coarse_score,index in sorted(coarse,reverse=True)[:12]:
            item,asset,variants=self.templates[index];best=-1.
            for template,mask,_,size in variants:
                if template.shape[0]>patch.shape[0] or template.shape[1]>patch.shape[1]:continue
                response=cv2.matchTemplate(patch,template,cv2.TM_CCOEFF_NORMED,mask=mask)
                response[~np.isfinite(response)]=-1
                best=max(best,float(cv2.minMaxLoc(response)[1]))
            ranked.append((best,coarse_score,item,asset))
        ranked.sort(key=lambda r:-r[0]);score,coarse_score,item,asset=ranked[0]
        if score<.66:return None
        margin=score-ranked[1][0] if len(ranked)>1 else 1.
        accepted=score>=.80 and coarse_score>=.70 and margin>=.08
        return item,asset,ranked,score,margin,accepted

    def analyze(self,image):
        height,width=image.shape[:2]
        if height<300 or width<500:return []
        y0,x0,x1=round(.90*height),round(.15*width),round(.43*width)
        strip=image[y0:,x0:x1]
        fingerprint=hashlib.sha256(strip.tobytes()).hexdigest()
        if fingerprint in self.cache:return deepcopy(self.cache[fingerprint])
        if self.templates is None:self.load()
        if not self.templates:return []
        scale=100/strip.shape[0]
        strip=cv2.resize(strip,(round(strip.shape[1]*scale),100))
        grey=cv2.cvtColor(strip,cv2.COLOR_BGR2GRAY)
        circles=cv2.HoughCircles(grey,cv2.HOUGH_GRADIENT,dp=1,minDist=48,param1=80,param2=28,minRadius=25,maxRadius=46)
        if circles is None:return []
        entries=[]
        for cx,cy,radius in sorted(circles[0],key=lambda p:p[0])[:6]:
            left,top=max(0,round(cx)-43),max(0,round(cy)-46)
            patch=strip[top:min(100,top+90),left:min(strip.shape[1],left+86)]
            if min(patch.shape[:2])<80:continue
            match=self.match_patch(patch)
            if match is None:continue
            item,asset,ranked,score,margin,accepted=match
            entry=dict(deepcopy(item),kind='relics',fields={'stacks':{'value':None,'source':'recent_icon','confidence':0.,'manual':False}},
                raw='近期获得图标',match_score=round(score,4),match_margin=round(margin,4),confidence=round(score,4),
                needs_review=not accepted,icon_source=asset['source_url'],page_fingerprint=fingerprint,
                box=[[round(x0+left/scale),round(y0+top/scale)],[round(x0+(left+patch.shape[1])/scale),round(y0+top/scale)],
                     [round(x0+(left+patch.shape[1])/scale),round(y0+(top+patch.shape[0])/scale)],[round(x0+left/scale),round(y0+(top+patch.shape[0])/scale)]],
                candidates=[dict(id=row[2]['id'],name=row[2]['name'],score=round(row[0],4)) for row in ranked[:3]])
            entries.append(entry)
        unique={}
        for entry in entries:
            if entry['id'] not in unique or entry['match_score']>unique[entry['id']]['match_score']:unique[entry['id']]=entry
        result=list(unique.values());self.cache[fingerprint]=deepcopy(result)
        if len(self.cache)>4:self.cache.pop(next(iter(self.cache)))
        return result
