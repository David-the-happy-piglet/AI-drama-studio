import argparse
import shutil
from pathlib import Path

from PIL import Image

EXPECTED_SIZE = (1280, 720)
CROP_BOX = (0, 0, 1168, 657)


def remove_watermark(path: Path) -> Path:
    backup = path.with_name(f"{path.stem}.watermarked{path.suffix}")
    if backup.exists():
        return backup

    with Image.open(path) as image:
        if image.size != EXPECTED_SIZE:
            raise ValueError(
                f"{path} has size {image.size}; expected {EXPECTED_SIZE}"
            )
        shutil.copy2(path, backup)
        cleaned = image.crop(CROP_BOX).resize(
            EXPECTED_SIZE,
            Image.Resampling.LANCZOS,
        )
        cleaned.save(path, format="JPEG", quality=94, subsampling=0)
    return backup


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Remove the fixed MiniMax AI watermark from legacy 1280x720 images."
    )
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()
    for path in args.paths:
        backup = remove_watermark(path)
        print(f"cleaned={path.resolve()} backup={backup.resolve()}")


if __name__ == "__main__":
    main()
