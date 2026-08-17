# -*- mode: python ; coding: utf-8 -*-
# 打包 zh-cn-to-tw-ocr-service 成獨立執行檔。
#
# 2026-08-17 從 paddleocr 換成 rapidocr_onnxruntime 之後，這份 spec 大幅
# 簡化——不再需要 paddle 專屬的三個相容性 hack（runtime hook 修正
# set_paddle_lib_path()、collect-data Cython、Tree() 複製 paddleocr 原始碼，
# 見 git 歷史裡舊版這個檔案的完整說明）。rapidocr_onnxruntime 是純
# Python + 資料檔（config.yaml、models/*.onnx）的套件，用
# collect_data_files() 把這些資料檔收進來就夠了。
import os

from PyInstaller.utils.hooks import collect_data_files

# SPECPATH 是 PyInstaller 注入 spec 檔執行環境的內建變數（這個 .spec
# 檔自己所在的絕對路徑），不要假設呼叫端的目前工作目錄，避免從不同地方
# 執行 pyinstaller 時路徑解析出錯。
repo_root = os.path.dirname(SPECPATH)

a = Analysis(
    [os.path.join(repo_root, "app.py")],
    pathex=[repo_root],
    binaries=[],
    datas=collect_data_files("rapidocr_onnxruntime"),
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

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
