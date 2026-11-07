from __future__ import annotations

import threading
import hashlib
from copy import deepcopy
import numpy as np


class OCRUnavailable(RuntimeError):
    pass


class TextReader:
    """Lazy, CPU-only OCR. Images stay on this computer."""
    def __init__(self):
        self._engine = None
        self._lock = threading.RLock()
        self._line_cache = {}
        self._read_cache = {}

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
            key=(image.shape,hashlib.blake2b(image.tobytes(),digest_size=16).digest())
            if key in self._read_cache:
                return deepcopy(self._read_cache[key])
            # RapidOCR remembers call-time flags. Explicitly restore detection
            # after the numeric ROI recognizer used use_det=False.
            result = self._load()(image, use_det=True, use_cls=True, use_rec=True)
        if result.boxes is None or result.txts is None:
            return []
        tokens=[{"box": np.asarray(box).tolist(), "text": str(text), "score": float(score)}
                for box, text, score in zip(result.boxes, result.txts, result.scores)]
        self._read_cache[key]=deepcopy(tokens)
        if len(self._read_cache)>64:
            self._read_cache.pop(next(iter(self._read_cache)))
        return tokens

    def line(self, image: np.ndarray):
        if min(image.shape[:2]) < 2:
            return "", 0.0
        with self._lock:
            key = (image.shape, hashlib.blake2b(image.tobytes(),digest_size=16).digest())
            if key in self._line_cache:
                return self._line_cache[key]
            result = self._load()(image, use_det=False, use_cls=False, use_rec=True)
        if result.txts is None or not len(result.txts):
            return "", 0.0
        value = str(result.txts[0]), float(result.scores[0])
        self._line_cache[key] = value
        if len(self._line_cache)>512:
            self._line_cache.pop(next(iter(self._line_cache)))
        return value
