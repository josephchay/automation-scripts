from pathlib import Path
from PIL import Image

INPUT_DIR = Path("./raw_images")
OUTPUT_DIR = Path("./optimized_images")
MAX_WIDTH = 1920
QUALITY = 82

def optimize_images():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    valid_exts = {".jpg", ".jpeg", ".png"}

    for img_path in INPUT_DIR.iterdir():
        if img_path.suffix.lower() not in valid_exts:
            continue

        with Image.open(img_path) as img:
            # Convert RGBA to RGB if saving without transparency isn't needed, 
            # though WebP supports both RGB and RGBA natively.
            if img.width > MAX_WIDTH:
                ratio = MAX_WIDTH / float(img.width)
                new_height = int(float(img.height) * ratio)
                img = img.resize((MAX_WIDTH, new_height), Image.Resampling.LANCZOS)

            output_file = OUTPUT_DIR / f"{img_path.stem}.webp"
            img.save(output_file, "WEBP", quality=QUALITY, optimize=True)
            
            orig_kb = img_path.stat().st_size / 1024
            new_kb = output_file.stat().st_size / 1024
            print(f"{img_path.name}: {orig_kb:.1f} KB -> {new_kb:.1f} KB")

if __name__ == "__main__":
    optimize_images()
    