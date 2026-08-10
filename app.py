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
import uuid
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
# 本機開發用（任意 port 的 localhost/127.0.0.1）也一併放行，跟
# zh-cn-to-tw-backend/app.py 的 CORS 設定保持一致——桌面殼測試時常用
# WEB_BASE_URL_OVERRIDE 指到本機的 http.server，origin 是
# http://localhost:<port>，不是正式的 GitHub Pages 網址，沒有這幾條
# 本機測試時打 /ocr/pdf 會被瀏覽器的 CORS 預檢擋下來（實測抓到：
# "Fetch API cannot load ... due to access control checks"）。
CORS(
    app,
    origins=[
        "https://beethoreven.github.io",
        r"http://localhost:\d+",
        r"http://127\.0\.0\.1:\d+",
    ],
    allow_headers=["Content-Type", "X-OCR-Token"],
)

_last_activity = time.time()
_activity_lock = threading.Lock()
_original_ppid = os.getppid()

# 逐頁 OCR 進度：/ocr/pdf 原本是單一個 request 等到整份 PDF 都做完才回應，
# 大檔案／高 DPI 時前端會長時間停在同一句「本機 OCR 辨識中」動也不動，
# 使用者分不出是在正常處理還是卡死了。改成跟 zh-cn-to-tw-backend 的
# job_manager 一樣的模式：先回 job_id，背景執行緒逐頁跑 OCR 並更新這裡的
# 進度，前端改成輪詢 /ocr/pdf/status/<job_id>。這支服務本來就是單機、
# 單一使用者、行程重啟就整個重來，所以用最簡單的記憶體內 dict 就夠，不需要
# 引入 DB。
_ocr_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()
_JOB_RETENTION_SECONDS = 15 * 60


def _update_job(job_id: str, **fields) -> None:
    with _jobs_lock:
        job = _ocr_jobs.get(job_id)
        if job is None:
            return
        job.update(fields)


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


def _run_local_ocr_job(job_id: str, pdf_path: str, dpi: int, detect_cover: bool) -> None:
    """在背景執行緒跑：封面偵測（可選）+ 逐頁 OCR，每處理完一頁就更新
    _ocr_jobs[job_id] 的進度，供 /ocr/pdf/status/<job_id> 輪詢讀取。邏輯跟
    zh-cn-to-tw-backend/pipeline/orchestrator.py 的 run_ocr_stage 對應。"""
    try:
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

        _update_job(job_id, total_pages=total_pages)

        pages: list[str] = []
        for image in page_images:
            pages.append(ocr_page(image))
            _update_job(job_id, current_page=len(pages))

        _update_job(job_id, status="done", pages=pages, logs=logs, finished_at=time.time())
    except Exception as exc:  # noqa: BLE001
        _update_job(
            job_id,
            status="failed",
            error=f"OCR 處理失敗：{exc}",
            finished_at=time.time(),
        )
    finally:
        try:
            os.remove(pdf_path)
        except OSError:
            pass
        _touch_activity()


@app.post("/ocr/pdf/start")
@require_token
def ocr_pdf_start():
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
    file.save(pdf_path)

    job_id = uuid.uuid4().hex
    with _jobs_lock:
        _ocr_jobs[job_id] = {
            "status": "running",
            "current_page": 0,
            "total_pages": None,
            "pages": None,
            "logs": None,
            "error": None,
            "finished_at": None,
        }
    threading.Thread(
        target=_run_local_ocr_job,
        args=(job_id, pdf_path, dpi, detect_cover),
        daemon=True,
    ).start()
    return jsonify({"job_id": job_id}), 202


@app.get("/ocr/pdf/status/<job_id>")
@require_token
def ocr_pdf_status(job_id):
    _touch_activity()
    with _jobs_lock:
        job = _ocr_jobs.get(job_id)
        if job is None:
            return jsonify({"error": "找不到這個 job，可能已逾時清除或 job_id 錯誤"}), 404
        response = {k: v for k, v in job.items() if k != "finished_at"}
    return jsonify(response), 200


def _job_cleanup_watchdog() -> None:
    """已結束（done/failed）的 job 結果留在記憶體裡一段時間，讓前端就算漏
    接了一次輪詢回應也還撈得到；但不能永遠留著（大檔案的 pages 內容不小，
    這支服務又常常一開就是好幾小時），超過保留時間就清掉。只清「已結束」
    的 job，還在 running 的不管跑多久都不會被這裡誤清掉。"""
    while True:
        time.sleep(60)
        now = time.time()
        with _jobs_lock:
            stale_ids = [
                job_id
                for job_id, job in _ocr_jobs.items()
                if job.get("finished_at") is not None
                and now - job["finished_at"] > _JOB_RETENTION_SECONDS
            ]
            for job_id in stale_ids:
                del _ocr_jobs[job_id]


def _has_running_job() -> bool:
    with _jobs_lock:
        return any(job["status"] == "running" for job in _ocr_jobs.values())


def _idle_watchdog() -> None:
    while True:
        time.sleep(30)
        with _activity_lock:
            idle_minutes = (time.time() - _last_activity) / 60
        if idle_minutes < config.IDLE_TIMEOUT_MINUTES:
            continue
        if _has_running_job():
            # _last_activity 只在有 HTTP 請求進來時更新——如果前端輪詢
            # 因為某些原因停了（WebView 重新整理、崩潰、使用者關掉分頁
            # 但沒關掉整個殼），但背景執行緒其實還在跑一個真正的 OCR
            # job（尤其大檔案/高 DPI 可能跑很久），這裡如果只看
            # 「多久沒收到請求」就自我關閉，會把一個還在正常工作、只是
            # 沒人在問進度的 job 直接砍掉，白白浪費已經跑掉的時間。只要
            # 還有 job 是 running 狀態，就不能關閉，不管閒置多久。
            continue
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
    threading.Thread(target=_job_cleanup_watchdog, daemon=True).start()

    # 殼靠讀 stdout 這一行拿到實際綁定的 port（啟動前無法預先知道），
    # 一定要在 app.run() 真正開始監聽前印出並 flush，不能被緩衝卡住。
    print(f"OCR_SERVICE_PORT={port}", flush=True)
    sys.stdout.flush()

    app.run(host="127.0.0.1", port=port, threaded=True)
