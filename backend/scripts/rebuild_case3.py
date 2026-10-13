"""Build Case 3 (news) JSON from event groups and claim triples.

One event_group is one artifact. One article (url_hash) is one source
record, named by outlet. Frame slots (时间, 地点, 人物, 原因, 方式, 结果,
出自) use the same ids across events, so a repeated entity becomes a
shared graph node. Article files are read from
data/news/reports/{url_hash}/.

Run from backend/:
    python scripts/rebuild_case3.py
"""

from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NEWS = ROOT / "data" / "news"
TRIPLES = NEWS / "triples"
REPORTS = NEWS / "reports"
OUT_PATH = NEWS / "processed" / "case3_news.json"

GROUP_PATH = TRIPLES / "event_groups.csv"
ENTITY_PATH = TRIPLES / "entities.csv"
TRIPLE_PATH = TRIPLES / "event_triples_new.csv"

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
FRAME_RELATIONS = {
    "时间": "time",
    "地点": "place",
    "人物": "person",
    "原因": "cause",
    "方式": "manner",
    "结果": "result",
    "出自": "attribution",
}
FRAME_ORDER = {name: index for index, name in enumerate(FRAME_RELATIONS)}
STATUS_ORDER = {"differ": 0, "overlap": 1, "consensus": 2, "single_source": 3}


def clean(value) -> str:
    return str(value or "").strip()


def norm_token(value: str) -> str:
    text = clean(value)
    text = (
        text.replace("\u2019", "'")
        .replace("\u2018", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
    )
    text = text.casefold()
    return re.sub(r"\s+", " ", text).strip()


def slug(value: str) -> str:
    token = re.sub(r"[^a-z0-9]+", "-", norm_token(value)).strip("-")
    return token[:80] or "item"


def article_dir(url_hash: str) -> Path | None:
    flat = REPORTS / url_hash
    if (flat / "full.txt").is_file() or (flat / "images").is_dir():
        return flat
    if not REPORTS.is_dir():
        return None
    for child in REPORTS.iterdir():
        nested = child / url_hash
        if nested.is_dir():
            return nested
    return None


def load_meta(url_hash: str) -> dict:
    folder = article_dir(url_hash)
    path = folder / "meta.json" if folder else None
    if not path or not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def list_images(url_hash: str) -> tuple[list[str], str | None, list[str]]:
    folder = article_dir(url_hash)
    image_dir = folder / "images" if folder else None
    if not image_dir or not image_dir.is_dir():
        return [], None, []
    files = sorted(
        path.name
        for path in image_dir.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    )
    paths = [f"/api/news/image/{url_hash}/{name}" for name in files]
    preferred = next((path for name, path in zip(files, paths) if name.lower().startswith("img_00")), None)
    if paths and not preferred:
        preferred = paths[0]
    return files, preferred, paths


def frame_slot(relation: str) -> tuple[str, str]:
    return f"frame-{FRAME_RELATIONS[relation]}", relation


def action_slot(head: str, relation: str) -> tuple[str, str]:
    return f"rel-{slug(head)}-{slug(relation)}", f"{head} · {relation}"


def add_value(bucket: dict[str, list[str]], slot_id: str, raw: str) -> None:
    if raw and raw not in bucket[slot_id]:
        bucket[slot_id].append(raw)


def load_articles() -> tuple[dict[str, list[dict]], dict[str, str]]:
    headlines = {}
    if ENTITY_PATH.is_file():
        with ENTITY_PATH.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                if clean(row.get("is Artifact")) not in {"1", "1.0", "true", "yes"}:
                    continue
                url_hash = clean(row.get("url_hash"))
                if url_hash:
                    headlines[url_hash] = clean(row.get("实体"))

    triples: dict[str, list[dict]] = defaultdict(list)
    with TRIPLE_PATH.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            url_hash = clean(row.get("url_hash"))
            relation = clean(row.get("relation"))
            tail = clean(row.get("tail"))
            head = clean(row.get("head"))
            if not url_hash or not relation or not tail:
                continue
            triples[url_hash].append({
                "subject": head,
                "predicate": relation,
                "object": tail,
                "claim_id": clean(row.get("claim_id")),
                "layer": clean(row.get("layer")) or "core",
                "sentence": "",
            })

    grouped: dict[str, list[dict]] = defaultdict(list)
    titles: dict[str, str] = {}
    with GROUP_PATH.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            event_id = clean(row.get("event_group"))
            url_hash = clean(row.get("url_hash"))
            if not event_id or not url_hash:
                continue
            meta = load_meta(url_hash)
            domain = clean(row.get("domain")) or clean(meta.get("domain")) or url_hash
            source_id = slug(domain)
            outlet = clean(meta.get("site_name")) or domain
            country = clean(row.get("book")) or clean(meta.get("source_country"))
            titles[source_id] = outlet
            title = clean(row.get("title")) or headlines.get(url_hash) or url_hash
            folder = article_dir(url_hash)
            text_file = folder / "full.txt" if folder else None
            files, preferred, paths = list_images(url_hash)

            slot_values: dict[str, list[str]] = defaultdict(list)
            slot_labels: dict[str, str] = {}
            for item in triples.get(url_hash, []):
                if item["predicate"] in FRAME_ORDER:
                    slot_id, label = frame_slot(item["predicate"])
                else:
                    slot_id, label = action_slot(item["subject"], item["predicate"])
                slot_labels[slot_id] = label
                add_value(slot_values, slot_id, item["object"])

            grouped[event_id].append({
                "record_id": url_hash,
                "source_id": source_id,
                "event_id": event_id,
                "country": country,
                "display_name": title,
                "headline": title,
                "outlet": outlet,
                "slots": dict(slot_values),
                "slot_labels": slot_labels,
                "normalized_slots": {
                    slot_id: [
                        {"raw": raw, "canonical": norm_token(raw), "value_type": "text"}
                        for raw in values
                    ]
                    for slot_id, values in slot_values.items()
                },
                "relations": triples.get(url_hash, []),
                "evidence": {
                    "text_url": f"/api/news/text/{url_hash}" if text_file and text_file.is_file() else None,
                    "text_chars": text_file.stat().st_size if text_file and text_file.is_file() else 0,
                    "image_files": files,
                    "image_paths": paths,
                    "preferred_image": preferred,
                },
            })
    return grouped, titles


def classify_slot(slot_id: str, label: str, records: list[dict]) -> dict:
    assertions = []
    for record in records:
        values = record["slots"].get(slot_id, [])
        details = record["normalized_slots"].get(slot_id, [])
        evidence = record["evidence"]
        preferred = evidence["preferred_image"]
        assertions.append({
            "source": record["source_id"],
            "record_id": record["record_id"],
            "record_name": record["display_name"],
            "values": values,
            "normalized_values": [item["canonical"] for item in details],
            "value_details": details,
            "image_path": preferred,
            "metadata": {
                "text_url": evidence["text_url"],
                "image_files": evidence["image_files"],
                "image_paths": evidence["image_paths"],
                "image_match": "record-level-fallback" if preferred else "missing",
                "outlet": record["outlet"],
                "country": record["country"],
            },
        })

    present = [item for item in assertions if item["values"]]
    canon_sets = [set(item["normalized_values"]) for item in present]
    if len(present) <= 1:
        status, conflict_type = "single_source", "single_source"
    elif len({tuple(sorted(items)) for items in canon_sets}) == 1:
        raw_sets = {tuple(sorted(item["values"])) for item in present}
        status, conflict_type = ("consensus", "consensus") if len(raw_sets) == 1 else ("overlap", "lexical_variant")
    elif set.intersection(*canon_sets):
        status, conflict_type = "overlap", "partial_overlap"
    else:
        status, conflict_type = "differ", "substantive_conflict"

    return {
        "slot": slot_id,
        "label": label,
        "status": status,
        "conflict_type": conflict_type,
        "source_count": len(present),
        "assertions": assertions,
    }


def slot_sort_key(slot: dict) -> tuple:
    frame_rank = FRAME_ORDER.get(slot["label"], 99)
    return (STATUS_ORDER.get(slot["status"], 9), 0 if slot["slot"].startswith("frame-") else 1, frame_rank, slot["label"])


def build_artifact(event_id: str, records: list[dict]) -> dict:
    records = sorted(records, key=lambda item: (item["source_id"], item["record_id"]))
    books = sorted({record["source_id"] for record in records})
    headlines = [record["headline"] for record in records if record["headline"]]
    name = min(headlines, key=len) if headlines else event_id
    labels: dict[str, str] = {}
    for record in records:
        for slot_id, label in record["slot_labels"].items():
            labels.setdefault(slot_id, label)
    slots = [classify_slot(slot_id, label, records) for slot_id, label in labels.items()]
    slots.sort(key=slot_sort_key)
    images: dict[str, list[str]] = {}
    for record in records:
        for filename in record["evidence"]["image_files"]:
            bucket = images.setdefault(record["source_id"], [])
            if filename not in bucket:
                bucket.append(filename)
    return {
        "artifact_id": f"news-{event_id}",
        "name": name,
        "canonical_name": event_id,
        "raw_names": sorted(set(headlines)) or [event_id],
        "books": books,
        "records": [record["record_id"] for record in records],
        "aligned": len(books) > 1,
        "alignment_status": "confirmed" if len(books) > 1 else "source-local",
        "featured": True,
        "all_slots": slots,
        "caus": slots,
        "images": images,
        "relations": {record["record_id"]: record["relations"] for record in records},
    }


def main() -> None:
    grouped, titles = load_articles()
    artifacts = [build_artifact(event_id, records) for event_id, records in sorted(grouped.items())]
    conflicts = []
    for artifact in artifacts:
        for slot in artifact["caus"]:
            if slot["status"] in {"consensus", "single_source"}:
                continue
            conflicts.append({
                "conflict_id": f"conflict:{artifact['artifact_id']}:{slot['slot']}",
                "artifact_id": artifact["artifact_id"],
                "slot": slot["slot"],
                "label": slot["label"],
                "status": slot["status"],
                "conflict_type": slot["conflict_type"],
                "source_count": slot["source_count"],
            })

    slot_status = Counter(slot["status"] for artifact in artifacts for slot in artifact["caus"])
    output = {
        "schema_version": "case3-news-2",
        "case": {
            "id": "news",
            "number": 3,
            "label": "Case 3 · News",
            "title": "Cross-outlet News Claim Conflicts",
            "note": "Event groups compared on shared entity and frame slots, with article text and images.",
        },
        "books": titles,
        "source_catalog": {
            source_id: {"id": source_id, "title": title, "role": "report_primary"}
            for source_id, title in titles.items()
        },
        "artifacts": artifacts,
        "conflicts": conflicts,
        "stats": {
            "records": sum(len(artifact["records"]) for artifact in artifacts),
            "artifacts": len(artifacts),
            "aligned_artifacts": sum(artifact["aligned"] for artifact in artifacts),
            "featured_artifacts": sum(artifact["featured"] for artifact in artifacts),
            "conflicts": len(conflicts),
            "differ": slot_status["differ"],
            "overlap": slot_status["overlap"],
            "consensus": slot_status["consensus"],
            "single_source": slot_status["single_source"],
            "conflict_types": dict(Counter(item["conflict_type"] for item in conflicts)),
            "primary_sources": len(titles),
            "artifacts_with_images": sum(bool(artifact["images"]) for artifact in artifacts),
            "source_image_cards": sum(len(artifact["images"]) for artifact in artifacts),
        },
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(OUT_PATH), "stats": output["stats"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
