from pathlib import Path
import re

from .appearance import DEFAULT_ACCENT, accent_foreground, normalize_accent

STYLE = """
* { font-family: 'Segoe UI'; font-size: 13px; }
QWidget { color: #e9ecf5; }
QMainWindow, QDialog { background: #131620; }
QWidget#root, QWidget#page { background: #131620; }
QLabel { background: transparent; border: none; }
QFrame#sidebar { background: #10131b; border-right: 1px solid #242937; }
QFrame#topbar { border-bottom: 1px solid #272c39; }
QFrame#card, QFrame#tableCard { background: #1b1f2b; border: 1px solid #2b3040; border-radius: 14px; }
QFrame#hero { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #26243e, stop:0.5 #23253a,stop:1 #1c2733); border: 1px solid #3a3d59; border-radius: 18px; }
QLabel#title { font-size: 29px; font-weight: 700; letter-spacing: -0.6px; }
QLabel#subtitle { color: #949db2; font-size: 13px; }
QLabel#muted { color: #949db2; }
QLabel#small { color: #8893aa; font-size: 11px; }
QLabel#section { font-size: 17px; font-weight: 600; }
QLabel#metric { font-size: 27px; font-weight: 650; }
QLabel#eyebrow { color: #aea4f5; font-size: 10px; font-weight: 700; letter-spacing: 1.5px; }
QLabel#badge { color: #86e3c3; background: #19362f; padding: 6px 10px; border-radius: 9px; font-size: 11px; }
QLabel#warning { color: #e5c68b; background: #322d24; padding: 11px 14px; border: 1px solid #4e432e; border-radius: 9px; }
QLabel#note { color: #a4aec2; background: #1a202e; padding: 12px; border: 1px solid #29334a; border-radius: 9px; }
QPushButton { background: #272c3b; border: 1px solid #373e51; border-radius: 9px; padding: 10px 17px; font-weight: 600; }
QPushButton:hover { background: #33394c; border-color: #555e79; }
QPushButton:pressed { background: #41465d; }
QPushButton:disabled { color: #626c80; background: #212534; border-color: #2b3040; }
QPushButton#primary { background: #9584ff; color: #141126; border: none; }
QPushButton#primary:hover { background: #ad9fff; }
QPushButton#primary:pressed { background: #8371ed; }
QPushButton#primary:disabled { background: #46405f; color: #9890b2; }
QPushButton#danger { background: #e5ac86; color: #271b18; border: none; }
QPushButton#ghost { background: transparent; border: none; color: #b1a6ff; padding: 6px; }
QPushButton#nav { background: transparent; border: none; text-align: left; padding: 13px 14px; color: #9ba5bb; font-weight: 500; }
QPushButton#nav:hover { background: #202332; color: #eceafb; }
QPushButton#nav:checked { background: #2d2946; color: #c6bcff; }
QPushButton#nav:disabled { color: #525967; }
QLineEdit, QSpinBox, QComboBox { background: #141824; border: 1px solid #343b4e; border-radius: 8px; padding: 9px 11px; selection-background-color: #514a79; }
QLineEdit:focus, QSpinBox:focus, QComboBox:focus { border-color: #9584ff; }
QComboBox::drop-down { border: none; width: 25px; }
QComboBox QAbstractItemView { background: #242939; selection-background-color: #403955; }
QCheckBox { spacing: 9px; background: transparent; }
QCheckBox::indicator { width: 17px; height: 17px; border: 1px solid #525c74; border-radius: 5px; background: #171b26; }
QCheckBox::indicator:checked { background: #9584ff; border: 4px solid #9584ff; image: none; }
QCheckBox:disabled { color: #647086; }
QTableView { background: #1b1f2b; alternate-background-color: #1d2230; border: none; gridline-color: #2b3040; selection-background-color: #33314c; selection-color: #eeeafd; outline: none; }
QTableView::item { padding: 8px; border-bottom: 1px solid #272d3c; }
QHeaderView::section { background: #212634; color: #9ca7bd; border: none; border-bottom: 1px solid #343b4d; padding: 10px 9px; font-size: 11px; font-weight: 600; }
QTableCornerButton::section { background: #212634; border: none; }
QScrollArea, QStackedWidget { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 2px; }
QScrollBar::handle:vertical { background: #424b60; border-radius: 3px; min-height: 35px; }
QScrollBar:horizontal { background: transparent; height: 8px; }
QScrollBar::handle:horizontal { background: #424b60; border-radius: 3px; min-width: 35px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QProgressBar { border: none; border-radius: 3px; background: #2b3142; height: 6px; text-align: center; color: transparent; }
QProgressBar::chunk { border-radius: 3px; background: #9787ff; }
QListWidget { background: #151a26; border: 1px solid #343b4e; border-radius: 9px; padding: 6px; }
QListWidget::item { padding: 9px; border-radius: 5px; }
QListWidget::item:selected { background: #38324e; }
QToolTip { color: #ecedf5; background: #2a3041; border: 1px solid #46506a; padding: 8px; }
QMenu { background: #242939; border: 1px solid #424a60; padding: 5px; }
QMenu::item { padding: 8px 25px; }
QMenu::item:selected { background: #413954; }
"""

_check = (Path(__file__).parent / "assets" / "check.svg").as_posix()
STYLE += f'QCheckBox::indicator:checked {{ image: url("{_check}"); border: 1px solid #9584ff; }}'
STYLE += f'QTableView::indicator:checked {{ image: url("{_check}"); background: #9584ff; border: 1px solid #9584ff; border-radius: 4px; width: 16px; height: 16px; }}'
STYLE += 'QTableView::indicator:unchecked { background: #171b26; border: 1px solid #525c74; border-radius: 4px; width: 16px; height: 16px; }'
_down = (Path(__file__).parent / "assets" / "down.svg").as_posix()
_up = (Path(__file__).parent / "assets" / "up.svg").as_posix()
STYLE += f'QComboBox::down-arrow, QSpinBox::down-arrow {{ image: url("{_down}"); width: 12px; height: 12px; }}'
STYLE += f'QSpinBox::up-arrow {{ image: url("{_up}"); width: 12px; height: 12px; }}'
STYLE += 'QSpinBox::up-button { subcontrol-origin: border; subcontrol-position: top right; width: 24px; border: none; background: #272c3b; border-top-right-radius: 7px; } QSpinBox::down-button { subcontrol-origin: border; subcontrol-position: bottom right; width: 24px; border: none; background: #272c3b; border-bottom-right-radius: 7px; }'


def stylesheet(accent=DEFAULT_ACCENT):
    accent = normalize_accent(accent)
    if accent == DEFAULT_ACCENT:
        return STYLE

    def mix(other, amount):
        return "#" + "".join(f"{round(int(accent[i:i+2], 16) * (1 - amount) + int(other[i:i+2], 16) * amount):02x}" for i in (1, 3, 5))

    colors = {"#9584ff": accent, "#9787ff": accent, "#141126": accent_foreground(accent),
              "#ad9fff": mix("#ffffff", .20), "#8371ed": mix("#000000", .15)}
    for color in ("#aea4f5", "#b1a6ff", "#c6bcff"):
        colors[color] = mix("#ffffff", .45)
    for color in ("#2d2946", "#514a79", "#403955", "#33314c", "#38324e", "#413954", "#46405f"):
        colors[color] = mix("#131620", .80)
    for color in ("#26243e", "#23253a", "#1c2733", "#3a3d59"):
        colors[color] = mix("#1b1f2b", .87 if color != "#3a3d59" else .65)
    result = re.sub(r"#[0-9a-f]{6}", lambda match: colors.get(match[0], match[0]), STYLE)
    if accent_foreground(accent) == "#ffffff":
        result = result.replace("check.svg", "check-light.svg")
    return result
