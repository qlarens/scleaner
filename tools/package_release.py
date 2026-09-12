"""Package the existing build and exact application sources, without deleting anything."""
from pathlib import Path
import hashlib
import shutil
import runpy
import tomllib
import zipfile

root = Path(__file__).resolve().parents[1]
version = runpy.run_path(str(root / "scleaner" / "__init__.py"))["__version__"]
if tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"] != version:
    raise SystemExit("Application and package versions must match")
release = root / "dist" / "SCleaner"
if not (release / "SCleaner.exe").is_file():
    raise SystemExit("Build the application first")
for name in ("README.md", "RELEASE_NOTES_1.2.0.md", "LICENSE", "THIRD_PARTY_NOTICES.md"):
    shutil.copy2(root / name, release / name)
shutil.copytree(root / "docs", release / "docs", dirs_exist_ok=True)
source_files = []
for directory in ("scleaner", "scleaner_server", "tests", "tools", "licenses", "installer", "docs"):
    source_files.extend(p for p in (root / directory).rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc")
source_files.extend(root / name for name in ("run.py", "start.bat", "build.ps1", "build-installer.ps1", "SCleaner.spec", "pyproject.toml", "requirements.txt", "requirements-build.txt", "README.md", "RELEASE_NOTES_1.2.0.md", "LICENSE", "THIRD_PARTY_NOTICES.md", ".gitignore"))
with zipfile.ZipFile(release / "SCleaner-source.zip", "w", zipfile.ZIP_DEFLATED) as archive:
    for file in sorted(source_files):
        archive.write(file, "SCleaner-source/" + file.relative_to(root).as_posix())
portable = root / "dist" / f"SCleaner-{version}-win64.zip"
with zipfile.ZipFile(portable, "w", zipfile.ZIP_DEFLATED) as archive:
    for file in sorted(release.rglob("*")):
        if file.is_file():
            archive.write(file, "SCleaner/" + file.relative_to(release).as_posix())
with portable.open("rb") as stream:
    digest = hashlib.file_digest(stream, "sha256").hexdigest()
portable.with_suffix(".sha256").write_text(f"{digest}  {portable.name}\n", encoding="ascii")
print(f"Portable: {portable}\nSize: {portable.stat().st_size / 1024 / 1024:.1f} MB\nSHA256: {digest}")
