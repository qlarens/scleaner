"""Free/Pro preview and activation, without gating existing cleanup features."""
from datetime import datetime

from PySide6.QtCore import Qt, QThread, QTimer, Signal, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QFileDialog, QGridLayout, QHBoxLayout, QLineEdit, QVBoxLayout

from .activation import ActivationClient
from .licensing import LicenseError, read_limited
from .widgets import button, card, label


class LicenseTask(QThread):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, action, parent):
        super().__init__(parent)
        self.action = action

    def run(self):
        try:
            self.succeeded.emit(self.action())
        except (LicenseError, OSError) as error:
            self.failed.emit(str(error))
        except Exception:
            self.failed.emit("Не удалось завершить активацию. Попробуйте ещё раз.")


class ProDialog(QDialog):
    license_changed = Signal()

    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.client = ActivationClient(manager)
        self.task = None
        self.setWindowTitle("SCleaner · Free и Pro")
        self.setMinimumWidth(720)
        self.resize(760, 680)
        self.setModal(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 24)
        layout.setSpacing(16)
        heading = QHBoxLayout()
        heading.addWidget(label("SCleaner под ваши задачи", "title"), 1)
        self.badge = label("Free", "badge")
        heading.addWidget(self.badge)
        layout.addLayout(heading)
        layout.addWidget(label("Ручная очистка остаётся бесплатной. Pro готовится к выпуску.", "subtitle", True))

        comparison = card()
        grid = QGridLayout(comparison)
        grid.setContentsMargins(20, 18, 20, 18)
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(12)
        for col, title in enumerate(("Возможности", "Free", "Pro")):
            grid.addWidget(label(title, "section"), 0, col)
        for row, (name, free, pro) in enumerate([
            ("Очистка и поиск крупных файлов", "Доступно", "Доступно"),
            ("История и восстановление реестра", "Доступно", "Доступно"),
            ("Очистка по расписанию", "—", "В разработке"),
            ("Профили и собственные правила", "—", "В разработке"),
        ], 1):
            for col, text in enumerate((name, free, pro)):
                grid.addWidget(label(text, "muted" if col else "", True), row, col)
        grid.setColumnStretch(0, 1)
        layout.addWidget(comparison)
        testing = manager.config.environment == "test"
        layout.addWidget(label(
            "Тестовый режим · деньги не списываются. Проверяем покупку и активацию; новые функции Pro ещё не доступны."
            if testing else "Продажи ещё не открыты. Здесь появится активация Pro после запуска платной версии.",
            "note", True))

        self.status = label("", "muted", True)
        layout.addWidget(self.status)
        layout.addWidget(label("Активация лицензии", "section"))
        entry = QHBoxLayout()
        self.key_input = QLineEdit()
        self.key_input.setPlaceholderText("Вставьте тестовый ключ SCT-…" if testing else "Лицензионный ключ")
        self.key_input.setMaxLength(90)
        self.key_input.setClearButtonEnabled(True)
        self.activate_button = button("Активировать", self.activate, True)
        entry.addWidget(self.key_input, 1)
        entry.addWidget(self.activate_button)
        layout.addLayout(entry)
        self.key_input.returnPressed.connect(self.activate)
        actions = QHBoxLayout()
        self.buy_button = button("Тестовая покупка" if testing else "Скоро в продаже", self.buy)
        self.check_button = button("Проверить покупку", self.check_purchase)
        self.import_button = button("Импорт лицензии…", self.import_license)
        for item in (self.buy_button, self.check_button, self.import_button):
            actions.addWidget(item)
        layout.addLayout(actions)
        self.notice = label("", "muted", True)
        self.notice.setMinimumHeight(38)
        layout.addWidget(self.notice)
        self.device_label = label("", "small", True)
        if testing:
            try:
                self.device_label.setText("Код этой установки: " + manager.device_id())
            except (LicenseError, OSError) as error:
                self.notice.setText(str(error))
        self.device_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.device_label)
        layout.addStretch()
        footer = QHBoxLayout()
        footer.addWidget(label("Результаты очистки остаются на этом компьютере.", "small"), 1)
        self.close_button = button("Закрыть", self.reject)
        footer.addWidget(self.close_button)
        layout.addLayout(footer)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(30000)
        self.refresh()

    @property
    def is_busy(self):
        return self.task is not None

    def refresh(self):
        state = self.manager.state()
        self.badge.setText(state.title)
        if state.is_pro:
            until = datetime.fromtimestamp(state.expires_at).strftime("%d.%m.%Y %H:%M") if state.expires_at else "бессрочно"
            self.status.setText(f"Лицензия активна · {until}\nАктивация сохранена. Повторный запуск работает без подключения к серверу.")
        else:
            self.status.setText(state.message or "Сейчас активна бесплатная версия.")
        online = bool(self.manager.config.server_url)
        for item in (self.buy_button, self.check_button, self.activate_button):
            item.setEnabled(online and not self.is_busy)
        self.import_button.setEnabled(bool(self.manager.config.public_keys) and not self.is_busy)
        self.key_input.setEnabled(online and not self.is_busy)
        self.close_button.setEnabled(not self.is_busy)

    def run_task(self, action, on_success, message):
        if self.is_busy:
            return
        self.notice.setText(message)
        self.task = LicenseTask(action, self)
        self.task.succeeded.connect(on_success)
        self.task.failed.connect(self.notice.setText)
        self.task.finished.connect(self.task_finished)
        self.refresh()
        self.task.start()

    def task_finished(self):
        task = self.task
        self.task = None
        task.deleteLater()
        self.refresh()
        self.license_changed.emit()

    def activate(self):
        if not self.manager.config.server_url:
            return
        key = self.key_input.text()
        self.run_task(lambda: self.client.activate(key), lambda _: self.notice.setText("SCleaner Pro активирован в тестовом режиме."), "Проверяем лицензию…")

    def buy(self):
        self.run_task(self.client.checkout, self.open_checkout, "Создаём тестовую покупку…")

    def open_checkout(self, url):
        if QDesktopServices.openUrl(QUrl(url)):
            self.notice.setText("Завершите тестовую покупку в браузере, затем нажмите «Проверить покупку».")
        else:
            self.notice.setText("Не удалось открыть браузер. Повторите попытку покупки.")

    def check_purchase(self):
        def complete(key):
            self.key_input.setText(key)
            self.notice.setText("Покупка подтверждена. SCleaner Pro активирован в тестовом режиме.")
        self.run_task(self.client.check_purchase, complete, "Проверяем покупку и активируем Pro…")

    def import_license(self):
        path, _ = QFileDialog.getOpenFileName(self, "Подписанная лицензия SCleaner", "", "Лицензии (*.sclicense *.json)")
        if path:
            from pathlib import Path
            self.install_file(Path(path))

    def install_file(self, path):
        try:
            self.manager.install(read_limited(path))
            self.notice.setText("Подпись проверена. Тестовая лицензия активирована.")
            self.refresh()
            self.license_changed.emit()
        except (LicenseError, OSError) as error:
            self.notice.setText(str(error))

    def reject(self):
        if not self.is_busy:
            super().reject()

    def closeEvent(self, event):
        if self.is_busy:
            event.ignore()
        else:
            super().closeEvent(event)
