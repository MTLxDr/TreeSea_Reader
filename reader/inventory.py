"""Local OCR proposals for owned item/roster pages. Never infer ownership from text alone."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from difflib import SequenceMatcher
import hashlib
import re
import time
import unicodedata

import cv2
import numpy as np

KINDS = {"relics": "藏品", "parts": "零件", "operators": "干员"}


def normalized(text):
    return re.sub(r"[^\w\u4e00-\u9fff]", "", unicodedata.normalize("NFKC", text)).lower()


def rect(token):
    box = np.asarray(token["box"])
    return float(box[:, 0].min()), float(box[:, 1].min()), float(box[:, 0].max()), float(box[:, 1].max())


def center(token):
    a, b, c, d = rect(token)
    return (a + c) / 2, (b + d) / 2


def name_candidates(text, catalog):
    value = normalized(text)
    if not value:
        return []
    rows = []
    for item in catalog:
        names = [normalized(n) for n in [item["name"]] + item.get("aliases", [])]
        name = max(names, key=lambda n: SequenceMatcher(None, value, n).ratio())
        similarity = SequenceMatcher(None, value, name).ratio()
        # Short names must be exact. Do not match names embedded in effect text.
        if value == name or (len(name) >= 4 and abs(len(name) - len(value)) <= 1 and similarity >= .82):
            rows.append((similarity, item))
    rows.sort(key=lambda row: (-row[0], row[1]["id"]))
    return rows[:3]


def page_kind(tokens, height):
    titles = [normalized(t["text"]) for t in tokens if rect(t)[1] < .30 * height]
    if any(t in {"零件箱", "引擎零件", "零件库存"} for t in titles):
        return "parts"
    if any(t in {"已招募干员", "干员列表", "编队", "干员详情"} for t in titles):
        return "operators"
    if any(t in {"收藏品", "藏品", "已获得藏品", "拟造物质"} for t in titles):
        return "relics"
    return None


def field(value, token):
    return {"value": value, "confidence": float(token["score"]), "raw": token["text"], "source": "ocr", "manual": False}


def nearby_fields(index, observations, tokens, width, height, kind):
    """Assign labels to the nearest observed name; ties stay unknown."""
    anchor = observations[index]["token"]
    ax, ay = center(anchor)
    others = [center(o["token"]) for i, o in enumerate(observations) if i != index]
    fields = {}
    for token in tokens:
        if token is anchor or token["score"] < .78:
            continue
        x, y = center(token)
        if abs(x - ax) > width * .17 or abs(y - ay) > height * .25:
            continue
        distance = ((x - ax) / width) ** 2 + ((y - ay) / height * .65) ** 2
        competing = [((x - ox) / width) ** 2 + ((y - oy) / height * .65) ** 2 for ox, oy in others]
        if competing and min(competing) <= distance * 1.25:
            continue
        text = unicodedata.normalize("NFKC", token["text"]).replace(" ", "")
        patterns = {"quantity": r"[xX×](\d{1,3})"} if kind != "operators" else {
            "level": r"(?:[Ll][Vv][.]?|等级[:：]?)(\d{1,2})(?:/\d{1,2})?",
            "elite": r"(?:精英化?|[Ee])[:：]?(\d)",
            "potential": r"潜能[:：]?(\d)",
            "skill_level": r"技能等级[:：]?(\d)",
            "skill_index": r"技能[:：]?([123])",
            "mastery": r"专精[:：]?(\d)"}
        for key, pattern in patterns.items():
            match = re.fullmatch(pattern, text)
            if match:
                value = int(match[1])
                limits = {"level": (1, 90), "elite": (0, 2), "potential": (1, 6), "skill_level": (1, 7), "skill_index": (1, 3), "mastery": (0, 3), "quantity": (1, 999)}
                lo, hi = limits[key]
                if lo <= value <= hi:
                    # Conflicting nearby values are never resolved by token order.
                    if key in fields and fields[key]["value"] != value:
                        fields[key] = {"value": None, "source": "conflicting_ocr", "raw": None, "confidence": 0.}
                    else:
                        fields[key] = field(value, token)
    if "mastery" in fields:
        fields["mastery"]["skill_index"] = fields.get("skill_index", {}).get("value")
        fields["mastery"]["scope"] = "selected_skill" if fields["mastery"]["skill_index"] else "unbound_skill"
    return fields


class InventoryRecognizer:
    def __init__(self, catalog, ocr, relic_icons=None):
        self.catalog, self.ocr = catalog, ocr
        self.relic_icons = relic_icons

    def relic_card_icon(self, image, box):
        if self.relic_icons is None:
            return None
        from .inventory_layout import crop

        x, y, _, _ = box
        patch = crop(image, (max(0, x-.086), max(0, y-.014), x-.004, y+.112))
        if not patch.size:
            return None
        patch = cv2.resize(patch, (105, 110), interpolation=cv2.INTER_AREA)
        match = self.relic_icons.match_patch(patch)
        if match is None:
            return None
        item, asset, _, score, margin, accepted = match
        if not accepted or score < .86 or margin < .10:
            return None
        h, w = image.shape[:2]
        a, b, c, d = box
        return dict(text=item['name'], score=score, icon_source=asset['source_url'],
                    box=[[a*w,b*h],[c*w,b*h],[c*w,d*h],[a*w,d*h]])

    def analyze(self, image, kind=None, source=None, tokens=None):
        started = time.perf_counter()
        height, width = image.shape[:2]
        if tokens is None:
            scale = min(1., 1800 / width)
            working = cv2.resize(image, (round(width * scale), round(height * scale))) if scale < 1 else image
            tokens = self.ocr.read(working)
            for token in tokens:
                token["box"] = (np.asarray(token["box"]) / scale).tolist()
        from .inventory_layout import boxes_for, detail_fields, layout_kind, line_token

        layout = layout_kind(image, tokens)
        detected = layout or page_kind(tokens, height)
        kind = kind or detected
        if kind not in KINDS:
            return None
        observations = []
        catalog = self.catalog.inventory[kind]
        card_count = 0
        unresolved = []
        candidates = [(token, {}, None) for token in tokens]
        if layout == kind:
            candidates = []
            for box, details in boxes_for(image, kind, tokens):
                card_count += 1
                x0, y0, x1, y1 = box
                inside = [t for t in tokens if x0-.006 <= center(t)[0]/width <= x1+.006
                          and y0-.004 <= center(t)[1]/height <= y1+.004
                          and name_candidates(t["text"], catalog)]
                strong = any(t["score"] >= .90 and name_candidates(t["text"], catalog)[0][0] == 1 for t in inside)
                local = line_token(self.ocr, image, box) if kind != "parts" or not strong else None
                if kind == "operators" and inside and not strong:
                    anchor = max(inside, key=lambda t: t["score"])
                    a, b, c, d = rect(anchor)
                    tight = (max(x0,(a-4)/width),max(y0,(b-4)/height),min(x1,(c+4)/width),min(y1,(d+4)/height))
                    focused = line_token(self.ocr, image, tight)
                    if focused and normalized(focused["text"]) == normalized(anchor["text"]):
                        local = focused
                options = inside + ([local] if local else [])
                exact = [t for t in options if any(s == 1 for s, _ in name_candidates(t["text"], catalog))]
                if kind == "relics" and not any(t["score"] >= .90 for t in exact):
                    icon = self.relic_card_icon(image, box)
                    if icon:
                        if not exact or any(normalized(t["text"]) == normalized(icon["text"]) for t in exact):
                            options.append(icon)
                            exact.append(icon)
                if options:
                    token = max(exact or options, key=lambda t: t["score"])
                    if local in exact and local["score"] >= .68 and any(
                            normalized(t["text"]) == normalized(local["text"]) and t["score"] >= .68 for t in inside):
                        token = dict(token, corroborated=True)
                    candidates.append((token, details, box))
                else:
                    unresolved.append(box)
        for token, details, card in candidates:
            if token["score"] < .68:
                continue
            choices = name_candidates(token["text"], catalog)
            if not choices:
                if card is not None:
                    unresolved.append(card)
                continue
            similarity, item = choices[0]
            gap = similarity - choices[1][0] if len(choices) > 1 else 1.
            observations.append({"token": token, "item": item, "similarity": similarity,
                                 "details": details, "card": card,
                                 "candidates": [{"id": i["id"], "name": i["name"], "score": s} for s, i in choices],
                                 "ambiguous": gap < .08 or similarity < 1. or (
                                     token["score"] < (.90 if card is None else .86) and not token.get("corroborated"))})
        entries = []
        repeated = Counter(o["item"]["id"] for o in observations)
        for index, observed in enumerate(observations):
            item, token = observed["item"], observed["token"]
            values = (detail_fields(self.ocr, image, observed["details"], tokens) if layout == kind
                      else nearby_fields(index, observations, tokens, width, height, kind))
            entries.append({"id": item["id"], "name": item["name"], "kind": kind,
                "confidence": token["score"] * observed["similarity"], "raw": token["text"],
                "corroborated": bool(token.get("corroborated")),
                "icon_source": token.get("icon_source"),
                "box": token["box"], "candidates": observed["candidates"],
                "needs_review": observed["ambiguous"] or (layout != kind and repeated[item["id"]] > 1),
                "document_id": item["document_id"], "source_url": item["source_url"],
                "fields": values})
        page_fields = {}
        if layout == "parts":
            total = detail_fields(self.ocr, image, {"valuation": (.132, .735, .197, .792)}).get("valuation")
            if total:
                page_fields["total_valuation"] = total
        # OCR layout/text fingerprint catches identical/overlapping screenshots;
        # it is evidence for repeated observations, never an inventory increment.
        signature = "|".join(sorted(f"{normalized(t['text'])}:{round(center(t)[0]/width*100)}:{round(center(t)[1]/height*100)}" for t in tokens))
        risk = layout != kind and any(re.search(r"(?:购买|售价|招募寻访|招募费用|商品列表)", t["text"]) for t in tokens)
        coverage = {"visible": card_count, "recognized": sum(not r["needs_review"] for r in entries),
                    "unresolved": unresolved} if layout == kind else None
        if layout == kind:
            signature += repr(([(r["id"], {k: v["value"] for k, v in r["fields"].items()}) for r in entries],
                               {k: v["value"] for k, v in page_fields.items()}))
        return {"schema_version": 2, "page_type": "inventory", "inventory_kind": kind,
            "captured_at": datetime.now(timezone.utc).isoformat(), "source": source,
            "image": {"width": width, "height": height}, "entries": entries, "ocr_tokens": tokens,
            "page_fingerprint": hashlib.sha256(signature.encode()).hexdigest(),
            "context_detected": detected, "ownership_risk": risk, "needs_review": True,
            "coverage": coverage, "page_fields": page_fields,
            "notices": ["翻页继续读取；可在表格中补录练度、数量或修正名称。"] +
                (["当前为购买或招募页面，请切到持有清单。"] if risk else []),
            "elapsed_seconds": time.perf_counter() - started}
