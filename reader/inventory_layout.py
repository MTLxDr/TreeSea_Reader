"""Read the regions of the in-run inventory overlays independently."""
import re
import unicodedata

import cv2
import numpy as np


def relic_titles(image):
    """Locate coloured title text independently of columns and scroll position."""
    h, w = image.shape[:2]
    b, g, r = [c.astype(np.int16) for c in cv2.split(image)]
    mask = ((g > 92) & (g-r > 26) & (g-b > 9)).astype(np.uint8)
    mask[:round(h*.025)] = 0
    mask[round(h*.87):] = 0
    mask[:, :round(w*.08)] = 0
    rows = np.flatnonzero(mask.sum(axis=1) > w*.009)
    bands = np.split(rows, np.where(np.diff(rows) > max(3, h*.005))[0]+1)
    boxes = []
    for band in bands:
        if not len(band) or not h*.009 <= band[-1]-band[0] <= h*.037:
            continue
        strip = mask[band[0]:band[-1]+1]
        xs = np.flatnonzero(strip.sum(axis=0) > 1)
        groups = np.split(xs, np.where(np.diff(xs) > max(8,.48*(band[-1]-band[0])))[0]+1)
        for group in groups:
            if len(group) < w*.025 or group[-1]-group[0] > w*.24:
                continue
            boxes.append((max(0,(group[0]-5)/w), max(0,(band[0]-5)/h),
                          min(1,(group[-1]+6)/w), min(1,(band[-1]+6)/h)))
    aligned=[box[1] for box in boxes if any(abs(box[1]-other[1])<.015 and abs(box[0]-other[0])>.12 for other in boxes)]
    if aligned:
        first=min(aligned)
        boxes=[box for box in boxes if not (box[1]<.20 and box[1]<first-.04)]
    return sorted(boxes, key=lambda box: (round(box[1]*h/18), box[0]))


def operator_cards(image):
    """Detect the dark roster cards against the light roster background."""
    h, w = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    mask = (gray < 120).astype(np.uint8)*255
    mask[:, :round(w*.28)] = 0
    mask[:round(h*.07)] = 0
    mask[round(h*.88):] = 0
    kernel = np.ones((max(3,round(h*.012)),max(5,round(w*.012))),np.uint8)
    mask = cv2.morphologyEx(mask,cv2.MORPH_CLOSE,kernel)
    contours, _ = cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for contour in contours:
        x,y,cw,ch = cv2.boundingRect(contour)
        if not (w*.13 < cw < w*.48 and h*.08 < ch < h*.24 and 2 < cw/ch < 5.5):
            continue
        if cv2.contourArea(contour)/(cw*ch) < .58:
            continue
        boxes.append((x/w,y/h,(x+cw)/w,(y+ch)/h))
    return sorted(boxes,key=lambda box:(round(box[1]*h/30),box[0]))


def crop(image, box):
    h, w = image.shape[:2]
    x0, y0, x1, y1 = box
    return image[round(y0*h):round(y1*h), round(x0*w):round(x1*w)]


def layout_kind(image, tokens):
    from .inventory import center, normalized

    h, w = image.shape[:2]
    if h < 300 or w < 500:
        return None
    if len(relic_titles(image))>=3 and float(np.median(image))<120:
        return "relics"
    text = [(normalized(t["text"]), center(t)[0]/w, center(t)[1]/h) for t in tokens]
    close = [x for name, x, y in text if name == "收起" and y > .90]
    if any(name.startswith("零件箱容量") and x < .25 and .37 < y < .67
           for name, x, y in text):
        return "parts"
    if operator_cards(image):
        roster = crop(image, (.30, .02, .88, .11))
        if np.median(roster) > 145:
            return "operators"
    if any(.07 < x < .23 for x in close):
        return "relics"
    return None


def boxes_for(image, kind, tokens):
    """Return name boxes and optional detail regions in image fractions."""
    from .inventory import center, normalized

    h, w = image.shape[:2]
    if kind == "relics":
        for box in relic_titles(image):
            yield box, {}
    elif kind == "operators":
        for x0,y0,x1,y1 in operator_cards(image):
            cw,ch=x1-x0,y1-y0
            yield (x0+cw*.56,y1-ch*.29,x1-cw*.015,y1-ch*.025), {
                "level": (x0+cw*.115,y1-ch*.24,x0+cw*.175,y1-ch*.065),
                "card": (x0,y0,x1,y1)}
    elif kind == "parts":
        # Use the layout's fixed top and row pitch. Horizontal scrolling can
        # shift the columns, so anchor the name boxes on visible OCR labels.
        from .inventory import rect

        for token in tokens:
            x, y = center(token)
            x, y = x/w, y/h
            name = normalized(token["text"])
            if not (.24 < x < .99 and .26 < y < .88 and 2 <= len(name) <= 12):
                continue
            row = round((y-.303)/.181)
            if not 0 <= row < 4 or abs(y-(.303+row*.181)) > .033:
                continue
            column = round((x-.29)/.337)
            start = .242 + .337*column
            if not start <= x <= start+.097:
                continue
            a, b, c, d = rect(token)
            bottom = .33 + .181*row
            detail = {}
            if start+.323 <= 1:
                detail["valuation"] = (start+.243, bottom-.156, start+.301, bottom-.126)
                detail["description"] = (start+.10, bottom-.119, start+.324, bottom-.024)
            yield (max(0, min(a/w-.012, start)), b/h-.003, min(1, c/w+.004), d/h+.003), detail


def line_token(ocr, image, box):
    if not hasattr(ocr, "line"):
        return None
    patch = crop(image, box)
    if min(patch.shape[:2]) < 4:
        return None
    if patch.shape[0] < 44:
        scale = 44/patch.shape[0]
        patch = cv2.resize(patch, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    text, score = ocr.line(patch)
    if not text:
        return None
    h, w = image.shape[:2]
    a, b, c, d = box
    return dict(text=text, score=score, box=[[a*w,b*h],[c*w,b*h],[c*w,d*h],[a*w,d*h]])


def detail_fields(ocr, image, details, page_tokens=()):
    from .inventory import center, field

    fields = {}
    for key in ("level", "valuation"):
        if key not in details:
            continue
        box = details[key]
        token = line_token(ocr, image, box)
        if not token:
            continue
        numbers = re.findall(r"\d+", token["text"])
        if key == "valuation" and numbers and len(numbers) == 1 and len(numbers[0]) >= 2:
            # The last number is printed in green when there is also a base value.
            patch = crop(image, box)
            b, g, r = [c.astype(np.int16) for c in cv2.split(patch)]
            mask = (g-r > 34) & (g-b > 10) & (g > 100)
            ys, xs = np.where(mask)
            if len(xs) > 12:
                a, y0, c, y1 = box
                left = a + (c-a)*max(0, xs.min()-2)/patch.shape[1]
                coloured = line_token(ocr, image, (left, y0, c, y1))
                if coloured and re.fullmatch(r"\d+", coloured["text"].strip()):
                    token = coloured
                    numbers = [coloured["text"].strip()]
                else:
                    continue
        if numbers and (key == "valuation" or 1 <= int(numbers[-1]) <= 90):
            fields[key] = field(int(numbers[-1]), token)
            fields[key]["source"] = "card_region_ocr"
    if "description" in details:
        x0, y0, x1, y1 = details["description"]
        height, width = image.shape[:2]
        tokens = [t for t in page_tokens if x0 <= center(t)[0]/width <= x1 and y0 <= center(t)[1]/height <= y1]
        if not tokens:
            tokens = ocr.read(crop(image, details["description"]))
        tokens.sort(key=lambda t: min(p[1] for p in t["box"]))
        text = "".join(unicodedata.normalize("NFKC", t["text"]).replace(" ", "") for t in tokens)
        match = re.search(r"可(?:移动|使用)(\d{1,2})次", text)
        if match:
            fields["uses"] = field(int(match[1]), {"score": min(t["score"] for t in tokens), "text": text})
            fields["uses"]["source"] = "card_region_ocr"
    return fields
