"""OCR 包裝層——底層引擎是 RapidOCR（ONNXRuntime），模型只在第一次呼叫時
載入、用 lock 序列化辨識呼叫、依文字框座標排序回傳閱讀順序。

2026-08-17 從 paddleocr（paddlepaddle 原生推論引擎）換成 rapidocr_onnxruntime：
paddlepaddle 的官方 pip wheel 假設 CPU 有 AVX/AVX2，實測在一台 Intel
Pentium Gold G5400（2018 年桌機晶片，py-cpuinfo 確認沒有 avx/avx2/fma）上，
`import paddle` 直接讓整個 process 崩潰（Windows「動態連結程式庫 (DLL)
初始化例行程序失敗」，連 Python try/except 都攔不住，是記憶體層級的
崩潰）。這不是單一台機器的特例——configs/config.py 裡舊註解就記錄過同一
類崩潰（SIGILL）曾經在 Render 的雲端主機上發生過，當時判斷是「那台主機
特定的問題，OCR 搬到使用者本機後不會再遇到」；這次證明是判斷錯了，這個
問題只要使用者自己的機器 CPU 缺 AVX2 就會重演，跟雲端還是本機無關——而
入門/預算款 CPU（例如這次踩到的 Pentium Gold）即使是近幾年出的，也常見
被廠商刻意閹割掉 AVX2。

RapidOCR 用的還是同一個 ch_PP-OCRv4 模型（det/cls/rec 三個 .onnx 檔，就是
PP-OCRv4 轉存成 ONNX 格式），辨識品質理論上跟原本 paddleocr 一致，差別
只在推論引擎換成 ONNXRuntime——這套引擎本身就以廣泛的硬體/OS 相容性
著稱，同一台無 AVX2 的機器上實測跑起來完全正常、辨識結果正確（繁簡體
文字都認得出來，信心分數 >0.96），不會出現原本那種載入階段直接崩潰的
問題。
"""

import threading

import numpy as np
from PIL import Image

_ocr = None
_ocr_lock = threading.Lock()


def preload() -> None:
    """先把辨識模型載入完成。

    不呼叫這支也不會壞——模型本來就會在第一次 ocr_page() 時自動載入——
    但那樣這段時間會「藏」在第一頁的辨識裡，對呼叫端來說就是進度停在
    第 0 頁不動一段時間，看起來跟當掉一模一樣。拉出來變成一個明確的
    步驟，呼叫端才能誠實告訴使用者現在在等什麼（見 app.py 的
    phase：preparing -> loading_model -> ocr）。
    """
    _get_ocr()


def _get_ocr():
    global _ocr
    if _ocr is None:
        from rapidocr_onnxruntime import RapidOCR

        _ocr = RapidOCR()
    return _ocr


def _line_key(item):
    box = item[0]
    top = min(p[1] for p in box)
    left = min(p[0] for p in box)
    return (top, left)


def ocr_page(image: Image.Image) -> str:
    """辨識單一頁面圖片，回傳依閱讀順序組合的文字（保留原始簡體，尚未轉繁）。"""
    ocr = _get_ocr()
    with _ocr_lock:
        result, _elapse = ocr(np.array(image))

    if not result:
        return ""

    # RapidOCR 回傳格式是 [box, text, score] 的扁平三元素列表（跟舊版
    # paddleocr 的 [box, [text, score]] 巢狀格式不同），排序邏輯不變，
    # 只有取文字的索引從 line[1][0] 改成 line[1]。
    lines_sorted = sorted(result, key=_line_key)
    texts = [line[1] for line in lines_sorted]
    return "\n".join(texts)
