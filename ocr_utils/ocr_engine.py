"""PaddleOCR 包裝層——跟 zh-cn-to-tw-backend/ocr_utils/ocr_engine.py 邏輯相同
（模型只在第一次呼叫時載入、用 lock 序列化辨識呼叫、依文字框座標排序回傳
閱讀順序），差別只在 CPU 執行緒數與 model 版本這兩個設定值改由
configs/config.py 提供，數值本身在桌面情境下比 Render 那份寬鬆（見該檔
說明）。
"""

import os
import threading

from PIL import Image

from configs import config

_ocr = None
_ocr_lock = threading.Lock()


def _get_ocr():
    global _ocr
    if _ocr is None:
        # 這幾個環境變數要在 paddle 的原生函式庫真正被載入之前設好
        # （底層 OpenMP/MKL/OpenBLAS 是在函式庫初始化時讀取這些值，
        # 不是每次呼叫才讀），所以放在 import paddleocr 之前設定。
        os.environ.setdefault("OMP_NUM_THREADS", str(config.OCR_CPU_THREADS))
        os.environ.setdefault("MKL_NUM_THREADS", str(config.OCR_CPU_THREADS))
        os.environ.setdefault("OPENBLAS_NUM_THREADS", str(config.OCR_CPU_THREADS))

        from paddleocr import PaddleOCR

        _ocr = PaddleOCR(
            use_angle_cls=True,
            lang="ch",
            show_log=False,
            cpu_threads=config.OCR_CPU_THREADS,
            ocr_version=config.OCR_VERSION,
        )
    return _ocr


def _line_key(item):
    box = item[0]
    top = min(p[1] for p in box)
    left = min(p[0] for p in box)
    return (top, left)


def ocr_page(image: Image.Image) -> str:
    """辨識單一頁面圖片，回傳依閱讀順序組合的文字（保留原始簡體，尚未轉繁）。"""
    import numpy as np

    ocr = _get_ocr()
    with _ocr_lock:
        result = ocr.ocr(np.array(image), cls=True)

    if not result or not result[0]:
        return ""

    lines = result[0]
    lines_sorted = sorted(lines, key=_line_key)
    texts = [line[1][0] for line in lines_sorted]
    return "\n".join(texts)
