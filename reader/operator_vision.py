"""Outfit-aware avatar evidence mapped to canonical operator IDs."""
import json
from pathlib import Path
import cv2
import numpy as np
from .catalog import ROOT


def colour_descriptor(image):
    hsv=cv2.cvtColor(image,cv2.COLOR_BGR2HSV)
    histogram=cv2.calcHist([hsv],[0,1],None,[12,4],[0,180,0,256])
    return cv2.normalize(histogram,None).flatten()


class OperatorVision:
    def __init__(self,folder=ROOT/'assets/operators'):
        self.folder=Path(folder)
        self.assets=None
        self.sift=cv2.SIFT_create(nfeatures=160)
        self.features={}

    def load(self):
        self.assets=[]
        manifest=self.folder/'manifest.json'
        if not manifest.exists():
            return
        for row in json.loads(manifest.read_text(encoding='utf-8'))['assets']:
            path=self.folder/row['file']
            if not path.is_file():
                continue
            image=cv2.imdecode(np.fromfile(path,np.uint8),cv2.IMREAD_COLOR)
            if image is not None:
                self.assets.append((row,colour_descriptor(image)))

    def match(self,image,identities=()):
        if self.assets is None:
            self.load()
        if not self.assets:
            return None
        descriptor=colour_descriptor(image)
        ranked=sorted(self.assets,key=lambda row:-float(np.dot(row[1],descriptor)))
        if identities:
            ranked=[row for row in ranked if row[0]['operator_id'] in identities]
        ranked=ranked[:40]
        gray=cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)
        points,features=self.sift.detectAndCompute(gray,None)
        if features is None or len(features)<8:
            return None
        matcher=cv2.BFMatcher()
        fits=[]
        for row,_ in ranked:
            filename=row['file']
            if filename not in self.features:
                avatar=cv2.imdecode(np.fromfile(self.folder/filename,np.uint8),cv2.IMREAD_GRAYSCALE)
                self.features[filename]=self.sift.detectAndCompute(avatar,None)
            reference,encoded=self.features[filename]
            if encoded is None or len(encoded)<8:
                continue
            pairs=matcher.knnMatch(encoded,features,k=2)
            good=[a for a,b in pairs if a.distance<.65*b.distance]
            if len(good)<6:
                continue
            source=np.float32([reference[m.queryIdx].pt for m in good])
            target=np.float32([points[m.trainIdx].pt for m in good])
            transform,inliers=cv2.estimateAffinePartial2D(source,target,method=cv2.RANSAC,ransacReprojThreshold=3)
            count=int(inliers.sum()) if transform is not None and inliers is not None else 0
            if count>=6 and count/len(good)>=.70:
                fits.append((count,row))
        fits.sort(key=lambda fit:-fit[0])
        if not fits:
            return None
        best,row=fits[0]
        competing=max((score for score,other in fits if other['operator_id']!=row['operator_id']),default=0)
        if best<competing+3:
            return None
        return dict(row,inliers=best)
