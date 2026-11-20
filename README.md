# SCleaner

[![Release](https://img.shields.io/github/v/release/qlarens/scleaner?style=for-the-badge&label=release)](https://github.com/qlarens/scleaner/releases/latest)
[![Downloads](https://img.shields.io/github/downloads/qlarens/scleaner/total?style=for-the-badge)](https://github.com/qlarens/scleaner/releases)
[![License](https://img.shields.io/badge/license-CC%20BY--NC--ND%204.0-lightgrey?style=for-the-badge)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](pyproject.toml)
[![Windows](https://img.shields.io/badge/Windows-10%2F11_x64-0078D4?style=for-the-badge)](https://github.com/qlarens/scleaner/releases)

Очистка Windows с русским интерфейсом. Работает локально, без телеметрии.

## Использование

Скачайте `SCleaner.exe` из [Releases](https://github.com/qlarens/scleaner/releases) и запустите — установка не нужна, потребуется подтвердить UAC.

Чистит кэши браузеров и приложений, Temp, Prefetch, дампы и кэш шейдеров; ищет крупные файлы, пустые папки и остатки в реестре. Файлы удаляются безвозвратно, для реестра создаются резервные копии. Данные приложения: `%LOCALAPPDATA%\SCleaner`.

## Разработка

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe run.py                          # запуск
.\.venv\Scripts\python.exe -m unittest discover -s tests   # тесты
.\build.ps1                                                # build\SCleaner.exe
```

## Лицензия

[CC BY-NC-ND 4.0](LICENSE), зависимости — [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). [Сообщить об ошибке](https://github.com/qlarens/scleaner/issues).
