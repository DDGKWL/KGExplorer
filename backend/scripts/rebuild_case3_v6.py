"""Synchronize the validated v6 news bundle into KGExplorer.

This does not alter the legacy Case 3 output. It copies only reports listed in
v6_validation_reports.csv and the two v6 JSON products.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = Path(r"C:\Users\admin\data\大众新闻\gdnews_v5")
DEST = BACKEND / "data" / "news" / "v6"


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the KGExplorer Case 3 v6 bundle")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    args = parser.parse_args()
    selected = read_csv(args.source / "v6_validation_reports.csv")
    if len(selected) != 14:
        raise SystemExit(f"Expected 14 validation reports, found {len(selected)}")

    reports_dest = DEST / "reports"
    processed_dest = DEST / "processed"
    reports_dest.mkdir(parents=True, exist_ok=True)
    processed_dest.mkdir(parents=True, exist_ok=True)
    image_count = 0
    for row in selected:
        url_hash = row["url_hash"]
        source_dir = args.source / "reports" / "articles" / url_hash
        dest_dir = reports_dest / url_hash
        for filename in ("full.txt", "meta.json"):
            source_file = source_dir / filename
            if not source_file.is_file():
                raise SystemExit(f"Missing {source_file}")
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, dest_dir / filename)
        source_images = source_dir / "images"
        if source_images.is_dir():
            shutil.copytree(source_images, dest_dir / "images", dirs_exist_ok=True)
            image_count += sum(path.is_file() for path in (dest_dir / "images").iterdir())

    for filename in ("v6_news_graph.json", "v6_event_comparisons.json"):
        source_file = args.source / filename
        payload = json.loads(source_file.read_text(encoding="utf-8"))
        (processed_dest / filename).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(f"reports={len(selected)} images={image_count} destination={DEST}")


if __name__ == "__main__":
    main()
