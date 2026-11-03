#!/usr/bin/env python3
"""Extract publicly referenced sunset side-map geometry without executing JS."""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import importlib.util
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "data/sources/lubiao"
OUTPUT = SOURCE_ROOT / "side_map_definitions.json"
HISTORY = SOURCE_ROOT / "side_maps"
SITE = "https://www.lubiao.wiki"
TOOL_URL = SITE + "/tools/blackstream-route"
ARRAY_PATTERN = re.compile(r'\[\s*\{\s*(?:id|["\']id["\'])\s*:\s*[`"\']sunset-')


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def checksum(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def literal_parser():
    spec = importlib.util.spec_from_file_location("collect_lubiao", ROOT / "scripts/collect_lubiao.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.LiteralParser


def slot(token):
    """Upstream tokens are row:column; normalized tuples are x=column, y=row."""
    if not isinstance(token, str) or not re.fullmatch(r"[0-2]:[0-2]", token):
        raise ValueError(f"Invalid 3x3 side-map slot: {token!r}")
    row, column = map(int, token.split(":"))
    return [column, row]


def extract_templates(text):
    matches = list(ARRAY_PATTERN.finditer(text))
    if len(matches) != 1:
        raise ValueError(f"Expected one sunset template array, found {len(matches)}")
    # Match the data content, not the minified assignment's variable name.
    rows = literal_parser()(text[matches[0].start():]).value()
    result, identifiers = [], set()
    for raw in rows:
        if not isinstance(raw, dict):
            raise ValueError("Side-map entry must be a data object")
        identifier = raw.get("id")
        floor = raw.get("floor")
        if not isinstance(identifier, str) or not re.fullmatch(r"sunset-[345]-\d+", identifier):
            raise ValueError(f"Invalid sunset template ID: {identifier!r}")
        if identifier in identifiers or floor not in (3, 4, 5) or int(identifier.split("-")[1]) != floor:
            raise ValueError(f"Duplicate ID or mismatched floor: {identifier}")
        identifiers.add(identifier)
        points = raw.get("traversable")
        original_edges = raw.get("edges")
        if not isinstance(points, list) or not points or not isinstance(original_edges, list):
            raise ValueError(f"Missing slot/edge arrays: {identifier}")
        occupied = [slot(point) for point in points]
        if len(set(points)) != len(points):
            raise ValueError(f"Duplicate traversable slots: {identifier}")
        point_set = set(points)
        if raw.get("fate") not in point_set:
            raise ValueError(f"Fate slot outside traversable set: {identifier}")
        edges, edge_set, adjacency = [], set(), {point: set() for point in points}
        for edge in original_edges:
            if not isinstance(edge, list) or len(edge) != 2 or any(p not in point_set for p in edge):
                raise ValueError(f"Edge endpoint outside traversable set: {identifier}: {edge!r}")
            a, b = map(slot, edge)
            key = tuple(sorted(edge))
            if key in edge_set or sum(abs(x - y) for x, y in zip(a, b)) != 1:
                raise ValueError(f"Duplicate/non-orthogonal edge: {identifier}: {edge!r}")
            edge_set.add(key)
            adjacency[edge[0]].add(edge[1])
            adjacency[edge[1]].add(edge[0])
            edges.append([a, b])
        reached, queue = set(), [points[0]]
        while queue:
            point = queue.pop()
            if point not in reached:
                reached.add(point)
                queue.extend(adjacency[point] - reached)
        if reached != point_set:
            raise ValueError(f"Disconnected published side-map graph: {identifier}")
        result.append({"id": identifier, "sourceId": identifier, "label": raw.get("label"),
                       "floor": floor, "region_kind": "sunset", "gridShape": [3, 3],
                       "occupiedSlots": occupied, "edges": edges, "fateSlot": slot(raw["fate"]),
                       "raw": {"traversable": points, "edges": original_edges, "fate": raw["fate"]}})
    return result


def coordinate_evidence(controller):
    # The actual topology-card renderer and sunset control both use this parser.
    parser_pattern = re.compile(
        r'function\s+(\w+)\((\w+)\)\{let\[(\w+)=0,(\w+)=0\]=\2\.split\([`"\']:[`"\']\)\.map\(Number\);return\{x:\4,y:\3\}\}')
    parsers = list(parser_pattern.finditer(controller))
    if not parsers:
        raise ValueError("No verified row:column -> x:column,y:row renderer evidence")
    position = controller.find("BlackstreamSunsetControls")
    if position < 0:
        raise ValueError("Sunset topology controller not found")
    section = controller[max(0, position - 1000):position + 4000]
    if not all(text in section for text in ("rows:3,cols:3", "命运所指", "2-")):
        raise ValueError("3x3 sunset card / label evidence changed; manual review required")
    alignments = [name for name in ("顶部对齐", "居中对齐", "底部对齐") if name in controller]
    return {"slot_parser": parsers[0][0],
            "renderer": "SVG x/cx use column; SVG y/cy use row; no vertical flip in geometry",
            "label_only_transform": "Sunset description shows (column, 2-row); not used for normalized geometry",
            "alignment_labels": alignments,
            "alignment_scope": "website placement controls; not a generated bridge or entry coordinate"}


def cached_sources():
    manifest = json.loads((SOURCE_ROOT / "latest_manifest.json").read_text(encoding="utf-8"))
    geometry, controller = None, None
    for item in manifest["resources"]:
        if item["origin"] != "live" or not item["url"].endswith(".js"):
            continue
        path = SOURCE_ROOT / item["relative_path"]
        data = path.read_bytes()
        text = data.decode("utf-8")
        record = {key: item[key] for key in ("url", "sha256", "fetched_at", "metadata")}
        record["path"] = str(path.relative_to(ROOT)).replace("\\", "/")
        record["acquisition"] = "existing public HTTP capture"
        if ARRAY_PATTERN.search(text):
            geometry = (record, text)
        if "BlackstreamSunsetControls" in text and "右侧余径构造" in text:
            controller = (record, text)
    if not geometry or not controller:
        raise ValueError("Cached sunset geometry/controller sources are missing")
    return geometry, controller


class Fetcher:
    def __init__(self, timeout, delay):
        self.timeout, self.delay = timeout, max(.25, delay)
        self.previous = 0.0
        self.resources = []

    def get(self, url):
        wait = self.delay - (time.monotonic() - self.previous)
        if wait > 0:
            time.sleep(wait)
        self.previous = time.monotonic()
        request = urllib.request.Request(url, headers={"User-Agent": "TreeSeaReaderDataCollector/0.1 (public side-map definitions; rate limited)"})
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            data = response.read(2 * 1024 * 1024 + 1)
            if len(data) > 2 * 1024 * 1024:
                raise ValueError("Source exceeds 2 MiB cap")
            timestamp = now()
        digest = checksum(data)
        suffix = ".js" if urllib.parse.urlparse(url).path.endswith(".js") else ".html"
        path = HISTORY / "blobs" / f"{digest}{suffix}"
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(data)
        record = {"url": url, "sha256": digest, "fetched_at": timestamp,
                  "path": str(path.relative_to(ROOT)).replace("\\", "/"),
                  "metadata": {}, "acquisition": "live public HTTP"}
        self.resources.append(record)
        print(f"OK {len(data)} {url}", flush=True)
        return record, data.decode("utf-8")

    def discover(self):
        _, html = self.get(TOOL_URL)
        scripts = re.findall(r'<script[^>]+src=["\']([^"\']+)', html)
        if not scripts:
            raise ValueError("Entry script missing")
        main_url = urllib.parse.urljoin(TOOL_URL, scripts[0])
        _, main = self.get(main_url)
        match = re.search(r'path:[`"\']/tools/blackstream-route[`"\'][\s\S]{0,150}?import\([`"\']([^`"\']+)', main)
        if not match:
            raise ValueError("Map tool route module missing from entry")
        controller_url = urllib.parse.urljoin(main_url, match[1])
        controller = self.get(controller_url)
        coordinate_evidence(controller[1])
        if ARRAY_PATTERN.search(controller[1]):
            return controller, controller
        imports = re.findall(r'import\{([^}]+)\}from[`"\']([^`"\']+)', controller[1])
        # The domain module exports most map helpers; prioritize it over UI pieces.
        imports.sort(key=lambda item: len(item[0]), reverse=True)
        fetched = set()
        for _, relative in imports[:8]:
            if not relative.startswith("./"):
                continue
            url = urllib.parse.urljoin(controller_url, relative)
            if url in fetched:
                continue
            fetched.add(url)
            candidate = self.get(url)
            if ARRAY_PATTERN.search(candidate[1]):
                return candidate, controller
        raise ValueError("Sunset array not found in first 8 directly referenced domain modules")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="Extract existing captured source without a network request")
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--delay", type=float, default=.5)
    args = parser.parse_args()
    fetcher = Fetcher(args.timeout, args.delay)
    try:
        geometry, controller = cached_sources() if args.offline else fetcher.discover()
        templates = extract_templates(geometry[1])
        evidence = coordinate_evidence(controller[1])
        stable = {"schemaVersion": 1, "topicId": "rogue_6", "region_kind": "sunset",
                  "coordinateSystem": "local 3x3 grid, x right/column, y down/row; origin at top-left",
                  "source_sha256": geometry[0]["sha256"], "templates": templates}
        definition_hash = checksum(json.dumps(stable, ensure_ascii=False, sort_keys=True).encode("utf-8"))
        result = {"schemaVersion": 1, "topicId": "rogue_6", "region_kind": "sunset",
                  "extracted_at": now(), "contentVersion": None,
                  "versionStatus": "No explicit game content version declared for the sunset array; identify exact source by SHA256",
                  "coordinateSystem": stable["coordinateSystem"],
                  "source": geometry[0], "controllerSource": controller[0],
                  "coordinateEvidence": evidence, "count": len(templates),
                  "floorCounts": dict(collections.Counter(str(item["floor"]) for item in templates)),
                  "definition_sha256": definition_hash, "templates": templates,
                  "boundaries": ["No startSlot is supplied by the upstream array or fabricated here",
                                 "No generated links or entry slots between the main map and side region",
                                 "No frequency prior supplied; do not merge into main-map template/distance statistics"]}
        write_json(OUTPUT, result)
        historical = HISTORY / f"definitions-{definition_hash}.json"
        if not historical.exists():
            write_json(historical, result)
        write_json(HISTORY / "latest_collection_manifest.json", {"run_at": now(),
                   "mode": "offline" if args.offline else "live", "resources": fetcher.resources,
                   "status": "success", "definition_sha256": definition_hash})
        print(json.dumps({"count": result["count"], "floorCounts": result["floorCounts"],
                          "output": str(OUTPUT), "definition_sha256": definition_hash}, ensure_ascii=False))
    except (OSError, ValueError, IndexError, KeyError) as error:
        write_json(HISTORY / "latest_collection_manifest.json", {"run_at": now(),
                   "mode": "offline" if args.offline else "live", "resources": fetcher.resources,
                   "status": "failed", "error": str(error)})
        raise


if __name__ == "__main__":
    main()
