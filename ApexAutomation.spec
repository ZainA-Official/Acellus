# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for ApexAutomation.
# Build with:  python -m PyInstaller ApexAutomation.spec

from PyInstaller.utils.hooks import collect_all, collect_submodules

# Collect the entire google.genai package (has many lazy submodules).
genai_datas, genai_binaries, genai_hiddenimports = collect_all("google.genai")
# Also pull in google.auth and google.api_core which genai depends on at runtime.
auth_datas, auth_binaries, auth_hiddenimports = collect_all("google.auth")
apicore_datas, apicore_binaries, apicore_hiddenimports = collect_all("google.api_core")

a = Analysis(
    ["gui.py"],
    pathex=[],
    binaries=genai_binaries + auth_binaries + apicore_binaries,
    datas=genai_datas + auth_datas + apicore_datas,
    hiddenimports=(
        genai_hiddenimports
        + auth_hiddenimports
        + apicore_hiddenimports
        + collect_submodules("google.genai")
        + collect_submodules("google.auth")
        + [
            "PIL._tkinter_finder",
            "mss",
            "mss.windows",
            "pyautogui",
            "winsound",
            "tkinter",
            "tkinter.scrolledtext",
            "tkinter.messagebox",
        ]
    ),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "matplotlib", "numpy", "scipy", "pandas",
        "IPython", "jupyter", "notebook",
        "test", "unittest",
    ],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="ApexAutomation",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,          # compress if UPX is available (reduces size)
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,     # no terminal window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
