from __future__ import annotations

import threading
import numpy as np


class OCRUnavailable(RuntimeError):
    pass


class TextReader:
    """Lazy, CPU-only OCR. Images stay on this computer."""
    def __init__(self):
        self._engine = None
        self._lock = threading.RLock()

    def _load(self):
        if self._engine is None:
            try:
                from rapidocr import RapidOCR
            except ImportError as exc:
                raise OCRUnavailable("缺少 RapidOCR。请使用项目 .venv 启动，或先运行安装脚本。") from exc
            self._engine = RapidOCR(params={
                "Global.log_level": "warning",
                "EngineConfig.onnxruntime.intra_op_num_threads": 4,
                "EngineConfig.onnxruntime.inter_op_num_threads": 1,
            })
        return self._engine

    def read(self, image: np.ndarray):
        with self._lock:
            # RapidOCR remembers call-time flags. Explicitly restore detection
            # after the numeric ROI recognizer used use_det=False.
            result = self._load()(image, use_det=True, use_cls=True, use_rec=True)
        if result.boxes is None or result.txts is None:
            return []
        return [{"box": np.asarray(box).tolist(), "text": str(text), "score": float(score)}
                for box, text, score in zip(result.boxes, result.txts, result.scores)]

    def line(self, image: np.ndarray):
        if min(image.shape[:2]) < 2:
            return "", 0.0
        with self._lock:
            result = self._load()(image, use_det=False, use_cls=False, use_rec=True)
        if result.txts is None or not len(result.txts):
            return "", 0.0
        return str(result.txts[0]), float(result.scores[0])
