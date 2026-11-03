"""Synthetic text-page smoke checks using the real OCR engine, not game screenshots."""
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image, ImageDraw, ImageFont
import numpy as np

from reader.catalog import ROOT, Catalog
from reader.ocr import TextReader
from reader.inventory import InventoryRecognizer
from reader.recognition import save_result


def main():
    catalog = Catalog()
    reader = InventoryRecognizer(catalog, TextReader())
    folder = ROOT / "reports/recognition/synthetic_inventory"
    folder.mkdir(parents=True, exist_ok=True)
    font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 44)
    heading = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 32)
    report = {"evidence_type": "synthetic text layout; not actual game pages", "cases": []}
    for kind, title in (("relics", "收藏品"), ("parts", "零件箱"), ("operators", "已招募干员")):
        image = Image.new("RGB", (1800, 1000), (21, 32, 40))
        draw = ImageDraw.Draw(image)
        draw.text((80, 60), title, font=font, fill="white")
        draw.text((80, 150), "合成文字识别测试页面", font=heading, fill=(150, 170, 185))
        names = ["银灰", "能天使"] if kind == "operators" else [row["name"] for row in catalog.inventory[kind][:2]]
        for index, name in enumerate(names):
            x = 150 + index * 800
            draw.rounded_rectangle((x - 40, 300, x + 650, 700), radius=15, fill=(35, 53, 65))
            draw.text((x, 520), name, font=font, fill="white")
            if kind == "operators":
                draw.text((x, 430), "LV90" if index == 0 else "LV60", font=font, fill="white")
                draw.text((x, 350), "E2" if index == 0 else "E1", font=font, fill="white")
            else:
                draw.text((x, 430), "×3" if index == 0 else "×2", font=font, fill="white")
        path = folder / (kind + ".png")
        image.save(path)
        bgr = np.array(image)[:, :, ::-1].copy()
        result = reader.analyze(bgr, kind, {"path": str(path), "method": "synthetic_qa"})
        save_result(result, folder / (kind + ".json"))
        actual = {row["name"]: row for row in result["entries"]}
        fields_ok = all(actual.get(name, {}).get("fields", {}).get("level" if kind == "operators" else "quantity", {}).get("value") == value
            for name, value in zip(names, [90, 60] if kind == "operators" else [3, 2]))
        row = {"kind": kind, "expected_names": names, "names_read": list(actual),
               "name_match": set(names) == set(actual), "fields_match": fields_ok,
               "elapsed_seconds": result["elapsed_seconds"]}
        report["cases"].append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    save_result(report, folder / "validation.json")
    return 0 if all(row["name_match"] and row["fields_match"] for row in report["cases"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
