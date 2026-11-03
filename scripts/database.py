"""Build/query the first source-traceable Blackstream SQLite database.

Standard library only. Local source files are read without modification.
Statistics are observed counts, not a claim about the game's hidden generator.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "reader.sqlite3"
BASELINE = Path(r"E:\学校\AI\大肥鱼\data\lubiao")
TOPIC = "rogue_6"


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def connect(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript((ROOT / "database" / "schema.sql").read_text(encoding="utf-8"))
    # Correct early local-import provenance without presenting local copy time
    # as a successful network fetch. Raw collector run manifests remain intact.
    for row in db.execute("SELECT * FROM source_observations WHERE acquisition IN ('local_baseline','local_snapshot') AND fetched_at IS NOT NULL").fetchall():
        key = hashlib.sha256(dumps([row["document_id"], row["acquisition"], row["source_path"], None]).encode()).hexdigest()
        db.execute("INSERT OR IGNORE INTO source_observations VALUES (?,?,?,?,?,?)",
                   (key, row["document_id"], row["acquisition"], row["source_path"], None, row["recorded_at"]))
        db.execute("DELETE FROM source_observations WHERE observation_key=?", (row["observation_key"],))
    return db


def issue(db, doc_id, code, detail, severity="warning"):
    db.execute("INSERT OR IGNORE INTO import_issues VALUES (?,?,?,?)",
               (doc_id, severity, code, detail))


def entity(db, doc_id, kind, key, item, name=None, updated=None):
    db.execute("INSERT OR REPLACE INTO entities VALUES (?,?,?,?,?,?,?)",
               (doc_id, TOPIC, kind, str(key), str(name or item.get("name") or key),
                updated, dumps(item)))


def normalize(db, doc_id, filename, data):
    """Typed tables for counts/templates; structured entities keep raw fields."""
    if not isinstance(data, dict):
        return
    if filename == "operator_catalog.json":
        for row in data.get("operators", []):
            entity(db, doc_id, "prts_operator", row["id"], row)
    if "node-distance-frequencies" in filename:
        for floor in data.get("floors", []):
            fid = str(floor["id"])
            for row in floor.get("rows", []):
                total = row["sampleCount"]
                db.execute("INSERT OR REPLACE INTO node_type_statistics VALUES (?,?,?,?,?,?)",
                           (doc_id, fid, row["name"], total, floor.get("mapSampleCount"),
                            data.get("denominator", "unknown")))
                counts = row.get("counts", {})
                if sum(counts.values()) != total:
                    issue(db, doc_id, "distance_count_mismatch", f"{fid}/{row['name']}")
                for distance, count in counts.items():
                    db.execute("INSERT OR REPLACE INTO node_distance_counts VALUES (?,?,?,?,?)",
                               (doc_id, fid, row["name"], int(distance), count))
    elif "foundation-map-constructions" in filename:
        for floor in data.get("floors", []):
            if sum(r["count"] for r in floor.get("rows", [])) != floor["sampleCount"]:
                issue(db, doc_id, "template_count_mismatch", str(floor["id"]))
            for row in floor.get("rows", []):
                db.execute("INSERT OR REPLACE INTO template_statistics VALUES (?,?,?,?,?,?)",
                           (doc_id, str(floor["id"]), row["templateId"], row["count"],
                            floor["sampleCount"], row.get("frequency")))
    elif "foundation-floors" in filename:
        for floor in data.get("floors", []) + data.get("specialLayers", []):
            fid = str(floor.get("id", floor.get("floor", "unknown")))
            db.execute("INSERT OR REPLACE INTO map_floors VALUES (?,?,?,?,?)",
                       (doc_id, fid, floor.get("name", fid), floor.get("zoneName"), dumps(floor)))
    elif filename.startswith("archives_blackstream"):
        updated = data.get("classifier", {}).get("sourceGeneratedAt")
        for item in data.get("classifier", {}).get("items", []):
            # A game item ID can have several levels/effect variants. Keep each
            # distinct source record, with its canonical itemId in payload_json.
            key = (item.get("itemId") or item["id"]) + ":" + hashlib.sha256(
                dumps(item).encode("utf-8")).hexdigest()[:16]
            entity(db, doc_id, item.get("dataType", "archive_item"),
                   key, item, updated=updated)
        prts = data.get("prts", {})
        for topic in prts.get("topics", {}).values():
            for item in topic.get("items", []):
                entity(db, doc_id, "prts_relic_reference", "base:" + str(item.get("sourceIconId") or item["number"]),
                       {**item, "_dataset_version": "base"}, updated=prts.get("syncedAt"))
        for version, topic in data.get("classifier", {}).get("versionedCollectibles", {}).items():
            for item in topic.get("items", []):
                entity(db, doc_id, "prts_relic_reference", version + ":" + str(item.get("sourceIconId") or item["number"]),
                       {**item, "_dataset_version": version})
        pools_data = data.get("pools", {})
        # defaultVersionId selects the site's UI version; it does not relabel
        # the root legacy pool array. Keep that source partition independently.
        versions = {"base": pools_data.get("pools", [])}
        versions.update(pools_data.get("dataVersions", {}))
        for version, pools in versions.items():
            if isinstance(pools, dict):
                pools = pools.get("pools", [])
            for pool in pools:
                key = pool.get("id") or pool.get("poolId") or hashlib.sha256(dumps(pool).encode()).hexdigest()
                entity(db, doc_id, "pool", str(version) + ":" + str(key), {**pool, "_dataset_version": version})
    elif filename == "battles_rogue_6.json":
        for stage in data.get("stages", []):
            entity(db, doc_id, "stage", stage["id"], stage)
    elif filename == "battles_rogue_6-details.json":
        for key, stage in data.get("stages", {}).items():
            entity(db, doc_id, "stage_detail", key, stage, updated=data.get("syncedAt"))
            for enemy in stage.get("enemies", []):
                # The same enemy ID can have several levels/variants in stages.
                ekey = f"{key}:{enemy['id']}:{enemy.get('level', 0)}"
                entity(db, doc_id, "stage_enemy", ekey, enemy, updated=data.get("syncedAt"))
    elif filename == "encounters_rogue_6.json":
        for key, value in data.get("events", {}).items():
            item = value if isinstance(value, dict) else {"value": value}
            entity(db, doc_id, "event", key, item)
    elif "wikitext" in data and "title" in data:
        entity(db, doc_id, "prts_page", data["title"], data, data["title"], data.get("page_updated_at"))
        for record in data.get("structured_data", {}).get("templates", []):
            fields = record.get("fields", {})
            template = record.get("template", "").strip()
            kind = "prts_relic" if template == "收藏品" else "prts_part"
            key = fields.get("iconId") or fields.get("ID") or fields.get("名称")
            if not key:
                issue(db, doc_id, "prts_template_without_id", template)
                continue
            entity(db, doc_id, kind, key, record, fields.get("名称"), data.get("page_updated_at"))
    elif filename.startswith(("foundation-", "utopia-")):
        # Preserve mechanism payloads without interpreting prose as executable rules.
        entity(db, doc_id, "mechanism_dataset", filename.removesuffix(".json"), data)


def add_document(db, path, *, provider, url, acquisition, fetched_at=None,
                 updated=None, revision=None, version="unknown", filename=None):
    if acquisition in {"local_baseline", "local_snapshot"}:
        fetched_at = None
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    payload = raw.decode("utf-8-sig")
    try:
        data = json.loads(payload)
        fmt = "json"
    except json.JSONDecodeError:
        data, fmt = None, path.suffix.lstrip(".") or "text"
    if isinstance(data, dict):
        updated = updated or data.get("updatedAt") or data.get("syncedAt")
    cur = db.execute(
        """INSERT OR IGNORE INTO documents
        (provider,source_url,sha256,format,acquisition,source_path,fetched_at,
         upstream_updated_at,revision_id,content_version,imported_at,payload)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (provider, url, sha, fmt, acquisition, str(path.resolve()), fetched_at,
         updated, str(revision) if revision is not None else None, version or "unknown",
         utcnow(), payload))
    doc_id = db.execute("SELECT id FROM documents WHERE provider=? AND source_url=? AND sha256=?",
                        (provider, url, sha)).fetchone()[0]
    # Rebuild only derived rows for this immutable document. This makes parser
    # improvements and repeated imports safe without accumulating observations.
    for table in ("node_distance_counts", "node_type_statistics", "template_statistics",
                  "map_floors", "entities", "import_issues"):
        db.execute(f"DELETE FROM {table} WHERE document_id=?", (doc_id,))
    normalize(db, doc_id, filename or path.name, data)
    observation_key = hashlib.sha256(dumps([doc_id, acquisition, str(path.resolve()), fetched_at]).encode()).hexdigest()
    db.execute("INSERT OR IGNORE INTO source_observations VALUES (?,?,?,?,?,?)",
               (observation_key, doc_id, acquisition, str(path.resolve()), fetched_at, utcnow()))
    current = db.execute("SELECT document_id FROM active_documents WHERE provider=? AND source_url=?",
                         (provider, url)).fetchone()
    # Importing an old local baseline must not replace a previously fetched
    # source revision if a later network update fails.
    current_has_live = current and db.execute("""SELECT 1 FROM source_observations
        WHERE document_id=? AND acquisition NOT IN ('local_baseline','local_snapshot') LIMIT 1""",
        (current[0],)).fetchone()
    if not (acquisition in {"local_baseline", "local_snapshot"} and current_has_live):
        db.execute("INSERT OR REPLACE INTO active_documents VALUES (?,?,?)", (provider, url, doc_id))
    return doc_id, bool(cur.rowcount)


def baseline_sources(folder):
    manifest_path = folder / "_manifest.json"
    if not manifest_path.exists():
        return
    for item in json.loads(manifest_path.read_text(encoding="utf-8-sig")):
        name = item["file"]
        if "rogue_6" not in name and name not in {"archives_blackstream.json", "rogue-quick-reference.json"}:
            continue
        path = folder / name
        if path.exists():
            yield path, dict(provider="lubiao", url="https://www.lubiao.wiki" + item["path"],
                             acquisition="local_baseline", filename=name)


def import_templates(db, path):
    """Read the collector's structured template export (adapter kept explicit)."""
    if not path.exists():
        return
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    # Keep only stable geometry in the content-addressed derived document;
    # extraction/retrieval timestamps belong to source observations.
    source = data.get("source", {})
    source_url = "https://www.lubiao.wiki/tools/blackstream-route#template-definitions"
    stable = {k: data[k] for k in ("schemaVersion", "topicId", "coordinateSystem", "contentVersion", "templates") if k in data}
    stable["source_sha256"] = source.get("sha256")
    stable["source_url"] = source.get("url", data.get("source_url"))
    stable_path = path.parent / "derived" / ("db-templates-" + hashlib.sha256(dumps(stable).encode()).hexdigest() + ".json")
    stable_path.parent.mkdir(parents=True, exist_ok=True)
    if not stable_path.exists():
        stable_path.write_text(dumps(stable), encoding="utf-8")
    doc_id, _ = add_document(db, stable_path, provider="lubiao", url=source_url,
                             acquisition="live_derived", fetched_at=source.get("fetched_at"),
                             version=data.get("contentVersion", "unknown"))
    for item in data.get("templates", []):
        key = item.get("sourceId") or item.get("template_id") or item.get("templateId") or item.get("id")
        nodes = item.get("occupiedSlots", item.get("occupied_slots", item.get("nodes", [])))
        edges = item.get("edges", [])
        if not key or not nodes:
            issue(db, doc_id, "template_missing_geometry", str(key))
            continue
        node_keys = {dumps(n) for n in nodes}
        for edge in edges:
            if isinstance(edge, list) and len(edge) == 2 and isinstance(edge[0], list):
                if any(dumps(endpoint) not in node_keys for endpoint in edge):
                    issue(db, doc_id, "template_edge_outside_nodes", str(key), "error")
        db.execute("INSERT OR REPLACE INTO map_templates VALUES (?,?,?,?,?,?,?,?,?)",
                   (doc_id, str(key), str(item.get("floor_id", item.get("floor", "unknown"))),
                    item.get("content_version", data.get("contentVersion", "unknown")),
                    dumps(nodes), dumps(edges), dumps(item.get("startSlot", item.get("start"))),
                    dumps(item.get("terminalSlots", item.get("terminals"))), dumps(item)))


def import_side_templates(db, path):
    if not path.exists():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    stable = {k: data[k] for k in ("schemaVersion", "topicId", "coordinateSystem", "contentVersion", "templates")}
    stable["source"] = {k: data["source"][k] for k in ("url", "sha256")}
    stable["coordinateEvidence"] = data["coordinateEvidence"]
    target = path.parent / "derived" / ("db-side-templates-" + hashlib.sha256(dumps(stable).encode()).hexdigest() + ".json")
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_text(dumps(stable), encoding="utf-8")
    doc_id, _ = add_document(db, target, provider="lubiao",
                            url="https://www.lubiao.wiki/tools/blackstream-route#sunset-definitions",
                            acquisition="live_derived", fetched_at=data["source"].get("fetched_at"), version="unknown")
    for item in data["templates"]:
        nodes = set(map(tuple, item["occupiedSlots"]))
        if item["gridShape"] != [3, 3] or tuple(item["fateSlot"]) not in nodes:
            raise ValueError("Invalid sunset geometry")
        if any(tuple(p) not in nodes for edge in item["edges"] for p in edge):
            raise ValueError("Sunset edge outside occupied slots")
        db.execute("INSERT OR REPLACE INTO side_map_templates VALUES (?,?,?,?,?)",
                   (doc_id, item["sourceId"], str(item["floor"]), "sunset", dumps(item)))


def ingest_live(db, root):
    combat_manifest=root/'combat_prts/latest_manifest.json'
    if combat_manifest.exists():
        manifest=json.loads(combat_manifest.read_text(encoding='utf-8'))
        for page in manifest.get('pages',[]):
            path=combat_manifest.parent/page['path']
            if not path.resolve().is_relative_to(combat_manifest.parent.resolve()):raise ValueError('Combat path escapes folder')
            if hashlib.sha256(path.read_bytes()).hexdigest()!=page['sha256']:raise ValueError('Combat source checksum mismatch')
            add_document(db,path,provider='prts_combat',url=page['url'],acquisition=page.get('origin','direct_http_api'),
                fetched_at=page.get('fetched_at'),updated=page.get('page_updated_at'),revision=page.get('revision_id'))
    lubiao_manifest = root / "lubiao" / "latest_manifest.json"
    if lubiao_manifest.exists():
        manifest = json.loads(lubiao_manifest.read_text(encoding="utf-8-sig"))
        for item in manifest.get("resources", []):
            rel = item.get("relative_path")
            if not rel or not item.get("url"):
                continue
            path = root / "lubiao" / rel
            if not path.resolve().is_relative_to((root / "lubiao").resolve()):
                raise ValueError("Source path escapes its provider directory")
            if not path.is_file():
                continue
            if item.get("sha256") and hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
                raise ValueError(f"Source checksum mismatch: {rel}")
            name = urlparse(item["url"]).path.removeprefix("/data/").replace("/", "_")
            # JSON content feeds the database; JS/HTML remain collector provenance.
            if not name.endswith(".json"):
                continue
            add_document(db, path, provider="lubiao", url=item["url"],
                         acquisition=item.get("origin", "live"), fetched_at=item.get("fetched_at"),
                         updated=item.get("metadata", {}).get("updatedAt"),
                         version=item.get("metadata", {}).get("dataVersion", "unknown"), filename=name)
    prts_manifest = root / "prts" / "latest_manifest.json"
    if prts_manifest.exists():
        manifest = json.loads(prts_manifest.read_text(encoding="utf-8-sig"))
        for item in manifest.get("pages", []):
            rel = item.get("path")
            if not rel or not item.get("url"):
                continue
            path = root / "prts" / rel
            if not path.resolve().is_relative_to((root / "prts").resolve()):
                raise ValueError("Source path escapes its provider directory")
            if path.is_file():
                if item.get("sha256") and hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
                    raise ValueError(f"Source checksum mismatch: {rel}")
                add_document(db, path, provider="prts", url=item["url"],
                             acquisition=item.get("origin", "live"), fetched_at=item.get("fetched_at"),
                             updated=item.get("page_updated_at"), revision=item.get("revision_id"))
    import_templates(db, root / "lubiao" / "template_definitions.json")
    import_side_templates(db, root / "lubiao" / "side_map_definitions.json")
    count_limits = root / "lubiao/node_count_limits.json"
    if count_limits.exists():
        data = json.loads(count_limits.read_text(encoding="utf-8"))
        doc_id,_ = add_document(db,count_limits,provider="lubiao",
            url="https://www.lubiao.wiki/tools/blackstream-route#node-count-limits",acquisition="live_derived",updated=data["updated_at"])
        for row in data["rows"]:
            if row["label"] == "地图节点总数": continue
            for floor, bounds in zip(data["floors"],row["values"]):
                if bounds is None:continue  # Blank means no named observation, not a zero limit.
                lo,hi = bounds
                db.execute("INSERT OR REPLACE INTO node_count_limits VALUES (?,?,?,?,?)",(doc_id,floor,row["label"],lo,hi))
    detail_manifest = root / "prts/operator_details/latest_manifest.json"
    if detail_manifest.exists():
        data=json.loads(detail_manifest.read_text(encoding="utf-8"))
        folder=detail_manifest.parent
        for page in data["pages"]:
            path=folder/page["path"]
            if not path.resolve().is_relative_to(folder.resolve()): raise ValueError("Operator source path escapes folder")
            if hashlib.sha256(path.read_bytes()).hexdigest()!=page["sha256"]: raise ValueError("Operator detail checksum mismatch")
            payload=json.loads(path.read_text(encoding="utf-8"))
            doc_id,_=add_document(db,path,provider="prts",url=page["url"],acquisition=page["acquisition"],
                revision=page.get("revision_id"),fetched_at=page.get("fetched_at"),updated=payload.get("page_updated_at"))
            db.execute("INSERT OR REPLACE INTO operator_details VALUES (?,?,?,?)",(doc_id,page["operator_id"],page["name"],dumps(payload)))
    operators = root / "prts" / "operator_catalog.json"
    if operators.exists():
        data = json.loads(operators.read_text(encoding="utf-8"))
        # Retrieval time is metadata, not a new content revision.
        stable = dict(data)
        stable["source"] = {k: data["source"][k] for k in ("url", "sha256")}
        target = operators.parent / "derived" / ("operators-" + hashlib.sha256(dumps(stable).encode()).hexdigest() + ".json")
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_text(dumps(stable), encoding="utf-8")
        add_document(db, target, provider="prts", url=data["source"]["url"], acquisition="live_derived",
                     fetched_at=data["source"].get("fetched_at"), filename="operator_catalog.json")


def status(db):
    tables = ["documents", "active_documents", "source_observations", "entities", "map_floors", "node_type_statistics",
              "node_distance_counts", "template_statistics", "map_templates", "side_map_templates", "operator_details", "node_count_limits", "effect_entries", "effect_modifiers", "import_issues"]
    return {"tables": {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables},
            "current_prts_catalog": dict(db.execute("SELECT kind, COUNT(*) FROM current_prts_catalog GROUP BY kind")),
            "entity_kinds": dict(db.execute("SELECT kind, COUNT(*) FROM entities GROUP BY kind")),
            "issues": [dict(r) for r in db.execute("SELECT severity,code,detail FROM import_issues")],
            "integrity": db.execute("PRAGMA integrity_check").fetchone()[0],
            "foreign_key_issues": len(db.execute("PRAGMA foreign_key_check").fetchall())}


def probability(db, floor, distance, types, document_id=None):
    if document_id is None:
        rows = db.execute("""SELECT DISTINCT document_id FROM active_node_distance_counts
                            WHERE floor_id=? ORDER BY document_id DESC""", (floor,)).fetchall()
        if len(rows) != 1:
            raise ValueError("Expected exactly one active distance dataset; pin --document-id explicitly.")
        document_id = rows[0][0]
    available = {r[0] for r in db.execute("SELECT node_type FROM node_type_statistics WHERE document_id=? AND floor_id=?",
                                         (document_id, floor))}
    if types and set(types) - available:
        raise ValueError("Unknown node types: " + ",".join(sorted(set(types) - available)))
    counts = {r[0]: r[1] for r in db.execute("""SELECT node_type,observation_count FROM node_distance_counts
                    WHERE document_id=? AND floor_id=? AND distance=?""", (document_id, floor, distance))}
    selected = types or sorted(available)
    counts = {t: counts.get(t, 0) for t in selected}
    total = sum(counts.values())
    return {"document_id": document_id, "floor": floor, "distance": distance,
            "estimate": "empirical conditional frequency in the selected observed dataset",
            "conditions": {"node_types": selected}, "sample_count": total,
            "probabilities": {t: {"count": n, "probability": n / total if total else None}
                              for t, n in counts.items()},
            "limitations": ["Unobserved types are not proven impossible.",
                            "No template/marker/reveal-state joint conditioning is claimed.",
                            "Do not add cumulative source snapshots together."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--baseline", type=Path, default=BASELINE)
    build.add_argument("--sources", type=Path, default=ROOT / "data" / "sources")
    sub.add_parser("status")
    query = sub.add_parser("probability")
    query.add_argument("--floor", required=True)
    query.add_argument("--distance", required=True, type=int)
    query.add_argument("--types", nargs="+")
    query.add_argument("--document-id", type=int)
    args = parser.parse_args()
    with connect(args.db) as db:
        if args.command == "build":
            for path, meta in baseline_sources(args.baseline):
                add_document(db, path, **meta)
            ingest_live(db, args.sources)
            output = status(db)
        elif args.command == "status":
            output = status(db)
        else:
            output = probability(db, args.floor, args.distance, args.types, args.document_id)
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
