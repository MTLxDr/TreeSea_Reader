"""Recognize an imported map screenshot without starting the desktop UI."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reader.recognition import Recognizer, load_image, save_result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--floor", type=int)
    parser.add_argument("--template")
    parser.add_argument("--page", choices=["auto", "map", "relics", "parts", "operators"], default="auto")
    parser.add_argument("--side-mode", choices=["auto", "present", "absent"], default="auto")
    parser.add_argument("--side-template")
    parser.add_argument("--output", type=Path, default=Path("data/recognition.json"))
    args = parser.parse_args()
    result = Recognizer().analyze(load_image(args.image), floor_hint=args.floor, template_hint=args.template,
                                  page_hint=args.page, side_mode=args.side_mode, side_template_hint=args.side_template,
                                  source={"path": str(args.image.resolve()), "method": "file_import"})
    save_result(result, args.output)
    print(json.dumps({"page_type": result["page_type"], "template_id": result.get("template_id"),
                      "needs_review": result["needs_review"], "fields": result.get("fields", {}),
                      "inventory_kind": result.get("inventory_kind"), "entries": len(result.get("entries", [])),
                      "nodes": len(result.get("nodes", [])), "elapsed_seconds": result["elapsed_seconds"],
                      "output": str(args.output.resolve())}, ensure_ascii=False, indent=2))
    return 0 if result["page_type"] in {"map", "inventory"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
