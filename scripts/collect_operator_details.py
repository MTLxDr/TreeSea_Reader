"""Import full PRTS wikitext caches, or refresh a bounded batch of operator pages.

Local cache timestamps are never represented as remote revisions. Every source
page and every nested calculation template remains available verbatim.
"""
from collections import Counter
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from collect_prts import template_blocks, split_top_level

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/sources/prts/operator_details"
BASELINE = Path(r"E:\学校\AI\大肥鱼\data\prts_operators")


def record(raw, offset):
    parts = split_top_level(raw[2:-2], "|")
    fields, items = {}, []
    for index, argument in enumerate(parts[1:], 1):
        pair = split_top_level(argument, "=", first_only=True)
        key, value = (pair[0].strip(), pair[1].strip()) if len(pair) == 2 else (str(index), argument.strip())
        fields[key] = value
        items.append({"key": key, "value": value})
    return {"template": parts[0].strip(), "fields": fields, "field_items": items,
            "source_offset": offset, "raw_wikitext": raw}


def extract(text):
    kinds = {"skills": [], "talents": [], "potentials": [], "modules": [], "attributes": [], "identity": []}
    section = None
    headings = list(re.finditer(r"^==\s*([^=\n]+?)\s*==\s*$", text, re.M))
    sections = {m.group(1): text[m.end():headings[i+1].start() if i+1 < len(headings) else len(text)]
                for i,m in enumerate(headings)}
    # Archive lore is outside the calculation source; raw full-page text is still saved.
    end = next((m.start() for m in headings if m.group(1) in {"相关道具", "干员档案"}), len(text))
    for offset, raw in template_blocks(text[:end]):
        name = split_top_level(raw[2:-2], "|")[0].strip()
        kind = ("skills" if name in {"技能", "技能2", "技能3"} else
                "talents" if name.startswith("天赋列表") else
                "potentials" if name == "潜能提升" else
                "modules" if name in {"模组", "模组2"} else
                "attributes" if name == "属性" else "identity" if name in {"CharinfoV2", "Charinfo"} else None)
        if kind:
            kinds[kind].append(record(raw, offset))
    for index, skill in enumerate(kinds["skills"], 1):
        skill["index"] = index
        f = skill["fields"]
        skill["levels"] = [{"level": level, "mastery": mastery,
            **{key: f.get(prefix + suffix) for key,suffix in (("description", "描述"), ("initial_sp", "初始"), ("sp_cost", "消耗"), ("duration", "持续"))}}
            for level, mastery, prefix in [(n,0,f"技能{n}") for n in range(1,8)] + [(7,n,f"技能专精{n}") for n in range(1,4)]
            if prefix + "描述" in f]
    return {"components": kinds, "sections": {key: value for key,value in sections.items()
            if key in {"属性", "攻击范围", "天赋", "潜能提升", "技能", "模组"}},
            "interpretation": "Source parameters and notes only; nested numeric templates are preserved, not executed."}


def save_page(item, text, acquisition, revision=None, fetched_at=None):
    content_hash = hashlib.sha256(text.encode()).hexdigest()
    raw_path = OUT / "raw" / (content_hash + ".txt")
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    if not raw_path.exists(): raw_path.write_text(text, encoding="utf-8")
    data = {"operator_id": item["id"], "name": item["name"], "source_url": "https://prts.wiki/w/" + quote(item["name"]),
            "content_sha256": content_hash, "raw_path": raw_path.relative_to(OUT).as_posix(),
            "revision_id": revision, **extract(text)}
    ids = [c["fields"].get("干员id") for c in data["components"]["identity"]]
    if item["id"].startswith("char_") and ids and item["id"] not in ids:
        raise ValueError(f"Operator ID mismatch for {item['name']}: {ids}")
    body = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(body.encode()).hexdigest()
    target = OUT / "objects" / (digest + ".json")
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists(): target.write_text(body, encoding="utf-8")
    return {"operator_id": item["id"], "name": item["name"], "url": data["source_url"],
            "path": target.relative_to(OUT).as_posix(), "sha256": digest, "acquisition": acquisition,
            "revision_id": revision, "fetched_at": fetched_at, "components": {k:len(v) for k,v in data["components"].items()}}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--online",action="store_true")
    parser.add_argument("--names",nargs="*")
    parser.add_argument("--limit",type=int,default=444)
    parser.add_argument("--minimum-age-hours",type=float,default=168)
    args=parser.parse_args()
    OUT.mkdir(parents=True,exist_ok=True)
    catalog=json.loads((OUT.parent/"operator_catalog.json").read_text(encoding="utf-8"))["operators"]
    manifest_path=OUT/"latest_manifest.json"
    previous=json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    pages={p["operator_id"]:p for p in previous.get("pages",[])}
    failures=[]
    selected=[i for i in catalog if not args.names or i["name"] in args.names][:args.limit]
    now=datetime.now(timezone.utc)
    due=[]
    for item in selected:
        old=pages.get(item["id"])
        if not old:
            path=BASELINE/(item["name"]+".txt")
            if path.is_file():
                try: pages[item["id"]]=save_page(item,path.read_text(encoding="utf-8"),"local_baseline")
                except ValueError as error: failures.append({"name":item["name"],"error":str(error)})
        old=pages.get(item["id"],{})
        age=(now-datetime.fromisoformat(old["fetched_at"])).total_seconds()/3600 if old.get("fetched_at") else None
        if args.online and (age is None or age>=args.minimum_age_hours): due.append(item)
    for start in range(0,len(due),5):
        batch=due[start:start+5]
        params={"action":"query","format":"json","formatversion":2,"redirects":1,"prop":"revisions",
                "rvprop":"ids|timestamp|content","rvslots":"main","titles":"|".join(i["name"] for i in batch)}
        try:
            with urlopen(Request("https://prts.wiki/api.php?"+urlencode(params),headers={"User-Agent":"TreeSeaReader/0.3 (rate limited personal reference collector)"}),timeout=25) as response:
                raw=response.read(8_000_001)
            if len(raw)>8_000_000: raise ValueError("Response too large")
            payload=json.loads(raw)
            fetched=datetime.now(timezone.utc).isoformat()
            for page in payload["query"]["pages"]:
                revision=(page.get("revisions") or [{}])[0]
                text=revision.get("slots",{}).get("main",{}).get("content")
                item=next((i for i in batch if i["name"]==page["title"]),None)
                if item and text:
                    pages[item["id"]]=save_page(item,text,"direct_http_api",revision.get("revid"),fetched)
                else: failures.append({"title":page.get("title"),"error":"Missing text or canonical title differs; manual check needed"})
            print(f"PRTS details refreshed {min(start+5,len(due))}/{len(due)}",flush=True)
            time.sleep(.6)
        except (OSError,ValueError,KeyError) as error:
            failures.append({"names":[i["name"] for i in batch],"error":str(error)})
            break
    summary={k:sum(p["components"].get(k,0) for p in pages.values()) for k in ("skills","talents","potentials","modules","attributes")}
    result={"generated_at":now.isoformat(),"pages":list(pages.values()),"failures":failures,
            "coverage":{"operators":len(pages),"catalog":len(catalog),"components":summary},
            "notes":["Local baselines have unknown remote revision and freshness.","All skill ranks, mastery variants, talent/module parameters and full wikitext are retained."]}
    temp=manifest_path.with_suffix(".json.tmp");temp.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8");temp.replace(manifest_path)
    print(json.dumps(result["coverage"],ensure_ascii=False))
    return int(bool(failures))


if __name__=="__main__": raise SystemExit(main())
