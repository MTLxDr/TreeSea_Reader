from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from difflib import SequenceMatcher
import json
import math
from pathlib import Path
import re
import time

import cv2
import numpy as np

from .catalog import Catalog, NODE_NAMES, ROOT, distances
from .ocr import TextReader
from .regions import partition_hypotheses, side_candidates, zoom_control
from .resident_tracking import infer_position, update_resident_constraints


def load_image(path):
    path = Path(path)
    if not path.is_file() or path.stat().st_size > 50_000_000:
        raise ValueError("图片不存在或超过50 MB。")
    image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None or min(image.shape[:2]) < 200:
        raise ValueError("无法读取图片，或图片尺寸太小。")
    if image.shape[0] * image.shape[1] > 40_000_000:
        raise ValueError("图片超过4000万像素，请先裁剪到游戏窗口。")
    return image


def bounds(token):
    box = np.asarray(token["box"])
    return float(box[:, 0].min()), float(box[:, 1].min()), float(box[:, 0].max()), float(box[:, 1].max())


def clean(text):
    return re.sub(r"[^\w\u4e00-\u9fff]", "", text)


def label_type(text, names):
    text = clean(text)
    ranked = [(SequenceMatcher(None, text, clean(name)).ratio(), kind) for kind, name in names.items()]
    if not ranked:
        return None, 0.0
    similarity, kind = max(ranked)
    # Two-character labels must match exactly: short OCR fragments otherwise
    # accidentally turn unrelated UI labels into combat nodes.
    limit = 1.0 if len(clean(names[kind])) <= 2 else .74
    return (kind, similarity) if similarity >= limit else (None, 0.0)


def anchors_from_text(tokens, width, height, names):
    result = []
    for token in tokens:
        x0, y0, x1, y1 = bounds(token)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        if not (.06 * width < cx < .96 * width and .16 * height < cy < .88 * height):
            continue
        kind, similarity = label_type(token["text"], names)
        if kind is None or token["score"] < .65:
            continue
        # Text sits below the icon. Larger exit icons have a lower caption.
        offset = 1.5 if kind in {"danger-end", "danger-path", "danger-enemy"} else .75
        result.append({"x": cx, "y": y0 - offset * (y1 - y0), "type": kind,
                       "text": token["text"], "confidence": token["score"] * similarity,
                       "text_box": token["box"]})
    return result


def grid_hypotheses(anchors, width, max_shape=(10, 5), robust=False):
    """Fit a regular lattice to observed labels; no fixed screen coordinates."""
    if len(anchors) < 4:
        return []
    xy = np.array([[a["x"], a["y"]] for a in anchors], dtype=float)
    choices = Counter()
    for dim in range(2):
        for i in range(len(xy)):
            for j in range(i):
                diff = abs(xy[i, dim] - xy[j, dim])
                for divisor in range(1, 5):
                    value = diff / divisor
                    if .035 * width <= value <= .20 * width:
                        choices[round(value)] += 1
    results = []
    for spacing, votes in choices.most_common(60):
        start = xy.min(axis=0)
        slots = np.rint((xy - start) / spacing).astype(int)
        origin = np.median(xy - slots * spacing, axis=0)
        residuals = np.linalg.norm(xy - (origin + slots * spacing), axis=1) / spacing
        values = np.exp(-np.square(residuals / .16))
        fit = float(np.mean(np.sort(values)[-max(4, math.ceil(len(values) * .65)):])) if robust else float(np.mean(values))
        span = slots.max(axis=0) + 1
        if fit < .65 or np.any(span > np.asarray(max_shape)):
            continue
        candidate = {"spacing": float(spacing), "origin": origin.tolist(), "slots": slots.tolist(),
                     "fit": fit, "span": span.tolist()}
        results.append(candidate)
    results.sort(key=lambda r: (round(r["fit"], 3), r["spacing"]), reverse=True)
    selected = []
    for candidate in results:
        if any(abs(candidate["spacing"] - old["spacing"]) < old["spacing"] * .06 for old in selected):
            continue
        selected.append(candidate)
        if len(selected) == 3:
            break
    return selected


def edge_strength(image, a, b, spacing):
    """Local ribbon evidence, excluding both node halos and caption regions.

    A weak line produces weak evidence, not a definitive absent edge.
    """
    a, b = np.array(a), np.array(b)
    direction = b - a
    length = np.linalg.norm(direction)
    if length < 1:
        return 0.0
    normal = np.array([-direction[1], direction[0]]) / length
    ts = np.linspace(.30, .70, 17)
    offsets = np.array([-.14, -.10, -.035, 0, .035, .10, .14]) * spacing
    points = a[None, None, :] + ts[:, None, None] * direction + offsets[None, :, None] * normal
    pixels = cv2.remap(image, points[:, :, 0].astype(np.float32), points[:, :, 1].astype(np.float32),
                       cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT).astype(float) / 255
    value = pixels.max(axis=2)
    saturation = (value - pixels.min(axis=2)) / np.maximum(value, .03)
    core_v = value[:, 2:5].mean(axis=1)
    flank_v = value[:, [0, 1, 5, 6]].mean(axis=1)
    core_s = saturation[:, 2:5].mean(axis=1)
    flank_s = saturation[:, [0, 1, 5, 6]].mean(axis=1)
    signal = np.abs(core_v - flank_v) + .13 * np.maximum(flank_s - core_s, 0)
    # Median suppresses isolated particle highlights crossing a possible edge.
    return float(np.clip(np.median(signal) / .11, 0, 1))


def match_templates(image, anchors, templates, floor=None):
    eligible = [t for t in templates if floor is None or t.get("floor") == floor]
    if not eligible:
        return []
    max_shape = tuple(max(t["gridShape"][i] for t in eligible) for i in range(2))
    hypotheses = grid_hypotheses(anchors, image.shape[1], max_shape)
    candidates = []
    evidence_cache = {}
    for template in templates:
        if floor is not None and template.get("floor") != floor:
            continue
        occupied = {tuple(p) for p in template["occupiedSlots"]}
        terminals = {tuple(p) for p in template.get("terminalSlots", [])}
        width, height = template["gridShape"]
        best = None
        for grid in hypotheses:
            spanx, spany = grid["span"]
            if spanx > width or spany > height:
                continue
            for shiftx in range(width - spanx + 1):
                for shifty in range(height - spany + 1):
                    slots = [(x + shiftx, y + shifty) for x, y in grid["slots"]]
                    weights = [a["confidence"] for a in anchors]
                    coverage = sum(w for w, slot in zip(weights, slots) if slot in occupied) / sum(weights)
                    exit_pairs = [(slot, a) for slot, a in zip(slots, anchors) if a["type"] in {"danger-end", "danger-path", "danger-enemy"}]
                    terminal_fit = sum(slot in terminals for slot, _ in exit_pairs) / len(exit_pairs) if exit_pairs else .5
                    base = .68 * coverage * grid["fit"] + .24 * terminal_fit
                    origin = [grid["origin"][0] - shiftx * grid["spacing"], grid["origin"][1] - shifty * grid["spacing"]]
                    projected = [(origin[0] + x * grid["spacing"], origin[1] + y * grid["spacing"]) for x, y in occupied]
                    outside = sum(not (0 <= x < image.shape[1] and .10 * image.shape[0] <= y < .90 * image.shape[0]) for x, y in projected)
                    base -= .15 * outside / len(projected)
                    row = {"template_id": template["template_id"], "floor": template["floor"], "geometry_score": coverage * grid["fit"],
                           "terminal_score": terminal_fit, "base": base, "origin": origin,
                           "spacing": grid["spacing"], "anchor_slots": [list(p) for p in slots],
                           "node_count": len(occupied), "edge_count": len(template["edges"])}
                    if best is None or base > best["base"]:
                        best = row
        if best:
            candidates.append(best)
    candidates.sort(key=lambda r: r["base"], reverse=True)
    # Only spatially plausible templates need local edge sampling.
    shortlist = candidates[:12]
    template_by_id = {t["template_id"]: t for t in templates}
    for row in shortlist:
        template = template_by_id[row["template_id"]]
        occupied = {tuple(p) for p in template["occupiedSlots"]}
        expected = {tuple(sorted(map(tuple, edge))) for edge in template["edges"]}
        evidence = {}
        for a in occupied:
            for b in ((a[0] + 1, a[1]), (a[0], a[1] + 1)):
                if b not in occupied:
                    continue
                edge = tuple(sorted((a, b)))
                key = (round(row["spacing"], 2), *(round(v, 2) for v in row["origin"]), edge)
                if key not in evidence_cache:
                    def point(p):
                        return [row["origin"][i] + p[i] * row["spacing"] for i in range(2)]
                    evidence_cache[key] = edge_strength(image, point(a), point(b), row["spacing"])
                evidence[edge] = evidence_cache[key]
        present = [value for edge, value in evidence.items() if edge in expected]
        absent = [value for edge, value in evidence.items() if edge not in expected]
        contrast = (np.mean(present) if present else 0) - (np.mean(absent) if absent else 0)
        edge_fit = float(np.clip(.5 + contrast, 0, 1))
        row["edge_score"] = edge_fit
        row["edge_contrast"] = float(contrast)
        row["score"] = float(np.clip(row["base"] + .08 * edge_fit, 0, 1))
        del row["base"]
    shortlist.sort(key=lambda r: r["score"], reverse=True)
    return shortlist[:5]


class Recognizer:
    def __init__(self, catalog=None, ocr=None):
        self.catalog = catalog or Catalog()
        if hasattr(self.catalog,'include_exploration_items'):self.catalog.include_exploration_items()
        self.ocr = ocr or TextReader()
        from .recent_relics import RecentRelicRecognizer
        self.recent_relics=RecentRelicRecognizer(self.catalog)
        from .event_images import EventImages
        self.event_images = EventImages()
        self.names = dict(NODE_NAMES)
        self.names['resident-occupant'] = '流窜居民'
        manifest = ROOT / "assets/nodes/manifest.json"
        if manifest.exists():
            for asset in json.loads(manifest.read_text(encoding="utf-8"))["assets"]:
                if asset.get("kind") == "node":
                    self.names.setdefault(asset["type"], asset["name"])

    def locate_overlays(self, image, tokens, region):
        if not region.get('nodes'):return
        spacing=region['grid']['spacing'];origin=region['grid']['origin']
        # English locator text stays visible above the avatar at multiple zooms.
        explicit=None
        for token in tokens:
            if clean(token['text']).lower() in {'youarehere','当前位置','当前所在位置'} and token['score']>=.65:
                x0,y0,x1,y1=bounds(token);point=((x0+x1)/2,(y0+y1)/2+spacing*.34)
                nearest=min(region['nodes'],key=lambda n:math.dist(n['pixel'],point))
                if math.dist(nearest['pixel'],point)<spacing*.6:explicit=nearest['slot']
        if explicit is None:
            figures=[]
            for node in region['nodes']:
                if node.get('evidence') not in {'template_only','small_marker_candidate'}:continue
                x,y=map(round,node['pixel']);radius=round(spacing*.55)
                a,b=max(0,x-radius),max(0,y-radius)
                patch=image[b:y+radius,a:x+radius]
                if not patch.size:continue
                mask=((patch.min(axis=2)>175)&(patch.max(axis=2)-patch.min(axis=2)<85)).astype(np.uint8)
                _,_,stats,centers=cv2.connectedComponentsWithStats(mask)
                for stat,center in zip(stats[1:],centers[1:]):
                    _,_,w,h,area=stat;dx=(center[0]+a-x)/spacing;dy=(center[1]+b-y)/spacing
                    if .25*spacing<h<.7*spacing and .04*spacing<w<.28*spacing and h/w>1.9 and area>.008*spacing**2 and -.45<dx<.18 and abs(dy)<.3:
                        figures.append(node['slot']);break
            if len(figures)==1:explicit=figures[0]
        asset=ROOT/'assets/nodes/mark-1.png'
        icon=cv2.imdecode(np.fromfile(asset,dtype=np.uint8),cv2.IMREAD_UNCHANGED) if asset.exists() else None
        gray=cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)
        for node in region['nodes']:
            if node['type']=='resident-occupant':
                node.update(type='unobserved',label=self.names['unobserved'],resident=True,evidence='resident_caption')
            if icon is None:continue
            x,y=map(round,node['pixel'])
            # Resident badges sit to the upper right of the node art.
            x0=max(0,round(x-.03*spacing));x1=round(x+.64*spacing)
            y0=max(0,round(y-.68*spacing));y1=round(y+.09*spacing)
            patch=gray[y0:y1,x0:x1]
            radius=round(.24*spacing)
            centered=gray[max(0,y-radius):y+radius,max(0,x-radius):x+radius]
            best=0.
            for ratio in (.20,.27,.34,.41):
                size=max(12,round(spacing*ratio));template=cv2.resize(icon,(size,size))
                if min(patch.shape[:2],default=0)<size:continue
                template=cv2.cvtColor(template[:,:,:3],cv2.COLOR_BGR2GRAY)
                if template.std()<5:continue
                score=cv2.matchTemplate(patch,template,cv2.TM_CCOEFF_NORMED).max()
                best=max(best,float(score))
                if min(centered.shape[:2],default=0)>=size:
                    centered_score=float(cv2.matchTemplate(centered,template,cv2.TM_CCOEFF_NORMED).max())
                    if centered_score>=.93:best=max(best,centered_score)
            if best>=.86 and node['type'] not in {'danger-end','danger-path','rogue-trader','secret-trader','fate-choice'}:
                node.update(resident=True,resident_score=best)
        infer_position(region,explicit)
        update_resident_constraints(region)

    def _field(self, value, confidence, source="ocr", raw=None):
        return {"value": value, "confidence": float(confidence), "source": source, "raw": raw, "manual": False}

    def refresh_probabilities(self,result):
        from .probability import refresh_map_probabilities
        refresh_map_probabilities(result,self.catalog)

    def read_fields(self, image, tokens):
        height, width = image.shape[:2]
        fields = {}
        for token in tokens:
            x0, y0, x1, y1 = bounds(token)
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            text = token["text"].strip()
            if .30 * width < cx < .75 * width and cy < .14 * height:
                for name, floor in (self.catalog.floor_names|{'盲目者所见':6}).items():
                    if SequenceMatcher(None, clean(text), name).ratio() >= .8:
                        fields["floor"] = self._field(floor, token["score"], raw=text)
                        fields["region"] = self._field(name, token["score"], raw=text)
                roman=re.match(r'^[（(]\s*(VI|IV|III|II|V|I)\s*[）)]',text)
                if roman and 'floor' not in fields:
                    fields['floor']=self._field({'I':1,'II':2,'III':3,'IV':4,'V':5,'VI':6}[roman[1]],token['score'],raw=text)
            if cx > .84 * width and .10 * height < cy < .23 * height and re.fullmatch(r"\d{1,2}", text):
                fields["action_points"] = self._field(int(text), token["score"], raw=text)
            if cx > .90 * width and cy < .085 * height and text.isdigit():
                fields["ingots"] = self._field(int(text), token["score"], raw=text)
            if .50 * width < cx < .72 * width and cy > .90 * height:
                match = re.fullmatch(r"(\d+)\s*[/／]\s*(\d+)", text)
                if match:
                    fields["parts_count"] = self._field(int(match[1]), token["score"], raw=text)
                    fields["parts_capacity"] = self._field(int(match[2]), token["score"], raw=text)
        # Numeric subregions are anchored to actual OCR labels, not fixed pixels.
        for token in tokens:
            x0, y0, x1, y1 = bounds(token)
            tw, th = x1 - x0, y1 - y0
            text = clean(token["text"])
            specs = []
            if text == "目标生命值" and y0 < height * .12:
                specs = [("life", (x0 - .08 * tw, y1, x0 + .82 * tw, y1 + 1.45 * th)),
                         ("shield", (x1 + .10 * tw, y1, x1 + .52 * tw, y1 + 1.40 * th))]
            elif text == "收藏品" and y0 > height * .85:
                # Keep the full width: a narrow single-digit crop truncates
                # legitimate two/three-digit collection counts.
                specs = [("relic_count", (x0, y0 - 1.4 * th, x1, y0 + .08 * th))]
            for key, rect in specs:
                a, b, c, d = map(int, rect)
                crop = image[max(0, b):min(height, d), max(0, a):min(width, c)]
                if not crop.size:
                    continue
                crop = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
                raw, score = self.ocr.line(crop)
                raw = raw.strip().replace(" ", "").replace("／", "/")
                normalized = raw
                if key == "relic_count":
                    # The gray pictogram behind the number can be read as K.
                    # Retain raw text and discount normalization evidence.
                    if re.fullmatch(r"[Kk]?\d{1,3}", raw):
                        normalized = raw.lstrip("Kk")
                    elif re.fullmatch(r"[Kk]?[oO〇]", raw):
                        normalized, score = "0", score * .85
                match = re.fullmatch(r"(\d+)/(\d+)", raw) if key == "life" else re.fullmatch(r"\d{1,3}", raw)
                if key == "relic_count":
                    match = re.fullmatch(r"\d{1,3}", normalized)
                threshold = .65 if key == "relic_count" else .75
                if match and score >= threshold:
                    if key == "life":
                        fields["life"] = self._field(int(match[1]), score, raw=raw)
                        fields["life_max"] = self._field(int(match[2]), score, raw=raw)
                    else:
                        fields[key] = self._field(int(normalized), score, raw=raw)
        return fields

    def _small_marker(self, image, x, y, spacing):
        radius = max(4, round(.13 * spacing))
        x, y = round(x), round(y)
        patch = image[max(0, y - radius):y + radius + 1, max(0, x - radius):x + radius + 1]
        if patch.shape[:2] != (2 * radius + 1, 2 * radius + 1):
            return 0.0
        gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY).astype(float)
        yy, xx = np.mgrid[-radius:radius + 1, -radius:radius + 1]
        dist = np.sqrt(xx * xx + yy * yy) / radius
        contrast = gray[dist < .65].mean() - gray[dist > .85].mean()
        return .72 if contrast > 7 and gray.std() > 10 else 0.0

    def _completed_markers(self,image,anchors,templates):
        """Use the visible small circles when a fixed sixth-floor map has few captions."""
        h,w=image.shape[:2];top=int(.18*h)
        gray=cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)[top:int(.88*h)]
        circles=cv2.HoughCircles(cv2.GaussianBlur(gray,(3,3),0),cv2.HOUGH_GRADIENT,1,
            minDist=w*.027,param1=65,param2=13,minRadius=max(3,round(w*.003)),maxRadius=round(w*.008))
        if circles is None:return anchors,0
        points=[{'x':float(x),'y':float(y+top),'type':'empty','confidence':.72,'text':'',
            'marker_only':True} for x,y,r in circles[0] if .10*w<x<.90*w
            and all(math.hypot(x-a['x'],y+top-a['y'])>w*.035 for a in anchors)]
        shape=tuple(max(t['gridShape'][i] for t in templates if t['floor']==6) for i in (0,1))
        grids=grid_hypotheses(points,w,shape,robust=True)
        if not grids:return anchors,0
        g=grids[0];origin=np.array(g['origin']);spacing=g['spacing']
        points=[p for p in points if np.linalg.norm((np.array([p['x'],p['y']])-origin)/spacing-
            np.rint((np.array([p['x'],p['y']])-origin)/spacing))<.18]
        if len(points)<8:return anchors,0
        snapped=[]
        for a in anchors:
            point=origin+np.rint((np.array([a['x'],a['y']])-origin)/spacing)*spacing
            if np.linalg.norm(point-np.array([a['x'],a['y']]))<=spacing*.4:
                a=dict(a,caption_position=[a['x'],a['y']],x=float(point[0]),y=float(point[1]))
            snapped.append(a)
        return snapped+points,len(points)

    def analyze(self, image, floor_hint=None, template_hint=None, source=None, side_mode="auto", side_template_hint=None, page_hint="auto"):
        from .inventory import InventoryRecognizer, KINDS
        inventory_reader = InventoryRecognizer(self.catalog, self.ocr, self.recent_relics)
        if page_hint in KINDS:
            return inventory_reader.analyze(image, page_hint, source)
        started = time.perf_counter()
        height, width = image.shape[:2]
        scale = min(1.0, 1280 / width)
        working = cv2.resize(image, (round(width * scale), round(height * scale))) if scale < 1 else image.copy()
        tokens = self.ocr.read(working)
        if page_hint == "auto":
            from .inventory_layout import layout_kind
            if layout := layout_kind(working, tokens):
                return inventory_reader.analyze(image, kind=layout, source=source)
        fields = self.read_fields(working, tokens)
        floor = floor_hint if floor_hint is not None else fields.get("floor", {}).get("value")
        anchors = anchors_from_text(tokens, working.shape[1], working.shape[0], self.names)
        templates = self.catalog.templates
        marker_count=0
        if floor==6 and len(anchors)<4 and anchors and 'action_points' in fields:
            anchors,marker_count=self._completed_markers(working,anchors,templates)
        if template_hint:
            templates = [t for t in templates if t["template_id"] == template_hint]
            if not templates:
                raise ValueError("未找到所选模板。")
            floor = templates[0]["floor"]
        eligible = [t for t in templates if floor is None or t.get("floor") == floor]
        max_shape = (max((t["gridShape"][0] for t in eligible), default=10) + 6, 8)
        grids = grid_hypotheses(anchors, working.shape[1], max_shape, robust=True)
        partitions = partition_hypotheses(anchors, eligible, grids, floor, side_mode)
        if side_mode == "present" and any(p["side"] for p in partitions):
            partitions = [p for p in partitions if p["side"]]
        solutions = []
        for partition in partitions:
            rows = match_templates(working, partition["main"], templates, floor)
            if not rows:
                continue
            side_rows = side_candidates(working, partition["side"], self.catalog.side_templates, floor,
                                        rows[0]["spacing"], edge_strength, side_template_hint)
            # Region evidence only breaks small ties. It cannot rescue a poor main fit.
            support = .02 * side_rows[0]["geometry_score"] if side_rows else 0
            if partition["side"] and (not side_rows or side_rows[0]["geometry_score"] < .8):
                continue
            solutions.append((rows[0]["score"] + support, partition, rows, side_rows))
        solutions.sort(key=lambda row: row[0], reverse=True)
        if solutions:
            _, partition, candidates, side_rows = solutions[0]
        else:
            partition, candidates, side_rows = partitions[0], [], []
        main_anchors = partition["main"]
        is_map = len(anchors) >= 4 and bool(candidates) and candidates[0]["geometry_score"] >= .75
        margin = candidates[0]["score"] - candidates[1]["score"] if len(candidates) > 1 else None
        review = not is_map or (not template_hint and (margin is None or margin < .025))
        if floor is None and not template_hint:
            review = True
        region_ambiguous = len(solutions) > 1 and solutions[0][0] - solutions[1][0] < .012
        if region_ambiguous or (side_mode == "present" and not partition["side"]):
            review = True
        result = {"schema_version": 2, "captured_at": datetime.now(timezone.utc).isoformat(),
                  "source": source, "image": {"width": width, "height": height},
                  "page_type": "map" if is_map else "unsupported", "fields": fields,
                  "candidates": candidates, "candidate_margin": margin, "needs_review": review,
                  "template_confirmed": bool(template_hint), "nodes": [], "edges": [],
                  "notices": [], "ocr_tokens": tokens}
        result['marker_anchor_count']=marker_count
        result["view"] = zoom_control(image)
        result["region_detection"] = {"mode": side_mode, "partition": partition["kind"],
                                      "reason": partition["reason"], "needs_review": region_ambiguous,
                                      "main_anchor_count": len(main_anchors), "side_anchor_count": len(partition["side"])}
        result["floor_hint_used"] = floor_hint is not None
        result["template_hint_used"] = template_hint is not None
        result["side_map"] = self._side_result(working, partition["side"], side_rows, scale, side_template_hint)
        if side_mode == "present" and not partition["side"]:
            result["side_map"].update(status="needs_review", needs_review=True)
            result["notices"].append("已指定存在右侧小图，但未能可靠分离；请使用完整视图并核对。")
        if result["view"]["state"] == "detail":
            result["notices"].append("检测到左下角为减号：建议先在游戏中缩小地图，再截图匹配完整结构。")
        elif result["view"]["state"] == "overview":
            result["notices"].append("检测到左下角为加号，当前可能为缩小视图；仍需核对画面边缘是否截断。")
        if partition["side"]:
            result["notices"].append("右侧熄日余径独立匹配，不参与主图模板和距起点概率统计。" +
                                     ("小图模板仍需确认。" if result["side_map"]["needs_review"] else ""))
        elif floor in (3, 4, 5):
            result["notices"].append("未分离出右侧小图不代表小图不存在；画面有小图时可指定“存在”后重读。")
        if region_ambiguous:
            result["notices"].append("主图与小图的分区存在近似候选，请核对后指定小图状态重读。")
        if is_map:
            chosen = candidates[0]
            template = next(t for t in templates if t["template_id"] == chosen["template_id"])
            visible = sum(0 <= chosen["origin"][0] + p[0] * chosen["spacing"] < working.shape[1] and
                          .10 * working.shape[0] <= chosen["origin"][1] + p[1] * chosen["spacing"] < .90 * working.shape[0]
                          for p in template["occupiedSlots"])
            result["view"]["projected_visible_fraction"] = visible / len(template["occupiedSlots"])
            if visible < len(template["occupiedSlots"]):
                review = result["needs_review"] = True
                result["notices"].append("候选主图有节点落在截图范围之外，请缩小/平移地图后重读。")
            result["template_id"] = chosen["template_id"]
            result["floor"] = template["floor"]
            result["grid"] = {"origin": [v / scale for v in chosen["origin"]], "spacing": chosen["spacing"] / scale}
            graph_distances = distances(template)
            observations = {tuple(slot): anchor for slot, anchor in zip(chosen["anchor_slots"], main_anchors)}
            for slot in template["occupiedSlots"]:
                x, y = [chosen["origin"][i] + slot[i] * chosen["spacing"] for i in range(2)]
                anchor = observations.get(tuple(slot))
                kind, confidence, evidence = (anchor["type"], anchor["confidence"], "ocr_label") if anchor else ("unobserved", 0.0, "template_only")
                if anchor and anchor.get('marker_only'):evidence='small_marker_candidate'
                if anchor is None:
                    marker_confidence = self._small_marker(working, x, y, chosen["spacing"])
                    if marker_confidence:
                        kind, confidence, evidence = "empty", marker_confidence, "small_marker_candidate"
                distance = graph_distances.get(tuple(slot))
                result["nodes"].append({"region_id": "main", "slot": slot, "pixel": [x / scale, y / scale], "type": kind,
                                        "label": self.names.get(kind, kind), "confidence": confidence,
                                        "evidence": evidence, "manual": False, "distance": distance,
                                        "probabilities": self.catalog.probabilities(template["floor"], distance, kind)})
            result["edges"] = template["edges"]
            result["start_slot"] = template["startSlot"]
            result["terminal_slots"] = template.get("terminalSlots", [])
            result["fixed_nodes"] = template.get("fixedNodes", [])
            # Overlay detection uses the same working coordinates as OCR/grid.
            region={'nodes':[dict(n,pixel=[v*scale for v in n['pixel']]) for n in result['nodes']],
                'grid':{'origin':chosen['origin'],'spacing':chosen['spacing']},'start_slot':result['start_slot'],
                'needs_review':result['needs_review'],'edges':result['edges']}
            self.locate_overlays(working,tokens,region)
            for old,new in zip(result['nodes'],region['nodes']):old.update({k:v for k,v in new.items() if k!='pixel'})
            for key in ('current_slot','position_evidence','at_start','resident_constraints'):result[key]=region.get(key)
            result["notices"].append("连通结构来自候选模板；匹配分数不是概率。起点来自模板，不等同于当前人物位置。")
            if review:
                result["notices"].append("模板证据尚不足，请核对候选或选择模板后确认。")
        else:
            result["notices"].append("未识别为支持的地图界面，请截取完整游戏窗口；本次不会覆盖当前地图。")
        result["notices"].append("空地小圆点为候选识别；图形精英标记、技能图标尚需人工核对。")
        if is_map and not result.get('needs_review'):
            result['recent_relics']=self.recent_relics.analyze(image)
        self.refresh_probabilities(result)
        result["elapsed_seconds"] = time.perf_counter() - started
        # Tokens are in working coordinates internally; export in original image coordinates.
        for token in result["ocr_tokens"]:
            token["box"] = (np.asarray(token["box"]) / scale).tolist()
        for candidate in result["candidates"]:
            candidate["origin"] = [v / scale for v in candidate["origin"]]
            candidate["spacing"] /= scale
        if page_hint == "auto" and not is_map:
            from .exploration import ExplorationCatalog
            inventory = inventory_reader.analyze(image, source=source, tokens=tokens)
            if inventory is None and any(clean(t['text']) == '收起' for t in tokens):
                inventory = inventory_reader.analyze(image, source=source)
            if inventory is not None:
                return inventory
            catalog = ExplorationCatalog.shared()
            title = catalog.match_event(tokens)
            preview = any('出发前往' in t['text'] for t in tokens)
            event = self.event_images.match(working, catalog.event_names, title) if not preview else None
            if event is None and title and not preview and len(anchors) < 2:
                event = dict(title, method='title')
            if event and not event.get('conflict'):
                result.update(page_type='event',event_id=event['id'],event_name=event['name'],
                              event_evidence=event,needs_review=False)
                return result
        return result

    def _side_result(self, image, anchors, rows, scale, hint):
        side = {"region_id": "sunset", "region_kind": "sunset", "status": "not_observed",
                "observed_in_frame": bool(anchors),
                "needs_review": True, "nodes": [], "edges": [], "candidates": rows,
                "template_confirmed": bool(hint)}
        if not rows:
            return side
        chosen = rows[0]
        template = next(t for t in self.catalog.side_templates if t["sourceId"] == chosen["template_id"])
        margin = chosen["score"] - rows[1]["score"] if len(rows) > 1 else None
        review = chosen["geometry_score"] < .9 or len(anchors) < 3 or (
            not hint and (margin is None or margin < .025))
        side.update(template_id=chosen["template_id"], status="needs_review" if review else "matched",
                    needs_review=review, candidate_margin=margin, document_id=template["document_id"],
                    grid={"origin": [v / scale for v in chosen["origin"]], "spacing": chosen["spacing"] / scale},
                    edges=template["edges"], fate_slot=template["fateSlot"])
        observations = {tuple(slot): a for slot, a in zip(chosen["anchor_slots"], anchors)}
        for slot in template["occupiedSlots"]:
            anchor = observations.get(tuple(slot))
            kind, confidence, evidence = (anchor["type"], anchor["confidence"], "ocr_label") if anchor else ("unobserved", 0., "template_only")
            side["nodes"].append({"region_id": "sunset", "slot": slot,
                "pixel": [(chosen["origin"][i] + slot[i] * chosen["spacing"]) / scale for i in range(2)],
                "type": kind, "label": self.names.get(kind, kind), "confidence": confidence, "evidence": evidence,
                "manual": False, "distance": None, "probabilities": None})
        for row in rows:
            row["origin"] = [v / scale for v in row["origin"]]
            row["spacing"] /= scale
        return side


def save_result(result, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
