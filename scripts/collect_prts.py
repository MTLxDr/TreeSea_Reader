"""Bounded PRTS collector and offline importer; Python standard library only.

The HTTP client never bypasses a rejected request. Browser API exports can be
imported independently when the local HTTP route is unavailable. Effect fields
remain source text, not executable game mechanics.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

TOPIC = "沉沦者的黑流树海"
API = "https://prts.wiki/api.php"
CORE_TITLES = [TOPIC] + [TOPIC + "/" + name for name in (
    "拟造物质编目", "引擎配件目录", "黑流数据库", "事件一览", "诡意行商",
    "秘境行商", "应急助力", "实践者列表", "关卡敌人数量速查",
)]
TARGET_TEMPLATES = {"收藏品", ":沉沦者的黑流树海/零件"}
PROJECT = Path(__file__).resolve().parents[1]


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def atomic_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def protected_end(text: str, pos: int) -> int | None:
    """Skip comments and literal tag bodies before interpreting wiki delimiters."""
    if text.startswith("<!--", pos):
        end = text.find("-->", pos + 4)
        return len(text) if end < 0 else end + 3
    if text[pos:pos + 1] == "<":
        match = re.match(r"<(nowiki|pre|source|syntaxhighlight)\b[^>]*>", text[pos:], re.I)
        if match:
            end_open = pos + match.end()
            if match.group(0).endswith("/>"):
                return end_open
            closing = re.search(r"</" + match.group(1) + r"\s*>", text[end_open:], re.I)
            return len(text) if closing is None else end_open + closing.end()
    return None


def template_blocks(text: str):
    """Yield balanced {{...}} blocks, including nested ones, in source order."""
    stack: list[tuple[str, int]] = []
    found: list[tuple[int, int]] = []
    pos = 0
    while pos < len(text):
        end = protected_end(text, pos)
        if end is not None:
            pos = end
        elif text.startswith("{{{", pos):
            stack.append(("parameter", pos))
            pos += 3
        elif text.startswith("{{", pos):
            stack.append(("template", pos))
            pos += 2
        elif stack and stack[-1][0] == "parameter" and text.startswith("}}}", pos):
            stack.pop()
            pos += 3
        elif stack and stack[-1][0] == "template" and text.startswith("}}", pos):
            _, start = stack.pop()
            pos += 2
            found.append((start, pos))
        else:
            pos += 1
    for start, end in sorted(found):
        yield start, text[start:end]


def split_top_level(text: str, separator: str, first_only: bool = False) -> list[str]:
    """Split wiki template parameters without splitting nested templates/links."""
    curly: list[str] = []
    links = 0
    square = 0
    parts: list[str] = []
    start = pos = 0
    while pos < len(text):
        end = protected_end(text, pos)
        if end is not None:
            pos = end
        elif text.startswith("{{{", pos):
            curly.append("parameter")
            pos += 3
        elif text.startswith("{{", pos):
            curly.append("template")
            pos += 2
        elif curly and curly[-1] == "parameter" and text.startswith("}}}", pos):
            curly.pop()
            pos += 3
        elif curly and curly[-1] == "template" and text.startswith("}}", pos):
            curly.pop()
            pos += 2
        elif text.startswith("[[", pos):
            links += 1
            pos += 2
        elif links and text.startswith("]]", pos):
            links -= 1
            pos += 2
        elif text[pos] == "[" and not links:
            square += 1
            pos += 1
        elif text[pos] == "]" and square and not links:
            square -= 1
            pos += 1
        elif not curly and not links and not square and text[pos] == separator:
            parts.append(text[start:pos])
            start = pos + 1
            pos += 1
            if first_only:
                return parts + [text[start:]]
        else:
            pos += 1
    return parts + [text[start:]]


def extract_templates(wikitext: str) -> list[dict]:
    result = []
    for offset, raw in template_blocks(wikitext):
        arguments = split_top_level(raw[2:-2], "|")
        name = arguments[0].strip().replace("_", " ")
        if name not in TARGET_TEMPLATES:
            continue
        fields: dict[str, str] = {}
        field_items = []
        positional = 1
        for argument in arguments[1:]:
            pair = split_top_level(argument, "=", first_only=True)
            if len(pair) == 2:
                key, value = pair[0].strip(), pair[1].strip()
            else:
                key, value = str(positional), argument.strip()
                positional += 1
            fields[key] = value
            field_items.append({"key": key, "value": value})
        result.append({"template": name, "fields": fields, "field_items": field_items,
                       "source_offset": offset, "raw_wikitext": raw})
    return result


def self_check() -> None:
    sample = """<!--{{收藏品|名字=假记录}}-->
{{收藏品|名字=测试|效果={{color|red|[[目标|文字含=与|]]}}；<nowiki>a|b=c</nowiki>|说明={{{说明|默认值}}}|重复=甲|重复=乙}}
{{:沉沦者的黑流树海/零件|编号=G01|效果=[[种子|种子说明]]|自然物}}
"""
    items = extract_templates(sample)
    assert len(items) == 2
    assert items[0]["fields"]["效果"] == "{{color|red|[[目标|文字含=与|]]}}；<nowiki>a|b=c</nowiki>"
    assert items[0]["fields"]["说明"] == "{{{说明|默认值}}}"
    assert items[0]["fields"]["重复"] == "乙"
    assert [item["value"] for item in items[0]["field_items"] if item["key"] == "重复"] == ["甲", "乙"]
    assert items[1]["fields"]["1"] == "自然物"


class Collector:
    def __init__(self, output: Path):
        self.output = output.resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.pages: dict[str, dict] = {}
        self.failures: list[dict] = []
        self.started_at = utcnow()
        self.last_request = 0.0
        previous = self.output / "latest_manifest.json"
        if previous.exists():
            for page in read_json(previous).get("pages", []):
                if page.get("status") == "ok":
                    self.pages[page["title"]] = page

    def import_payload(self, payload: dict, origin: str, fetched_at: str, source_path: str | None = None) -> int:
        if "error" in payload:
            raise ValueError("MediaWiki API error: " + json.dumps(payload["error"], ensure_ascii=False))
        pages = payload.get("query", {}).get("pages", [])
        if isinstance(pages, dict):
            pages = list(pages.values())
        imported = 0
        for page in pages:
            title = page.get("title", "")
            revision = (page.get("revisions") or [{}])[0]
            slot = revision.get("slots", {}).get("main", {})
            content = slot.get("content", slot.get("*", revision.get("*")))
            if page.get("missing") is not None or content is None:
                self.failures.append({"title": title, "origin": origin, "fetched_at": fetched_at,
                                      "error": "Missing page or revision content", "source_path": source_path})
                continue
            url = "https://prts.wiki/w/" + quote(title.replace(" ", "_"), safe="/")
            templates = extract_templates(content)
            obj = {"schema_version": 1, "source": "prts", "title": title, "url": url,
                   "page_id": page.get("pageid"), "revision_id": revision.get("revid"),
                   "page_updated_at": revision.get("timestamp"), "format": "mediawiki_wikitext",
                   "wikitext": content,
                   "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                   "structured_data": {"parser": "balanced_wiki_templates_v1", "templates": templates,
                                       "interpretation": "Source fields only; effects are not calculation rules."}}
            body = canonical(obj)
            digest = hashlib.sha256(body).hexdigest()
            relative = Path("objects") / (digest + ".json")
            destination = self.output / relative
            if not destination.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(body)
            entry = {"title": title, "url": url, "status": "ok", "origin": origin,
                     "fetched_at": fetched_at, "revision_id": revision.get("revid"),
                     "page_updated_at": revision.get("timestamp"), "sha256": digest,
                     "content_sha256": obj["content_sha256"], "path": relative.as_posix(),
                     "format": "mediawiki_wikitext", "template_count": len(templates)}
            if source_path:
                entry["capture_path"] = source_path
            old = self.pages.get(title)
            old_revision = (old or {}).get("revision_id") or 0
            new_revision = revision.get("revid") or 0
            if old is None or new_revision > old_revision or (
                new_revision == old_revision and fetched_at >= old.get("fetched_at", "")
            ):
                self.pages[title] = entry
            imported += 1
        return imported

    def import_exports(self, directory: Path) -> int:
        imported = 0
        for path in sorted(directory.glob("*.json")):
            try:
                export = read_json(path)
                imported += self.import_payload(export.get("payload", export),
                    export.get("method", "offline_api_export"), export.get("fetched_at", "unknown"), str(path.resolve()))
            except (ValueError, OSError, TypeError) as error:
                self.failures.append({"origin": "offline_api_export", "path": str(path), "error": str(error)})
        return imported

    def fetch(self, titles: list[str], delay: float, timeout: float, retries: int, max_bytes: int) -> bool:
        params = {"action": "query", "format": "json", "formatversion": 2, "maxlag": 5,
                  "prop": "revisions", "rvprop": "ids|timestamp|content", "rvslots": "main",
                  "redirects": 1, "titles": "|".join(titles)}
        url = API + "?" + urlencode(params)
        for attempt in range(retries + 1):
            time.sleep(max(0, delay - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            try:
                request = Request(url, headers={"User-Agent": "ShuhaiReader/0.1 (personal reference collector)",
                                               "Accept": "application/json"})
                with urlopen(request, timeout=timeout) as response:
                    raw = response.read(max_bytes + 1)
                if len(raw) > max_bytes:
                    raise ValueError("Response exceeds configured byte limit")
                payload = json.loads(raw)
                self.import_payload(payload, "direct_http_api", utcnow())
                return True
            except (HTTPError, URLError, OSError, ValueError) as error:
                status = getattr(error, "code", None)
                retryable = status in (429, 500, 502, 503, 504) or isinstance(error, (URLError, TimeoutError))
                if retryable and attempt < retries:
                    time.sleep(min(10, 2 ** attempt))
                    continue
                self.failures.append({"url": url, "titles": titles, "origin": "direct_http_api",
                    "fetched_at": utcnow(), "http_status": status, "attempts": attempt + 1, "error": str(error)})
                print(f"PRTS HTTP collection stopped: {error}", file=sys.stderr)
                return False
        return False

    def save_manifest(self) -> dict:
        templates = Counter()
        catalogs = {}
        for page in self.pages.values():
            obj = read_json(self.output / page["path"])
            extracted = obj["structured_data"]["templates"]
            templates.update(t["template"] for t in extracted)
            if page["title"] in (TOPIC + "/拟造物质编目", TOPIC + "/引擎配件目录"):
                identifiers = [t["fields"].get("ID") for t in extracted]
                frequencies = Counter(identifier for identifier in identifiers if identifier)
                catalogs[page["title"]] = {
                    "template_count": len(extracted), "unique_id_count": len(frequencies),
                    "missing_id_count": sum(not identifier for identifier in identifiers),
                    "duplicate_ids": [identifier for identifier, count in frequencies.items() if count > 1],
                }
        manifest = {"schema_version": 1, "source": "prts", "scope": TOPIC,
                    "generated_at": utcnow(), "started_at": self.started_at,
                    "pages": sorted(self.pages.values(), key=lambda p: p["title"]), "failures": self.failures,
                    "summary": {"page_count": len(self.pages), "templates": dict(templates),
                                "catalogs": catalogs},
                    "notes": ["fetched_at is capture time; page_updated_at is MediaWiki revision time.",
                              "Game/DLC version is not inferred from either timestamp.",
                              "Object hashes exclude capture time; repeated imports do not duplicate content."]}
        atomic_json(self.output / "latest_manifest.json", manifest)
        snapshot_name = (manifest["generated_at"].replace(":", "").replace("-", "")
                         + "-" + hashlib.sha256(canonical(manifest)).hexdigest()[:12] + ".json")
        atomic_json(self.output / "manifests" / snapshot_name, manifest)
        return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=PROJECT / "data/sources/prts")
    parser.add_argument("--import-exports", type=Path, help="Directory of saved MediaWiki query API JSON exports")
    parser.add_argument("--online", action="store_true", help="Try direct HTTP API after importing saved exports")
    parser.add_argument("--title", action="append", help="Explicit page title; repeatable, no recursive crawl")
    parser.add_argument("--page-limit", type=int, default=20)
    parser.add_argument("--delay", type=float, default=1.2)
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--max-response-bytes", type=int, default=5_000_000)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
    if not 1 <= args.page_limit <= 50 or args.delay < 0.5 or not 0 <= args.retries <= 2:
        parser.error("page-limit must be 1..50, delay >= 0.5, retries 0..2")
    collector = Collector(args.output)
    export_dir = args.import_exports or args.output / "api_exports"
    imported = collector.import_exports(export_dir)
    online_ok = True
    if args.online:
        titles = list(dict.fromkeys(args.title or CORE_TITLES))[:args.page_limit]
        for start in range(0, len(titles), 5):
            if not collector.fetch(titles[start:start + 5], args.delay, args.timeout,
                                   args.retries, args.max_response_bytes):
                online_ok = False
                break
    manifest = collector.save_manifest()
    print(json.dumps({"imported_pages": imported, "manifest": str(args.output.resolve() / "latest_manifest.json"),
                      **manifest["summary"], "failed_requests": len(manifest["failures"])}, ensure_ascii=False))
    return 0 if online_ok and not manifest["failures"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
