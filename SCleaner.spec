# Run: .venv\Scripts\python.exe -m PyInstaller --noconfirm SCleaner.spec
from pathlib import Path
import runpy
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.win32.versioninfo import VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable, StringStruct, VarFileInfo, VarStruct

root = Path(SPECPATH)
metadata = runpy.run_path(str(root / 'scleaner' / '__init__.py'))
version = metadata['__version__']
copyright_holder = metadata['__copyright_holder__']
version_tuple = tuple(int(part) for part in version.split('.')) + (0,)
version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=version_tuple, prodvers=version_tuple, mask=0x3f, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
    kids=[StringFileInfo([StringTable('040904B0', [
        StringStruct('CompanyName', copyright_holder), StringStruct('FileDescription', 'SCleaner — Windows cleanup utility'),
        StringStruct('FileVersion', version), StringStruct('ProductVersion', version),
        StringStruct('ProductName', 'SCleaner'), StringStruct('OriginalFilename', 'SCleaner.exe'),
        StringStruct('LegalCopyright', f'Copyright (c) 2026 {copyright_holder}'),
    ])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])],
)
datas = collect_data_files('scleaner')
datas += [(str(root / name), '.') for name in ('README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md')]
if (root / 'licenses').exists():
    datas.append((str(root / 'licenses'), 'licenses'))
a = Analysis(
    [str(root / 'run.py')], pathex=[str(root)], binaries=[], datas=datas,
    hiddenimports=[], hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtNetwork', 'PySide6.QtOpenGL', 'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'tkinter', 'unittest'], noarchive=False,
)
# Qt on Windows uses the OS ICU API. A developer PATH can contain a different,
# incompatible ICU (e.g. from Poppler). Never ship that in place of system ICU.
a.binaries = [entry for entry in a.binaries if Path(entry[0]).name.casefold() not in ('icuuc.dll', 'icuin.dll') and not Path(entry[0]).name.casefold().startswith('icudt')]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='SCleaner',
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False, disable_windowed_traceback=False, icon=str(root / 'artifacts' / 'SCleaner.ico'),
    # Interactive startup requests UAC through elevation.py. Read-only screenshots
    # remain available without elevation for build verification.
    uac_admin=False,
    version=version_info,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='SCleaner')
