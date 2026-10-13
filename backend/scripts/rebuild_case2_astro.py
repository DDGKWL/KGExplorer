"""Build Case 2 (astronomy) JSON from v2 classified tables.

Does not modify Guqin data or rebuild_case1.py.
NASA / SIMBAD / Gaia rows that share belongs_to become one artifact.
Slot values come from relation tails (尾实体), matching rebuild_case1.py.
Images are read only from data/astro/raw/images/; CSV images_files paths
are ignored.

Run from backend/:
    python scripts/rebuild_case2_astro.py
"""

from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ASTRO = ROOT / "data" / "astro"
CLASSIFIED = ASTRO / "raw" / "classified"
IMAGE_ROOT = ASTRO / "raw" / "images"
OUT_PATH = ASTRO / "processed" / "case2_astro.json"

INDEX_PATH = CLASSIFIED / "astro_kg_v2_index.csv"
ENTITY_PATH = CLASSIFIED / "astro_kg_v2_entities_full.csv"
RELATION_PATH = CLASSIFIED / "astro_kg_v2_relations.csv"

FEATURED_HOSTS = {"MOA-bin-29", "SWEEPS-11", "RR Cae"}

PRIMARY_SOURCES = {
    "nasa": {"title": "NASA", "role": "catalog_primary"},
    "simbad": {"title": "SIMBAD", "role": "catalog_primary"},
    "gaia": {"title": "Gaia", "role": "catalog_primary"},
}

RELATION_TO_SLOT = {
    "光谱型": "spectral_type",
    "坐标": "coordinate",
    "距离": "distance",
    "源标识": "source_id",
    "Gaia源": "source_id",
    "视差": "parallax",
    "视差误差": "parallax_error",
    "主标识": "alias",
    "天体类型": "object_type",
    "TIC": "tic",
    "G星等": "g_mag",
    "BP星等": "bp_mag",
    "RP星等": "rp_mag",
    "自行RA": "pm_ra",
    "自行Dec": "pm_dec",
    "径向速度": "radial_velocity",
    "RUWE": "ruwe",
    "有效温度": "teff",
    "恒星质量": "stellar_mass",
    "恒星半径": "stellar_radius",
    "金属丰度": "metallicity",
}

SLOT_LABELS = {
    "spectral_type": "光谱型",
    "coordinate": "坐标",
    "distance": "距离",
    "source_id": "源标识",
    "parallax": "视差",
    "parallax_error": "视差误差",
    "alias": "主标识",
    "object_type": "天体类型",
    "tic": "TIC",
    "g_mag": "G星等",
    "bp_mag": "BP星等",
    "rp_mag": "RP星等",
    "pm_ra": "自行RA",
    "pm_dec": "自行Dec",
    "radial_velocity": "径向速度",
    "ruwe": "RUWE",
    "teff": "有效温度",
    "stellar_mass": "恒星质量",
    "stellar_radius": "恒星半径",
    "metallicity": "金属丰度",
}

# Curated conflict projection, same idea as Case 1 ANALYSIS_SLOTS.
ANALYSIS_SLOTS = {
    "spectral_type",
    "distance",
    "coordinate",
    "source_id",
    "parallax",
}

ANALYSIS_ORDER = {slot: index for index, slot in enumerate(
    ("spectral_type", "distance", "coordinate", "source_id", "parallax")
)}

SOURCE_IMAGE_PREFIXES = tuple(PRIMARY_SOURCES)
DISTANCE_NUM = re.compile(r"(-?\d+(?:\.\d+)?)\s*(pc|parsec)?", re.I)
COORD_PAIR = re.compile(r"(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)")
NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def is_artifact(value) -> bool:
    return str(value).strip().lower() in {"1", "1.0", "true", "yes"}


def clean(value) -> str:
    return str(value or "").strip()


def canonical_value(slot: str, raw: str) -> dict:
    original = clean(raw)
    if slot == "distance":
        match = DISTANCE_NUM.search(original)
        if match:
            number = float(match.group(1))
            return {
                "raw": original,
                "canonical": f"{number:.3f} pc",
                "value_type": "numeric",
                "numeric_value": number,
                "unit": "pc",
            }
    if slot == "coordinate":
        match = COORD_PAIR.search(original)
        if match:
            ra, dec = float(match.group(1)), float(match.group(2))
            return {
                "raw": original,
                "canonical": f"{ra:.5f},{dec:.5f}",
                "value_type": "numeric",
                "ra": ra,
                "dec": dec,
            }
    if slot == "spectral_type":
        compact = re.sub(r"\s+", "", original)
        return {"raw": original, "canonical": compact, "value_type": "categorical"}
    if slot in {
        "parallax", "parallax_error", "g_mag", "bp_mag", "rp_mag",
        "pm_ra", "pm_dec", "radial_velocity", "ruwe", "teff",
        "stellar_mass", "stellar_radius", "metallicity",
    }:
        match = NUMBER.search(original)
        if match:
            number = float(match.group())
            return {
                "raw": original,
                "canonical": f"{number:.4g}",
                "value_type": "numeric",
                "numeric_value": number,
            }
    if slot == "source_id":
        compact = re.sub(r"\s+", " ", original)
        return {"raw": original, "canonical": compact, "value_type": "identifier"}
    return {"raw": original, "canonical": original, "value_type": "text"}


def classify_slot(slot: str, records: list[dict]) -> dict:
    assertions = []
    for record in records:
        values = record["slots"].get(slot, [])
        details = record["normalized_slots"].get(slot, [])
        preferred = record["evidence"]["preferred_image"]
        assertions.append({
            "source": record["source_id"],
            "record_id": record["record_id"],
            "record_name": record["display_name"],
            "values": values,
            "normalized_values": [item["canonical"] for item in details],
            "value_details": details,
            "image_path": preferred,
            "metadata": {
                "image_files": record["evidence"]["image_files"],
                "image_match": "attribute-linked" if preferred else "missing",
            },
        })

    present = [item for item in assertions if item["values"]]
    normalized_keys = [tuple(sorted(item["normalized_values"])) for item in present]
    if len(present) <= 1:
        status, conflict_type = "single_source", "single_source"
    elif len(set(normalized_keys)) == 1:
        raw_sets = [tuple(item["values"]) for item in present]
        status, conflict_type = (
            ("consensus", "consensus") if len(set(raw_sets)) == 1 else ("overlap", "lexical_variant")
        )
    elif all(
        item["value_details"] and all(detail.get("value_type") == "numeric" for detail in item["value_details"])
        for item in present
    ):
        status, conflict_type = "differ", "numeric_discrepancy"
    else:
        status, conflict_type = "differ", "substantive_conflict"

    return {
        "slot": slot,
        "label": SLOT_LABELS.get(slot, slot),
        "status": status,
        "conflict_type": conflict_type,
        "source_count": len(present),
        "assertions": assertions,
    }


def image_dir_index() -> dict[str, Path]:
    if not IMAGE_ROOT.exists():
        return {}
    return {path.name.lower(): path for path in IMAGE_ROOT.iterdir() if path.is_dir()}


def image_dir_for(hostname: str, folders: dict[str, Path]) -> Path | None:
    if not hostname or not folders:
        return None
    variants = [hostname]
    for text in list(variants):
        variants.extend([
            text.replace(" ", "_"),
            text.replace(" ", "-"),
            text.replace("_", " "),
            text.replace("'", "_"),
            text.replace("'", ""),
            text.replace("'", "_").replace(" ", "_"),
            text.replace("'", "").replace(" ", "_"),
        ])
    seen = set()
    for name in variants:
        key = name.lower()
        if not name or key in seen:
            continue
        seen.add(key)
        hit = folders.get(key)
        if hit:
            return hit
    return None


def list_images(hostname: str, source_id: str, folders: dict[str, Path]) -> tuple[list[str], str | None]:
    folder = image_dir_for(hostname, folders)
    if not folder:
        return [], None
    files = sorted(
        path.name for path in folder.iterdir()
        if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png"}
    )
    prefixed = [name for name in files if name.lower().startswith(source_id.lower())]
    if prefixed:
        pool = prefixed
    else:
        pool = [
            name for name in files
            if not any(name.lower().startswith(prefix) for prefix in SOURCE_IMAGE_PREFIXES)
        ]
    preferred = None
    for token in ("dss2", "2mass", "ps1"):
        for name in pool:
            if token in name.lower():
                preferred = f"/api/astro/image/{folder.name}/{name}"
                break
        if preferred:
            break
    if pool and not preferred:
        preferred = f"/api/astro/image/{folder.name}/{pool[0]}"
    return pool, preferred


def load_index() -> dict[str, str]:
    with INDEX_PATH.open(encoding="utf-8-sig", newline="") as handle:
        return {clean(row["belongs_to"]): clean(row["hostname"]) for row in csv.DictReader(handle)}


def load_relations() -> dict[tuple[str, str], list[dict]]:
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    with RELATION_PATH.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            source = clean(row.get("book")).lower()
            if source not in PRIMARY_SOURCES:
                continue
            belongs = clean(row.get("belongs_to"))
            predicate = clean(row.get("关系"))
            value = clean(row.get("尾实体"))
            grouped[(belongs, source)].append({
                "subject": clean(row.get("头实体")),
                "predicate": predicate,
                "object": value,
                "level": clean(row.get("层级标签")),
                "slot": RELATION_TO_SLOT.get(predicate),
            })
    return grouped


def load_host_records(
    index: dict[str, str],
    relations: dict[tuple[str, str], list[dict]],
    folders: dict[str, Path],
) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    with ENTITY_PATH.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if not is_artifact(row.get("is Artifact")):
                continue
            source = clean(row.get("book")).lower()
            if source not in PRIMARY_SOURCES:
                continue
            belongs = clean(row.get("belongs_to"))
            entity = clean(row.get("实体"))
            hostname = index.get(belongs, entity)
            rels = relations.get((belongs, source), [])
            matched = [item for item in rels if not item["subject"] or item["subject"] == entity]
            if not matched:
                matched = rels
            slot_values: dict[str, list[str]] = defaultdict(list)
            for item in matched:
                slot = item["slot"]
                value = item["object"]
                if not slot or not value or value in slot_values[slot]:
                    continue
                slot_values[slot].append(value)
            values = dict(slot_values)
            normalized = {
                slot: [canonical_value(slot, raw) for raw in items]
                for slot, items in values.items()
            }
            files, preferred = list_images(hostname, source, folders)
            grouped[belongs].append({
                "record_id": f"{entity}@{source}",
                "source_id": source,
                "belongs_to": belongs,
                "display_name": hostname,
                "raw_name": entity,
                "slots": values,
                "normalized_slots": normalized,
                "relations": matched,
                "evidence": {
                    "image_files": files,
                    "preferred_image": preferred,
                },
            })
    return grouped


def slot_sort_key(slot: str) -> tuple[int, int, str]:
    if slot in ANALYSIS_SLOTS:
        return (0, ANALYSIS_ORDER.get(slot, 99), slot)
    return (1, 99, slot)


def build_artifact(belongs_to: str, hostname: str, records: list[dict]) -> dict:
    records = sorted(records, key=lambda item: item["source_id"])
    books = sorted({record["source_id"] for record in records})
    alignment = "confirmed" if len(books) > 1 else "source-local"
    slots_used = sorted({slot for record in records for slot in record["slots"]}, key=slot_sort_key)
    all_slots = [classify_slot(slot, records) for slot in slots_used]
    caus = [item for item in all_slots if item["slot"] in ANALYSIS_SLOTS]
    images = {}
    for record in records:
        files = record["evidence"]["image_files"]
        if files:
            images[record["source_id"]] = files
    return {
        "artifact_id": f"astro-{belongs_to}",
        "name": hostname,
        "canonical_name": hostname,
        "raw_names": sorted({record["raw_name"] for record in records}),
        "books": books,
        "records": [record["record_id"] for record in records],
        "aligned": alignment == "confirmed",
        "alignment_status": alignment,
        "featured": hostname in FEATURED_HOSTS,
        "all_slots": all_slots,
        "caus": caus,
        "images": images,
    }


def main() -> None:
    index = load_index()
    relations = load_relations()
    folders = image_dir_index()
    grouped = load_host_records(index, relations, folders)
    artifacts = []
    for belongs_to, records in sorted(
        grouped.items(),
        key=lambda item: int(item[0]) if item[0].isdigit() else item[0],
    ):
        hostname = index.get(belongs_to, records[0]["display_name"])
        artifacts.append(build_artifact(belongs_to, hostname, records))

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
    artifacts_with_images = sum(bool(artifact["images"]) for artifact in artifacts)
    source_image_cards = sum(len(artifact["images"]) for artifact in artifacts)
    output = {
        "schema_version": "case2-astro-2",
        "case": {
            "id": "astro",
            "number": 2,
            "label": "Case 2 · Astronomy",
            "title": "NASA, SIMBAD and Gaia Host Catalog Conflicts",
            "note": "v2 host records compared across NASA, SIMBAD, and Gaia on shared attribute slots.",
        },
        "books": {source_id: metadata["title"] for source_id, metadata in PRIMARY_SOURCES.items()},
        "source_catalog": {
            source_id: {"id": source_id, "title": metadata["title"], "role": metadata["role"]}
            for source_id, metadata in PRIMARY_SOURCES.items()
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
            "primary_sources": len(PRIMARY_SOURCES),
            "image_folders": len(folders),
            "artifacts_with_images": artifacts_with_images,
            "source_image_cards": source_image_cards,
            "analysis_slots": sorted(ANALYSIS_SLOTS),
        },
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(OUT_PATH), "stats": output["stats"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
