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
# OCR 模型常駐的記憶體。殼本身的健康監控/自動重啟邏輯會在下次真的需要時
# 無感重新拉起這支服務，使用者不需要重開整個 App。
IDLE_TIMEOUT_MINUTES = float(os.environ.get("OCR_SERVICE_IDLE_TIMEOUT_MINUTES", "30"))

PDF_RENDER_DPI = int(os.environ.get("PDF_RENDER_DPI", "200"))

# RapidOCR 偵測階段候選文字框的信心分數門檻（不是辨識信心）。套件自帶
# config.yaml 預設 0.5，實測撞到真實案例：一份劇本裡有兩整行清清楚楚的
# 文字，候選框信心分數卡在 0.3~0.5 之間，被 0.5 這個門檻擋掉、整行憑空
# 消失，沒有任何錯誤或警告；辨識信心分數其實是 0.998，證明文字本身完全
# 清楚，純粹是偵測門檻設太高。降到 0.3 後正確出現，同一頁其他行重新
# 驗證過沒有因此多出雜訊行。開放成前端「OCR門檻值」下拉選單可調整
# （越低越不會漏字，但抓到雜訊的風險也越高，見 ocr_utils/ocr_engine.py
# 的 ocr_page 呼叫端），這裡只是使用者沒有明確帶值時的預設。
OCR_DET_BOX_THRESH_DEFAULT = float(os.environ.get("OCR_DET_BOX_THRESH_DEFAULT", "0.3"))

COVER_DETECT_DEFAULT = os.environ.get("COVER_DETECT_DEFAULT", "true").lower() == "true"
COVER_DETECT_DARK_RATIO_THRESHOLD = float(os.environ.get("COVER_DETECT_DARK_RATIO_THRESHOLD", "0.35"))
COVER_DETECT_SATURATION_THRESHOLD = float(os.environ.get("COVER_DETECT_SATURATION_THRESHOLD", "20"))
COVER_DETECT_RELATIVE_MARGIN = float(os.environ.get("COVER_DETECT_RELATIVE_MARGIN", "1.5"))
