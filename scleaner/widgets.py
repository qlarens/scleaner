from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QRectF, QSize, QSortFilterProxyModel, Qt, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog,
    QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu, QPushButton, QTableView, QVBoxLayout, QWidget)

from .catalog import CATEGORIES
from .models import size_text

PATHS = {
    "home": '<path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1Z"/>',
    "spark": '<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5Z"/><path d="M20 2v4M18 4h4"/>',
    "globe": '<circle cx="12" cy="12" r="9"/><ellipse cx="12" cy="12" rx="4" ry="9"/><path d="M3 12h18"/>',
    "grid": '<rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/>',
    "file": '<path d="M14 3H5v18h14V8Z"/><path d="M14 3v5h5M8 12h8M8 16h5"/>',
    "chip": '<rect x="6" y="6" width="12" height="12" rx="3"/><path d="M9 2v4m6-4v4M9 18v4m6-4v4M2 9h4m-4 6h4m12-6h4m-4 6h4"/><rect x="10" y="10" width="4" height="4" rx="1"/>',
    "folder": '<path d="M3 7V4h6l3 3h9v13H3Z"/>',
    "layers": '<path d="m12 3 10 5-10 5L2 8Zm-9 10 9 5 9-5M3 18l9 5 9-5"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v6l4 2"/>',
    "settings": '<path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="3" fill="BG"/><circle cx="15" cy="17" r="3" fill="BG"/>',
    "shield": '<path d="m12 2 8 3v7c0 5-8 10-8 10S4 17 4 12V5Z"/><path d="m8 12 3 3 5-6"/>',
    "arrow": '<path d="M4 12h16m-6-6 6 6-6 6"/>',
    "search": '<circle cx="10" cy="10" r="7"/><path d="m15 15 6 6"/>',
    "disk": '<rect x="3" y="3" width="18" height="18" rx="4"/><circle cx="12" cy="10" r="4"/><path d="M7 17h.01M11 17h6"/>',
    "check": '<path d="m5 12 4 4L19 6"/>',
    "chevron": '<path d="m9 6 6 6-6 6"/>',
    "download": '<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
    "trash": '<path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7"/>',
}


def icon(name, color="#a99cff", size=22, background="#10131b") -> QIcon:
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">{PATHS.get(name, PATHS["spark"]).replace("BG", background)}</svg>'
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    QSvgRenderer(svg.encode()).render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


def label(text="", name="", wrap=False):
    result = QLabel(text)
    result.setTextFormat(Qt.TextFormat.PlainText)
    if name:
        result.setObjectName(name)
    result.setWordWrap(wrap)
    return result


def button(text, callback=None, primary=False, glyph=None):
    result = QPushButton(text)
    result.setCursor(Qt.CursorShape.PointingHandCursor)
    if primary:
        result.setObjectName("primary")
    if glyph:
        result.setProperty("glyph", glyph)
        result.setIcon(icon(glyph, "#201831" if primary else "#b8afd7", 18))
        result.setIconSize(QSize(18, 18))
    if callback:
        result.clicked.connect(callback)
    return result


def card():
    result = QFrame()
    result.setObjectName("card")
    return result


class DiskRing(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(235, 230)
        self.setMaximumWidth(280)
        self.disk_path = Path(os.environ.get("SystemRoot", str(Path.home()))).anchor or str(Path.home())
        self.usage = None
        self.accent = "#a497ff"
        self.refresh()

    def refresh(self):
        try:
            self.usage = shutil.disk_usage(self.disk_path)
        except OSError:
            self.usage = None
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx, cy = self.width() / 2, self.height() / 2
        ring = QRectF(cx - 91, cy - 91, 182, 182)
        painter.setPen(QPen(QColor("#373b50"), 12, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawArc(ring, 0, 360 * 16)
        if self.usage:
            painter.setPen(QPen(QColor(self.accent), 12, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawArc(ring, 90 * 16, -int(self.usage.used / self.usage.total * 360 * 16))
        painter.setPen(QColor("#a7b0c6"))
        painter.setFont(QFont("Segoe UI", 9))
        painter.drawText(QRectF(cx - 90, cy - 41, 180, 24), Qt.AlignmentFlag.AlignCenter, "СВОБОДНО")
        painter.setPen(QColor("#f0effc"))
        painter.setFont(QFont("Segoe UI", 21, QFont.Weight.DemiBold))
        painter.drawText(QRectF(cx - 90, cy - 13, 180, 39), Qt.AlignmentFlag.AlignCenter, size_text(self.usage.free) if self.usage else "—")
        painter.setPen(QColor("#a7b0c6"))
        painter.setFont(QFont("Segoe UI", 9))
        painter.drawText(QRectF(cx - 90, cy + 32, 180, 25), Qt.AlignmentFlag.AlignCenter, f"Диск {self.disk_path.rstrip(chr(92) + '/')}" )
        painter.end()


class FindingModel(QAbstractTableModel):
    selection_changed = Signal()
    HEADERS = ["", "ОБЪЕКТ", "КАТЕГОРИЯ", "РАЗМЕР", "РАСПОЛОЖЕНИЕ"]

    def __init__(self, readonly=False):
        super().__init__()
        self.items = []
        self.readonly = readonly

    def set_items(self, items):
        self.beginResetModel()
        self.items = items
        self.endResetModel()
        self.selection_changed.emit()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.items)

    def columnCount(self, parent=QModelIndex()):
        return 5

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.HEADERS[section]

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        item = self.items[index.row()]
        column = index.column()
        if role == Qt.ItemDataRole.CheckStateRole and column == 0 and not self.readonly:
            return Qt.CheckState.Checked if item.selected else Qt.CheckState.Unchecked
        if role == Qt.ItemDataRole.DisplayRole:
            category = "Ручная проверка" if item.registry.get("review_required") else CATEGORIES.get(item.category, ("Крупный файл",))[0]
            return ["", item.label, category, size_text(item.size) if item.kind not in ("registry", "directory") else "—", item.path][column]
        if role == Qt.ItemDataRole.UserRole:
            return item.size if column == 3 else str(self.data(index, Qt.ItemDataRole.DisplayRole)).casefold()
        if role == Qt.ItemDataRole.ToolTipRole:
            return f"{item.path}\n\n{item.details}"
        if role == Qt.ItemDataRole.ForegroundRole:
            return QColor("#d8be91" if item.kind in ("registry", "directory") else "#a9b3c9" if column in (2, 4) else "#e8eaf4")

    def flags(self, index):
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.column() == 0 and not self.readonly:
            flags |= Qt.ItemFlag.ItemIsUserCheckable
        return flags

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if role == Qt.ItemDataRole.CheckStateRole and not self.readonly:
            self.items[index.row()].selected = value == Qt.CheckState.Checked.value or value == Qt.CheckState.Checked
            self.dataChanged.emit(index, index, [role])
            self.selection_changed.emit()
            return True
        return False


class FindingFilter(QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self.category = ""
        self.query = ""
        self.setSortRole(Qt.ItemDataRole.UserRole)

    def filterAcceptsRow(self, row, parent):
        item = self.sourceModel().items[row]
        return (not self.category or item.category == self.category) and (not self.query or self.query in (item.path + " " + item.label).casefold())


class FindingsTable(QWidget):
    def __init__(self, readonly=False, parent=None):
        super().__init__(parent)
        self.model = FindingModel(readonly)
        self.proxy = FindingFilter()
        self.proxy.setSourceModel(self.model)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        toolbar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск по названию или пути…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.filter_changed)
        toolbar.addWidget(self.search, 1)
        self.category = QComboBox()
        self.category.addItem("Все категории", "")
        for key, (title, _, _) in CATEGORIES.items():
            self.category.addItem(title, key)
        self.category.currentIndexChanged.connect(self.filter_changed)
        if not readonly:
            toolbar.addWidget(self.category)
            toolbar.addWidget(button("Выбрать видимые", lambda: self.select_visible(True)))
            toolbar.addWidget(button("Снять выбор", lambda: self.select_visible(False)))
        layout.addLayout(toolbar)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(46)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.context_menu)
        self.table.doubleClicked.connect(self.details)
        header = self.table.horizontalHeader()
        for column, width in ((0, 42), (1, 230), (2, 175), (3, 100)):
            header.resizeSection(column, width)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        header.setMinimumSectionSize(36)
        self.table.sortByColumn(3, Qt.SortOrder.DescendingOrder)
        if readonly:
            self.table.hideColumn(0)
            self.table.hideColumn(2)
        self.table.setMinimumHeight(215)
        layout.addWidget(self.table, 1)
        self.empty = label("Запустите сканирование — здесь появится подробный список.", "muted")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.empty)

    def set_items(self, items):
        self.model.set_items(items)
        self.empty.setVisible(not items)
        self.empty.setText("Подходящих объектов не найдено." if not items else "")

    def filter_changed(self, *_):
        self.proxy.query = self.search.text().casefold()
        self.proxy.category = self.category.currentData()
        self.proxy.invalidate()

    def select_visible(self, selected):
        for row in range(self.proxy.rowCount()):
            source = self.proxy.mapToSource(self.proxy.index(row, 0))
            item = self.model.items[source.row()]
            if selected and item.registry.get("review_required"):
                continue
            item.selected = selected
        if self.model.items:
            self.model.dataChanged.emit(self.model.index(0, 0), self.model.index(len(self.model.items) - 1, 0))
        self.model.selection_changed.emit()

    def current_item(self, index):
        return self.model.items[self.proxy.mapToSource(index).row()] if index.isValid() else None

    def details(self, index):
        item = self.current_item(index)
        if item:
            dialog = QDialog(self)
            dialog.setWindowTitle("Сведения об объекте")
            dialog.resize(680, 250)
            layout = QVBoxLayout(dialog)
            layout.setContentsMargins(24, 24, 24, 24)
            layout.addWidget(label(item.label, "section", True))
            path = label(item.path, "muted", True)
            path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(path)
            layout.addWidget(label(item.details, "note", True))
            layout.addWidget(button("Закрыть", dialog.accept))
            dialog.exec()

    def context_menu(self, position):
        item = self.current_item(self.table.indexAt(position))
        if not item:
            return
        menu = QMenu(self)
        menu.addAction("Скопировать путь", lambda: QApplication.clipboard().setText(item.path))
        menu.addAction("Сведения", lambda: self.details(self.table.indexAt(position)))
        if item.kind != "registry":
            menu.addAction("Показать в Проводнике", lambda: reveal(item.path))
        menu.exec(self.table.viewport().mapToGlobal(position))


def reveal(path):
    path = Path(path)
    if os.name == "nt":
        subprocess.Popen(["explorer.exe", "/select,", str(path)])
    else:
        from PySide6.QtCore import QUrl
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))
