# -*- mode: python ; coding: utf-8 -*-
# 打包 zh-cn-to-tw-ocr-service 成獨立執行檔。三個修正對應
# PaddlePaddle/paddleocr 在 PyInstaller 凍結環境下的已知相容性問題，
# 詳細原因見 README.md「打包成獨立執行檔」一節：
#   1. runtime hook（rthook_paddle_libpath.py）修正 paddle 自己的
#      set_paddle_lib_path() 在凍結環境下找不到 paddle/libs 的問題。
#   2. --collect-data Cython：paddle.utils.cpp_extension 需要的樣板檔案。
#   3. Tree() 複製整個 paddleocr 原始碼 + 明確列出的 hiddenimports +
#      copy_metadata：paddleocr 用檔案路徑 + sys.path trick 動態載入
#      自己的子模組，PyInstaller 的靜態分析完全看不到這些依賴。
from PyInstaller.utils.hooks import collect_data_files, copy_metadata

paddleocr_hidden_imports = [
    "requests", "shapely", "scipy", "skimage", "lmdb", "pyclipper", "six",
    "yaml", "tqdm", "rapidfuzz", "imgaug", "albumentations", "apted",
    "bs4", "docx", "editdistance", "tablepyxl", "qtpy", "cv2",
]

metadata_packages = [
    "imageio", "imgaug", "scikit-image", "shapely", "Pillow", "opencv-python",
    "paddleocr", "paddlepaddle", "pyclipper", "lmdb", "rapidfuzz", "requests",
    "tqdm", "six", "PyYAML", "numpy", "Flask", "Flask-Cors",
]
metadata_datas = []
for pkg in metadata_packages:
    try:
        metadata_datas += copy_metadata(pkg)
    except Exception:
        pass

import os

# SPECPATH 是 PyInstaller 注入 spec 檔執行環境的內建變數（這個 .spec
# 檔自己所在的絕對路徑），不要假設呼叫端的目前工作目錄，避免從不同地方
# 執行 pyinstaller 時路徑解析出錯。
repo_root = os.path.dirname(SPECPATH)

a = Analysis(
    [os.path.join(repo_root, "app.py")],
    pathex=[repo_root],
    binaries=[],
    datas=collect_data_files('Cython') + metadata_datas,
    hiddenimports=paddleocr_hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[os.path.join(SPECPATH, "rthook_paddle_libpath.py")],
    excludes=[],
    noarchive=False,
)

# PADDLEOCR_SITE_PACKAGES 由 build 指令的環境變數帶入（跑 pyinstaller 的
# venv 的 site-packages 路徑），避免這個 .spec 檔寫死某一台機器的路徑。
paddleocr_src = os.path.join(
    os.environ["PADDLEOCR_SITE_PACKAGES"], "paddleocr"
)
a.datas += Tree(paddleocr_src, prefix="paddleocr")

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='zh-cn-to-tw-ocr-service',
    debug=False,
    strip=False,
    upx=True,
    console=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name='zh-cn-to-tw-ocr-service',
)
