"""Separate a main map from an optional right-hand sunset region.

No cross-region edges, start point, or probability distribution are invented.
"""
from __future__ import annotations

import cv2
import numpy as np


def partition_hypotheses(anchors, templates, grids, floor, mode="auto"):
    original = {"main": anchors, "side": [], "kind": "single", "reason": "未分离出右侧区域"}
    if mode == "absent" or floor not in (3, 4, 5) or len(anchors) < 5:
        return [original]
    if not grids:
        return [original]
    spacing = grids[0]["spacing"]
    max_columns = max(t["gridShape"][0] for t in templates if t.get("floor") == floor)
    sorted_anchors = sorted(anchors, key=lambda a: a["x"])
    whole_span = (sorted_anchors[-1]["x"] - sorted_anchors[0]["x"]) / spacing + 1
    proposals = []
    for index in range(4, len(sorted_anchors)):
        left, right = sorted_anchors[:index], sorted_anchors[index:]
        gap = (right[0]["x"] - left[-1]["x"]) / spacing
        rx = (max(a["x"] for a in right) - min(a["x"] for a in right)) / spacing
        ry = (max(a["y"] for a in right) - min(a["y"] for a in right)) / spacing
        fate = any(a["type"] == "fate-choice" for a in right)
        if gap < .85 or len(right) > 9 or rx > 2.6 or ry > 2.6:
            continue
        if mode != "present" and whole_span <= max_columns + .5 and not fate:
            continue
        proposals.append({"main": left, "side": right, "kind": "main+sunset",
                          "spacing": spacing, "gap_cells": gap, "forced": mode == "present",
                          "reason": "右侧独立3×3范围；" + ("观察到命运所指" if fate else "整体宽度超过本层主图")})
    proposals.sort(key=lambda p: (p["gap_cells"], len(p["main"])), reverse=True)
    return [original] + proposals[:3]


def side_candidates(image, anchors, definitions, floor, spacing, edge_function, hint=None):
    if not anchors:
        return []
    rows = []
    for template in definitions:
        if template["floor"] != floor or (hint and template["sourceId"] != hint):
            continue
        slots = {tuple(p) for p in template["occupiedSlots"]}
        fate_anchors = [a for a in anchors if a["type"] == "fate-choice"]
        origins = []
        if fate_anchors:
            for anchor in fate_anchors:
                origins.append([anchor["x"] - template["fateSlot"][0] * spacing,
                                anchor["y"] - template["fateSlot"][1] * spacing])
        else:
            first = anchors[0]
            origins = [[first["x"] - x * spacing, first["y"] - y * spacing] for x, y in slots]
        best = None
        for origin in origins:
            positions = []
            errors = []
            for anchor in anchors:
                point = np.array([anchor["x"], anchor["y"]])
                slot = tuple(np.rint((point - origin) / spacing).astype(int))
                residual = float(np.linalg.norm(point - (np.array(origin) + np.array(slot) * spacing)) / spacing)
                valid = slot in slots and residual < .28
                positions.append([int(v) for v in slot])
                errors.append(np.exp(-(residual / .20) ** 2) if valid else 0)
            coverage = float(np.mean(errors))
            signals = [edge_function(image,
                        [origin[i] + a[i] * spacing for i in range(2)],
                        [origin[i] + b[i] * spacing for i in range(2)], spacing)
                       for a, b in template["edges"]]
            line_score = float(np.mean(signals)) if signals else 0
            score = .9 * coverage + .1 * line_score
            row = {"template_id": template["sourceId"], "floor": floor, "origin": origin,
                   "spacing": spacing, "score": score, "geometry_score": coverage,
                   "anchor_slots": positions, "node_count": len(slots), "edge_count": len(template["edges"]),
                   "fate_observed": bool(fate_anchors)}
            if best is None or score > best["score"]:
                best = row
        if best:
            rows.append(best)
    rows.sort(key=lambda row: row["score"], reverse=True)
    return rows


def zoom_control(image):
    """Read the lower-left magnifier's +/- state without clicking the game.

    Returns unknown on ambiguous images; '+' means far-view mode, not proof
    that panning has left every node inside the screenshot.
    """
    height, width = image.shape[:2]
    scale = min(1.0, 1280 / width)
    work = cv2.resize(image, (round(width * scale), round(height * scale))) if scale < 1 else image
    h, w = work.shape[:2]
    x0, x1, y0, y1 = round(.012 * w), round(.095 * w), round(.57 * h), round(.89 * h)
    crop = work[y0:y1, x0:x1]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), .8)
    circles = cv2.HoughCircles(gray, cv2.HOUGH_GRADIENT, 1, 12, param1=90, param2=17,
                               minRadius=max(5, round(w * .005)), maxRadius=round(w * .018))
    if circles is None:
        return {"state": "unknown", "confidence": 0.0}
    options = []
    for x, y, radius in circles[0]:
        # Sample only the inner sign, excluding the circular lens border.
        def sample(dx, dy):
            xx = np.clip(np.rint(x + np.asarray(dx) * radius).astype(int), 0, gray.shape[1] - 1)
            yy = np.clip(np.rint(y + np.asarray(dy) * radius).astype(int), 0, gray.shape[0] - 1)
            return gray[yy, xx].astype(float)
        axis = np.linspace(-.36, .36, 9)
        horizontal = sample(axis, np.zeros(9)).mean()
        vertical_outer = sample(np.zeros(6), np.array([-.48, -.36, -.25, .25, .36, .48])).mean()
        background = sample(np.array([-.45, .45, -.45, .45]), np.array([-.45, -.45, .45, .45])).mean()
        line_contrast = horizontal - background
        if line_contrast < 35:
            continue
        state = "overview" if vertical_outer - background > 30 else "detail"
        confidence = float(np.clip(line_contrast / 120, 0, 1))
        options.append({"state": state, "confidence": confidence, "pixel": [float((x + x0) / scale), float((y + y0) / scale)],
                        "radius": float(radius / scale), "symbol": "+" if state == "overview" else "-"})
    # The lower of the two controls is the zoom magnifier; the upper is locate.
    return max(options, key=lambda row: row["pixel"][1]) if options else {"state": "unknown", "confidence": 0.0}
