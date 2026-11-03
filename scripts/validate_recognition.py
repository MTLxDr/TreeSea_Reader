"""Run OCR/map regression on two real screenshots and simple image variations."""
from __future__ import annotations
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cv2
import numpy as np
from reader.catalog import ROOT
from reader.recognition import Recognizer, load_image, save_result


def main():
    engine = Recognizer()
    cases = [
        ("provided_f2", ROOT / "tests/fixtures/map_sample.png", "F2-T05",
         {"floor": 2, "life": 4, "life_max": 4, "shield": 4, "action_points": 5, "relic_count": 5,
          "ingots": 10, "parts_count": 5, "parts_capacity": 10}),
        ("live_f1", ROOT / "tests/fixtures/map_live_f1.png", "F1-T01",
         {"floor": 1, "life": 4, "life_max": 4, "shield": 2, "action_points": 5, "relic_count": 0,
          "ingots": 21, "parts_count": 3, "parts_capacity": 10})]
    results = []
    for name, path, template, expected in cases:
        original = load_image(path)
        variations = [("original", original)]
        small = cv2.resize(original, (1024, round(original.shape[0] * 1024 / original.shape[1])), interpolation=cv2.INTER_AREA)
        variations.append(("width_1024", small))
        ok, encoded = cv2.imencode(".jpg", original, [cv2.IMWRITE_JPEG_QUALITY, 65])
        variations.append(("jpeg_quality_65", cv2.imdecode(encoded, cv2.IMREAD_COLOR)))
        variations.append(("dim_70_percent", np.clip(original.astype(float) * .7, 0, 255).astype(np.uint8)))
        for variation, image in variations:
            result = engine.analyze(image, source={"fixture": str(path), "variation": variation})
            fields = {key: value["value"] for key, value in result["fields"].items()}
            wrong = {key: {"expected": value, "actual": fields.get(key)} for key, value in expected.items() if fields.get(key) != value}
            row = {"case": name, "variation": variation, "expected_template": template,
                   "template": result.get("template_id"), "needs_review": result["needs_review"],
                   "field_mismatches": wrong, "elapsed_seconds": result["elapsed_seconds"],
                   "pass": result.get("template_id") == template and not wrong}
            results.append(row)
            save_result(result, ROOT / f"reports/recognition/{name}_{variation}.json")
            print(json.dumps(row, ensure_ascii=False), flush=True)
    report = {"independent_real_screenshots": 2, "variation_cases": len(results),
              "passed": sum(row["pass"] for row in results), "cases": results,
              "template_matches": sum(row["template"] == row["expected_template"] for row in results),
              "wrong_numeric_values": sum(v["actual"] is not None for row in results for v in row["field_mismatches"].values()),
              "missing_numeric_values": sum(v["actual"] is None for row in results for v in row["field_mismatches"].values()),
              "limitations": "Variations are derived from two screenshots, not eight independent maps. Only F1-T01/F2-T05 validated here."}
    save_result(report, ROOT / "reports/recognition/validation.json")
    return 0 if report["passed"] == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
