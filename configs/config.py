"""zh-cn-to-tw-ocr-service 設定值。這支服務只在使用者本機執行、只服務同一台
機器上的桌面 App 殼，不連 DB、不碰任何 Gemini/Claude/Neon 憑證——那些全部
留在 zh-cn-to-tw-backend（Render）那一側，這裡刻意保持「無憑證」。
"""

import os

# 桌面殼啟動這支服務時會透過環境變數帶一個隨機 token 進來，/ocr/pdf 要求
# 呼叫端在 header 帶同一個 token 才放行——同一台機器上任何網頁（包括桌面殼
# 裡的 WKWebView 載入的頁面本身如果被injection）理論上都碰得到 localhost
# 的固定 port，用一個殼啟動時才產生、只有殼自己知道的隨機值，避免其他
# 本機程式或惡意頁面冒充呼叫這支服務。
OCR_SERVICE_TOKEN = os.environ.get("OCR_SERVICE_TOKEN", "")

# 監聽 port：0 代表讓作業系統配一個目前沒人用的空 port，啟動後把實際拿到
# 的 port 印到 stdout 給殼讀取——絕不寫死固定 port，這個專案本機測試階段
# 吃過很多次「port 被舊 process 卡住」的虧，桌面 App 尤其不能重蹈覆轍。
PORT = int(os.environ.get("OCR_SERVICE_PORT", "0"))

# 閒置這麼多分鐘沒有任何 /ocr/pdf 請求就自動關閉，避免無限期占用
# PaddleOCR 模型常駐的記憶體（實測第一次推論就會吃到 2.6-2.8GB）。
# 殼本身的健康監控/自動重啟邏輯會在下次真的需要時無感重新拉起這支服務，
# 使用者不需要重開整個 App。
IDLE_TIMEOUT_MINUTES = float(os.environ.get("OCR_SERVICE_IDLE_TIMEOUT_MINUTES", "30"))

# CPU 執行緒數：跟 zh-cn-to-tw-backend 那邊刻意鎖 1 完全相反——那邊是為了
# 閃避 Render 免費方案 0.1 顆 CPU 配額被榨乾的問題，這裡跑在使用者自己的
# 機器上，多執行緒平行運算是純粹的效能加分，沒有那個限制的理由。
OCR_CPU_THREADS = int(os.environ.get("OCR_CPU_THREADS", str(min(4, os.cpu_count() or 1))))

# 跟 zh-cn-to-tw-backend/ocr_utils/ocr_engine.py 保持一致：PP-OCRv3 是已經
# 驗證過穩定可用的版本，PP-OCRv4 曾經在 Render 上因為特定主機的 CPU
# 缺某個向量化指令直接 SIGILL 崩潰——雖然那是 Render 那台機器的問題，
# 但目前還沒有在使用者自己的機器上驗證過 PP-OCRv4，先沿用已知安全的版本，
# 之後有餘裕再個別驗證要不要換更新版本。
OCR_VERSION = os.environ.get("OCR_VERSION", "PP-OCRv3")

PDF_RENDER_DPI = int(os.environ.get("PDF_RENDER_DPI", "200"))

COVER_DETECT_DEFAULT = os.environ.get("COVER_DETECT_DEFAULT", "true").lower() == "true"
COVER_DETECT_DARK_RATIO_THRESHOLD = float(os.environ.get("COVER_DETECT_DARK_RATIO_THRESHOLD", "0.35"))
COVER_DETECT_SATURATION_THRESHOLD = float(os.environ.get("COVER_DETECT_SATURATION_THRESHOLD", "20"))
COVER_DETECT_RELATIVE_MARGIN = float(os.environ.get("COVER_DETECT_RELATIVE_MARGIN", "1.5"))
