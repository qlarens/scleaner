# SCleaner

[![Release](https://img.shields.io/github/v/release/qlarens/scleaner?style=for-the-badge&label=release)](https://github.com/qlarens/scleaner/releases/latest)
[![Downloads](https://img.shields.io/github/downloads/qlarens/scleaner/total?style=for-the-badge)](https://github.com/qlarens/scleaner/releases)
[![License](https://img.shields.io/badge/license-CC%20BY--NC--ND%204.0-lightgrey?style=for-the-badge)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](pyproject.toml)
[![Windows](https://img.shields.io/badge/Windows-10%2F11_x64-0078D4?style=for-the-badge)](https://github.com/qlarens/scleaner/releases)

**SCleaner 1.2.0 · by qlarens** — очистка Windows с русским интерфейсом на Python + PySide6. Работает локально, без телеметрии. [Сообщить об ошибке](https://github.com/qlarens/scleaner/issues).

## Установка и использование

Скачайте `SCleaner.exe` со страницы [Releases](https://github.com/qlarens/scleaner/releases) и запустите. Установка не нужна, Python отдельно не нужен. При запуске SCleaner автоматически запрашивает права администратора через UAC; подтвердите запрос Windows. При отмене приложение не запускается. Закройте очищаемые приложения, выполните сканирование, проверьте список и подтвердите очистку. Файлы удаляются безвозвратно; резервные копии создаются для реестра.

Доступны кэши браузеров и приложений, `Windows\Temp`, пользовательская `Temp`, фактическая `%TEMP%`, старые `Prefetch\*.pf`, дампы и кэш шейдеров. По умолчанию учитываются файлы старше 24 часов; Prefetch снят с выбора. Также есть поиск крупных файлов, пустых папок и остатков в реестре, история с восстановлением реестра и настройка цвета акцента. Пароли, закладки, сессии и документы не входят в стандартные правила очистки.

Настройки, история и копии: `%LOCALAPPDATA%\SCleaner`. Неопределённые записи реестра требуют отдельного выбора и подтверждения, что программа удалена.

## Разработка

### Предварительная версия Free / Pro

В интерфейсе доступно окно Free/Pro. Подписанные тестовые лицензии, локальная
имитация покупки и адаптер Stripe test mode: `scleaner_server/` и `tools/license_sandbox.py`.
Текущие функции остаются бесплатными; расписание и другие новые функции Pro ещё в разработке.
Продажи не открыты. Тестовая активация включается отдельно и не влияет на обычный запуск.

Windows, Python 3.11+ (для релизной сборки — 3.12 x64):

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py
```

Тесты работают с временными файлами в `artifacts/test-fixtures` и имитацией реестра:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Сборка

```powershell
.\build.ps1    # тесты и build\SCleaner.exe
```

Результат — один файл `build/SCleaner.exe` с Python, Qt и лицензиями внутри. Версия берётся из `scleaner/__init__.py`; цифровая подпись не настроена.

## Структура и правила изменений

| Файлы | Назначение |
| --- | --- |
| `catalog.py`, `models.py` | Явные цели сканирования и модели результатов |
| `engine.py`, `safety.py` | Сканирование, очистка, проверки путей и изменений файлов |
| `registry.py`, `software_registry.py` | Uninstall, деревья HKLM\SOFTWARE, копии и восстановление |
| `storage.py` | Настройки, история JSON и журнал JSONL |
| `app.py`, `widgets.py`, `appearance.py`, `theme.py` | Интерфейс и оформление |
| `SCleaner.spec`, `build.ps1`, `tools/` | Сборка exe и проверки |

Новые кэши добавляйте через `Target` в `catalog.py` с точными путями и именами процессов. Сохраняйте проверку возраста, исключений, ссылок, идентификатора файла и разрешённых корней перед удалением. Для правил удаления добавляйте тесты на сохранение соседних и изменённых данных.

Лицензия приложения — [CC BY-NC-ND 4.0](LICENSE), зависимости — [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
