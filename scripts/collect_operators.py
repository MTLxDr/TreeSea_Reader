"""Collect the public PRTS operator list; preserve HTML and source checksum."""
import argparse
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
URL = "https://prts.wiki/w/%E5%B9%B2%E5%91%98%E4%B8%80%E8%A7%88"
EXCLUSIVE_URL = URL + "/%E4%B8%93%E5%B1%9E%E5%B9%B2%E5%91%98"


class OperatorParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.records = {}

    def handle_starttag(self, tag, attrs):
        data = dict(attrs)
        if data.get("data-char-id") and data.get("data-zh"):
            key = data["data-char-id"]
            self.records[key] = {"id": key, "name": data["data-zh"], "attributes": data}


class ExclusiveParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.records, self.heading, self.in_heading, self.scope = {}, "", False, ""

    def handle_starttag(self, tag, attrs):
        data = dict(attrs)
        if tag == "th":
            self.in_heading, self.heading = True, ""
        title = data.get("title", "")
        if tag == "a" and self.scope and data.get("href", "").startswith("/w/") and title and "(卫戍协议)" not in title:
            key = "prts-exclusive:" + title
            self.records[key] = {"id": key, "name": title, "source_url": EXCLUSIVE_URL,
                "attributes": {"id_type": "PRTS page title", "mode": "集成战略", "section": self.scope}}

    def handle_data(self, text):
        if self.in_heading:
            self.heading += text

    def handle_endtag(self, tag):
        if tag == "th":
            self.in_heading = False
            self.scope = self.heading.strip() if any(s in self.heading for s in ("初始3★", "初始5★")) else ""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    folder = ROOT / "data/sources/prts"
    folder.mkdir(parents=True, exist_ok=True)
    html_path = folder / "operators_page.html"
    meta_path = folder / "operators_fetch.json"
    if not args.offline:
        request = urllib.request.Request(URL, headers={"User-Agent": "TreeSeaReader/0.2 (local catalog research)"})
        with urllib.request.urlopen(request, timeout=25) as response:
            raw = response.read(8_000_001)
        if len(raw) > 8_000_000:
            raise ValueError("Operator page exceeds size limit")
        html_path.write_bytes(raw)
        meta_path.write_text(json.dumps({"url": URL, "fetched_at": datetime.now(timezone.utc).isoformat(),
            "sha256": hashlib.sha256(raw).hexdigest()}, indent=2), encoding="utf-8")
        with urllib.request.urlopen(urllib.request.Request(EXCLUSIVE_URL, headers={"User-Agent": "TreeSeaReader/0.2"}), timeout=25) as response:
            exclusive_raw = response.read(4_000_001)
        if len(exclusive_raw) > 4_000_000:
            raise ValueError("Exclusive page exceeds size limit")
        (folder / "exclusive_operators_page.html").write_bytes(exclusive_raw)
    raw = html_path.read_bytes()
    parser_html = OperatorParser()
    parser_html.feed(raw.decode("utf-8"))
    if len(parser_html.records) < 300:
        raise ValueError("Unexpectedly short PRTS operator list; previous catalog preserved")
    metadata = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {"url": URL, "fetched_at": None}
    exclusive = ExclusiveParser()
    exclusive_raw = (folder / "exclusive_operators_page.html").read_bytes()
    exclusive.feed(exclusive_raw.decode("utf-8"))
    if len(exclusive.records) != 10:
        raise ValueError("Unexpected exclusive operator table; review before replacing catalog")
    archive = folder / "operators/blobs"
    archive.mkdir(parents=True, exist_ok=True)
    for content in (raw, exclusive_raw):
        target = archive / (hashlib.sha256(content).hexdigest() + ".html")
        if not target.exists():
            target.write_bytes(content)
    data = {"schemaVersion": 1, "source": dict(metadata, sha256=hashlib.sha256(raw).hexdigest()),
            "exclusive_source": {"url": EXCLUSIVE_URL, "sha256": hashlib.sha256(exclusive_raw).hexdigest()},
            "operators": sorted(list(parser_html.records.values()) + list(exclusive.records.values()), key=lambda row: row["id"])}
    output = folder / "operator_catalog.json"
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"operators": len(data["operators"]), "output": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
