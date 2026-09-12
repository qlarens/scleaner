# Third-party components

SCleaner uses Python, Qt for Python (PySide6 Essentials and Shiboken6), Qt libraries, and psutil. The application build is created with PyInstaller; the installer is built with Inno Setup. These components retain their respective copyrights and licenses.

- Python 3.12: Python Software Foundation license. Source and license: https://www.python.org/downloads/source/
- PySide6 Essentials / Shiboken6 6.11.2: package metadata declares LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only. Source: https://code.qt.io/cgit/pyside/pyside-setup.git/ (tag v6.11.2).
- Qt 6.11.2 Core / Gui / Widgets / Svg and platform/image plugins: open-source Qt licensing. Source: https://code.qt.io/cgit/qt/ and https://download.qt.io/archive/qt/6.11/6.11.2/submodules/ . Applicable component licenses are preserved with those sources.
- psutil 7.2.2: BSD 3-Clause. Source: https://github.com/giampaolo/psutil/tree/release-7.2.2
- cryptography 50.0.1: Apache-2.0 OR BSD-3-Clause. Source: https://github.com/pyca/cryptography/tree/50.0.1 . Used for Ed25519 license signature verification; wheel license texts are included in `licenses/cryptography`.
- cffi 2.1.1: MIT No Attribution (with component notices). Source: https://github.com/python-cffi/cffi . Wheel license text is included in `licenses/cffi`.
- pycparser 3.0: BSD 3-Clause. Source: https://github.com/eliben/pycparser . Wheel license text is included in `licenses/pycparser`.
- PyInstaller 6.22.2: GPL with the PyInstaller bootloader distribution exception. Source: https://github.com/pyinstaller/pyinstaller/tree/v6.22.2
- Inno Setup: its own license permits redistribution of compiled installers. Compiler source and license: https://github.com/jrsoftware/issrc . The compiler is a build tool and is not included in SCleaner.

Qt/PySide shared libraries remain separate files in the `_internal` directory in both portable and installed copies and can be replaced with compatible builds. SCleaner imposes no restriction on modifying or reverse engineering these components for debugging such modifications. The application source and build instructions are included with this project.

License texts included in the `licenses` directory are for their respective third-party components, not a change to the licensing of any other component.
