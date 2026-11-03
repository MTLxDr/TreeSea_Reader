#!/usr/bin/env python3
"""Download referenced Blackstream node PNGs without modifying source pixels."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import io
import json
import re
import struct
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "data/sources/lubiao"
OUTPUT = ROOT / "assets/nodes"
ALIASES = {"unknown-ferocity": ["unknown-fierce"], "unknown-mystery": ["unknown-mysterious"],
           "start": ["current-position"], "empty": ["forest", "clearing"]}


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def source_record(suffix):
    manifest = json.loads((SOURCE_ROOT / "latest_manifest.json").read_text(encoding="utf-8"))
    record = next((r for r in manifest["resources"] if r["origin"] == "live" and r["url"].endswith(suffix)), None)
    if record:
        path = SOURCE_ROOT / record["relative_path"]
        return path.read_bytes(), {"url": record["url"], "sha256": record["sha256"],
                                   "path": str(path.relative_to(ROOT)).replace("\\", "/"),
                                   "fetched_at": record["fetched_at"]}
    inspection = SOURCE_ROOT / "inspection" / suffix
    if not inspection.exists():
        raise FileNotFoundError(f"No previously captured source: {suffix}")
    data = inspection.read_bytes()
    return data, {"url": "https://www.lubiao.wiki/_nuxt/" + suffix, "sha256": sha(data),
                  "path": str(inspection.relative_to(ROOT)).replace("\\", "/"),
                  "fetched_at": None, "origin": "previous public-HTTP inspection capture; exact fetch time unavailable"}


def parse_png(data):
    if not data.startswith(b"\x89PNG\r\n\x1a\n") or data[12:16] != b"IHDR":
        raise ValueError("Source is not a PNG image")
    width, height, bit_depth, color_type = struct.unpack(">IIBB", data[16:26])
    mode = {0: "L", 2: "RGB", 3: "P", 4: "LA", 6: "RGBA"}.get(color_type, "unknown")
    info = {"width": width, "height": height, "bit_depth": bit_depth,
            "color_type": color_type, "mode": mode,
            "has_alpha_channel": color_type in (4, 6)}
    try:
        from PIL import Image
    except ImportError:
        info["alpha_inspection"] = "Pillow unavailable; PNG header only"
    else:
        with Image.open(io.BytesIO(data)) as image:
            alpha = image.convert("RGBA").getchannel("A")
            info["alpha_extrema"] = list(alpha.getextrema())
            info["has_transparent_pixels"] = info["alpha_extrema"][0] < 255
            info["alpha_bbox"] = alpha.getbbox()
            info["alpha_core_bbox"] = alpha.point(lambda x: 255 if x >= 128 else 0).getbbox()
    return info


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--delay", type=float, default=0.4)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location("collect_lubiao", ROOT / "scripts/collect_lubiao.py")
    literal_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(literal_module)

    type_bytes, type_source = source_record("fmCScQ0Q.js")
    index_bytes, index_source = source_record("Dgqgu8wI.js")
    html_bytes, runtime_source = source_record("/tools/blackstream-route")
    type_text, index_text, html = (b.decode("utf-8") for b in (type_bytes, index_bytes, html_bytes))
    start = type_text.index("[[`start`,`起点`]")
    node_types = literal_module.LiteralParser(type_text[start:]).value()
    # Parse data-only index sections; no upstream JavaScript is executed.
    aliases_start = index_text.index("{", index_text.index("aliases:"))
    aliases = literal_module.LiteralParser(index_text[aliases_start:]).value()
    objects_start = index_text.index("{", index_text.index("objects:"))
    objects_end = index_text.index("},responsive:", objects_start) + 1
    objects = dict(re.findall(r'"([^"\\]+)":`([^`]+)`', index_text[objects_start:objects_end]))
    base = re.search(r'assetBaseUrl:"([^"]+)"', html)[1]
    prefix = re.search(r'assetPrefix:"([^"]+)"', html)[1]
    OUTPUT.mkdir(parents=True, exist_ok=True)
    assets, missing, requests = [], [], {}
    selected = [(t, name) for t, name in node_types if t not in
                ("empty", "ideal-source-n0-n11", "ideal-source-n12-plus")]
    selected.append(("mark-1", "流窜“居民”标记"))
    last_request = 0.0
    for node_type, name in selected:
        kind = "mark" if node_type.startswith("mark-") else "node"
        key = f"themes/blackstream/{'marks' if kind == 'mark' else 'nodes'}/{node_type}.png"
        resolved = key
        seen = set()
        while resolved in aliases:
            if resolved in seen:
                raise ValueError(f"Alias cycle: {resolved}")
            seen.add(resolved)
            resolved = aliases[resolved]
        object_key = objects.get(resolved, resolved)
        source_url = base.rstrip("/") + "/" + prefix.strip("/") + "/" + urllib.parse.quote(object_key, safe="/")
        if base.startswith(("https://assets.lubiao.wiki", "https://img.lubiao.wiki")):
            source_url += "?response-policy=corp-v1"
        try:
            if source_url not in requests:
                pause = max(.25, args.delay) - (time.monotonic() - last_request)
                if pause > 0:
                    time.sleep(pause)
                last_request = time.monotonic()
                request = urllib.request.Request(source_url, headers={"User-Agent": "TreeSeaReaderDataCollector/0.1 (public node assets; rate limited)"})
                with urllib.request.urlopen(request, timeout=args.timeout) as response:
                    data = response.read(4 * 1024 * 1024 + 1)
                    if len(data) > 4 * 1024 * 1024:
                        raise ValueError("Asset exceeds 4 MiB cap")
                    info = parse_png(data)
                    timestamp = now()
                file_name = Path(resolved).stem + ".png"
                path = OUTPUT / file_name
                if not path.exists() or sha(path.read_bytes()) != sha(data):
                    path.write_bytes(data)
                requests[source_url] = {"path": str(path.relative_to(ROOT)).replace("\\", "/"),
                                        "sha256": sha(data), "bytes": len(data), "fetched_at": timestamp, **info}
            item = {"type": node_type, "name": name, "kind": kind, "aliases": ALIASES.get(node_type, []),
                    "asset_key": key, "resolved_asset_key": resolved, "source_url": source_url,
                    **requests[source_url]}
            if node_type == "start":
                item["note"] = "Human position marker, opaque black-background source including YOU ARE HERE and white direction lines; visual presence alone does not prove the initial floor origin."
            assets.append(item)
            print(f"OK {node_type}: {item['width']}x{item['height']} {item['mode']}", flush=True)
        except (OSError, ValueError) as error:
            missing.append({"type": node_type, "name": name, "asset_key": key,
                            "source_url": source_url, "error": str(error)})
            print(f"FAIL {node_type}: {error}", flush=True)
    missing.append({"type": "empty", "name": "林间空地", "aliases": ALIASES["empty"],
                    "reason": "No referenced game PNG in node index. Website Qf5PB7ro.js draws an SVG approximation; not downloaded or redrawn as a game original."})
    manifest = {"schemaVersion": 1, "generated_at": now(), "topicId": "rogue_6",
                "path_base": "project_root", "sources": {"node_types": type_source,
                "asset_index": index_source, "runtime_config": runtime_source},
                "runtime_asset_base": base, "runtime_asset_prefix": prefix,
                "pixel_policy": "Unmodified original PNG response bytes; source alpha retained; no generated or redrawn icons.",
                "assets": assets, "missing": missing}
    save_json(OUTPUT / "manifest.json", manifest)
    print(json.dumps({"assets": len(assets), "unique_downloads": len(requests), "missing": len(missing),
                      "manifest": str(OUTPUT / "manifest.json")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
