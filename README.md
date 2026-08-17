# 中文

## 劇本殺繁化助手 — 本機 OCR 服務

只在使用者本機執行的小型 HTTP 服務，由桌面版 App（`zh-cn-to-tw-mac`）啟動時當內嵌引擎子行程拉起來。這份文件分兩部分：

- **[專案報告](#專案報告)**：為什麼 OCR 要獨立成一支本機服務、打包上踩過的坑。
- **[架設 SOP](#架設-sop)**：本機開發、打包成獨立執行檔的完整步驟。

---

## 專案報告

### 為什麼需要這支服務

`zh-cn-to-tw-backend` 部署在 Render 免費方案（0.1 CPU、512MB RAM），實測無法穩定跑 PaddleOCR——OOM、CPU 配額耗盡被判定無回應、特定主機 CPU 不支援 PP-OCRv4 用到的向量化指令導致 SIGILL。PaddleOCR 光是第一次推論就會把 RSS 衝到 2.6-2.8GB，遠超過免費方案上限，付費方案在這個低毛利專案上不可行。解法是把 OCR 這一段搬到使用者自己的機器上執行，其餘（DB、Gemini/Claude API 金鑰、登入、管理員介面、job/review 狀態、OpenCC+LLM 潤飾）維持在 Render 不變。完整的資源限制細節見 `zh-cn-to-tw-backend` README。

### 職責範圍（刻意畫得很窄）

- `POST /ocr/pdf/start`：收 PDF，立刻回一個 `job_id`，實際辨識丟到背景執行緒跑。
- `GET /ocr/pdf/status/<job_id>`：查詢進度，含目前階段（見下方「進度回報」）、目前頁數/總頁數，完成時附上每頁辨識出來的文字。
- `GET /health`：就緒探測，不算活動、不重置閒置計時器。
- **不連 DB、不碰任何 API 金鑰**——job/review 擁有權檢查、管理員權限、個人專案額度限制等規則全部留在 `zh-cn-to-tw-backend`，不會因為多了這支本機服務而被繞過。

原本是單一個 `POST /ocr/pdf` request 等到整份 PDF 都做完才回應——大檔案/高 DPI 時前端會長時間停在同一句「本機 OCR 辨識中」動也不動，使用者分不出是在正常處理還是卡死了。改成跟 `zh-cn-to-tw-backend` 的 job manager 一樣的模式：先回 `job_id`，前端輪詢進度。

### 進度回報：把模型載入從「藏在第一頁裡」拉出來

PaddleOCR 的模型不是 process 啟動時就載入，是第一次真的呼叫辨識時才載入——這一步實測要 25 秒左右（不同機器差異很大），而且會把記憶體從 26MB 拉到 393MB。如果不特別處理，這 25 秒會直接藏在「第一頁的辨識」裡，前端輪詢看到的就是「第 0/N 頁」停住不動 25 秒，跟當掉沒兩樣——尤其現在服務是用到才開的（見下方），每次上傳都要重新付一次這個代價，更容易讓人誤判成沒啟動成功。

解法是把模型載入拉成一個明確的步驟，job 狀態多一個 `phase` 欄位（`preparing` → `loading_model` → `ocr`），前端依這個欄位顯示對應的文字，不是等第一頁跑完才有反應。

### 服務生命週期：用到才開、用完就關

這支服務本身不決定自己什麼時候啟動/關閉——完全由桌面殼跟網頁前端控制（見 `zh-cn-to-tw-mac`/`zh-cn-to-tw-web` README 的完整說明）。這支服務自己只保留兩個保險：

- **閒置逾時自我關閉**：檢查的是「有沒有 job 在 running」，不是單純「多久沒收到請求」——一個大檔案跑很久、沒人在輪詢的 job，不該被誤判成閒置而砍掉正在進行的工作。
- **孤兒行程看門狗**：每隔幾秒確認一次自己的 parent pid 有沒有變。桌面殼正常結束時會主動關掉這個子行程；這是萬一桌面殼是被系統直接砍掉（例如崩潰）的保險，parent 死掉後這個 process 會被作業系統重新掛到別的 parent 底下，一偵測到就自我了結，不留下孤兒 process 佔用 port 或吃記憶體。

### 安全性

`/ocr/pdf/start` 要求呼叫端在 `X-OCR-Token` header 帶出殼啟動這支服務時給的隨機 token。同一台機器上任何其他本機程式或頁面理論上都碰得到 localhost 的這個 port，用一個殼啟動時才產生、只有殼自己知道的隨機值來防止冒充呼叫。

CORS 放行清單裡有一條 `"null"`——桌面殼用 `file://` 載入頁面後，瀏覽器對跨來源請求送出的 `Origin` header 字面上就是 `"null"`，不放行的話桌面版打這支服務的每個請求都會被瀏覽器 CORS 擋下來（實測撞過：`Origin null is not allowed by Access-Control-Allow-Origin`）。

### 為什麼從 paddleocr 換成 rapidocr_onnxruntime

2026-08-17：Windows 版開發初期，在一台真實的使用者機器（Intel Pentium Gold G5400，2018 年桌機晶片）上實測 `import paddle` 直接讓整個 process 崩潰——Windows 回報「動態連結程式庫 (DLL) 初始化例行程序失敗」，連 Python 的 `try/except` 都攔不住，是記憶體層級的崩潰，不是乾淨的例外。用 `py-cpuinfo` 直接查這顆 CPU 的指令集，確認沒有 `avx`/`avx2`/`fma`——paddlepaddle 官方 PyPI 上的 pip wheel 是假設 CPU 有 AVX2 才編譯的，沒有的話不是變慢，是直接跑不起來。

這不是單一台機器的特例，也不是新發現的問題——上面「為什麼需要這支服務」那段提到的、曾經在 Render 主機上遇過的 SIGILL 崩潰，就是同一類問題的前一次現身；當時的結論是「那是 Render 那台主機特定的問題，OCR 搬到使用者本機執行後就不會再遇到」，這次證明那個結論下錯了——問題不是「雲端 vs 本機」，是「這顆 CPU 有沒有 AVX2」，任何使用者自己的機器只要 CPU 沒有 AVX2 都會踩到，而 Pentium/Celeron 這類入門款 CPU，即使是近幾年出的，也常見被廠商刻意閹割掉 AVX2（這台 G5400 不是老古董，是 2018 年的桌機晶片）。

換成 `rapidocr_onnxruntime`（ONNXRuntime 為底的推論引擎）解決了這個問題，而且是同一組模型：RapidOCR 內建的就是 `ch_PP-OCRv4_det/rec_infer.onnx`——PP-OCRv4 本人轉存成 ONNX 格式，辨識品質理論上不變。在同一台無 AVX2 的機器上實測：`RapidOCR()` 初始化 0.4 秒、單頁推論約 5 秒，簡體、繁體文字都正確辨識出來，信心分數全部 >0.96，完全沒有 paddlepaddle 那種載入階段崩潰的問題。ONNXRuntime 本身在 ML 推論引擎裡就是以廣泛硬體/OS 相容性著稱，這次的實測結果符合這個名聲。

這是共用 repo（`zh-cn-to-tw-mac` 跟 `zh-cn-to-tw-windows` 都用同一份原始碼各自 PyInstaller 打包），這個決策同時影響兩個平台；Mac 版沒有實測撞過這個崩潰（Mac 上的 Intel/Apple Silicon CPU 目前用到的機型都有 AVX2 或本來就不受這個限制），但為了兩邊共用同一套邏輯、不要維護兩份 OCR 引擎，兩邊一起換。

### Windows 上的孤兒行程看門狗不可靠

上面「服務生命週期」那段的孤兒行程看門狗（輪詢 `os.getppid()` 有沒有變）在 Windows 上基本上失效：POSIX 系統（macOS/Linux）parent process 死掉後，子行程會被系統重新掛到別的 parent（通常是 init/launchd），`getppid()` 讀到的值真的會變；Windows 不會重新掛接子行程的 parent pid——那個值是建立當下就固定住的，parent 死了也不會變，除非剛好有新 process 巧合搶到同一個 pid。`zh-cn-to-tw-windows` 那邊改用 Windows 原生的 Job Object（`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`）當作對應的保險機制，殼死掉時系統核心會直接連坐砍掉這個子行程，不依賴這裡的輪詢邏輯——細節見 `zh-cn-to-tw-windows` 的 `ProcessJobObject.cs`。這個看門狗本身留著沒拿掉：Mac 版還是用它，拿掉對 Windows 沒有壞處也沒有好處（Job Object 已經是更可靠的保險），拿掉反而讓 Mac 版少一層保護。

### 已知限制

- Windows 版對應服務已經接上（`zh-cn-to-tw-windows`），細節見上面兩節。

---

# 架設 SOP / Setup Guide

## 本機開發

macOS：

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 app.py
```

Windows：

```powershell
python -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

啟動後會在 stdout 印出 `OCR_SERVICE_PORT=<port>`——沒有帶 `OCR_SERVICE_PORT` 環境變數時，會讓作業系統配一個空 port，不寫死固定 port（這個專案本機測試階段吃過很多次「port 被舊 process 卡住」的虧）。

測試（不需要 token，本機開發沒設 `OCR_SERVICE_TOKEN` 時不會擋）：

```bash
curl -X POST http://127.0.0.1:<port>/ocr/pdf/start -F "file=@test.pdf"
curl http://127.0.0.1:<port>/ocr/pdf/status/<job_id>
```

## 打包成獨立執行檔（PyInstaller）

**打包用的環境要是這個 repo 自己的 `venv/`，不要借用專案外、跟這個 repo 無關的暫存目錄。** 之前一度直接沿用另一個實驗（DPI 耗時測試）臨時建立、放在系統暫存路徑下的 venv，那個目錄不屬於任何 repo、隨時可能被系統清掉，會導致「換一台機器、甚至只是隔了一段時間」就沒辦法重新打包出結果，完全不符合長期維護的需求。正確做法是照下面步驟在這個 repo 底下建一個永久的 `venv/`（已被 `.gitignore` 排除，不進版控，但目錄本身留在磁碟上，不依賴任何跟這個 repo 無關的路徑）。

換成 `rapidocr_onnxruntime` 之後（見「為什麼從 paddleocr 換成 rapidocr_onnxruntime」），打包大幅簡化：不再需要 paddle 專屬的三個相容性 hack（原本的 runtime hook、`collect_data_files("Cython")`、`Tree()` 複製 paddleocr 原始碼，完整原因見 git 歷史裡舊版這份 README 跟 `packaging/ocr_service.spec` 的說明）。`pyinstaller` 的 `pyinstaller-hooks-contrib` 依賴已經內建 onnxruntime/opencv/lxml 等套件的打包規則，`.spec` 檔只需要 `collect_data_files("rapidocr_onnxruntime")` 把套件內建的 `config.yaml`、`models/*.onnx` 收進來就夠了，不需要任何自訂 runtime hook。

macOS：

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt -r requirements-build.txt
pyinstaller packaging/ocr_service.spec --noconfirm
```

Windows：

```powershell
python -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt -r requirements-build.txt
pyinstaller packaging\ocr_service.spec --noconfirm
```

打包結果在 `dist/zh-cn-to-tw-ocr-service/`，是完全自足的目錄——**執行期完全不需要 Python、不需要任何 venv**，這是 PyInstaller onedir 打包的重點，凍結執行檔可以在沒有裝 Python 的機器上直接跑。上面這個 venv 只在「重新產生這份打包結果」時才需要，跟最終使用者拿到的東西（.app/.exe 裡的內容）無關。實測 Windows 打包結果約 278MB，比舊版 paddleocr 打包（約 850MB）小非常多——這不只是 Win7 決策帶來的效果（見 `zh-cn-to-tw-windows` README），主要就是 onnxruntime 本身遠比 paddlepaddle 精簡。

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

`zh-cn-to-tw-backend` deploys on Render's free tier (0.1 CPU, 512MB RAM), which turned out unable to run PaddleOCR reliably — OOM kills, CPU-quota exhaustion misread as unresponsiveness, and a SIGILL crash from a specific host CPU not supporting a vectorized instruction PP-OCRv4 used. PaddleOCR's very first inference call alone spikes RSS to 2.6–2.8GB, far past the free-tier ceiling, and a paid tier isn't realistic for this low-margin project. The fix was moving OCR to the user's own machine, while everything else (DB, Gemini/Claude API keys, login, the admin interface, job/review state, OpenCC+LLM polishing) stays on Render. Full resource-constraint detail is in `zh-cn-to-tw-backend`'s README.

### Scope (deliberately narrow)

- `POST /ocr/pdf/start`: accepts a PDF, immediately returns a `job_id`, runs the actual recognition on a background thread.
- `GET /ocr/pdf/status/<job_id>`: reports progress, including the current phase (see "Progress Reporting" below) and current/total page counts, with each page's recognized text once done.
- `GET /health`: a readiness probe — doesn't count as activity, doesn't reset the idle timer.
- **No database connection, no API keys of any kind** — job/review ownership checks, admin permissions, per-project quota limits all stay in `zh-cn-to-tw-backend` and can't be bypassed just because this local service exists.

Originally a single `POST /ocr/pdf` request that didn't respond until the entire PDF was done — for large files or high DPI, the frontend would sit on "本機 OCR 辨識中" motionless for a long stretch, and users couldn't tell whether it was working or stuck. Changed to the same pattern as `zh-cn-to-tw-backend`'s job manager: return a `job_id` immediately, let the frontend poll.

### Progress Reporting: Pulling Model Loading Out From Inside Page One

PaddleOCR's model isn't loaded at process startup — it loads the first time recognition is actually called, which measures at around 25 seconds (varies a lot by machine) and pulls memory from 26MB to 393MB. Left alone, those 25 seconds hide inside "recognizing page one," so what the frontend's polling sees is "page 0/N" frozen for 25 seconds — indistinguishable from a hang, especially now that the service starts on demand (see below) and pays this cost fresh on every single upload, making it even easier to mistake for a failed startup.

The fix was pulling model loading into its own explicit step: the job state gained a `phase` field (`preparing` → `loading_model` → `ocr`), and the frontend shows text matching the actual phase instead of waiting for page one to finish before showing anything.

### Service Lifecycle: Start on Use, Stop When Done

This service doesn't decide its own start/stop timing at all — that's entirely controlled by the desktop shell and the web frontend (full story in `zh-cn-to-tw-mac`/`zh-cn-to-tw-web`'s READMEs). It keeps only two safety nets of its own:

- **Idle-timeout self-shutdown**: checks whether any job is currently `running`, not simply "how long since the last request" — a large file that takes a long time with nobody polling it shouldn't be mistaken for idle and killed mid-work.
- **Orphan-process watchdog**: checks every few seconds whether its parent PID has changed. The desktop shell normally kills this subprocess itself on clean exit; this is the safety net for the shell being killed abruptly (e.g. a crash) — once the parent dies, the OS reparents this process to something else, which the watchdog detects and self-terminates on, so no orphan process is left holding a port or memory.

### Security

`/ocr/pdf/start` requires the caller to supply, in the `X-OCR-Token` header, the random token the shell generated when it launched this service. Any other local program or page on the same machine could in theory reach this localhost port, so a value that's randomly generated per-launch and known only to the shell itself prevents impersonated calls.

The CORS allowlist includes a literal `"null"` entry — once the desktop shell loads its page via `file://`, the browser's `Origin` header on cross-origin requests is the literal string `"null"`; without allowing it, every desktop-mode call to this service gets blocked by CORS (confirmed by reproduction: `Origin null is not allowed by Access-Control-Allow-Origin`).

### Why paddleocr Was Replaced With rapidocr_onnxruntime

2026-08-17: early in Windows development, `import paddle` crashed the entire process on a real user-grade machine (an Intel Pentium Gold G5400, a 2018 desktop chip) — Windows reported "the dynamic-link library (DLL) initialization routine failed," a crash even Python's own `try/except` couldn't catch, since it's a memory-level fault, not a clean exception. Checking that CPU's instruction set directly with `py-cpuinfo` confirmed it has no `avx`/`avx2`/`fma` — the official paddlepaddle wheel on PyPI is compiled assuming AVX2 is present; without it, the library doesn't just run slower, it doesn't run at all.

This isn't a one-machine fluke, and it isn't even a new problem — the SIGILL crash mentioned above under "Why This Service Exists," once seen on a Render host, was the same class of failure showing up earlier; the conclusion drawn at the time was "that was specific to that one Render host, and moving OCR to the user's own machine means it won't happen again." This session proved that conclusion wrong: the real variable isn't "cloud vs. local," it's "does this CPU have AVX2" — any user's own machine can hit this if its CPU lacks it, and budget-tier chips (Pentium/Celeron) commonly have AVX2 deliberately disabled by the vendor even on relatively recent silicon (the G5400 here isn't an antique — it's a 2018 desktop chip).

Switching to `rapidocr_onnxruntime` (an ONNXRuntime-backed inference engine) fixed this, using the same underlying model: RapidOCR ships `ch_PP-OCRv4_det/rec_infer.onnx` — literally PP-OCRv4 itself, exported to ONNX format — so recognition quality should be unchanged in principle. Tested on the exact same non-AVX2 machine: `RapidOCR()` initializes in 0.4s, single-page inference takes about 5s, both Simplified and Traditional Chinese text recognized correctly with confidence scores all above 0.96, with none of paddlepaddle's load-time crash. ONNXRuntime is known industry-wide for broad hardware/OS compatibility as an inference engine, and this result matches that reputation.

Since this is a shared repo (`zh-cn-to-tw-mac` and `zh-cn-to-tw-windows` each PyInstaller-package the exact same source), this decision affects both platforms at once. The Mac build never actually reproduced this crash (the Intel/Apple Silicon chips currently in use on Mac either have AVX2 or aren't subject to this limitation at all), but both platforms switched together anyway, to keep a single shared OCR-engine codepath instead of maintaining two.

### The Orphan-Process Watchdog Is Unreliable on Windows

The orphan-process watchdog mentioned above under "Service Lifecycle" (polling whether `os.getppid()` has changed) is effectively broken on Windows: on POSIX systems (macOS/Linux), once a parent process dies, the child gets reparented to something else by the OS (typically init/launchd), so `getppid()`'s return value genuinely changes and the polling loop catches it. Windows does not reparent a child's tracked parent PID — that value is fixed at process-creation time and stays the same even after the parent dies, unless some unrelated new process happens to reuse that exact PID. `zh-cn-to-tw-windows` uses the native Windows equivalent instead — a Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` — so the OS kernel itself kills this subprocess the moment the shell process dies, with no dependence on polling; see `ProcessJobObject.cs` in that repo for details. This watchdog itself wasn't removed: the Mac build still relies on it, and removing it would cost the Mac build a safety net for zero benefit on the Windows side (which already has the more reliable Job Object guarantee).

### Known Limitations

- The Windows counterpart service is now wired up (`zh-cn-to-tw-windows`); see the two sections above for details.

---

# Setup Guide

## Local Development

macOS:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 app.py
```

Windows:

```powershell
python -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

On startup it prints `OCR_SERVICE_PORT=<port>` to stdout — without an `OCR_SERVICE_PORT` environment variable set, the OS assigns a free port; a fixed port is never hardcoded (this project got burned repeatedly during local testing by a stale process squatting a fixed port).

Testing (no token needed — without `OCR_SERVICE_TOKEN` set, local dev doesn't enforce it):

```bash
curl -X POST http://127.0.0.1:<port>/ocr/pdf/start -F "file=@test.pdf"
curl http://127.0.0.1:<port>/ocr/pdf/status/<job_id>
```

## Building a Standalone Executable (PyInstaller)

**The build environment must be this repo's own `venv/` — never borrow a scratch directory unrelated to this repo.** This project once reused a venv that had been temporarily created for an unrelated experiment (a DPI timing test) and lived under a system temp path; that directory belonged to no repo and could vanish at any time, meaning "switch machines, or even just wait a while" was enough to make rebuilding impossible — completely unfit for long-term maintenance. The correct approach is a permanent `venv/` inside this repo itself (excluded from version control via `.gitignore`, but the directory itself stays on disk, independent of any path unrelated to this repo).

Since switching to `rapidocr_onnxruntime` (see "Why paddleocr Was Replaced With rapidocr_onnxruntime" above), packaging is dramatically simpler: the three paddle-specific compatibility hacks are gone (the custom runtime hook, `collect_data_files("Cython")`, and the `Tree()` copy of paddleocr's source — full history of why they existed is preserved in git history for the old version of this README and `packaging/ocr_service.spec`). `pyinstaller`'s `pyinstaller-hooks-contrib` dependency already ships packaging rules for onnxruntime/opencv/lxml and friends, so the `.spec` file only needs `collect_data_files("rapidocr_onnxruntime")` to pull in the package's bundled `config.yaml` and `models/*.onnx` — no custom runtime hook needed at all.

macOS:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt -r requirements-build.txt
pyinstaller packaging/ocr_service.spec --noconfirm
```

Windows:

```powershell
python -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt -r requirements-build.txt
pyinstaller packaging\ocr_service.spec --noconfirm
```

The result lands in `dist/zh-cn-to-tw-ocr-service/`, a fully self-contained directory — **it needs no Python and no venv at runtime**, which is the whole point of PyInstaller's onedir mode: the frozen executable runs on a machine with no Python installed at all. The venv above is only needed to *regenerate* this build output — it has nothing to do with what the end user actually receives (what ships inside the `.app`/`.exe`). Measured on Windows: about 278MB, far smaller than the old paddleocr-based build (roughly 850MB) — that's not primarily a side effect of dropping Windows 7 support (see `zh-cn-to-tw-windows`'s README), it's mostly just onnxruntime being far leaner than paddlepaddle.

## Environment Variables

| Variable | Description |
|---|---|
| `OCR_SERVICE_PORT` | Port to listen on; unset lets the OS assign a free one |
| `OCR_SERVICE_TOKEN` | Token required by `/ocr/pdf/start`; unset means no verification (for local dev) |
| `OCR_SERVICE_IDLE_TIMEOUT_MINUTES` | How long idle (with no job running) before self-shutdown |
