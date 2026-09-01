import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
OUTPUT_FILE = PROJECT_ROOT / "project_structure.txt"

EXCLUDED_DIRECTORIES = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
}

paths = {".", OUTPUT_FILE.name}

for current_directory, directories, files in os.walk(PROJECT_ROOT):
    directories[:] = sorted(
        directory
        for directory in directories
        if directory not in EXCLUDED_DIRECTORIES
    )

    current_path = Path(current_directory)

    for directory in directories:
        relative_path = (current_path / directory).relative_to(PROJECT_ROOT)
        paths.add(f"{relative_path.as_posix()}/")

    for filename in files:
        relative_path = (current_path / filename).relative_to(PROJECT_ROOT)
        paths.add(relative_path.as_posix())

OUTPUT_FILE.write_text(
    "\n".join(sorted(paths, key=str.casefold)) + "\n",
    encoding="utf-8",
)

print(f"Project structure written to: {OUTPUT_FILE}")