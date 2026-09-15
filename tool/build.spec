# PyInstaller spec for MatreshkaRemapRenderer.
#
#   pyinstaller --noconfirm build.spec
#
# A spec rather than a command line because --exclude-module only reaches
# Python modules, and most of the weight here is Qt DLLs that PySide6 ships
# whether or not anything imports them.

import os

HERE = os.path.abspath(os.path.dirname(SPECPATH))
TOOL = HERE if os.path.exists(os.path.join(HERE, "main.py")) else SPECPATH

# Qt libraries nothing in this application touches. The window is plain
# widgets drawn by the raster engine, and the GPU work goes through wgpu's own
# Vulkan/Metal backend, so Qt's QML stack and its software OpenGL fallback are
# dead weight -- about 20 MB of it.
UNWANTED = (
    "opengl32sw.dll",
    "Qt6Quick.dll", "Qt6QuickControls2.dll", "Qt6QuickTemplates2.dll",
    "Qt6QuickWidgets.dll", "Qt6Qml.dll", "Qt6QmlModels.dll", "Qt6QmlWorkerScript.dll",
    "Qt6Pdf.dll", "Qt6PdfWidgets.dll",
    "Qt6Network.dll", "Qt6Sql.dll", "Qt6Test.dll", "Qt6Designer.dll",
    "d3dcompiler_47.dll",
)

# Not on that list, deliberately: _ssl.pyd needs libssl and libcrypto, and
# without them the first-run offer to fetch ffmpeg cannot reach GitHub. They
# cost a few megabytes; a dead download button costs more.


def keep(item):
    name = os.path.basename(item[0])
    return name not in UNWANTED


datas = [
    (os.path.join(TOOL, "Check.png"), "."),
    (os.path.join(TOOL, "remap_tool.json"), "."),
    (os.path.join(TOOL, "tables", "table_full.npz"), "."),
    (os.path.join(TOOL, "tables", "table_half.npz"), "."),
    (os.path.join(TOOL, "tables", "table_quarter.npz"), "."),
    (os.path.join(TOOL, "tables", "screen_geometry.npz"), "."),
    (os.path.join(TOOL, "tables", "viewer_table.npz"), "."),
]
# The icon set, as a folder: it is looked for by name at run time, so it has to
# arrive as one rather than as files scattered into the root.
datas += [(os.path.join(TOOL, "icons"), "icons")]

a = Analysis(
    [os.path.join(TOOL, "main.py")],
    pathex=[TOOL],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    excludes=[
        "cv2", "tkinter", "matplotlib", "scipy", "PIL", "pandas",
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
        "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtMultimedia",
        "PySide6.QtCharts", "PySide6.QtNetwork", "PySide6.QtPdf",
        "PySide6.QtOpenGL", "PySide6.Qt3DCore", "PySide6.QtSql",
    ],
    noarchive=False,
)

a.binaries = [item for item in a.binaries if keep(item)]
a.datas = [item for item in a.datas if keep(item)]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="MatreshkaRemapRenderer",
    debug=False,
    strip=False,
    upx=False,
    console=False,
)
