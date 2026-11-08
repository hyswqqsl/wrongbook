import sys
# -*- mode: python ; coding: utf-8 -*-
# AI错题本打包配置（R4）
# - onedir：exe/app + _internal/，静态资源（static/ engine/ data/kb 种子）由构建脚本复制到程序目录
# - 数据目录已外置（paths.py）：打包态写平台数据目录，程序目录只读、升级直接覆盖
# - export_paper 等引擎模块为进程内动态调用，必须列在 hiddenimports

import os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(SPEC)))   # 工程根（spec 在 packaging/ 下）

a = Analysis(
    [os.path.join(ROOT, 'app.py')],
    pathex=[ROOT, os.path.join(ROOT, 'engine')],
    binaries=[],
    datas=[],
    hiddenimports=[
        # engine/ 模块（pipeline 冻结态进程内 import export_paper；其余为静态依赖兜底）
        'export_paper', 'generate_any', 'geocheck', 'geoconstruct',
        'schema', 'verify_answers', 'make_figures',
        # 本工程模块（动态引用兜底，确保全部入包）
        'paths', 'common', 'store', 'stats', 'topics', 'kbtree',
        'reviewflow', 'figureflow', 'pipeline', 'wordexport',
        'ai_config', 'license_client',
    ],
    hookspath=[],
    excludes=['tkinter', 'pytest', 'pip', 'setuptools'],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='AI错题本',
    debug=False,
    strip=False,
    upx=False,          # UPX 压缩会显著提高杀软误报率，禁用
    console=False,      # windowed：无终端窗口（mac 生成 .app；Windows 生成无窗口 exe）
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='AI错题本',
)

if sys.platform == 'darwin':
    app = BUNDLE(
        coll,
        name='AI错题本.app',
        info_plist={'CFBundleDisplayName': 'AI错题本', 'CFBundleName': 'AI错题本'},
    )
