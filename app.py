"""zh-cn-to-tw-ocr-service：只在使用者本機執行的小型 HTTP 服務，桌面殼
（zh-cn-to-tw-mac / zh-cn-to-tw-windows）啟動時把這支服務當內嵌引擎
subprocess 拉起來。職責範圍刻意畫得很窄：PDF -> (可選)封面偵測 ->
逐頁 PaddleOCR -> 每頁簡體文字，回傳給呼叫端；不連 DB、不碰任何
Gemini/Claude/Neon 憑證——那些全部留在 zh-cn-to-tw-backend（Render）
那一側，job/review 擁有權檢查、管理員權限、額度限制等規則也都留在
那裡，不會因為多了這支本機服務而被繞過。
"""

from __future__ import annotations

import itertools
import os
import socket
import sys
import tempfile
import threading
import time
from functools import wraps

from flask import Flask, jsonify, request
from flask_cors import CORS

from configs import config
from ocr_utils.cover_detect import evaluate_cover_page
from ocr_utils.ocr_engine import ocr_page
from ocr_utils.pdf_to_images import get_pdf_page_count, render_pdf_pages

app = Flask(__name__)

# 桌面殼目前載入的是線上的 GitHub Pages 網址（見 desktop_app_plan 記憶），
# 之後如果改成殼直接內嵌網頁資源，origin 會變成 file://，屆時要再加一條。
CORS(
    app,
    origins=["https://beethoreven.github.io"],
    allow_headers=["Content-Type", "X-OCR-Token"],
)

_last_activity = time.time()
_activity_lock = threading.Lock()
_original_ppid = os.getppid()


def _touch_activity() -> None:
    global _last_activity
    with _activity_lock:
        _last_activity = time.time()


def _log_best_effort(message: str) -> None:
    """看門狗執行緒要自我了結前的最後訊息：殼（parent）已經死掉時，
    stdout 這端的 pipe 可能已經斷了，print() 會直接拋 BrokenPipeError，
    如果沒接住，會讓整個看門狗執行緒在真正呼叫 os._exit(0) 之前就先
    當掉，反而讓自我了結永遠不會發生——這裡只是想留個 log，絕不能讓
    它擋到真正要做的事（結束這個 process）。"""
    try:
        print(message, flush=True)
    except OSError:
        pass


def require_token(view):
    """/ocr/pdf 要求呼叫端帶出殼啟動這支服務時給的隨機 token（見
    configs/config.py 說明）；沒設定 token（例如本機手動測試）就不擋，
    方便開發，正式由殼啟動時一定會帶。"""

    @wraps(view)
    def wrapper(*args, **kwargs):
        if config.OCR_SERVICE_TOKEN:
            supplied = request.headers.get("X-OCR-Token", "")
            if supplied != config.OCR_SERVICE_TOKEN:
                return jsonify({"error": "unauthorized"}), 401
        return view(*args, **kwargs)

    return wrapper


@app.get("/health")
def health():
    # 刻意不要求 token、也不更新閒置計時器：殼可能會用這支做輕量存活檢查，
    # 如果每次健康檢查都算「活動」，閒置自動關閉這個機制就永遠不會觸發。
    return jsonify({"status": "ok"})


def _run_local_ocr(pdf_path: str, dpi: int, detect_cover: bool):
    """封面偵測（可選）+ 逐頁 OCR，回傳 (每頁文字, 實際頁數, 過程訊息)。
    邏輯跟 zh-cn-to-tw-backend/pipeline/orchestrator.py 的 run_ocr_stage
    對應，差別只在這裡沒有 job_manager 可以即時推進度，改成呼叫結束後
    一次把過程訊息隨回應回傳。"""
    logs: list[str] = []
    total_pages = get_pdf_page_count(pdf_path)
    page_images = render_pdf_pages(pdf_path, dpi=dpi)

    if not detect_cover:
        logs.append("「偵測首頁是否為封面」已關閉，略過封面偵測")
    elif total_pages < 2:
        logs.append(f"PDF 只有 {total_pages} 頁，略過封面偵測")
    else:
        lookahead = []
        try:
            lookahead.append(next(page_images))
            lookahead.append(next(page_images))
            result = evaluate_cover_page(
                lookahead[0],
                lookahead[1],
                config.COVER_DETECT_DARK_RATIO_THRESHOLD,
                config.COVER_DETECT_SATURATION_THRESHOLD,
                config.COVER_DETECT_RELATIVE_MARGIN,
            )
            if result.is_cover:
                lookahead = lookahead[1:]
                total_pages -= 1
                logs.append("首頁判定為封面，已自動移除，不列入 OCR 範圍")
        except Exception as exc:  # noqa: BLE001
            logs.append(f"封面偵測發生錯誤：{exc}，跳過偵測，正常處理全部頁面")
        page_images = itertools.chain(lookahead, page_images)

    pages = [ocr_page(image) for image in page_images]
    return pages, total_pages, logs


@app.post("/ocr/pdf")
@require_token
def ocr_pdf():
    _touch_activity()
    if "file" not in request.files:
        return jsonify({"error": "缺少檔案，請用 file 欄位上傳 PDF"}), 400
    file = request.files["file"]

    try:
        dpi = int(request.form.get("dpi") or config.PDF_RENDER_DPI)
    except (TypeError, ValueError):
        return jsonify({"error": "dpi 必須是整數"}), 400

    detect_cover_raw = request.form.get("detect_cover")
    detect_cover = (
        config.COVER_DETECT_DEFAULT
        if detect_cover_raw is None
        else detect_cover_raw.lower() in ("true", "1", "on")
    )

    fd, pdf_path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    try:
        file.save(pdf_path)
        pages, total_pages, logs = _run_local_ocr(pdf_path, dpi, detect_cover)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"OCR 處理失敗：{exc}"}), 500
    finally:
        os.remove(pdf_path)

    _touch_activity()
    return jsonify({"pages": pages, "total_pages": total_pages, "logs": logs}), 200


def _idle_watchdog() -> None:
    while True:
        time.sleep(30)
        with _activity_lock:
            idle_minutes = (time.time() - _last_activity) / 60
        if idle_minutes >= config.IDLE_TIMEOUT_MINUTES:
            _log_best_effort(f"[ocr-service] 閒置 {idle_minutes:.1f} 分鐘，自動關閉")
            os._exit(0)


def _orphan_watchdog() -> None:
    """每 5 秒確認一次自己的 parent pid 有沒有變——殼正常結束時會先主動
    把這個 process 關掉，這裡是萬一殼是被系統直接砍掉（例如崩潰）的保險：
    parent 死掉後這個 process 會被作業系統重新掛到別的 parent 底下
    （macOS 上通常是 launchd），一偵測到就自我了結，不留下孤兒 process
    佔用 port 或吃記憶體。"""
    while True:
        time.sleep(5)
        if os.getppid() != _original_ppid:
            _log_best_effort("[ocr-service] parent process 已改變，自我結束")
            os._exit(0)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


if __name__ == "__main__":
    port = config.PORT or _find_free_port()

    threading.Thread(target=_idle_watchdog, daemon=True).start()
    threading.Thread(target=_orphan_watchdog, daemon=True).start()

    # 殼靠讀 stdout 這一行拿到實際綁定的 port（啟動前無法預先知道），
    # 一定要在 app.run() 真正開始監聽前印出並 flush，不能被緩衝卡住。
    print(f"OCR_SERVICE_PORT={port}", flush=True)
    sys.stdout.flush()

    app.run(host="127.0.0.1", port=port, threaded=True)
