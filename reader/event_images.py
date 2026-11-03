"""Match event artwork using image features and a consistent geometric fit."""
import json

import cv2
import numpy as np

from .catalog import ROOT


class EventImages:
    def __init__(self, folder=ROOT/'assets/events'):
        self.folder = folder
        self.sift = cv2.SIFT_create(nfeatures=900)
        self.references = None

    def load(self):
        self.references = []
        manifest = self.folder/'manifest.json'
        if not manifest.exists():
            return
        for row in json.loads(manifest.read_text(encoding='utf-8'))['assets']:
            path = self.folder/row['file']
            if not path.is_file():
                continue
            image = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_GRAYSCALE)
            if image is None:
                continue
            points, features = self.sift.detectAndCompute(image, None)
            if features is not None and len(features) >= 12:
                self.references.append((row, points, features))

    def match(self, image, names, title=None):
        if self.references is None:
            self.load()
        if not self.references:
            return None
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        gray = gray[round(.06*gray.shape[0]):round(.90*gray.shape[0])]
        points, features = self.sift.detectAndCompute(gray, None)
        if features is None or len(features) < 12:
            return None
        index = cv2.FlannBasedMatcher(dict(algorithm=1, trees=4), dict(checks=40))
        index.add([features])
        index.train()
        fits = []
        for row, reference_points, reference_features in self.references:
            known = [name for name in row['names'] if name in names]
            if not known:
                continue
            pairs = index.knnMatch(reference_features, k=2)
            good = [a for a, b in pairs if a.distance < .73*b.distance]
            if len(good) < 12:
                continue
            src = np.float32([reference_points[m.queryIdx].pt for m in good])
            dst = np.float32([points[m.trainIdx].pt for m in good])
            affine, inliers = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC,
                                                         ransacReprojThreshold=4)
            if affine is None or inliers is None:
                continue
            keep = inliers.ravel().astype(bool)
            count = int(keep.sum())
            spread = np.ptp(dst[keep], axis=0) if count else (0, 0)
            if (count >= 12 and count/len(good) >= .60
                    and spread[0] >= gray.shape[1]*.07 and spread[1] >= gray.shape[0]*.08):
                fits.append((count, row, known))
        fits.sort(key=lambda fit: -fit[0])
        if not fits or (len(fits) > 1 and fits[0][0] < 1.2*fits[1][0] and fits[0][2] != fits[1][2]):
            return None
        count, row, known = fits[0]
        # Shared illustrations need the exact event title to disambiguate.
        if title:
            if title['name'] not in known:
                return {'conflict': True}
            name = title['name']
        elif len(known) == 1 and len(row['names']) == 1:
            name = known[0]
        else:
            return None
        return {'id': names[name], 'name': name, 'inliers': count, 'source_url': row['source_url'],
                'method': 'image_and_title' if title else 'image'}
