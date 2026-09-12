from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import threading
import time

from PySide6.QtCore import QLockFile, QSize, Qt, QThread, QTimer, Signal, QUrl
from PySide6.QtGui import QCloseEvent, QDesktopServices, QFont, QFontDatabase
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QFileDialog, QFrame, QGridLayout,
    QHBoxLayout, QInputDialog, QLabel, QListWidget, QMainWindow, QMessageBox, QProgressBar,
    QPushButton, QScrollArea, QSizePolicy, QSpinBox, QStackedWidget, QTextEdit, QVBoxLayout, QWidget)

from . import __author__, __version__
from .appearance import DEFAULT_ACCENT, accent_foreground, make_accent_dialog
from .catalog import CATEGORIES, DEFAULT_CATEGORIES
from .engine import Engine
from .elevation import ensure_admin
from .models import ScanResult, size_text
from .licensing import LicenseConfig, LicenseError, LicenseManager
from .pro_dialog import ProDialog
from .safety import is_admin
from .storage import Storage
from .theme import stylesheet
from .widgets import DiskRing, FindingsTable, button, card, icon, label


class Worker(QThread):
    done = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int, str)

    def __init__(self, task, parent=None):
        super().__init__(parent)
        self.task = task
        self.cancel = threading.Event()
        self.last_progress = 0.0

    def report(self, value, maximum, text):
        now = time.monotonic()
        if now - self.last_progress >= 0.08 or (maximum and value == maximum):
            self.progress.emit(value, maximum, text)
            self.last_progress = now

    def run(self):
        try:
            self.done.emit(self.task(self.cancel, self.report))
        except Exception as error:
            self.failed.emit(f"{type(error).__name__}: {error}")


class MainWindow(QMainWindow):
    def __init__(self, storage=None, engine=None, license_config=None):
        super().__init__()
        self.storage = storage or Storage()
        self.settings = self.storage.read_settings()
        self.licenses = LicenseManager(self.storage, license_config)
        self.pro_dialog = None
        self.engine = engine or Engine(storage=self.storage)
        self.worker = None
        self.busy = False
        self.closing = False
        self.results = {"clean": ScanResult(), "leftovers": ScanResult(), "large": ScanResult()}
        self.clean_scanned = False
        self.active_operation = ""
        self.setWindowTitle(f"SCleaner {__version__} — порядок в вашей системе")
        self.setWindowIcon(icon("spark", "#ab9dff", 64))
        self.resize(1440, 960)
        self.setMinimumSize(1180, 780)
        self.setStyleSheet(stylesheet(self.settings["accent_color"]))
        self.accent_dialog = None
        central = QWidget()
        central.setObjectName("root")
        self.setCentralWidget(central)
        outer = QHBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self.make_sidebar())
        body = QVBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        outer.addLayout(body, 1)
        body.addWidget(self.make_topbar())
        self.stack = QStackedWidget()
        body.addWidget(self.stack, 1)
        self.stack.addWidget(self.scroll_page(self.make_overview()))
        self.stack.addWidget(self.make_cleaner())
        self.stack.addWidget(self.make_leftovers())
        self.stack.addWidget(self.make_large())
        self.stack.addWidget(self.make_history())
        self.stack.addWidget(self.scroll_page(self.make_settings()))
        self.operation_frame = QFrame()
        self.operation_frame.setObjectName("topbar")
        operation_layout = QHBoxLayout(self.operation_frame)
        operation_layout.setContentsMargins(30, 14, 30, 14)
        self.operation_label = label("Готово к работе", "muted")
        self.operation_bar = QProgressBar()
        self.operation_bar.setMaximumWidth(240)
        self.operation_bar.setTextVisible(False)
        self.cancel_button = button("Остановить", self.cancel_operation)
        operation_layout.addWidget(self.operation_label, 1)
        operation_layout.addWidget(self.operation_bar)
        operation_layout.addWidget(self.cancel_button)
        body.addWidget(self.operation_frame)
        self.operation_frame.hide()
        self.select_page(0)
        self.refresh_history()
        self.apply_accent(self.settings["accent_color"])
        self.refresh_license()
        self.license_timer = QTimer(self)
        self.license_timer.timeout.connect(self.refresh_license)
        self.license_timer.start(30000)

    def make_sidebar(self):
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(224)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(18, 27, 18, 24)
        layout.setSpacing(7)
        brand = QHBoxLayout()
        mark = label()
        mark.setProperty("accentGlyph", "spark")
        mark.setProperty("accentSize", 34)
        mark.setPixmap(icon("spark", "#b6a8ff", 34).pixmap(34, 34))
        brand.addWidget(mark)
        name = label("SCleaner")
        name.setStyleSheet("font-size: 23px; font-weight: 700; letter-spacing: -0.6px;")
        brand.addWidget(name)
        brand.addStretch()
        layout.addLayout(brand)
        layout.addSpacing(5)
        layout.addWidget(label("ПРОСТРАНСТВО ДЛЯ ВАЖНОГО", "small"))
        layout.addSpacing(35)
        layout.addWidget(label("  РАБОЧЕЕ ПРОСТРАНСТВО", "small"))
        layout.addSpacing(8)
        self.nav = []
        for index, (text, glyph) in enumerate([
            ("Обзор", "home"), ("Очистка системы", "spark"), ("Остатки приложений", "layers"),
            ("Крупные файлы", "folder"), ("История", "clock"),
        ]):
            b = button(text, lambda checked=False, i=index: self.select_page(i), glyph=glyph)
            b.setObjectName("nav")
            b.setCheckable(True)
            b.setIconSize(QSize(19, 19))
            self.nav.append(b)
            layout.addWidget(b)
        layout.addStretch()
        tip = card()
        tips = QVBoxLayout(tip)
        tips.setContentsMargins(15, 18, 15, 18)
        shield = label()
        shield.setPixmap(icon("shield", "#7bd6ba", 25).pixmap(25, 25))
        tips.addWidget(shield)
        tips.addSpacing(6)
        tips.addWidget(label("Вы управляете очисткой"))
        tips.addWidget(label("Сначала просмотр.\nЗатем ваше решение.", "small", True))
        layout.addWidget(tip)
        self.pro_button = button("Free · О версии Pro", self.open_pro_dialog, glyph="spark")
        layout.addWidget(self.pro_button)
        layout.addSpacing(16)
        settings = button("Настройки", lambda: self.select_page(5), glyph="settings")
        settings.setObjectName("nav")
        settings.setCheckable(True)
        self.nav.append(settings)
        layout.addWidget(settings)
        layout.addSpacing(12)
        layout.addWidget(label(f"  SCleaner {__version__}  ·  Windows", "small"))
        layout.addWidget(label(f"  by {__author__}", "small"))
        return sidebar

    def make_topbar(self):
        top = QFrame()
        top.setObjectName("topbar")
        top.setFixedHeight(73)
        layout = QHBoxLayout(top)
        layout.setContentsMargins(32, 0, 32, 0)
        self.breadcrumb = label("Рабочее пространство  /  Обзор", "muted")
        layout.addWidget(self.breadcrumb)
        layout.addStretch()
        self.accent_button = button("Цвет акцента", self.open_accent_dialog, glyph="spark")
        layout.addWidget(self.accent_button)
        layout.addSpacing(10)
        self.status_badge = label("●  Локально на вашем ПК", "badge")
        self.status_badge.setFixedHeight(28)
        layout.addWidget(self.status_badge)
        layout.addSpacing(15)
        layout.addWidget(label("Администратор" if is_admin() else "Обычный доступ", "small"))
        return top

    def scroll_page(self, widget):
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(widget)
        return area

    def page(self, title, subtitle):
        widget = QWidget()
        widget.setObjectName("page")
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(32, 28, 32, 25)
        layout.setSpacing(18)
        layout.addWidget(label(title, "title"))
        layout.addWidget(label(subtitle, "subtitle", True))
        return widget, layout

    def make_overview(self):
        page, layout = self.page("Порядок начинается здесь", "Освободите место для того, что действительно важно.")
        layout.addSpacing(2)
        hero = QFrame()
        hero.setObjectName("hero")
        hero.setMinimumHeight(258)
        hero_layout = QHBoxLayout(hero)
        hero_layout.setContentsMargins(30, 22, 27, 22)
        words = QVBoxLayout()
        words.setSpacing(10)
        words.addWidget(label("УХОД ЗА ВАШЕЙ СИСТЕМОЙ", "eyebrow"))
        headline = label("Меньше лишнего.\nБольше пространства.")
        headline.setStyleSheet("font-size: 31px; font-weight: 700; letter-spacing: -0.7px;")
        words.addWidget(headline)
        words.addWidget(label("Найдём накопившиеся кэши и временные файлы.\nВы сами выберете, с чем пора расстаться.", "subtitle", True))
        words.addSpacing(5)
        action_row = QHBoxLayout()
        self.hero_scan = button("Сканировать систему", self.start_scan, True, "search")
        self.hero_scan.setMinimumHeight(43)
        action_row.addWidget(self.hero_scan)
        action_row.addWidget(label("Ничего не удаляет", "small"))
        action_row.addStretch()
        words.addLayout(action_row)
        hero_layout.addLayout(words, 1)
        self.disk_ring = DiskRing()
        hero_layout.addWidget(self.disk_ring)
        layout.addWidget(hero)
        metrics = QHBoxLayout()
        metrics.setSpacing(15)
        self.metric_values = []
        for title, value, subtitle, glyph, color in [
            ("Найдено для очистки", "—", "Сначала запустите сканирование", "search", "#b4a5ff"),
            ("Освобождено всего", "0 Б", "За всё время работы SCleaner", "spark", "#7cdbc0"),
            ("Последняя очистка", "Ещё не было", "Начнём с первого сканирования", "clock", "#89b7ee"),
        ]:
            box = card()
            box_layout = QVBoxLayout(box)
            box_layout.setContentsMargins(20, 18, 20, 18)
            row = QHBoxLayout()
            row.addWidget(label(title, "muted"), 1)
            pic = label()
            if glyph == "search":
                pic.setProperty("accentGlyph", glyph)
                pic.setProperty("accentSize", 20)
            pic.setPixmap(icon(glyph, color, 20).pixmap(20, 20))
            row.addWidget(pic)
            box_layout.addLayout(row)
            metric = label(value, "metric")
            if title == "Последняя очистка":
                metric.setStyleSheet("font-size: 23px; font-weight: 600;")
            box_layout.addWidget(metric)
            description = label(subtitle, "small", True)
            box_layout.addWidget(description)
            self.metric_values.append((metric, description))
            metrics.addWidget(box, 1)
        layout.addLayout(metrics)
        layout.addSpacing(6)
        section = QHBoxLayout()
        section.addWidget(label("Что приведём в порядок", "section"))
        section.addStretch()
        details = button("Настроить очистку  →", lambda: self.select_page(1))
        details.setObjectName("ghost")
        section.addWidget(details)
        layout.addLayout(section)
        categories = QGridLayout()
        categories.setSpacing(14)
        self.overview_checks = {}
        for index, key in enumerate(DEFAULT_CATEGORIES):
            box, checkbox = self.category_card(key, self.settings["categories"])
            self.overview_checks[key] = checkbox
            checkbox.toggled.connect(lambda checked, k=key: self.sync_category(k, checked, "overview"))
            categories.addWidget(box, index // 2, index % 2)
        layout.addLayout(categories)
        layout.addSpacing(2)
        foot = QHBoxLayout()
        pic = label()
        pic.setPixmap(icon("shield", "#78cbb0", 17).pixmap(17, 17))
        foot.addWidget(pic)
        foot.addWidget(label("Пароли, закладки, документы и сохранённые сессии остаются на месте.", "small"))
        foot.addStretch()
        layout.addLayout(foot)
        layout.addStretch()
        return page

    def category_card(self, key, selected):
        title, description, glyph = CATEGORIES[key]
        box = card()
        box.setMinimumHeight(91)
        layout = QHBoxLayout(box)
        layout.setContentsMargins(18, 15, 18, 15)
        layout.setSpacing(14)
        pic = label()
        pic.setPixmap(icon(glyph, "#b6a7fa", 26).pixmap(26, 26))
        pic.setProperty("accentGlyph", glyph)
        pic.setProperty("accentSize", 26)
        pic.setFixedWidth(32)
        layout.addWidget(pic)
        texts = QVBoxLayout()
        texts.setSpacing(5)
        title_label = label(title)
        title_label.setStyleSheet("font-size: 14px; font-weight: 600;")
        texts.addWidget(title_label)
        texts.addWidget(label(description, "small", True))
        layout.addLayout(texts, 1)
        checkbox = QCheckBox()
        checkbox.setAccessibleName(title)
        checkbox.setToolTip("Включить в сканирование: " + title)
        checkbox.setChecked(key in selected)
        layout.addWidget(checkbox)
        return box, checkbox

    def sync_category(self, key, checked, source):
        checks = self.clean_checks if source == "overview" else self.overview_checks
        checks[key].blockSignals(True)
        checks[key].setChecked(checked)
        checks[key].blockSignals(False)
        self.settings["categories"] = [k for k, c in checks.items() if c.isChecked()]

    def make_cleaner(self):
        page, layout = self.page("Очистка системы", "Выберите категории, изучите найденное и удалите только то, что вам больше не нужно.")
        categories = QGridLayout()
        categories.setSpacing(12)
        self.clean_checks = {}
        for index, key in enumerate(DEFAULT_CATEGORIES):
            box, checkbox = self.category_card(key, self.settings["categories"])
            self.clean_checks[key] = checkbox
            checkbox.toggled.connect(lambda checked, k=key: self.sync_category(k, checked, "clean"))
            categories.addWidget(box, index // 2, index % 2)
        layout.addLayout(categories)
        actions = QHBoxLayout()
        self.scan_button = button("Сканировать", self.start_scan, True, "search")
        actions.addWidget(self.scan_button)
        self.scan_summary = label("Сканирование ещё не запускалось", "muted")
        actions.addWidget(self.scan_summary)
        actions.addStretch()
        actions.addWidget(button("Отчёт", lambda: self.export_result("clean"), glyph="download"))
        layout.addLayout(actions)
        self.clean_table = FindingsTable()
        self.clean_table.model.selection_changed.connect(lambda: self.update_selection("clean"))
        layout.addWidget(self.clean_table, 1)
        bottom = QHBoxLayout()
        self.clean_selection = label("Ничего не выбрано", "muted")
        bottom.addWidget(self.clean_selection, 1)
        self.clean_notes = button("Примечания", lambda: self.show_notes("clean"))
        bottom.addWidget(self.clean_notes)
        self.clean_button = button("Очистить выбранное", lambda: self.start_clean("clean"), True, "spark")
        self.clean_button.setEnabled(False)
        bottom.addWidget(self.clean_button)
        layout.addLayout(bottom)
        layout.addWidget(label("Закройте браузеры и приложения перед сканированием. Свежие и занятые файлы будут пропущены.", "small", True))
        return page

    def make_leftovers(self):
        page, layout = self.page("Остатки приложений", "Записи удаления, ключи приложений в HKLM\\SOFTWARE и пустые папки в AppData.")
        layout.addWidget(label("Требует вашего внимания: отсутствующий файл или пустая папка не всегда означают остаток удалённой программы. Все находки здесь сняты с выбора по умолчанию.", "warning", True))
        checks = QHBoxLayout()
        self.left_checks = {}
        for key in ("registry", "empty"):
            box, checkbox = self.category_card(key, ("registry", "empty"))
            self.left_checks[key] = checkbox
            checks.addWidget(box, 1)
        layout.addLayout(checks)
        actions = QHBoxLayout()
        self.left_scan = button("Проверить остатки", self.start_leftover_scan, True, "search")
        actions.addWidget(self.left_scan)
        self.registry_review = QCheckBox("Ручная проверка")
        self.registry_review.setChecked(True)
        self.registry_review.setToolTip("Также показать ключи без достаточных данных. Выбор каждой записи — вручную; перед удалением нужно подтвердить, что приложение удалено.")
        actions.addWidget(self.registry_review)
        self.left_summary = label("Проверка ещё не запускалась", "muted")
        self.left_summary.setWordWrap(True)
        actions.addWidget(self.left_summary, 1)
        actions.addWidget(button("Отчёт", lambda: self.export_result("leftovers"), glyph="download"))
        layout.addLayout(actions)
        self.left_table = FindingsTable()
        self.left_table.model.selection_changed.connect(lambda: self.update_selection("leftovers"))
        layout.addWidget(self.left_table, 1)
        bottom = QHBoxLayout()
        self.left_selection = label("Ничего не выбрано", "muted")
        bottom.addWidget(self.left_selection, 1)
        bottom.addWidget(button("Причины пропуска", lambda: self.show_notes("leftovers")))
        self.left_clean = button("Удалить выбранное", lambda: self.start_clean("leftovers"), glyph="trash")
        self.left_clean.setEnabled(False)
        bottom.addWidget(self.left_clean)
        layout.addLayout(bottom)
        layout.addWidget(label("«Ручная проверка» — записи без доказательства удаления приложения; выбирайте их по одной. Перед удалением создаётся копия.", "small", True))
        return page

    def make_large(self):
        page, layout = self.page("Куда уходит место", "Найдите самые объёмные файлы и решите, какие из них вам нужны.")
        panel = card()
        row = QHBoxLayout(panel)
        row.setContentsMargins(18, 18, 18, 18)
        self.large_folder = label("Папка не выбрана", "muted", True)
        row.addWidget(self.large_folder, 1)
        self.choose_large_button = button("Выбрать папку", self.choose_large, glyph="folder")
        row.addWidget(self.choose_large_button)
        layout.addWidget(panel)
        actions = QHBoxLayout()
        actions.addWidget(label("Минимальный размер", "muted"))
        self.large_size = QSpinBox()
        self.large_size.setRange(10, 100000)
        self.large_size.setSuffix(" МБ")
        self.large_size.setValue(self.settings["large_mb"])
        self.large_size.setFixedWidth(140)
        actions.addWidget(self.large_size)
        self.large_scan = button("Найти файлы", self.start_large_scan, True, "search")
        actions.addWidget(self.large_scan)
        self.large_scan.setEnabled(False)
        actions.addStretch()
        actions.addWidget(button("Отчёт", lambda: self.export_result("large"), glyph="download"))
        layout.addLayout(actions)
        self.large_summary = label("Укажите папку для анализа", "muted")
        layout.addWidget(self.large_summary)
        self.large_table = FindingsTable(readonly=True)
        layout.addWidget(self.large_table, 1)
        note = QHBoxLayout()
        note.addWidget(label("Правый клик → «Показать в Проводнике». Этот экран ничего не удаляет.", "note", True), 1)
        note.addWidget(button("Примечания", lambda: self.show_notes("large")))
        layout.addLayout(note)
        return page

    def make_history(self):
        page, layout = self.page("История и восстановление", "Каждая очистка оставляет понятный отчёт. Копии реестра сохраняются отдельно.")
        actions = QHBoxLayout()
        actions.addWidget(button("Обновить", self.refresh_history, glyph="clock"))
        actions.addWidget(button("Открыть папку отчётов", self.open_history_folder, glyph="folder"))
        actions.addStretch()
        self.restore_button = button("Восстановить запись реестра", self.restore_registry, glyph="layers")
        actions.addWidget(self.restore_button)
        layout.addLayout(actions)
        self.history_list = QListWidget()
        self.history_list.itemDoubleClicked.connect(self.history_details)
        layout.addWidget(self.history_list, 1)
        layout.addWidget(label("Двойной клик по операции — подробности и причины пропуска. Удалённые кэши не восстанавливаются; история хранит отчёт об их удалении.", "note", True))
        return page

    def make_settings(self):
        page, layout = self.page("Настройки", "Настройте очистку под себя. Настройки и история очистки хранятся на этом компьютере.")
        license_panel = card()
        license_row = QHBoxLayout(license_panel)
        license_row.setContentsMargins(23, 20, 23, 20)
        license_row.addWidget(label("Лицензия SCleaner", "section"), 1)
        self.license_status = label("Free", "badge")
        license_row.addWidget(self.license_status)
        license_row.addWidget(button("Free / Pro…", self.open_pro_dialog))
        layout.addWidget(license_panel)
        appearance = card()
        appearance_row = QHBoxLayout(appearance)
        appearance_row.setContentsMargins(23, 20, 23, 20)
        appearance_row.addWidget(label("Цвет акцента", "section"), 1)
        self.accent_value = label(self.settings["accent_color"].upper(), "muted")
        appearance_row.addWidget(self.accent_value)
        appearance_row.addWidget(button("Изменить цвет…", self.open_accent_dialog, glyph="spark"))
        layout.addWidget(appearance)
        panel = card()
        settings_layout = QVBoxLayout(panel)
        settings_layout.setContentsMargins(23, 23, 23, 23)
        settings_layout.setSpacing(15)
        settings_layout.addWidget(label("Возраст файлов", "section"))
        settings_layout.addWidget(label("Не предлагать файлы, изменённые за последние указанное число часов. Минимум — 1 час.", "muted", True))
        self.age = QSpinBox()
        self.age.setRange(1, 720)
        self.age.setSuffix(" ч")
        self.age.setValue(self.settings["age_hours"])
        self.age.setFixedWidth(155)
        settings_layout.addWidget(self.age)
        layout.addWidget(panel)
        panel = card()
        exceptions = QVBoxLayout(panel)
        exceptions.setContentsMargins(23, 23, 23, 23)
        exceptions.addWidget(label("Исключения", "section"))
        exceptions.addWidget(label("Эти папки и всё внутри них будут исключены из сканирования и очистки.", "muted", True))
        self.exclusions = QListWidget()
        self.exclusions.addItems(self.settings["exclusions"])
        self.exclusions.setMinimumHeight(110)
        self.exclusions.setMaximumHeight(180)
        exceptions.addWidget(self.exclusions)
        actions = QHBoxLayout()
        actions.addWidget(button("Добавить папку", self.add_exclusion, glyph="folder"))
        actions.addWidget(button("Удалить из списка", self.remove_exclusion))
        actions.addStretch()
        exceptions.addLayout(actions)
        layout.addWidget(panel)
        save_row = QHBoxLayout()
        save_row.addWidget(button("Сохранить настройки", self.save_settings, True, "check"))
        self.settings_notice = label("", "muted")
        save_row.addWidget(self.settings_notice, 1)
        layout.addLayout(save_row)
        panel = card()
        system = QVBoxLayout(panel)
        system.setContentsMargins(23, 23, 23, 23)
        system.addWidget(label("Инструменты Windows", "section"))
        system.addWidget(label("Корзина, компоненты Windows и установленные программы управляются штатными средствами.", "muted", True))
        actions = QHBoxLayout()
        actions.addWidget(button("Память устройства", lambda: QDesktopServices.openUrl(QUrl("ms-settings:storagesense")), glyph="disk"))
        actions.addWidget(button("Установленные приложения", lambda: QDesktopServices.openUrl(QUrl("ms-settings:appsfeatures")), glyph="grid"))
        actions.addStretch()
        system.addLayout(actions)
        layout.addWidget(panel)
        layout.addWidget(label(f"SCleaner {__version__} · by {__author__} · Python + Qt · Без телеметрии\nДанные приложения: {self.storage.root}", "small", True))
        layout.addStretch()
        return page

    def refresh_license(self):
        state = self.licenses.state()
        self.license_status.setText(state.title)
        self.pro_button.setText(state.title if state.is_pro else "Free · О версии Pro")

    def open_pro_dialog(self):
        if self.pro_dialog is None:
            self.pro_dialog = ProDialog(self.licenses, self)
            self.pro_dialog.license_changed.connect(self.refresh_license)
        self.pro_dialog.refresh()
        self.pro_dialog.show()
        self.pro_dialog.raise_()
        self.pro_dialog.activateWindow()

    def select_page(self, index):
        self.stack.setCurrentIndex(index)
        for i, nav in enumerate(self.nav):
            nav.setChecked(index == i)
        self.breadcrumb.setText("Рабочее пространство  /  " + self.nav[index].text())
        if index == 0:
            self.disk_ring.refresh()
        if index == 4:
            self.refresh_history()

    def launch(self, task, on_done, title):
        if self.busy:
            return
        self.busy = True
        self.active_operation = title
        self.operation_frame.show()
        self.operation_label.setText(title)
        self.operation_bar.setRange(0, 0)
        self.cancel_button.setEnabled(True)
        self.status_badge.setText("●  " + title)
        self.update_buttons()
        self.worker = Worker(task, self)
        self.worker.done.connect(on_done)
        self.worker.failed.connect(self.operation_failed)
        self.worker.progress.connect(self.progress_changed)
        self.worker.finished.connect(self.operation_finished)
        self.worker.start()

    def update_buttons(self):
        for b in (self.hero_scan, self.scan_button, self.left_scan, self.choose_large_button, self.restore_button):
            b.setEnabled(not self.busy)
        self.large_scan.setEnabled(not self.busy and bool(getattr(self, "large_root", None)))
        for kind, table, clean in (("clean", self.clean_table, self.clean_button), ("leftovers", self.left_table, self.left_clean)):
            table.setEnabled(not self.busy)
            clean.setEnabled(not self.busy and any(f.selected for f in self.results[kind].findings))

    def progress_changed(self, value, maximum, text):
        self.operation_label.setText(text[:100])
        self.operation_bar.setRange(0, maximum)
        if maximum:
            self.operation_bar.setValue(value)

    def cancel_operation(self):
        if self.worker:
            self.worker.cancel.set()
            self.cancel_button.setEnabled(False)
            self.operation_label.setText("Останавливаем операцию после текущего объекта…")

    def operation_failed(self, message):
        self.operation_label.setText("Операция не завершена")
        self.message("Не удалось завершить операцию", message, error=True)

    def operation_finished(self):
        self.busy = False
        self.status_badge.setText("●  Локально на вашем ПК")
        self.operation_frame.hide()
        self.update_buttons()
        self.refresh_history()
        self.disk_ring.refresh()
        if self.worker:
            self.worker.deleteLater()
            self.worker = None
        if self.closing:
            self.close()

    def start_scan(self):
        if self.busy:
            return
        categories = [key for key, checkbox in self.clean_checks.items() if checkbox.isChecked()]
        if not categories:
            self.message("Выберите категории", "Отметьте хотя бы одну категорию для сканирования.")
            return
        self.select_page(1)
        settings = dict(self.settings)
        self.launch(lambda cancel, progress: self.engine.scan(categories, settings, cancel, progress), lambda result: self.scan_done("clean", result), "Сканирование системы")

    def start_leftover_scan(self):
        categories = [key for key, checkbox in self.left_checks.items() if checkbox.isChecked()]
        if not categories:
            self.message("Выберите категории", "Отметьте реестр или пустые папки.")
            return
        settings = {**self.settings, "registry_review": self.registry_review.isChecked()}
        self.launch(lambda cancel, progress: self.engine.scan(categories, settings, cancel, progress), lambda result: self.scan_done("leftovers", result), "Проверка остатков")

    def choose_large(self):
        folder = QFileDialog.getExistingDirectory(self, "Выберите папку для анализа", str(Path.home()))
        if folder:
            self.large_root = folder
            self.large_folder.setText(folder)
            self.large_scan.setEnabled(not self.busy)

    def start_large_scan(self):
        if not getattr(self, "large_root", None):
            return
        settings = {**self.settings, "large_mb": self.large_size.value()}
        self.launch(lambda cancel, progress: self.engine.large_files(self.large_root, settings, cancel, progress), lambda result: self.scan_done("large", result), "Анализ файлов")

    def scan_done(self, kind, result):
        self.results[kind] = result
        table = {"clean": self.clean_table, "leftovers": self.left_table, "large": self.large_table}[kind]
        table.set_items(result.findings)
        count = len(result.findings)
        summary = f"{count:,} объектов · {size_text(result.total_size)}".replace(",", " ")
        review_count = sum(bool(f.registry.get("review_required")) for f in result.findings)
        if review_count:
            summary += f" · ручная проверка: {review_count}"
        if result.cancelled:
            summary = "Остановлено · " + summary
        if result.notes:
            summary += f" · примечаний: {len(result.notes)}"
        {"clean": self.scan_summary, "leftovers": self.left_summary, "large": self.large_summary}[kind].setText(summary)
        if kind == "clean":
            self.clean_scanned = True
            self.metric_values[0][0].setText(size_text(result.total_size))
            self.metric_values[0][1].setText(f"{count:,} объектов в последнем сканировании".replace(",", " "))
        self.update_buttons()

    def update_selection(self, kind):
        table = self.clean_table if kind == "clean" else self.left_table
        selected = [f for f in table.model.items if f.selected]
        target = self.clean_selection if kind == "clean" else self.left_selection
        target.setText(f"Выбрано: {len(selected):,} · {size_text(sum(f.size for f in selected))}".replace(",", " "))
        self.update_buttons_if_ready()

    def update_buttons_if_ready(self):
        if hasattr(self, "restore_button"):
            self.update_buttons()

    def start_clean(self, kind):
        if self.busy:
            return
        selected = [f for f in self.results[kind].findings if f.selected]
        if not selected:
            return
        count, total = len(selected), sum(f.size for f in selected)
        registry_count = sum(f.kind == "registry" for f in selected)
        folders = sum(f.kind == "directory" for f in selected)
        description = f"Выбрано объектов: {count:,}".replace(",", " ")
        if any(f.kind == "file" for f in selected):
            description += f"\nОбъём файлов: {size_text(total)}\n\nКэши и временные файлы будут удалены безвозвратно. Закройте использующие их приложения."
        if folders:
            description += f"\n\nПустых папок: {folders}. Убедитесь, что они вам не нужны."
        if registry_count:
            description += f"\n\nЗаписей реестра: {registry_count}. До удаления будет сохранена копия каждой записи. Восстановление — в разделе «История»."
        software = [f for f in selected if f.registry.get("kind") == "software"]
        if software:
            description += f"\n\nВетки HKLM\\SOFTWARE: {len(software)}. Будут удалены и их вложенные настройки — всего ключей: {sum(f.registry.get('key_count', 1) for f in software)}. Убедитесь, что эти приложения удалены."
        review = [f for f in selected if f.registry.get("review_required")]
        if review:
            description += "\n\nДля записей из ручной проверки SCleaner не смог установить, удалено ли приложение. Подтвердите это сами; не удаляйте настройки программ, которыми пользуетесь."
        if not self.confirm("Очистить выбранные объекты?", description, "Начать очистку", review):
            return
        settings = dict(self.settings)
        confirmed_registry_ids = frozenset(f.id for f in review)
        self.launch(lambda cancel, progress: self.engine.clean(selected, settings, cancel, progress, confirmed_registry_ids=confirmed_registry_ids), lambda result: self.clean_done(kind, result), "Очистка выбранного")

    def clean_done(self, kind, result):
        self.results[kind] = ScanResult()
        table = self.clean_table if kind == "clean" else self.left_table
        table.set_items([])
        table.empty.setText("Операция завершена. Повторите сканирование для актуального списка.")
        summary = self.scan_summary if kind == "clean" else self.left_summary
        summary.setText(f"Удалено: {result.removed} · пропущено: {result.skipped} · освобождено: {size_text(result.freed)}")
        if kind == "clean":
            self.metric_values[0][0].setText("—")
            self.metric_values[0][1].setText("Обновите сканирование после очистки")
        self.refresh_history()
        self.message("Очистка остановлена" if result.cancelled else "Очистка завершена",
            f"Освобождено: {size_text(result.freed)}\nУдалено объектов: {result.removed}\nПропущено: {result.skipped}\n\nПодробности сохранены в истории. Необработанные и пропущенные объекты можно проверить повторным сканированием.")

    def confirm(self, title, description, action, review_items=()):
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setMinimumWidth(530)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(28, 25, 28, 25)
        layout.setSpacing(20)
        layout.addWidget(label(title, "section", True))
        layout.addWidget(label(description, "muted", True))
        if review_items:
            listing = QListWidget()
            listing.setMaximumHeight(140)
            listing.setMinimumHeight(70)
            listing.addItems([f"{f.label} · {f.registry.get('view')} бит\n{f.path}" for f in review_items])
            layout.addWidget(listing)
            acknowledgement = QCheckBox("Я подтверждаю, что эти приложения удалены")
            acknowledgement.setObjectName("registryAcknowledgement")
            layout.addWidget(acknowledgement)
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = button("Отмена", dialog.reject)
        cancel.setDefault(True)
        buttons.addWidget(cancel)
        confirm = button(action, dialog.accept, True)
        confirm.setAutoDefault(False)
        if review_items:
            confirm.setEnabled(False)
            acknowledgement.toggled.connect(confirm.setEnabled)
        buttons.addWidget(confirm)
        layout.addLayout(buttons)
        return dialog.exec() == QDialog.DialogCode.Accepted

    def message(self, title, text, error=False):
        message = QMessageBox(self)
        message.setWindowTitle(title)
        message.setTextFormat(Qt.TextFormat.PlainText)
        message.setText(title)
        message.setInformativeText(text)
        message.setIcon(QMessageBox.Icon.Warning if error else QMessageBox.Icon.Information)
        message.setStandardButtons(QMessageBox.StandardButton.Ok)
        message.exec()

    def text_dialog(self, title, content):
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(840, 540)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.addWidget(label(title, "section"))
        text = QTextEdit()
        text.setReadOnly(True)
        text.setPlainText(content)
        text.setStyleSheet("background: #171b27; border: 1px solid #343b4e; border-radius: 8px; padding: 12px;")
        layout.addWidget(text)
        layout.addWidget(button("Закрыть", dialog.accept))
        dialog.exec()

    def show_notes(self, kind):
        notes = self.results[kind].notes
        self.text_dialog("Примечания к сканированию", "\n\n".join(notes) if notes else "Нет дополнительных примечаний. Работающие приложения, файлы моложе заданного возраста, исключения и ссылки не входят в список очистки.")

    def export_result(self, kind):
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить отчёт", f"SCleaner-{datetime.now():%Y-%m-%d}.json", "JSON (*.json)")
        if path:
            try:
                self.storage.write_json(Path(path), asdict(self.results[kind]))
                self.message("Отчёт сохранён", path)
            except OSError as error:
                self.message("Не удалось сохранить отчёт", str(error), True)

    def refresh_history(self):
        if not hasattr(self, "history_list"):
            return
        records = self.storage.history()
        self.history_list.clear()
        self.history_records = records
        for record in records:
            try:
                date = datetime.fromisoformat(record["date"]).astimezone().strftime("%d.%m.%Y  %H:%M")
            except (ValueError, KeyError):
                date = "Дата неизвестна"
            status = {"running": "Прервана / не завершена", "failed": "Ошибка", "cancelled": "Остановлена", "complete": "Завершена"}.get(record.get("status"), "Неизвестно")
            self.history_list.addItem(f"{date}    ·    {status}\nОсвобождено {size_text(record.get('freed', 0))}    ·    удалено {record.get('removed', 0)}    ·    пропущено {record.get('skipped', 0)}")
        if not records:
            self.history_list.addItem("История пока пуста. Здесь появятся результаты вашей первой очистки.")
        self.metric_values[1][0].setText(size_text(sum(r.get("freed", 0) for r in records)))
        if records:
            try:
                date = datetime.fromisoformat(records[0]["date"]).astimezone()
                self.metric_values[2][0].setText(date.strftime("%d.%m.%Y"))
                self.metric_values[2][1].setText("Последняя операция в " + date.strftime("%H:%M"))
            except (KeyError, ValueError):
                pass

    def history_details(self, item):
        row = self.history_list.row(item)
        if row < len(self.history_records):
            self.text_dialog("Подробности операции", json.dumps(self.history_records[row], ensure_ascii=False, indent=2))

    def open_history_folder(self):
        path = self.storage.root / "history"
        try:
            path.mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        except OSError as error:
            self.message("Папка недоступна", str(error), True)

    def restore_registry(self):
        backups = []
        for record in self.storage.history():
            for path in record.get("backups", []):
                try:
                    data = json.loads(Path(path).read_text(encoding="utf-8"))
                    backups.append((f"{data.get('label', 'Запись')} · {record.get('date', '')[:16]} · {data['hive']} · {data.get('view', '?')} бит", path))
                except (OSError, ValueError, KeyError):
                    continue
        if not backups:
            self.message("Нет резервных копий", "Копии появляются после удаления записей реестра через SCleaner.")
            return
        names = [f"{i + 1}. {name}" for i, (name, _) in enumerate(backups)]
        chosen, ok = QInputDialog.getItem(self, "Восстановление реестра", "Выберите запись:", names, 0, False)
        if not ok:
            return
        path = backups[names.index(chosen)][1]
        if not self.confirm("Восстановить запись реестра?", chosen + "\n\nСуществующая запись не будет перезаписана.", "Восстановить"):
            return
        self.launch(lambda cancel, progress: self.engine.registry.restore(path), lambda _: self.message("Запись восстановлена", "Значения реестра восстановлены из резервной копии."), "Восстановление реестра")

    def add_exclusion(self):
        path = QFileDialog.getExistingDirectory(self, "Исключить папку")
        if path and path not in [self.exclusions.item(i).text() for i in range(self.exclusions.count())]:
            self.exclusions.addItem(path)

    def remove_exclusion(self):
        row = self.exclusions.currentRow()
        if row >= 0:
            self.exclusions.takeItem(row)

    def apply_accent(self, color):
        self.setStyleSheet(stylesheet(color))
        self.setWindowIcon(icon("spark", color, 64))
        self.disk_ring.accent = "#a497ff" if color == DEFAULT_ACCENT else color
        self.disk_ring.update()
        self.accent_value.setText(color.upper())
        for widget in self.findChildren(QLabel):
            glyph = widget.property("accentGlyph")
            if glyph:
                size = widget.property("accentSize")
                widget.setPixmap(icon(glyph, "#b6a8ff" if color == DEFAULT_ACCENT else color, size).pixmap(size, size))
        for widget in self.findChildren(QPushButton):
            glyph = widget.property("glyph")
            if glyph:
                foreground = accent_foreground(color) if widget.objectName() == "primary" else ("#b8afd7" if color == DEFAULT_ACCENT else color)
                widget.setIcon(icon(glyph, foreground, 18))

    def open_accent_dialog(self):
        if self.accent_dialog is not None:
            self.accent_dialog.raise_()
            return
        dialog = make_accent_dialog(self.settings["accent_color"], self)
        self.accent_dialog = dialog
        dialog.color_changed.connect(self.apply_accent)

        def finished(result):
            if result == QDialog.DialogCode.Accepted:
                updated = {**self.settings, "accent_color": dialog.color}
                try:
                    # Saving appearance must not persist pending category edits.
                    stored = self.storage.read_settings()
                    self.storage.save_settings({**stored, "accent_color": dialog.color})
                    self.settings = updated
                except OSError as error:
                    self.message("Не удалось сохранить цвет", str(error), True)
            self.apply_accent(self.settings["accent_color"])
            self.accent_dialog = None
            dialog.deleteLater()

        dialog.finished.connect(finished)
        dialog.open()

    def save_settings(self):
        self.settings["age_hours"] = self.age.value()
        self.settings["exclusions"] = [self.exclusions.item(i).text() for i in range(self.exclusions.count())]
        self.settings["large_mb"] = self.large_size.value()
        try:
            self.storage.save_settings(self.settings)
            self.settings_notice.setText("Сохранено. Применится к следующей операции.")
        except OSError as error:
            self.message("Не удалось сохранить настройки", str(error), True)

    def closeEvent(self, event: QCloseEvent):
        if self.pro_dialog is not None and self.pro_dialog.is_busy:
            event.ignore()
            return
        if self.busy:
            self.closing = True
            self.cancel_operation()
            event.ignore()
        else:
            event.accept()


def main():
    parser = argparse.ArgumentParser(description="SCleaner for Windows")
    parser.add_argument("--screenshot", type=Path, help="Save a screenshot and exit without scanning or cleaning")
    parser.add_argument("--data-dir", type=Path, help="Override application data directory")
    parser.add_argument("--license-test-config", type=Path, help="Explicitly enable test licensing using a public configuration file")
    parser.add_argument("--elevated", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        elevation_options = {"screenshot": args.screenshot is not None, "elevated": args.elevated}
        if args.license_test_config:
            elevation_options["license_test_config"] = args.license_test_config
        if not ensure_admin(args.data_dir, **elevation_options):
            return 0
    except OSError as error:
        if getattr(error, "winerror", None) != 1223:  # UAC cancellation closes the launcher quietly.
            error_app = QApplication(sys.argv[:1])
            QMessageBox.critical(None, "SCleaner", f"Не удалось запустить SCleaner от имени администратора.\n\n{error}")
        return 1
    app = QApplication(sys.argv[:1])
    if os.environ.get("QT_QPA_PLATFORM") == "offscreen" and os.name == "nt":
        for font in ("segoeui.ttf", "segoeuib.ttf", "seguisb.ttf"):
            QFontDatabase.addApplicationFont(str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "Fonts" / font))
    app.setApplicationName("SCleaner")
    app.setOrganizationName("SCleaner")
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))
    storage = Storage(args.data_dir)
    lock = None
    if not args.screenshot:
        try:
            storage.root.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            QMessageBox.critical(None, "SCleaner", f"Не удалось открыть папку данных: {error}")
            return 1
        lock = QLockFile(str(storage.root / "app.lock"))
        lock.setStaleLockTime(0)
        if not lock.tryLock(100):
            QMessageBox.information(None, "SCleaner уже открыт", "Используйте уже запущенное окно приложения.")
            return 0
    try:
        license_config = LicenseConfig.test_file(args.license_test_config) if args.license_test_config else None
    except (OSError, LicenseError) as error:
        QMessageBox.critical(None, "Тестовый режим Pro", str(error))
        return 1
    window = MainWindow(storage=storage, license_config=license_config)
    window.show()
    if args.screenshot:
        def capture():
            args.screenshot.parent.mkdir(parents=True, exist_ok=True)
            if not window.grab().save(str(args.screenshot)):
                app.exit(1)
            else:
                app.quit()
        QTimer.singleShot(350, capture)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
