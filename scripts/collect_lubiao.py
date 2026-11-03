#!/usr/bin/env python3
"""Collect public Blackstream data, retaining immutable source blobs and provenance.

Only Python's standard library is required. No upstream JavaScript is executed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SITE = "https://www.lubiao.wiki"
PROJECT = Path(__file__).resolve().parents[1]
USER_AGENT = "TreeSeaReaderDataCollector/0.1 (public research; rate limited)"
ROUTES = ["/tools/blackstream-route", "/mechanisms/map-constructions/rogue_6",
          "/mechanisms/node-distances/rogue_6", "/mechanisms/utopia/rogue_6",
          "/mechanisms/hopeful/rogue_6", "/archives/blackstream",
          "/mechanisms/rogue_6", "/pools/rogue_6", "/nodes/rogue_6"]

# Fail closed if this upstream helper changes. The data parser never runs JS.
# This is the exact helper observed in the source module recorded in the manifest.
UPSTREAM_ADJACENCY_HELPER = (
    "function o(e){let{excludedEdges:t=[],...n}=e,r=new Set(n.occupiedSlots.map(i)),"
    "o=new Set(t.map(([e,t])=>a(e,t))),s=[];for(let e of n.occupiedSlots){let[t,n]=e;"
    "for(let c of[[t+1,n],[t,n+1]])r.has(i(c))&&!o.has(a(e,c))&&s.push([e,c])}"
    "return{...n,edges:s}}"
)


def utc_now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def dump(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def metadata(data):
    try:
        value = json.loads(data)
    except (ValueError, UnicodeError):
        return {}
    if not isinstance(value, dict):
        return {}
    keys = ("schemaVersion", "revision", "topicId", "updatedAt", "syncedAt", "ruleVersion",
            "sampleCount", "mapSampleCount", "denominator", "dataVersion", "version")
    result = {k: value[k] for k in keys if k in value}
    if isinstance(value.get("prts"), dict):
        result["prts"] = {k: value["prts"][k] for k in ("source", "syncedAt") if k in value["prts"]}
    version_evidence = {}
    if isinstance(value.get("versions"), dict):
        version_evidence["versions"] = list(value["versions"])
    classifier = value.get("classifier")
    if isinstance(classifier, dict):
        version_evidence["classifierSourceGeneratedAt"] = classifier.get("sourceGeneratedAt")
        if isinstance(classifier.get("versionedCollectibles"), dict):
            version_evidence["versionedCollectibles"] = list(classifier["versionedCollectibles"])
        introduced = {}
        for item in classifier.get("items", []) if isinstance(classifier.get("items"), list) else []:
            if not isinstance(item, dict):
                continue
            label = item.get("introducedVersion") or "unspecified"
            introduced[label] = introduced.get(label, 0) + 1
        if introduced:
            version_evidence["introducedVersionCounts"] = introduced
    pools = value.get("pools")
    if isinstance(pools, dict) and isinstance(pools.get("dataVersions"), dict):
        version_evidence["poolDataVersions"] = {key: len(rows) if isinstance(rows, list) else None
                                               for key, rows in pools["dataVersions"].items()}
        version_evidence["poolDefaultVersionId"] = pools.get("defaultVersionId")
    if version_evidence:
        result["contentVersionEvidence"] = version_evidence
    return result


class LiteralParser:
    """Small, non-executing parser for the site's data-only JS template literals."""

    def __init__(self, text, allowed_helpers=()):
        self.text, self.i = text, 0
        self.allowed_helpers = frozenset(allowed_helpers)

    def ws(self):
        while self.i < len(self.text) and self.text[self.i].isspace():
            self.i += 1

    def consume(self, char):
        self.ws()
        if not self.text.startswith(char, self.i):
            raise ValueError(f"Expected {char!r} at {self.i}")
        self.i += len(char)

    def ident(self):
        self.ws()
        match = re.match(r"[A-Za-z_$][\w$]*", self.text[self.i:])
        if not match:
            raise ValueError(f"Expected data identifier at {self.i}")
        self.i += len(match[0])
        return match[0]

    def string(self):
        quote = self.text[self.i]
        self.i += 1
        chars = []
        while self.i < len(self.text):
            char = self.text[self.i]
            self.i += 1
            if char == quote:
                return "".join(chars)
            if char == "\\":
                if self.i >= len(self.text):
                    break
                char = self.text[self.i]
                self.i += 1
                if char == "u":
                    chars.append(chr(int(self.text[self.i:self.i + 4], 16)))
                    self.i += 4
                    continue
                char = {"n": "\n", "r": "\r", "t": "\t"}.get(char, char)
            if quote == "`" and char == "$" and self.text[self.i:self.i + 1] == "{":
                raise ValueError("Template interpolation is not data")
            chars.append(char)
        raise ValueError("Unterminated string")

    def value(self):
        self.ws()
        char = self.text[self.i]
        if char in "\"'`":
            return self.string()
        if char == "[":
            self.i += 1
            result = []
            self.ws()
            while self.text[self.i] != "]":
                result.append(self.value())
                self.ws()
                if self.text[self.i] == "]":
                    break
                self.consume(",")
            self.consume("]")
            return result
        if char == "{":
            self.i += 1
            result = {}
            self.ws()
            while self.text[self.i] != "}":
                self.ws()
                key = self.string() if self.text[self.i] in "\"'`" else self.ident()
                self.consume(":")
                result[key] = self.value()
                self.ws()
                if self.text[self.i] == "}":
                    break
                self.consume(",")
            self.consume("}")
            return result
        number = re.match(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", self.text[self.i:])
        if number:
            self.i += len(number[0])
            return json.loads(number[0])
        if self.text.startswith("!0", self.i) or self.text.startswith("!1", self.i):
            result = self.text[self.i + 1] == "0"
            self.i += 2
            return result
        name = self.ident()
        if name in ("true", "false", "null"):
            return {"true": True, "false": False, "null": None}[name]
        # The template module's one helper derives orthogonal edges from slots.
        # The caller checks that helper's implementation before allowing it.
        if name not in self.allowed_helpers:
            raise ValueError(f"Unsupported data helper: {name}")
        self.consume("(")
        result = self.value()
        self.consume(")")
        if not isinstance(result, dict):
            raise ValueError("Unsupported data helper argument")
        result["_deriveOrthogonalEdges"] = True
        return result


def extract_templates(text, source):
    match = re.search(r"\b(?:var|let|const)\s+\w+=\[\{id:[`\"']floor-1-", text)
    if not match:
        return None
    start = text.index("[", match.start())
    helper_verified = re.sub(r"\s+", "", UPSTREAM_ADJACENCY_HELPER) in re.sub(r"\s+", "", text[:start])
    rows = LiteralParser(text[start:], allowed_helpers={"o"} if helper_verified else ()).value()
    if not all(isinstance(x, dict) and "occupiedSlots" in x and "gridShape" in x for x in rows):
        raise ValueError("Template schema changed")
    for item in rows:
        if item.pop("_deriveOrthogonalEdges", False):
            if not helper_verified:
                raise ValueError("Upstream adjacency helper changed; manual review required")
            slots = {tuple(p) for p in item["occupiedSlots"]}
            excluded = {tuple(sorted(map(tuple, edge))) for edge in item.get("excludedEdges", [])}
            edges = []
            for point in item["occupiedSlots"]:
                x, y = point
                for other in ((x + 1, y), (x, y + 1)):
                    if other in slots and tuple(sorted((tuple(point), other))) not in excluded:
                        edges.append([point, list(other)])
            item["edges"] = edges
            item["edgesDerivation"] = "upstream orthogonal-neighbor helper; excludedEdges applied"
        slots = {tuple(p) for p in item["occupiedSlots"]}
        if tuple(item["startSlot"]) not in slots:
            raise ValueError(f"Start outside slots: {item['id']}")
        if any(tuple(p) not in slots for edge in item["edges"] for p in edge):
            raise ValueError(f"Edge outside slots: {item['id']}")
        item["floor"] = item["layerIndex"] + 1 if item["layerIndex"] < 6 else "treehole"
    return {"schemaVersion": 1, "topicId": "rogue_6", "extracted_at": utc_now(),
            "coordinateSystem": "source grid: x right, y down, origin top-left",
            "contentVersion": "unversioned upstream geometry module; do not infer game version from capture date",
            "source": source, "count": len(rows), "templates": rows}


def template_definition_hash(template_data):
    """Hash structural contents and upstream source, excluding all wall-clock times."""
    stable = {key: template_data[key] for key in
              ("schemaVersion", "topicId", "coordinateSystem", "templates")}
    stable["source_sha256"] = template_data["source"]["sha256"]
    return hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()


class Collector:
    def __init__(self, output, delay, timeout, asset_limit):
        self.output, self.delay, self.timeout, self.asset_limit = output, delay, timeout, asset_limit
        self.output.mkdir(parents=True, exist_ok=True)
        self.resources, self.failures, self.seen, self.last_request = [], [], {}, 0.0
        previous = output / "latest_manifest.json"
        self.previous = json.loads(previous.read_text(encoding="utf-8")) if previous.exists() else {}

    def store(self, url, body, origin, **extra):
        sha = hashlib.sha256(body).hexdigest()
        suffix = Path(urllib.parse.urlparse(url).path).suffix
        if suffix not in (".json", ".js", ".html", ".txt", ".svg"):
            suffix = ".html"
        path = self.output / "blobs" / f"{sha}{suffix}"
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(body)
        acquired_at = utc_now()
        item = {"url": url, "origin": origin, "sha256": sha,
                "relative_path": str(path.relative_to(self.output)).replace("\\", "/"),
                "bytes": len(body), "acquired_at": acquired_at,
                "fetched_at": acquired_at if origin == "live" else None,
                "metadata": metadata(body), **extra}
        if origin == "local_baseline":
            item["imported_at"] = acquired_at
        self.resources.append(item)
        if origin == "live":
            self.seen[url] = (item, body)
        return item, body

    def get(self, url):
        if url in self.seen:
            return self.seen[url]
        wait = self.delay - (time.monotonic() - self.last_request)
        if wait > 0:
            time.sleep(wait)
        self.last_request = time.monotonic()
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read(12 * 1024 * 1024 + 1)
                if len(body) > 12 * 1024 * 1024:
                    raise ValueError("Source exceeds 12 MiB cap")
                if urllib.parse.urlparse(url).path.endswith(".json"):
                    json.loads(body)  # A 200 HTML error page is not a JSON collection success.
                item, body = self.store(url, body, "live", status=response.status,
                                        etag=response.headers.get("ETag"),
                                        last_modified=response.headers.get("Last-Modified"),
                                        final_url=response.url,
                                        content_type=response.headers.get("Content-Type"))
                print(f"OK {len(body):8d} {url}", flush=True)
                return item, body
        except (OSError, ValueError) as error:
            self.failures.append({"url": url, "attempted_at": utc_now(), "error": str(error)})
            print(f"FAIL {url}: {error}", flush=True)
            return None

    def baseline(self, directory):
        path = directory / "_manifest.json"
        entries = json.loads(path.read_text(encoding="utf-8"))
        urls = []
        for entry in entries:
            if not any(key in entry["path"] for key in ("rogue_6", "blackstream", "rogue-quick-reference")):
                continue
            url = SITE + entry["path"]
            body = (directory / entry["file"]).read_bytes()
            checksum = hashlib.sha256(body).hexdigest()
            if not checksum.startswith(entry["sha256"]):
                raise ValueError(f"Baseline hash mismatch: {entry['file']}")
            self.store(url, body, "local_baseline", local_path=str(directory / entry["file"]),
                       snapshot_label="local export documented as 2026-10-21; per-source timestamps retained")
            urls.append(url)
        return urls

    def site_assets(self):
        page = self.get(SITE + ROUTES[0])
        if not page:
            return []
        entries = re.findall(r'<script[^>]+src=["\']([^"\']+)', page[1].decode("utf-8"))
        if not entries:
            raise ValueError("Site entry script not found")
        main_url = urllib.parse.urljoin(SITE, entries[0])
        main = self.get(main_url)
        if not main:
            return []
        source = main[1].decode("utf-8")
        route_assets = re.findall(r'path:["\'`]([^"\'`]+)["\'`][\s\S]{0,100}?import\(["\'`]([^"\'`]+)', source)
        chosen = []
        for route, relative in route_assets:
            if route in ROUTES or route in ("/archives/:theme()", "/mechanisms/:topicId()", "/nodes/:topicId()", "/pools/:topicId()"):
                chosen.append(urllib.parse.urljoin(main_url, relative))
        loaded = []
        queue = [(url, 0) for url in dict.fromkeys(chosen)]
        processed = set()
        while queue and len(processed) < self.asset_limit:
            url, depth = queue.pop(0)
            if url in processed:
                continue
            processed.add(url)
            fetched = self.get(url)
            if not fetched:
                continue
            record, body = fetched
            text = body.decode("utf-8")
            loaded.append((record, text))
            if depth < 2:
                for relative in re.findall(r'(?:from\s*|import\s*\(?)["\'`]([^"\'`]+)', text):
                    # UI/vendor runtime code is unnecessary for the data audit.
                    if relative.startswith("./") and relative.endswith(".js") and not re.search(r"(?:CTt-lgAj|BDNMzG2s|Dgqgu8wI)", relative):
                        queue.append((urllib.parse.urljoin(url, relative), depth + 1))
        # Record the budget rather than silently claiming complete discovery.
        remaining = sorted({url for url, _ in queue} - processed)
        if remaining:
            self.failures.append({"kind": "asset_discovery_budget", "remaining": len(remaining),
                                  "remaining_urls": remaining, "limit": self.asset_limit})
        return loaded

    def finish(self, template_data=None, operation="collect"):
        now = dt.datetime.now(dt.timezone.utc)
        manifest = {"schemaVersion": 1, "collector": "collect_lubiao.py", "topicId": "rogue_6",
                    "run_at": now.isoformat(), "operation": operation,
                    "resources": self.resources, "failures": self.failures,
                    "semantics": {"snapshots": "immutable content-addressed blobs; unchanged bodies reuse paths",
                                  "counts": "upstream cumulative observations: never append counts across snapshots",
                                  "times": "fetched_at is network acquisition only; imported_at is local baseline ingestion",
                                  "version": "capture date, upstream data update, and game content version are distinct"}}
        if template_data:
            shape_hash = template_definition_hash(template_data)
            template_data["definition_sha256"] = shape_hash
            dump(self.output / "template_definitions.json", template_data)
            derived_path = self.output / "derived" / f"templates-{shape_hash}.json"
            if not derived_path.exists():
                dump(derived_path, template_data)
            manifest["template_definitions"] = {"relative_path": "template_definitions.json", "count": template_data["count"],
                                                 "definition_sha256": shape_hash}
        dump(self.output / "runs" / f"{now.strftime('%Y%m%dT%H%M%S%fZ')}.json", manifest)
        dump(self.output / "latest_manifest.json", manifest)
        return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=PROJECT / "data/sources/lubiao")
    parser.add_argument("--baseline", type=Path, default=Path(r"E:\学校\AI\大肥鱼\data\lubiao"))
    parser.add_argument("--offline", action="store_true", help="Import baseline without network")
    parser.add_argument("--delay", type=float, default=0.5)
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--asset-limit", type=int, default=70)
    args = parser.parse_args()
    collector = Collector(args.output, max(0.25, args.delay), args.timeout, args.asset_limit)
    template_data = None
    urls = collector.baseline(args.baseline)
    if not args.offline:
        for url in urls:
            collector.get(url)
        assets = collector.site_assets()
        more_urls = set()
        for record, text in assets:
            for path in re.findall(r'["\'`](/data/[^"\'`]+\.json)["\'`]', text):
                if "${" not in path and any(key in path for key in ("rogue_6", "blackstream", "reference")):
                    more_urls.add(SITE + path)
            if "occupiedSlots" in text and "sourceId" in text:
                try:
                    extracted = extract_templates(text, {k: record[k] for k in ("url", "sha256", "relative_path", "fetched_at")})
                    if extracted:
                        template_data = extracted
                except (ValueError, IndexError) as error:
                    collector.failures.append({"kind": "template_extraction", "url": record["url"], "error": str(error)})
        for url in sorted(more_urls):
            collector.get(url)
    manifest = collector.finish(template_data, operation="local baseline-only import" if args.offline else "network collect")
    print(json.dumps({"resources": len(manifest["resources"]), "failures": len(manifest["failures"]),
                      "templates": template_data["count"] if template_data else 0,
                      "manifest": str(args.output / "latest_manifest.json")}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
