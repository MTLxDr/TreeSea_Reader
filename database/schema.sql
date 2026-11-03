PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS database_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
INSERT OR IGNORE INTO database_meta VALUES ('schema_version', '1');

-- Documents are immutable, content-addressed source revisions. Acquisition time
-- and game content version are deliberately separate fields.
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    provider TEXT NOT NULL,
    source_url TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    format TEXT NOT NULL,
    acquisition TEXT NOT NULL,
    source_path TEXT NOT NULL,
    fetched_at TEXT,
    upstream_updated_at TEXT,
    revision_id TEXT,
    content_version TEXT NOT NULL DEFAULT 'unknown',
    imported_at TEXT NOT NULL,
    payload TEXT NOT NULL,
    UNIQUE(provider, source_url, sha256)
);
CREATE TABLE IF NOT EXISTS active_documents (
    provider TEXT NOT NULL,
    source_url TEXT NOT NULL,
    document_id INTEGER NOT NULL REFERENCES documents(id),
    PRIMARY KEY(provider, source_url)
);
CREATE TABLE IF NOT EXISTS source_observations (
    observation_key TEXT PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id),
    acquisition TEXT NOT NULL,
    source_path TEXT NOT NULL,
    fetched_at TEXT,
    recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS import_issues (
    document_id INTEGER NOT NULL REFERENCES documents(id),
    severity TEXT NOT NULL,
    code TEXT NOT NULL,
    detail TEXT NOT NULL,
    UNIQUE(document_id, code, detail)
);

CREATE TABLE IF NOT EXISTS entities (
    document_id INTEGER NOT NULL REFERENCES documents(id),
    topic_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    name TEXT NOT NULL,
    upstream_updated_at TEXT,
    payload_json TEXT NOT NULL,
    PRIMARY KEY(document_id, kind, entity_id)
);
CREATE INDEX IF NOT EXISTS entities_lookup ON entities(kind, entity_id, name);

CREATE TABLE IF NOT EXISTS map_floors (
    document_id INTEGER NOT NULL REFERENCES documents(id),
    floor_id TEXT NOT NULL,
    label TEXT NOT NULL,
    region_name TEXT,
    payload_json TEXT NOT NULL,
    PRIMARY KEY(document_id, floor_id)
);
CREATE TABLE IF NOT EXISTS node_type_statistics (
    document_id INTEGER NOT NULL REFERENCES documents(id),
    floor_id TEXT NOT NULL,
    node_type TEXT NOT NULL,
    sample_count INTEGER NOT NULL CHECK(sample_count >= 0),
    floor_map_sample_count INTEGER,
    denominator TEXT NOT NULL,
    PRIMARY KEY(document_id, floor_id, node_type)
);
CREATE TABLE IF NOT EXISTS node_distance_counts (
    document_id INTEGER NOT NULL,
    floor_id TEXT NOT NULL,
    node_type TEXT NOT NULL,
    distance INTEGER NOT NULL CHECK(distance >= 0),
    observation_count INTEGER NOT NULL CHECK(observation_count >= 0),
    PRIMARY KEY(document_id, floor_id, node_type, distance),
    FOREIGN KEY(document_id, floor_id, node_type)
        REFERENCES node_type_statistics(document_id, floor_id, node_type)
);
CREATE TABLE IF NOT EXISTS template_statistics (
    document_id INTEGER NOT NULL REFERENCES documents(id),
    floor_id TEXT NOT NULL,
    template_id TEXT NOT NULL,
    observation_count INTEGER NOT NULL CHECK(observation_count >= 0),
    floor_sample_count INTEGER NOT NULL CHECK(floor_sample_count >= 0),
    reported_frequency REAL,
    PRIMARY KEY(document_id, floor_id, template_id)
);
CREATE TABLE IF NOT EXISTS map_templates (
    document_id INTEGER NOT NULL REFERENCES documents(id),
    template_id TEXT NOT NULL,
    floor_id TEXT NOT NULL,
    content_version TEXT NOT NULL,
    nodes_json TEXT NOT NULL,
    edges_json TEXT NOT NULL,
    start_json TEXT,
    terminals_json TEXT,
    payload_json TEXT NOT NULL,
    PRIMARY KEY(document_id, template_id)
);

-- A view never adds cumulative snapshots together. Each source URL contributes
-- only its selected revision; a running session can pin document IDs later.
CREATE VIEW IF NOT EXISTS active_node_distance_counts AS
SELECT c.*, d.upstream_updated_at, d.content_version, d.acquisition
FROM node_distance_counts c
JOIN documents d ON d.id = c.document_id
JOIN active_documents a ON a.document_id = d.id;

CREATE VIEW IF NOT EXISTS active_map_templates AS
SELECT t.* FROM map_templates t JOIN active_documents a ON a.document_id=t.document_id;

-- Independent sunset region: no main-map start or frequency semantics.
CREATE TABLE IF NOT EXISTS side_map_templates (
    document_id INTEGER NOT NULL REFERENCES documents(id),
    template_id TEXT NOT NULL,
    floor_id TEXT NOT NULL,
    region_kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY(document_id, template_id)
);
CREATE VIEW IF NOT EXISTS active_side_map_templates AS
SELECT t.* FROM side_map_templates t JOIN active_documents a ON a.document_id=t.document_id;

CREATE TABLE IF NOT EXISTS operator_details (
    document_id INTEGER NOT NULL REFERENCES documents(id),
    operator_id TEXT NOT NULL,
    name TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY(document_id,operator_id)
);
CREATE VIEW IF NOT EXISTS active_operator_details AS
SELECT t.* FROM operator_details t JOIN active_documents a ON a.document_id=t.document_id;
CREATE TABLE IF NOT EXISTS node_count_limits (
    document_id INTEGER NOT NULL REFERENCES documents(id),
    floor_id TEXT NOT NULL,
    node_name TEXT NOT NULL,
    minimum INTEGER NOT NULL,
    maximum INTEGER NOT NULL,
    PRIMARY KEY(document_id,floor_id,node_name)
);
CREATE VIEW IF NOT EXISTS active_node_count_limits AS
SELECT t.* FROM node_count_limits t JOIN active_documents a ON a.document_id=t.document_id;

CREATE TABLE IF NOT EXISTS effect_entries (
    id TEXT PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id),
    category TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    name TEXT NOT NULL,
    variant TEXT NOT NULL,
    side TEXT NOT NULL,
    has_numeric INTEGER NOT NULL,
    review_status TEXT NOT NULL,
    execution_status TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS effects_lookup ON effect_entries(category,name,side);
CREATE TABLE IF NOT EXISTS effect_modifiers (
    effect_id TEXT NOT NULL REFERENCES effect_entries(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    stat TEXT,
    bucket TEXT NOT NULL,
    value REAL,
    evidence TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY(effect_id,ordinal)
);

-- Catalog pages are authoritative for this view. Repeated item examples on
-- practitioner pages and old relay archives remain available as source records.
DROP VIEW IF EXISTS current_prts_catalog;
CREATE VIEW current_prts_catalog AS
SELECT e.document_id,e.kind,e.entity_id,e.name,
       json_extract(e.payload_json,'$.fields.效果') AS effect_wikitext,
       json_extract(e.payload_json,'$.fields.稀有度') AS rarity,
       json_extract(e.payload_json,'$.fields.售价') AS price,
       e.payload_json,d.source_url,d.revision_id,d.upstream_updated_at
FROM entities e
JOIN documents d ON d.id=e.document_id
JOIN active_documents a ON a.document_id=d.id
WHERE d.provider='prts' AND ((e.kind='prts_relic' AND json_extract(d.payload,'$.title')='沉沦者的黑流树海/拟造物质编目')
   OR (e.kind='prts_part' AND json_extract(d.payload,'$.title')='沉沦者的黑流树海/引擎配件目录'));
