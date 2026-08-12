# 中文

## 劇本殺繁化助手 — 本機 OCR 服務

只在使用者本機執行的小型 HTTP 服務，由桌面版 App（`zh-cn-to-tw-mac`）
啟動時當內嵌引擎子行程拉起來。這份文件分兩部分：

- **[專案報告](#專案報告)**：為什麼 OCR 要獨立成一支本機服務、打包上踩過的坑。
- **[架設 SOP](#架設-sop)**：本機開發、打包成獨立執行檔的完整步驟。

---

## 專案報告

### 為什麼需要這支服務

`zh-cn-to-tw-backend` 部署在 Render 免費方案（0.1 CPU、512MB RAM），
實測無法穩定跑 PaddleOCR——OOM、CPU 配額耗盡被判定無回應、特定主機
CPU 不支援 PP-OCRv4 用到的向量化指令導致 SIGILL。PaddleOCR 光是第一次
推論就會把 RSS 衝到 2.6-2.8GB，遠超過免費方案上限，付費方案在這個
低毛利專案上不可行。解法是把 OCR 這一段搬到使用者自己的機器上執行，
其餘（DB、Gemini/Claude API 金鑰、登入、管理員介面、job/review 狀態、
OpenCC+LLM 潤飾）維持在 Render 不變。完整的資源限制細節見
`zh-cn-to-tw-backend` README。

### 職責範圍（刻意畫得很窄）

- `POST /ocr/pdf/start`：收 PDF，立刻回一個 `job_id`，實際辨識丟到
  背景執行緒跑。
- `GET /ocr/pdf/status/<job_id>`：查詢進度，含目前階段（見下方
  「進度回報」）、目前頁數/總頁數，完成時附上每頁辨識出來的文字。
- `GET /health`：就緒探測，不算活動、不重置閒置計時器。
- **不連 DB、不碰任何 API 金鑰**——job/review 擁有權檢查、管理員權限、
  個人專案額度限制等規則全部留在 `zh-cn-to-tw-backend`，不會因為多了
  這支本機服務而被繞過。

原本是單一個 `POST /ocr/pdf` request 等到整份 PDF 都做完才回應——大
檔案/高 DPI 時前端會長時間停在同一句「本機 OCR 辨識中」動也不動，
使用者分不出是在正常處理還是卡死了。改成跟 `zh-cn-to-tw-backend` 的
job manager 一樣的模式：先回 `job_id`，前端輪詢進度。

### 進度回報：把模型載入從「藏在第一頁裡」拉出來

PaddleOCR 的模型不是 process 啟動時就載入，是第一次真的呼叫辨識時才
載入——這一步實測要 25 秒左右（不同機器差異很大），而且會把記憶體從
26MB 拉到 393MB。如果不特別處理，這 25 秒會直接藏在「第一頁的辨識」
裡，前端輪詢看到的就是「第 0/N 頁」停住不動 25 秒，跟當掉沒兩樣——
尤其現在服務是用到才開的（見下方），每次上傳都要重新付一次這個代價，
更容易讓人誤判成沒啟動成功。

解法是把模型載入拉成一個明確的步驟，job 狀態多一個 `phase` 欄位
（`preparing` → `loading_model` → `ocr`），前端依這個欄位顯示對應的
文字，不是等第一頁跑完才有反應。

### 服務生命週期：用到才開、用完就關

這支服務本身不決定自己什麼時候啟動/關閉——完全由桌面殼跟網頁前端
控制（見 `zh-cn-to-tw-mac`/`zh-cn-to-tw-web` README 的完整說明）。這支
服務自己只保留兩個保險：

- **閒置逾時自我關閉**：檢查的是「有沒有 job 在 running」，不是單純
  「多久沒收到請求」——一個大檔案跑很久、沒人在輪詢的 job，不該被
  誤判成閒置而砍掉正在進行的工作。
- **孤兒行程看門狗**：每隔幾秒確認一次自己的 parent pid 有沒有變。
  桌面殼正常結束時會主動關掉這個子行程；這是萬一桌面殼是被系統直接
  砍掉（例如崩潰）的保險，parent 死掉後這個 process 會被作業系統重新
  掛到別的 parent 底下，一偵測到就自我了結，不留下孤兒 process 佔用
  port 或吃記憶體。

### 安全性

`/ocr/pdf/start` 要求呼叫端在 `X-OCR-Token` header 帶出殼啟動這支
服務時給的隨機 token。同一台機器上任何其他本機程式或頁面理論上都碰
得到 localhost 的這個 port，用一個殼啟動時才產生、只有殼自己知道的
隨機值來防止冒充呼叫。

CORS 放行清單裡有一條 `"null"`——桌面殼用 `file://` 載入頁面後，
瀏覽器對跨來源請求送出的 `Origin` header 字面上就是 `"null"`，不放行
的話桌面版打這支服務的每個請求都會被瀏覽器 CORS 擋下來（實測撞過：
`Origin null is not allowed by Access-Control-Allow-Origin`）。

### 已知限制

- Windows 版的對應服務尚未開始（`zh-cn-to-tw-windows`，規劃中）。

---

# 架設 SOP / Setup Guide

## 本機開發

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 app.py
```

啟動後會在 stdout 印出 `OCR_SERVICE_PORT=<port>`——沒有帶
`OCR_SERVICE_PORT` 環境變數時，會讓作業系統配一個空 port，不寫死固定
port（這個專案本機測試階段吃過很多次「port 被舊 process 卡住」的虧）。

測試（不需要 token，本機開發沒設 `OCR_SERVICE_TOKEN` 時不會擋）：

```bash
curl -X POST http://127.0.0.1:<port>/ocr/pdf/start -F "file=@test.pdf"
curl http://127.0.0.1:<port>/ocr/pdf/status/<job_id>
```

## 打包成獨立執行檔（PyInstaller）

**打包用的環境要是這個 repo 自己的 `venv/`，不要借用專案外、跟這個
repo 無關的暫存目錄。** 之前一度直接沿用另一個實驗（DPI 耗時測試）
臨時建立、放在系統暫存路徑下的 venv，那個目錄不屬於任何 repo、
隨時可能被系統清掉，會導致「換一台機器、甚至只是隔了一段時間」就
沒辦法重新打包出結果，完全不符合長期維護的需求。正確做法是照下面
步驟在這個 repo 底下建一個永久的 `venv/`（已被 `.gitignore` 排除，
不進版控，但目錄本身留在磁碟上，不依賴任何跟這個 repo 無關的路徑）。

PaddlePaddle/paddleocr 在 PyInstaller 凍結環境下有三個已知、已驗證
修復的相容性問題，三個修復都已經寫進 `packaging/ocr_service.spec`
（不是靠一長串 CLI 參數手動組出來，用 `.spec` 檔才能表達第 3 點需要
的 `Tree()` 複製，純 CLI 做不到）：

1. `paddle/base/core.py` 的 `set_paddle_lib_path()` 靠
   `site.getsitepackages()` 找 `paddle/libs`，凍結環境沒有真正的
   site-packages 目錄，會 fallback 到 `site.USER_SITE`（凍結環境下是
   `None`），直接 `TypeError` 崩潰在還沒真正開始執行任何程式碼之前。
   用 `packaging/rthook_paddle_libpath.py` 這個 PyInstaller runtime
   hook 把 `site.getsitepackages()` patch 成回報凍結後 `paddle/libs`
   實際所在的位置解決。
2. `paddle.utils.cpp_extension`（OCR 推論用不到、但 paddle 套件初始化
   時就會 import 到）依賴 Cython 編譯器，Cython 的 `Utility/*.cpp`
   樣板檔案預設不會被 PyInstaller 當成資料檔收進去，缺檔會在 import
   階段就丟 `FileNotFoundError`。`.spec` 檔用 `collect_data_files
   ("Cython")` 解決——**打包當下的 venv 必須裝有 Cython 本體**（見
   `requirements-build.txt`），不只是需要它產出的那幾個檔案。
3. `paddleocr` 套件本身用 `sys.path.append` + 動態 import 載入自己的
   子模組（`ppocr`/`ppstructure`/`tools`），PyInstaller 的靜態分析
   完全看不到這些依賴。`.spec` 檔用 `Tree()` 把整個 `paddleocr` 原始碼
   目錄當純資料檔複製進去解決，這一步需要知道打包當下 venv 的
   site-packages 實際路徑在哪，透過環境變數 `PADDLEOCR_SITE_PACKAGES`
   帶入（見下方指令），刻意不寫死在 `.spec` 檔裡，才能在不同機器上
   重建都不用改程式碼。

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt -r requirements-build.txt

export PADDLEOCR_SITE_PACKAGES="$(pwd)/venv/lib/python3.9/site-packages"
pyinstaller packaging/ocr_service.spec --noconfirm
```

打包結果在 `dist/zh-cn-to-tw-ocr-service/`，是完全自足的目錄（含
`_internal/`，約 850MB）——**執行期完全不需要 Python、不需要任何
venv**，這是 PyInstaller onedir 打包的重點，凍結執行檔可以在沒有裝
Python 的機器上直接跑。上面這個 venv 只在「重新產生這份打包結果」時
才需要，跟最終使用者拿到的東西（.app 裡的內容）無關。

## 環境變數

| 變數 | 說明 |
|---|---|
| `OCR_SERVICE_PORT` | 監聽的 port，沒設定就讓系統配一個空的 |
| `OCR_SERVICE_TOKEN` | `/ocr/pdf/start` 要求的驗證 token，沒設定就不驗證（本機開發用） |
| `OCR_SERVICE_IDLE_TIMEOUT_MINUTES` | 閒置多久自我關閉（沒有 job 在跑才算） |

---

# English

## Script Murder Mystery Traditionalization Assistant — Local OCR Service

A small HTTP service that only ever runs on the user's own machine, launched by the desktop app (`zh-cn-to-tw-mac`) as an embedded engine subprocess. This document has two parts:

- **[Project Report](#project-report)**: why OCR became its own local service, and the packaging pitfalls hit along the way.
- **[Setup Guide](#setup-guide)**: the full steps for local dev and building a standalone executable.

## Project Report

### Why This Service Exists

`zh-cn-to-tw-backend` deploys on Render's free tier (0.1 CPU, 512MB
RAM), which turned out unable to run PaddleOCR reliably — OOM kills,
CPU-quota exhaustion misread as unresponsiveness, and a SIGILL crash
from a specific host CPU not supporting a vectorized instruction
PP-OCRv4 used. PaddleOCR's very first inference call alone spikes RSS
to 2.6–2.8GB, far past the free-tier ceiling, and a paid tier isn't
realistic for this low-margin project. The fix was moving OCR to the
user's own machine, while everything else (DB, Gemini/Claude API keys,
login, the admin interface, job/review state, OpenCC+LLM polishing)
stays on Render. Full resource-constraint detail is in
`zh-cn-to-tw-backend`'s README.

### Scope (deliberately narrow)

- `POST /ocr/pdf/start`: accepts a PDF, immediately returns a `job_id`,
  runs the actual recognition on a background thread.
- `GET /ocr/pdf/status/<job_id>`: reports progress, including the
  current phase (see "Progress Reporting" below) and current/total
  page counts, with each page's recognized text once done.
- `GET /health`: a readiness probe — doesn't count as activity, doesn't
  reset the idle timer.
- **No database connection, no API keys of any kind** — job/review
  ownership checks, admin permissions, per-project quota limits all
  stay in `zh-cn-to-tw-backend` and can't be bypassed just because this
  local service exists.

Originally a single `POST /ocr/pdf` request that didn't respond until
the entire PDF was done — for large files or high DPI, the frontend
would sit on "本機 OCR 辨識中" motionless for a long stretch, and users
couldn't tell whether it was working or stuck. Changed to the same
pattern as `zh-cn-to-tw-backend`'s job manager: return a `job_id`
immediately, let the frontend poll.

### Progress Reporting: Pulling Model Loading Out From Inside Page One

PaddleOCR's model isn't loaded at process startup — it loads the first
time recognition is actually called, which measures at around 25
seconds (varies a lot by machine) and pulls memory from 26MB to 393MB.
Left alone, those 25 seconds hide inside "recognizing page one," so
what the frontend's polling sees is "page 0/N" frozen for 25 seconds —
indistinguishable from a hang, especially now that the service starts
on demand (see below) and pays this cost fresh on every single upload,
making it even easier to mistake for a failed startup.

The fix was pulling model loading into its own explicit step: the job
state gained a `phase` field (`preparing` → `loading_model` → `ocr`),
and the frontend shows text matching the actual phase instead of
waiting for page one to finish before showing anything.

### Service Lifecycle: Start on Use, Stop When Done

This service doesn't decide its own start/stop timing at all — that's
entirely controlled by the desktop shell and the web frontend (full
story in `zh-cn-to-tw-mac`/`zh-cn-to-tw-web`'s READMEs). It keeps only
two safety nets of its own:

- **Idle-timeout self-shutdown**: checks whether any job is currently
  `running`, not simply "how long since the last request" — a large
  file that takes a long time with nobody polling it shouldn't be
  mistaken for idle and killed mid-work.
- **Orphan-process watchdog**: checks every few seconds whether its
  parent PID has changed. The desktop shell normally kills this
  subprocess itself on clean exit; this is the safety net for the
  shell being killed abruptly (e.g. a crash) — once the parent dies,
  the OS reparents this process to something else, which the watchdog
  detects and self-terminates on, so no orphan process is left holding
  a port or memory.

### Security

`/ocr/pdf/start` requires the caller to supply, in the `X-OCR-Token`
header, the random token the shell generated when it launched this
service. Any other local program or page on the same machine could in
theory reach this localhost port, so a value that's randomly generated
per-launch and known only to the shell itself prevents impersonated
calls.

The CORS allowlist includes a literal `"null"` entry — once the
desktop shell loads its page via `file://`, the browser's `Origin`
header on cross-origin requests is the literal string `"null"`; without
allowing it, every desktop-mode call to this service gets blocked by
CORS (confirmed by reproduction: `Origin null is not allowed by
Access-Control-Allow-Origin`).

### Known Limitations

- The Windows counterpart service hasn't been started yet
  (`zh-cn-to-tw-windows`, planned).

---

# Setup Guide

## Local Development

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 app.py
```

On startup it prints `OCR_SERVICE_PORT=<port>` to stdout — without an
`OCR_SERVICE_PORT` environment variable set, the OS assigns a free
port; a fixed port is never hardcoded (this project got burned
repeatedly during local testing by a stale process squatting a fixed
port).

Testing (no token needed — without `OCR_SERVICE_TOKEN` set, local dev
doesn't enforce it):

```bash
curl -X POST http://127.0.0.1:<port>/ocr/pdf/start -F "file=@test.pdf"
curl http://127.0.0.1:<port>/ocr/pdf/status/<job_id>
```

## Building a Standalone Executable (PyInstaller)

**The build environment must be this repo's own `venv/` — never borrow
a scratch directory unrelated to this repo.** This project once reused
a venv that had been temporarily created for an unrelated experiment
(a DPI timing test) and lived under a system temp path; that directory
belonged to no repo and could vanish at any time, meaning "switch
machines, or even just wait a while" was enough to make rebuilding
impossible — completely unfit for long-term maintenance. The correct
approach is a permanent `venv/` inside this repo itself (excluded from
version control via `.gitignore`, but the directory itself stays on
disk, independent of any path unrelated to this repo).

PaddlePaddle/paddleocr have three known, already-fixed compatibility
issues under a PyInstaller frozen build, all three fixes already
written into `packaging/ocr_service.spec` (not assembled from a long
CLI flag list — a `.spec` file is required to express the `Tree()`
copy needed for issue 3, which plain CLI flags can't do):

1. `paddle/base/core.py`'s `set_paddle_lib_path()` finds `paddle/libs`
   via `site.getsitepackages()`, which doesn't exist in a frozen
   environment and falls back to `site.USER_SITE` (`None` when frozen),
   raising a `TypeError` before any real code even runs. Fixed with
   `packaging/rthook_paddle_libpath.py`, a PyInstaller runtime hook
   that patches `site.getsitepackages()` to report where `paddle/libs`
   actually ends up once frozen.
2. `paddle.utils.cpp_extension` (never used for OCR inference, but
   imported anyway during paddle's package init) depends on the Cython
   compiler, and Cython's `Utility/*.cpp` template files aren't
   collected by PyInstaller as data files by default — the missing
   file raises `FileNotFoundError` at import time. Fixed in the `.spec`
   with `collect_data_files("Cython")` — **the build-time venv must
   have Cython itself installed** (see `requirements-build.txt`), not
   just the files it produces.
3. The `paddleocr` package itself loads its own submodules
   (`ppocr`/`ppstructure`/`tools`) via `sys.path.append` plus dynamic
   import, invisible to PyInstaller's static analysis. Fixed in the
   `.spec` with `Tree()`, copying the entire `paddleocr` source
   directory in as plain data — this needs to know where the build-time
   venv's site-packages actually lives, passed in via the
   `PADDLEOCR_SITE_PACKAGES` environment variable (see below),
   deliberately not hardcoded into the `.spec` so rebuilding on a
   different machine needs no code changes.

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt -r requirements-build.txt

export PADDLEOCR_SITE_PACKAGES="$(pwd)/venv/lib/python3.9/site-packages"
pyinstaller packaging/ocr_service.spec --noconfirm
```

The result lands in `dist/zh-cn-to-tw-ocr-service/`, a fully
self-contained directory (including `_internal/`, roughly 850MB) —
**it needs no Python and no venv at runtime**, which is the whole point
of PyInstaller's onedir mode: the frozen executable runs on a machine
with no Python installed at all. The venv above is only needed to
*regenerate* this build output — it has nothing to do with what the
end user actually receives (what ships inside the `.app`).

## Environment Variables

| Variable | Description |
|---|---|
| `OCR_SERVICE_PORT` | Port to listen on; unset lets the OS assign a free one |
| `OCR_SERVICE_TOKEN` | Token required by `/ocr/pdf/start`; unset means no verification (for local dev) |
| `OCR_SERVICE_IDLE_TIMEOUT_MINUTES` | How long idle (with no job running) before self-shutdown |
