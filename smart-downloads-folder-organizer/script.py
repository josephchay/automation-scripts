import os
import shutil
from pathlib import Path

# Change this path if you want to organize a different directory
TARGET_DIR = Path.home() / "Downloads"

CATEGORIES = {
    "Images": {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".heic"},
    "Documents": {".pdf", ".docx", ".doc", ".txt", ".xlsx", ".csv", ".pptx", ".md"},
    "Archives": {".zip", ".tar", ".gz", ".rar", ".7z"},
    "Audio_Video": {".mp3", ".wav", ".mp4", ".mkv", ".mov", ".flac"},
    "Installers": {".exe", ".msi", ".dmg", ".pkg", ".deb", ".iso"},
    "Code": {".py", ".js", ".ts", ".html", ".css", ".json", ".sql", ".sh"},
}

def get_unique_path(destination: Path) -> Path:
    """Appends a counter if a file with the same name already exists."""
    if not destination.exists():
        return destination
    stem, suffix, parent = destination.stem, destination.suffix, destination.parent
    counter = 1
    while True:
        new_path = parent / f"{stem}_{counter}{suffix}"
        if not new_path.exists():
            return new_path
        counter += 1

def organize_folder(directory: Path):
    if not directory.exists():
        print(f"Directory not found: {directory}")
        return

    moved_count = 0
    for item in directory.iterdir():
        if item.is_dir() or item.name.startswith("."):
            continue

        ext = item.suffix.lower()
        target_category = "Misc"
        for category, extensions in CATEGORIES.items():
            if ext in extensions:
                target_category = category
                break

        dest_folder = directory / target_category
        dest_folder.mkdir(exist_ok=True)

        dest_path = get_unique_path(dest_folder / item.name)
        shutil.move(str(item), str(dest_path))
        print(f"Moved: {item.name} -> {target_category}/{dest_path.name}")
        moved_count += 1

    print(f"Done. Organized {moved_count} files.")

if __name__ == "__main__":
    organize_folder(TARGET_DIR)
    