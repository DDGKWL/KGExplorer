"""Precompute 2D Lens coordinates for processed case artifacts.

Conflict features cluster objects with similar disagreement patterns.
Attribute features cluster objects with similar normalized catalogue values.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

BACKEND = Path(__file__).resolve().parents[1]
DATA = BACKEND / "data"

CASES = {
    "guqin": {
        "source": DATA / "processed" / "case1_guqin.json",
        "output": DATA / "processed" / "case1_umap.json",
        "slot_key": "caus",
        "attribute_slots": (
            "form", "dating", "dating_assessment", "lacquer", "ash",
            "duanwen", "material", "current_holder",
        ),
    },
    "astro": {
        "source": DATA / "astro" / "processed" / "case2_astro.json",
        "output": DATA / "astro" / "processed" / "case2_umap.json",
        "slot_key": "caus",
        "attribute_slots": (
            "coordinate", "distance", "parallax", "spectral_type", "source_id",
        ),
    },
}

STATUS_KEYS = ("differ", "overlap", "consensus", "single_source", "missing")
ALIGN_KEYS = ("confirmed", "candidate", "source-local", "unresolved")
MAX_ATTR_VALUES = 36


def slot_items(artifact: dict, slot_key: str) -> list[dict]:
    return artifact.get(slot_key) or artifact.get("slots") or []


def status_of(slot: dict) -> str:
    status = slot.get("status") or "missing"
    if status in STATUS_KEYS:
        return status
    return "missing"


def value_tokens(slot: dict) -> list[str]:
    tokens = []
    for assertion in slot.get("assertions") or []:
        details = assertion.get("value_details") or []
        if details:
            for detail in details:
                token = (detail.get("group") or detail.get("canonical") or "").strip()
                if token and token != "numeric:cm":
                    tokens.append(token)
            continue
        for value in assertion.get("normalized_values") or assertion.get("values") or []:
            token = str(value or "").strip()
            if token:
                tokens.append(token)
    seen = []
    for token in tokens:
        if token not in seen:
            seen.append(token)
    return seen[:8]


def collect_slots(artifacts: list[dict], slot_key: str) -> list[str]:
    names = []
    for artifact in artifacts:
        for slot in slot_items(artifact, slot_key):
            name = slot.get("slot")
            if name and name not in names:
                names.append(name)
    return names


def conflict_matrix(artifacts: list[dict], slot_key: str, slots: list[str]) -> np.ndarray:
    rows = []
    for artifact in artifacts:
        by_slot = {item.get("slot"): item for item in slot_items(artifact, slot_key)}
        row = []
        for name in slots:
            status = status_of(by_slot[name]) if name in by_slot else "missing"
            row.extend(1 if status == key else 0 for key in STATUS_KEYS)
        alignment = artifact.get("alignment_status") or "unresolved"
        row.extend(1 if alignment == key else 0 for key in ALIGN_KEYS)
        books = artifact.get("books") or []
        row.append(1 if len(books) > 1 else 0)
        row.append(1 if artifact.get("images") else 0)
        rows.append(row)
    return np.asarray(rows, dtype=np.float32)


def attribute_vocab(artifacts: list[dict], slot_key: str, names: tuple[str, ...]) -> list[tuple[str, str]]:
    counts: dict[tuple[str, str], int] = {}
    for artifact in artifacts:
        by_slot = {item.get("slot"): item for item in slot_items(artifact, slot_key)}
        for name in names:
            slot = by_slot.get(name)
            if not slot:
                continue
            for token in value_tokens(slot):
                counts[(name, token)] = counts.get((name, token), 0) + 1
    vocab = []
    for name in names:
        ranked = sorted(
            ((token, n) for (slot, token), n in counts.items() if slot == name),
            key=lambda item: (-item[1], item[0]),
        )[:MAX_ATTR_VALUES]
        vocab.extend((name, token) for token, _n in ranked)
    return vocab


def attribute_matrix(
    artifacts: list[dict],
    slot_key: str,
    names: tuple[str, ...],
    vocab: list[tuple[str, str]],
) -> np.ndarray:
    rows = []
    for artifact in artifacts:
        by_slot = {item.get("slot"): item for item in slot_items(artifact, slot_key)}
        present = {name: set(value_tokens(by_slot[name])) for name in names if name in by_slot}
        row = [1 if token in present.get(name, set()) else 0 for name, token in vocab]
        for name in names:
            slot = by_slot.get(name)
            tokens = present.get(name, set())
            row.append(0 if tokens else 1)
            if slot:
                row.extend(1 if status_of(slot) == key else 0 for key in ("differ", "overlap"))
            else:
                row.extend([0, 0])
        rows.append(row)
    return np.asarray(rows, dtype=np.float32)


def embed(matrix: np.ndarray, n_neighbors: int) -> tuple[np.ndarray, str]:
    if matrix.shape[0] < 3:
        coords = np.zeros((matrix.shape[0], 2), dtype=np.float32)
        return coords, "identity"
    filled = matrix.copy()
    if filled.shape[1] == 0:
        filled = np.zeros((filled.shape[0], 1), dtype=np.float32)
    try:
        import umap
        neighbors = max(2, min(n_neighbors, filled.shape[0] - 1))
        reducer = umap.UMAP(
            n_neighbors=neighbors,
            min_dist=0.25,
            metric="hamming",
            random_state=42,
        )
        return np.asarray(reducer.fit_transform(filled), dtype=np.float32), "umap"
    except Exception:
        from sklearn.decomposition import PCA
        pca = PCA(n_components=2, random_state=42)
        return np.asarray(pca.fit_transform(filled), dtype=np.float32), "pca-fallback"


def scale_unit(coords: np.ndarray) -> list[dict]:
    if not len(coords):
        return []
    lo = coords.min(axis=0)
    hi = coords.max(axis=0)
    span = np.where(hi - lo < 1e-6, 1.0, hi - lo)
    normed = (coords - lo) / span
    return [{"x": float(x), "y": float(y)} for x, y in normed]


def build_case(case_id: str, n_neighbors: int) -> dict:
    spec = CASES[case_id]
    source = spec["source"]
    if not source.exists():
        raise FileNotFoundError(source)
    data = json.loads(source.read_text(encoding="utf-8"))
    artifacts = data.get("artifacts") or []
    slot_key = spec["slot_key"]
    slots = collect_slots(artifacts, slot_key)
    attr_names = tuple(name for name in spec["attribute_slots"] if name in slots) or tuple(slots[:8])
    vocab = attribute_vocab(artifacts, slot_key, attr_names)
    conflict_coords, conflict_method = embed(conflict_matrix(artifacts, slot_key, slots), n_neighbors)
    attribute_coords, attribute_method = embed(
        attribute_matrix(artifacts, slot_key, attr_names, vocab),
        n_neighbors,
    )
    conflict_xy = scale_unit(conflict_coords)
    attribute_xy = scale_unit(attribute_coords)
    points = {}
    for index, artifact in enumerate(artifacts):
        points[artifact["artifact_id"]] = {
            "conflict": conflict_xy[index],
            "attribute": attribute_xy[index],
        }
    payload = {
        "schema_version": "umap-projection-1",
        "case": case_id,
        "n_neighbors": n_neighbors,
        "methods": {
            "conflict": conflict_method,
            "attribute": attribute_method,
        },
        "slot_order": slots,
        "attribute_slots": list(attr_names),
        "attribute_features": [f"{name}:{token}" for name, token in vocab],
        "points": points,
    }
    spec["output"].parent.mkdir(parents=True, exist_ok=True)
    spec["output"].write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", default="guqin", choices=sorted(CASES))
    parser.add_argument("--n-neighbors", type=int, default=10)
    args = parser.parse_args()
    payload = build_case(args.case, args.n_neighbors)
    print(
        f"Wrote {len(payload['points'])} points for {args.case} "
        f"({payload['methods']['conflict']} / {payload['methods']['attribute']})"
    )


if __name__ == "__main__":
    main()
