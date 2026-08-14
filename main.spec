# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_all

datas = [('D:\\anaconda_2022\\lib\\site-packages\\fugashi.libs\\.load-order-fugashi-1.5.2', 'fugashi.libs'), ('D:\\anaconda_2022\\lib\\site-packages\\pyopenjtalk\\open_jtalk_dic_utf_8-1.11', 'pyopenjtalk/open_jtalk_dic_utf_8-1.11')]
binaries = [('D:\\anaconda_2022\\lib\\site-packages\\fugashi.libs\\libmecab-d8ddc0791437ce8c4a187ae7c69a2f68.dll', 'fugashi.libs')]
hiddenimports = []
datas += collect_data_files('unidic_lite')
datas += collect_data_files('fitz')
datas += collect_data_files('pdfplumber')
datas += collect_data_files('pypdf')
datas += collect_data_files('pdf2docx')
datas += collect_data_files('docx')
datas += collect_data_files('cv2')
hiddenimports += collect_submodules('pkg_resources')
tmp_ret = collect_all('fugashi')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('pyopenjtalk')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('jaraco')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('more_itertools')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('zipp')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'tkinter', 'matplotlib', 'numpy.f2py', 'numpy.testing', 'pandas', 'scipy', 'unittest', 'distutils', 'setuptools', 'IPython', 'jupyter', 'notebook', 'sphinx', 'pytest', 'sympy', 'tornado', 'boto3', 'google', 'azure', 'paddle.distribution', 'paddle.autograd', 'paddle.optimizer', 'paddle.fluid', 'paddle.incubate', 'paddle.dataset', 'paddle.audio', 'paddle.hapi', 'paddle.quantization', 'paddle.fleet', 'paddle.distributed', 'cv2.contrib'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='main',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
