"""Extract observed floor count ranges from captured public route JS; never execute JS."""
import hashlib
import json
from pathlib import Path
import re
from collect_lubiao import LiteralParser

ROOT=Path(__file__).resolve().parents[1]


def extract(text):
    marker=re.search(r'\w+=\{schemaVersion:1,topicId:`rogue_6`,updatedAt:`([^`]+)`,sampleCount:(\d+),columns:',text)
    if not marker: raise ValueError("Count range dataset changed")
    parser=LiteralParser(text[marker.end():]);columns=parser.value()
    parser.consume(",");parser.consume("rows:");rows=parser.value()
    if columns!=["I","II","III","IV","IV追忆","V"] or len(rows)<15: raise ValueError("Unexpected count range schema")
    return {"updated_at":marker[1],"sample_count":int(marker[2]),"floors":["1","2","3","4","追忆","5"],"rows":rows,
            "interpretation":"Observed count ranges used as model constraints; not a proof of hidden generation rules."}


def main():
    folder=ROOT/"data/sources/lubiao"
    manifest=json.loads((folder/"latest_manifest.json").read_text(encoding="utf-8"))
    sources=[(folder/row["relative_path"],row["url"]) for row in manifest["resources"]
             if row.get("relative_path") and row.get("url","").endswith(".js")]
    sources.append((folder/"inspection/qM9KoKUi.js","https://www.lubiao.wiki/_nuxt/qM9KoKUi.js"))
    for path,url in sources:
        if not path.resolve().is_relative_to(folder.resolve()):raise ValueError("Source escapes folder")
        raw=path.read_bytes()
        try:data=extract(raw.decode("utf-8"))
        except ValueError:continue
        break
    else:raise ValueError("No count range dataset found in captured source modules")
    data["source"]={"url":url,"sha256":hashlib.sha256(raw).hexdigest()}
    output=ROOT/"data/sources/lubiao/node_count_limits.json"
    output.write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("Extracted",len(data["rows"]),"node count rows")


if __name__=="__main__":main()
