# SCleaner

[![Release](https://img.shields.io/github/v/release/qlarens/scleaner?style=for-the-badge&label=release)](https://github.com/qlarens/scleaner/releases/latest)
[![Downloads](https://img.shields.io/github/downloads/qlarens/scleaner/total?style=for-the-badge)](https://github.com/qlarens/scleaner/releases)
[![License](https://img.shields.io/github/license/qlarens/scleaner?style=for-the-badge)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](pyproject.toml)
[![Windows](https://img.shields.io/badge/Windows-10%2F11_x64-0078D4?style=for-the-badge)](https://github.com/qlarens/scleaner/releases)

**SCleaner 1.2.0 · by qlarens** — очистка Windows с русским интерфейсом на Python + PySide6. Работает локально, без телеметрии. [Сообщить об ошибке](https://github.com/qlarens/scleaner/issues).

## Установка и использование

Скачайте сборку со страницы [Releases](https://github.com/qlarens/scleaner/releases):

- **Installer:** `SCleaner-1.2.0-win64-Setup.exe` — установка для текущего пользователя, выбор папки, ярлыки и удаление через настройки Windows.
- **Portable:** `SCleaner-1.2.0-win64.zip` — распакуйте весь архив и запустите `SCleaner/SCleaner.exe`. Папка `_internal` должна оставаться рядом.

Python отдельно не нужен. При запуске SCleaner автоматически запрашивает права администратора через UAC; подтвердите запрос Windows. При отмене приложение не запускается. Закройте очищаемые приложения, выполните сканирование, проверьте список и подтвердите очистку. Файлы удаляются безвозвратно; резервные копии создаются для реестра.

Доступны кэши браузеров и приложений, `Windows\Temp`, пользовательская `Temp`, фактическая `%TEMP%`, старые `Prefetch\*.pf`, дампы и кэш шейдеров. По умолчанию учитываются файлы старше 24 часов; Prefetch снят с выбора. Также есть поиск крупных файлов, пустых папок и остатков в реестре, история с восстановлением реестра и настройка цвета акцента. Пароли, закладки, сессии и документы не входят в стандартные правила очистки.

Настройки, история и копии: `%LOCALAPPDATA%\SCleaner`. Установщик сохраняет их при обновлении и удалении приложения. Неопределённые записи реестра требуют отдельного выбора и подтверждения, что программа удалена.

## Разработка

### Предварительная версия Free / Pro

В интерфейсе доступно окно Free/Pro. Подписанные тестовые лицензии, локальная
имитация покупки и адаптер Stripe test mode описаны в [docs/PRO_LICENSING.md](docs/PRO_LICENSING.md).
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

## Сборка релиза

Для `Setup.exe` нужен [Inno Setup 6.3+](https://jrsoftware.org/isdl.php). `ISCC.exe` ищется в `PATH`, стандартных каталогах Inno Setup 6 и `build/tools/inno`; другой путь можно передать явно.

```powershell
.\build.ps1                                      # тесты, приложение, ZIP и Setup.exe
.\build.ps1 -PortableOnly                        # без Inno Setup
.\build-installer.ps1 -IsccPath 'C:\Tools\Inno\ISCC.exe'  # из готовой portable-сборки
```

Результаты в `dist/`: `SCleaner/SCleaner.exe`, `SCleaner-1.2.0-win64.zip`, `SCleaner-1.2.0-win64-Setup.exe` и отдельные `.sha256`. В обе поставки входят лицензии, документация и `SCleaner-source.zip`. Версия берётся из `scleaner/__init__.py` и сверяется с `pyproject.toml`; цифровая подпись не настроена.

## Структура и правила изменений

| Файлы | Назначение |
| --- | --- |
| `catalog.py`, `models.py` | Явные цели сканирования и модели результатов |
| `engine.py`, `safety.py` | Сканирование, очистка, проверки путей и изменений файлов |
| `registry.py`, `software_registry.py` | Uninstall, деревья HKLM\SOFTWARE, копии и восстановление |
| `storage.py` | Настройки, история JSON и журнал JSONL |
| `app.py`, `widgets.py`, `appearance.py`, `theme.py` | Интерфейс и оформление |
| `installer/`, `tools/`, `build*.ps1` | Установщик, проверки и упаковка |

Новые кэши добавляйте через `Target` в `catalog.py` с точными путями и именами процессов. Сохраняйте проверку возраста, исключений, ссылок, идентификатора файла и разрешённых корней перед удалением. Для правил удаления добавляйте тесты на сохранение соседних и изменённых данных.

Подробности архитектуры, ограничений реестра и проверки установщика: [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md). Лицензия приложения — [CC BY-NC-ND 4.0](LICENSE), зависимости — [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
