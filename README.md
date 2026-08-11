# zh-cn-to-tw-ocr-service

只在使用者本機執行的小型 HTTP 服務，桌面版 App（`zh-cn-to-tw-mac` /
`zh-cn-to-tw-windows`）啟動時把這支服務當內嵌引擎 subprocess 拉起來。

## 為什麼需要這支服務

`zh-cn-to-tw-backend` 部署在 Render 免費方案（0.1 CPU、512MB RAM），實測
無法穩定跑 PaddleOCR（OOM、CPU 配額耗盡被判定無回應、特定主機 CPU 不支援
PP-OCRv4 用到的向量化指令導致 SIGILL）。PaddleOCR 光是第一次推論就會把
RSS 衝到 2.6-2.8GB，遠超過免費方案上限，而付費方案在這個低毛利的專案上
不可行。解法是把 OCR 這一段搬到使用者自己的機器上執行，其餘（DB、
Gemini/Claude API 金鑰、登入、管理員介面、job/review 狀態、OpenCC+LLM
潤飾）維持在 Render 不變。

## 職責範圍（刻意畫得很窄）

- `POST /ocr/pdf`：收 PDF，做（可選的）封面偵測 + 逐頁 PaddleOCR，回傳
  `{"pages": [...], "total_pages": N, "logs": [...]}`（每頁一則字串，
  簡體、未潤飾）。
- `GET /health`：輕量存活檢查，不算活動、不重置閒置計時器。
- **不連 DB、不碰任何 API 金鑰**——job/review 擁有權檢查、管理員權限、
  個人專案額度限制等規則全部留在 `zh-cn-to-tw-backend`，不會因為多了
  這支本機服務而被繞過。

## 安全性

`/ocr/pdf` 要求呼叫端在 `X-OCR-Token` header 帶出殼啟動這支服務時給的
隨機 token（環境變數 `OCR_SERVICE_TOKEN`）。同一台機器上任何其他本機
程式或頁面理論上都碰得到 localhost 的這個 port，用一個殼啟動時才產生、
只有殼自己知道的隨機值來防止冒充呼叫。

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

## 打包成獨立執行檔（PyInstaller）

**打包用的環境要是這個 repo 自己的 `venv/`，不要借用專案外、跟這個
repo 無關的暫存目錄。** 之前一度直接沿用另一個實驗（DPI 耗時測試）
臨時建立、放在系統暫存路徑下的 venv，那個目錄不屬於任何 repo、
隨時可能被系統清掉，會導致「換一台機器、甚至只是隔了一段時間」就
沒辦法重新打包出 DMG，完全不符合長期維護的需求。正確做法是照下面
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
才需要，跟最終使用者拿到的東西（DMG 裡的內容）無關。
