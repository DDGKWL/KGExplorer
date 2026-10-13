import json
import re
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_from_directory
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

DATA = Path(__file__).resolve().parent / "data"
PROCESSED = DATA / "processed" / "case1_guqin.json"
GUQIN_UMAP = DATA / "processed" / "case1_umap.json"
GUQIN_CURATED = DATA / "curated" / "guqin" / "explorer.json"
IMAGE_ROOT = DATA / "raw" / "image"
TEXT_ROOT = DATA / "raw" / "text"
ASTRO_PROCESSED = DATA / "astro" / "processed" / "case2_astro.json"
ASTRO_UMAP = DATA / "astro" / "processed" / "case2_umap.json"
ASTRO_IMAGE_ROOT = DATA / "astro" / "raw" / "images"
NEWS_PROCESSED = DATA / "news" / "processed" / "case3_news.json"
NEWS_UMAP = DATA / "news" / "processed" / "case3_umap.json"
NEWS_REPORTS = DATA / "news" / "reports"
# NEWS_V6_ROOT = DATA / "news" / "v6"
# NEWS_V6_GRAPH = NEWS_V6_ROOT / "processed" / "v6all_news_graph.json"
# NEWS_V6_COMPARISONS = NEWS_V6_ROOT / "processed" / "v6all_event_comparisons.json"
NEWS_V6_ROOT = DATA / "news" / "v7"
NEWS_V6_GRAPH = NEWS_V6_ROOT / "processed" / "v6all_news_graph.json"
NEWS_V6_COMPARISONS = NEWS_V6_ROOT / "processed" / "v6all_event_comparisons.json"
NEWS_V6_REPORTS = NEWS_V6_ROOT / "reports"
# DeepSeek semantic verdicts sidecar (written by scripts/run_llm_judge_news.py).
NEWS_LLM_VERDICTS = NEWS_V6_ROOT / "processed" / "llm_verdicts.json"
# DORIS/FrameNet framing verdicts sidecar (written by scripts/run_doris_framing.py).
NEWS_FRAMING_VERDICTS = NEWS_V6_ROOT / "processed" / "framing_verdicts.json"


def attach_projection(data, projection_path):
    """Join precomputed Lens coordinates onto artifacts without altering source JSON."""
    if not projection_path.exists():
        data["projection"] = {"ready": False, "methods": {}}
        return data
    payload = json.loads(projection_path.read_text(encoding="utf-8"))
    points = payload.get("points") or {}
    for artifact in data.get("artifacts") or []:
        artifact["projection"] = points.get(artifact.get("artifact_id"))
    data["projection"] = {
        "ready": True,
        "methods": payload.get("methods") or {},
        "n_neighbors": payload.get("n_neighbors"),
        "attribute_slots": payload.get("attribute_slots") or [],
        "point_count": len(points),
    }
    return data


@app.get("/api/health")
def health():
    return jsonify({"status": "ok", "service": "KGExplorer Flask API"})


def assertion_scope(record, slot):
    """Recover whole-object values separately from component-level values."""
    names = {
        re.sub(r"\s+", "", str(record.get(key, "")).split("·")[0])
        for key in ("raw_name", "normalized_name", "display_name")
    }
    names.discard("")
    object_values = []
    component_values = {}
    for relation in record.get("relations", []):
        if relation.get("slot") != slot:
            continue
        subject = re.sub(r"^\d+[_-]", "", str(relation.get("subject", "")))
        subject = re.sub(r"\s+", "", subject)
        value = relation.get("object", "")
        if subject in names:
            if value not in object_values:
                object_values.append(value)
        else:
            component_values.setdefault(relation.get("subject", ""), [])
            if value not in component_values[relation.get("subject", "")]:
                component_values[relation.get("subject", "")].append(value)
    return {
        "object_values": object_values,
        "component_values": [
            {"subject": subject, "values": values}
            for subject, values in component_values.items()
        ],
    }


def guqin_data():
    if not PROCESSED.exists():
        abort(404)
    data = json.loads(PROCESSED.read_text(encoding="utf-8"))
    record_index = {record["record_id"]: record for record in data["records"]}
    for artifact in data["artifacts"]:
        for slot in artifact.get("caus", []):
            for assertion in slot.get("assertions", []):
                record = record_index.get(assertion.get("record_id"))
                if record:
                    assertion["scope"] = assertion_scope(record, slot["slot"])
    if GUQIN_CURATED.exists():
        curated = json.loads(GUQIN_CURATED.read_text(encoding="utf-8"))
        artifact_audit = {
            artifact["artifact_id"]: artifact for artifact in curated["artifacts"]
        }
        conflict_audit = {
            (conflict["artifact_id"], conflict["slot"]): conflict
            for conflict in curated["conflicts"]
        }
        for artifact in data["artifacts"]:
            audit_artifact = artifact_audit.get(artifact["artifact_id"], {})
            artifact["audit_identity_grade"] = audit_artifact.get("identity_grade", "source_local")
            artifact["audit_readiness_counts"] = audit_artifact.get("readiness_counts", {})
            for slot in artifact.get("caus", []):
                audit = conflict_audit.get((artifact["artifact_id"], slot["slot"]))
                if not audit:
                    continue
                slot["audit"] = {
                    key: audit[key] for key in (
                        "case_row_id", "conflict_id", "identity_grade",
                        "review_readiness", "text_support", "image_support",
                        "audit_flags", "machine_audit_note",
                    )
                }
                source_audit = {
                    (assertion["source_id"], assertion["record_id"]): assertion
                    for assertion in audit["assertions"]
                }
                for assertion in slot.get("assertions", []):
                    key = (assertion.get("source"), assertion.get("record_id"))
                    if key in source_audit:
                        assertion["audit"] = source_audit[key]
        data["evidence_audit"] = curated["summary"]
        data["quality_issues"] = curated["quality_issues"]
    data["case"] = {
        "id": "guqin",
        "number": 1,
        "label": "Case 1 · Guqin",
        "title": "Guqin Heritage Knowledge Revision",
        "note": "Four scanned guqin books with OCR, extracted attributes, and instrument images.",
    }
    return attach_projection(data, GUQIN_UMAP)


def curated_guqin_data():
    if not GUQIN_CURATED.exists():
        abort(404)
    return json.loads(GUQIN_CURATED.read_text(encoding="utf-8"))


def astro_data():
    """Case 2 catalog JSON. Separate from Guqin processed files."""
    if not ASTRO_PROCESSED.exists():
        abort(404)
    return attach_projection(json.loads(ASTRO_PROCESSED.read_text(encoding="utf-8")), ASTRO_UMAP)


def news_data():
    """Case 3 article JSON. Evidence files stay under data/news/reports."""
    if not NEWS_PROCESSED.exists():
        abort(404)
    return attach_projection(json.loads(NEWS_PROCESSED.read_text(encoding="utf-8")), NEWS_UMAP)


def news_v6_json(path):
    if not path.exists():
        abort(404)
    return json.loads(path.read_text(encoding="utf-8"))


def news_v6_comparisons():
    """Return project-local evidence URLs instead of source-machine paths."""
    data = news_v6_json(NEWS_V6_COMPARISONS)
    for event in data.get("events", []):
        for report in event.get("reports", []):
            url_hash = report.get("url_hash", "")
            folder = NEWS_V6_REPORTS / url_hash
            image_dir = folder / "images"
            images = (
                sorted(
                    path.name
                    for path in image_dir.iterdir()
                    if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".gif", ".webp"}
                )
                if image_dir.is_dir()
                else []
            )
            report["text_url"] = f"/api/news/v6/text/{url_hash}"
            report["image_paths"] = [
                f"/api/news/v6/image/{url_hash}/{filename}" for filename in images
            ]
            report.pop("clean_body_path", None)
    return data


@app.get("/api/contested-kg")
def contested_kg():
    return jsonify(guqin_data())


@app.get("/api/case-data/<case_id>")
def case_data(case_id):
    if case_id == "guqin":
        return jsonify(guqin_data())
    if case_id == "astro":
        return jsonify(astro_data())
    if case_id == "news":
        return jsonify(news_data())
    return jsonify({"error": "unknown case"}), 404


@app.get("/api/news/v6/graph")
def news_v6_graph():
    return jsonify(news_v6_json(NEWS_V6_GRAPH))


@app.get("/api/news/v6/comparisons")
def news_v6_comparison_data():
    return jsonify(news_v6_comparisons())


@app.get("/api/news/v6/text/<url_hash>")
def news_v6_text(url_hash):
    target = NEWS_V6_REPORTS / url_hash / "full.txt"
    if not target.is_file():
        abort(404)
    return target.read_text(encoding="utf-8", errors="replace"), 200, {"Content-Type": "text/plain; charset=utf-8"}


@app.get("/api/news/v6/image/<url_hash>/<path:filename>")
def news_v6_image(url_hash, filename):
    image_dir = NEWS_V6_REPORTS / url_hash / "images"
    target = image_dir / Path(filename).name
    if not target.is_file():
        abort(404)
    return send_from_directory(image_dir, target.name)


@app.get("/api/news/llm-verdicts")
def news_llm_verdicts():
    """Case 3 sidecar LLM verdicts; empty payload until the batch script runs."""
    if not NEWS_LLM_VERDICTS.exists():
        return jsonify({"schema_version": "news-llm-verdicts-1", "model": None, "verdicts": {}})
    return jsonify(json.loads(NEWS_LLM_VERDICTS.read_text(encoding="utf-8")))


@app.get("/api/news/framing-verdicts")
def news_framing_verdicts():
    """Case 3 DORIS/FrameNet framing sidecar (third judgment method).

    Written by scripts/run_doris_framing.py. Same additive contract as the LLM
    sidecar: an empty payload until the batch script runs, never a 404, so the
    frontend can treat it as optional.
    """
    if not NEWS_FRAMING_VERDICTS.exists():
        return jsonify({
            "schema_version": "news-framing-verdicts-1",
            "annotator": None,
            "model": None,
            "verdicts": {},
        })
    return jsonify(json.loads(NEWS_FRAMING_VERDICTS.read_text(encoding="utf-8")))


@app.post("/api/llm/judge")
def llm_judge_slot():
    """Online single-slot judgment (fallback when the sidecar lacks a key).

    Body: {entity, attribute_label, entries: [{source, values, evidence}],
    missing_sources}. Degrades to verdict "uncertain" when the module, key, or
    call fails, so the frontend always gets a well-formed answer.
    """
    try:
        from llm_judge import judge_consistency  # optional dependency, lazy import
    except ImportError:
        return jsonify({"verdict": "uncertain", "reason": "llm_judge 模块不可用", "model": None})
    payload = request.get_json(silent=True) or {}
    result = judge_consistency(
        payload.get("entity") or "",
        payload.get("attribute_label") or payload.get("attribute") or "",
        payload.get("entries") or [],
        payload.get("missing_sources") or [],
    )
    return jsonify(result)


@app.get("/api/guqin/explorer")
def guqin_explorer():
    """Search the curated Guqin evidence index without changing raw values."""
    data = curated_guqin_data()
    query = request.args.get("q", "").strip().lower()
    source = request.args.get("source", "").strip()
    slot = request.args.get("slot", "").strip()
    readiness = request.args.get("readiness", "").strip()

    conflicts = data["conflicts"]
    if source:
        conflicts = [
            row for row in conflicts
            if source in row["sources"]
            or any(item["source_id"] == source for item in row["assertions"])
        ]
    if slot:
        conflicts = [row for row in conflicts if row["slot"] == slot]
    if readiness:
        allowed = {value.strip() for value in readiness.split(",") if value.strip()}
        conflicts = [row for row in conflicts if row["review_readiness"] in allowed]
    if query:
        def conflict_text(row):
            assertion_text = " ".join(
                f"{item['record_id']} {item['raw_values']} {item['text_snippet']}"
                for item in row["assertions"]
            )
            return (
                f"{row['artifact_name']} {row['slot_label']} "
                f"{row['machine_audit_note']} {assertion_text}"
            ).lower()

        conflicts = [row for row in conflicts if query in conflict_text(row)]

    artifact_ids = {row["artifact_id"] for row in conflicts}
    artifacts = [
        artifact for artifact in data["artifacts"]
        if artifact["artifact_id"] in artifact_ids
    ]
    record_ids = {
        assertion["record_id"]
        for conflict in conflicts
        for assertion in conflict["assertions"]
    }
    records = [record for record in data["records"] if record["record_id"] in record_ids]
    return jsonify({
        "schema_version": data["schema_version"],
        "summary": data["summary"],
        "sources": data["sources"],
        "filters": {
            "q": query,
            "source": source,
            "slot": slot,
            "readiness": readiness,
        },
        "result_counts": {
            "artifacts": len(artifacts),
            "conflicts": len(conflicts),
            "records": len(records),
        },
        "artifacts": artifacts,
        "conflicts": conflicts,
        "records": records,
        "quality_issues": data["quality_issues"],
    })


@app.get("/api/guqin/conflicts/<path:conflict_id>")
def guqin_conflict(conflict_id):
    data = curated_guqin_data()
    conflict = next(
        (row for row in data["conflicts"] if row["conflict_id"] == conflict_id),
        None,
    )
    if not conflict:
        abort(404)
    return jsonify(conflict)


@app.get("/api/guqin/records/<record_id>")
def guqin_record(record_id):
    data = curated_guqin_data()
    record = next(
        (row for row in data["records"] if row["record_id"] == record_id),
        None,
    )
    if not record:
        abort(404)
    return jsonify(record)


@app.get("/api/image/<record_id>/<path:filename>")
def artifact_image(record_id, filename):
    folder = IMAGE_ROOT / record_id
    if not folder.exists():
        return jsonify({"error": "no images for record"}), 404
    image_folder = folder / "images" if (folder / "images").exists() else folder
    return send_from_directory(image_folder, filename)


@app.get("/api/astro/image/<star>/<path:filename>")
def astro_image(star, filename):
    """Serve Case 2 cutouts from data/astro/raw/images/<star>/."""
    folder = ASTRO_IMAGE_ROOT / Path(star).name
    target = folder / Path(filename).name
    if not folder.is_dir() or not target.is_file():
        abort(404)
    return send_from_directory(folder, target.name)


@app.get("/api/astro/images/<star>")
def astro_image_list(star):
    folder = ASTRO_IMAGE_ROOT / Path(star).name
    if not folder.is_dir():
        return jsonify([])
    return jsonify(sorted(path.name for path in folder.glob("*.jpg")))


def news_article_dir(url_hash, event_id=None):
    """Flat reports/<hash>/ is the current layout; event/hash remains for older cases."""
    url_hash = Path(url_hash).name
    flat = NEWS_REPORTS / url_hash
    if (flat / "full.txt").is_file() or (flat / "images").is_dir():
        return flat
    if event_id:
        nested = NEWS_REPORTS / Path(event_id).name / url_hash
        if nested.is_dir():
            return nested
    return None


@app.get("/api/news/image/<url_hash>/<path:filename>")
def news_image(url_hash, filename):
    """Serve Case 3 article images from data/news/reports/<hash>/images/."""
    folder = news_article_dir(url_hash)
    image_dir = folder / "images" if folder else None
    target = image_dir / Path(filename).name if image_dir else None
    if not image_dir or not image_dir.is_dir() or not target or not target.is_file():
        abort(404)
    return send_from_directory(image_dir, target.name)


@app.get("/api/news/text/<url_hash>")
def news_text(url_hash):
    """Serve the fetched article body for a Case 3 source record."""
    folder = news_article_dir(url_hash)
    target = folder / "full.txt" if folder else None
    if not target or not target.is_file():
        abort(404)
    return send_from_directory(folder, "full.txt", mimetype="text/plain")


@app.get("/api/text/<record_id>")
def artifact_text(record_id):
    """Serve the id-aligned OCR text for a Guqin source record."""
    match = re.match(r"^(Gq|Yjzq|QNQY|CQL)\d+$", record_id)
    if not match:
        abort(404)
    folder = TEXT_ROOT / f"{match.group(1)}-txt-id"
    filename = f"{record_id}.txt"
    if not (folder / filename).exists():
        abort(404)
    return send_from_directory(folder, filename, mimetype="text/plain; charset=utf-8")


@app.get("/api/images/<record_id>")
def artifact_image_list(record_id):
    folder = IMAGE_ROOT / record_id
    if not folder.exists():
        return jsonify([])
    image_folder = folder / "images" if (folder / "images").exists() else folder
    return jsonify(sorted(p.name for p in image_folder.glob("*.jpg")))


@app.post("/api/chat")
def chat():
    payload = request.get_json(silent=True) or {}
    message = payload.get("message", "")
    return jsonify({"reply": f"LLM placeholder received: {message}", "source": "placeholder"})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5001, debug=True)
