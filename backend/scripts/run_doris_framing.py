"""DORIS-based framing-conflict detection for Case 3 (News).

Implements the DORIS narrative-framing rule as a THIRD judgment method next
to the deterministic rule judge and the DeepSeek semantic judge
(scripts/run_llm_judge_news.py): when the SAME canonical entity of the same
event is cast into DIFFERENT frame-element roles by DIFFERENT sources, the
sources frame the narrative differently.

Pipeline (single offline script):
  1. CDCR mapping export - pure Python. Canonical entity ids per report come
     from the v6 comparisons payload itself (the `roles` slot stores values as
     `<entity node id>::<role>`, e.g. `person:donald-trump::assailant`).
  2. DeepSeek constrained-JSON FrameNet annotation per report text
     (temperature=0, sha256 disk cache in processed/framing_annotation_cache/,
     vocabulary-validated against scripts/framenet_index.json with fuzzy
     normalization; failures degrade to "no annotation" for that report).
  3. rdflib KG per event - FrameAnnotation / FrameElementAssignment /
     SourceDocument nodes (Grounding/Encoding/Decoding style) plus PROV-O
     provenance for the annotation run. --dump-kg writes Turtle files.
  4. SPARQL conflict queries over the per-event KG:
       a. strict DORIS: same frame + different FE + different source document;
       b. narrative role divergence: same entity + different frames/FEs across
          documents (news sources often cast an actor into an entirely
          different frame, e.g. Assailant in Attack vs Participant_1 in
          Meet_with - still a framing conflict for the comparison slot).
  5. Verdict mapping to the `roles` and `attitude` comparison slots and atomic
     sidecar write to processed/framing_verdicts.json. Keys are IDENTICAL to
     llm_judge.verdict_key() (event_id::attribute::sorted "report_id:canonical")
     so the frontend addresses the same slots. Verdicts are ADDITIVE metadata:
     they never overwrite the rule-based status.

Usage:
    python backend/scripts/run_doris_framing.py --dry-run
    python backend/scripts/run_doris_framing.py --events event:all-meeting-03c45110c7f05770 --dump-kg
    python backend/scripts/run_doris_framing.py             # full run, resumable
    python backend/scripts/run_doris_framing.py --refresh   # ignore caches

Environment (see backend/.env.example):
    DEEPSEEK_API_KEY  required for live annotation (cache hits need no key)
    LLM_MODEL         optional (default deepseek-chat)
    LLM_BASE_URL      optional (default https://api.deepseek.com)
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

import llm_judge  # noqa: E402  (path bootstrap must run first)
from llm_judge import utc_now_iso, verdict_key  # noqa: E402

try:
    from rdflib import Graph, Literal, Namespace, URIRef
    from rdflib.namespace import RDF, RDFS, XSD
except ImportError:  # surfaced with an actionable message before the KG stage
    Graph = None

NEWS_V6_ROOT = BACKEND_DIR / "data" / "news" / "v7"
GRAPH_PATH = NEWS_V6_ROOT / "processed" / "v6all_news_graph.json"
COMPARISONS_PATH = NEWS_V6_ROOT / "processed" / "v6all_event_comparisons.json"
REPORTS_ROOT = NEWS_V6_ROOT / "reports"
OUTPUT_PATH = NEWS_V6_ROOT / "processed" / "framing_verdicts.json"
CACHE_DIR = NEWS_V6_ROOT / "processed" / "framing_annotation_cache"
KG_DIR = NEWS_V6_ROOT / "processed" / "framing_kg"
FRAMENET_INDEX_PATH = Path(__file__).resolve().parent / "framenet_index.json"

GRAPH_SCHEMA = "gdnews-v6-graph-1"
COMPARISONS_SCHEMA = "gdnews-v6-comparisons-1"
SIDECAR_SCHEMA = "news-framing-verdicts-1"
ANNOTATOR = "deepseek"
METHOD = "doris-framenet"
PROMPT_VERSION = "doris-framenet-v1"
TARGET_ATTRIBUTES = ("roles", "attitude")
MAX_BODY_CHARS = 12000
MAX_FRAMES_PER_REPORT = 10
MAX_ELEMENTS_PER_FRAME = 12
MAX_SENTENCE_CHARS = 240
MAX_EVIDENCE_ROWS = 4
FUZZY_CUTOFF = 0.8

DORIS = Namespace("http://kgexplorer.org/ontology/doris/")
PROV = Namespace("http://www.w3.org/ns/prov#")

ANNOTATION_SYSTEM_PROMPT = (
    "你是FrameNet框架语义标注助手。输入是一篇新闻报道正文、一组规范实体(ID+名称+别名)"
    "和一个受约束的FrameNet词汇表(框架→框架元素)。请识别报道中与事件相关的语义框架实例,"
    "每个实例包含: frame(框架名)、trigger(触发词)、sentence(所在句子,不超过240字符)、"
    "elements(框架元素列表,每项包含fe(框架元素名)、entity(规范实体ID)、surface(原文表面形式))。"
    "硬性约束: 1) frame必须取自词汇表, fe必须取自该框架的元素列表; "
    "2) elements只标注词汇表列出的元素, entity必须逐字取自实体ID列表, 无法对应实体的元素直接省略; "
    "3) 不确定的框架实例不要输出, 不要发明新框架或新元素。"
    '只输出JSON对象: {"annotations": [ ... ]}, 无相关框架时输出 {"annotations": []}。'
)

VERDICTS = ("framing_conflict", "framing_consistent", "insufficient")


def _norm(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def _safe_id(text) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]", "_", str(text or ""))[-80:]
    return slug or "x"


def atomic_write_json(path: Path, payload) -> None:
    """Write JSON atomically (temp file + rename) so readers never see halves."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Stage 1-2: CDCR mapping export + DeepSeek FrameNet annotation
# ---------------------------------------------------------------------------

def load_inputs():
    """Load and schema-validate the two v6 payloads. Data is read-only."""
    if not COMPARISONS_PATH.exists():
        sys.exit(f"input not found: {COMPARISONS_PATH}")
    if not GRAPH_PATH.exists():
        sys.exit(f"input not found: {GRAPH_PATH}")
    comparisons = json.loads(COMPARISONS_PATH.read_text(encoding="utf-8"))
    graph = json.loads(GRAPH_PATH.read_text(encoding="utf-8"))
    if comparisons.get("schema_version") != COMPARISONS_SCHEMA:
        sys.exit(
            f"unexpected comparisons schema_version {comparisons.get('schema_version')!r} "
            f"(expected {COMPARISONS_SCHEMA!r}); regenerate the v6 payload first"
        )
    if graph.get("schema_version") != GRAPH_SCHEMA:
        sys.exit(
            f"unexpected graph schema_version {graph.get('schema_version')!r} "
            f"(expected {GRAPH_SCHEMA!r})"
        )
    return graph, comparisons


def load_framenet_index(path=None):
    path = Path(path) if path else FRAMENET_INDEX_PATH
    if not path.exists():
        sys.exit(f"FrameNet index not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    frames = payload.get("frames") or {}
    if not frames:
        sys.exit(f"FrameNet index has no frames: {path}")
    return {str(frame): [str(fe) for fe in (fes or [])] for frame, fes in frames.items()}


def event_entity_meta(event, graph_nodes):
    """Canonical entities of one event, from the roles slot (`id::role`) with a
    participants-slot fallback. Mirrors the CDCR alignment done by the v6
    pipeline, so no re-clustering happens here."""
    nodes = {
        node.get("id"): node
        for node in (graph_nodes or [])
        if node.get("type") != "event"
    }
    meta = {}

    def add(entity_id):
        node = nodes.get(entity_id)
        if node and entity_id not in meta:
            meta[entity_id] = {
                "label": node.get("label") or entity_id,
                "aliases": [alias for alias in (node.get("aliases") or []) if alias],
            }

    roles = next(
        (c for c in event.get("comparisons", []) if c.get("attribute") == "roles"),
        None,
    )
    if roles:
        for items in (roles.get("reports") or {}).values():
            for item in items or []:
                entity_id = str((item or {}).get("canonical") or "").split("::", 1)[0]
                if entity_id:
                    add(entity_id)
    if not meta:
        participants = next(
            (c for c in event.get("comparisons", []) if c.get("attribute") == "participants"),
            None,
        )
        for value in (participants or {}).get("canonical_values") or []:
            add(str(value))
    return meta


def report_text(report):
    """Report body text: project-local full.txt first, external clean body as
    fallback, None when neither exists (the report is then skipped)."""
    url_hash = report.get("url_hash") or ""
    primary = REPORTS_ROOT / url_hash / "full.txt"
    if url_hash and primary.exists():
        return primary.read_text(encoding="utf-8", errors="replace")
    fallback = report.get("clean_body_path")
    if fallback and Path(fallback).exists():
        return Path(fallback).read_text(encoding="utf-8", errors="replace")
    return None


def cache_path(model: str, text: str) -> Path:
    digest = hashlib.sha256(
        f"{model}::{PROMPT_VERSION}::{text[:MAX_BODY_CHARS]}".encode("utf-8")
    ).hexdigest()
    return CACHE_DIR / f"{digest}.json"


def load_cached_annotation(path: Path):
    """Cache files hold the NORMALIZED annotation list; [] means 'annotated,
    nothing found' (a real result), a missing file means 'not annotated yet'."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def build_lookup(names):
    return {_norm(name): str(name) for name in names if _norm(name)}


def resolve_vocabulary(raw_name, lookup):
    token = _norm(raw_name)
    if not token:
        return None
    if token in lookup:
        return lookup[token]
    matches = difflib.get_close_matches(token, list(lookup), n=1, cutoff=FUZZY_CUTOFF)
    return lookup[matches[0]] if matches else None


def resolve_entity(raw_id, raw_surface, entities, entity_lookup):
    entity_id = _norm(raw_id)
    if entity_id and entity_id in entities:
        return entity_id
    for surface in (raw_surface, raw_id):
        token = _norm(surface)
        if token and token in entity_lookup:
            return entity_lookup[token]
    return None


def normalize_annotation(raw, entities, frame_index):
    """Constrain + normalize one DeepSeek annotation payload.

    Returns a list of {frame, trigger, sentence, elements:[{fe, entity, surface}]}
    dicts. Frames/FEs outside the vocabulary are fuzzy-matched then dropped;
    elements without a resolvable canonical entity are dropped; per-report and
    per-frame caps keep the KG bounded.
    """
    if not isinstance(raw, dict):
        return []
    rows = raw.get("annotations")
    if not isinstance(rows, list):
        return []
    frame_lookup = build_lookup(frame_index)
    entity_lookup = {}
    for entity_id, meta in (entities or {}).items():
        for surface in [meta.get("label")] + list(meta.get("aliases") or []):
            token = _norm(surface)
            if token:
                entity_lookup.setdefault(token, entity_id)
    normalized = []
    for row in rows[:MAX_FRAMES_PER_REPORT]:
        if not isinstance(row, dict):
            continue
        frame = resolve_vocabulary(row.get("frame"), frame_lookup)
        if not frame or not frame_index.get(frame):
            continue
        fe_lookup = build_lookup(frame_index[frame])
        trigger = re.sub(r"\s+", " ", str(row.get("trigger") or ""))[:60]
        sentence = re.sub(r"\s+", " ", str(row.get("sentence") or ""))[:MAX_SENTENCE_CHARS]
        elements, seen = [], set()
        raw_elements = row.get("elements")
        raw_elements = raw_elements if isinstance(raw_elements, list) else []
        for element in raw_elements[: MAX_ELEMENTS_PER_FRAME * 2]:
            if not isinstance(element, dict):
                continue
            fe = resolve_vocabulary(element.get("fe"), fe_lookup)
            if not fe:
                continue
            entity_id = resolve_entity(
                element.get("entity"), element.get("surface"), entities, entity_lookup
            )
            if not entity_id:
                continue
            pair = (fe, entity_id)
            if pair in seen:
                continue
            seen.add(pair)
            elements.append({
                "fe": fe,
                "entity": entity_id,
                "surface": re.sub(r"\s+", " ", str(element.get("surface") or ""))[:80],
            })
            if len(elements) >= MAX_ELEMENTS_PER_FRAME:
                break
        if not elements:
            continue
        normalized.append({
            "frame": frame,
            "trigger": trigger,
            "sentence": sentence,
            "elements": elements,
        })
    return normalized


def _annotation_user_prompt(text, entities, frame_index):
    entity_lines = "\n".join(
        f"- {entity_id}: {meta.get('label') or entity_id}"
        + (f" (别名: {', '.join(meta.get('aliases') or [])})" if meta.get("aliases") else "")
        for entity_id, meta in (entities or {}).items()
    ) or "(none)"
    return (
        f"规范实体列表:\n{entity_lines}\n\n"
        f"FrameNet词汇表(JSON):\n{json.dumps(frame_index, ensure_ascii=False)}\n\n"
        f"报道正文:\n{text[:MAX_BODY_CHARS]}\n\n"
        "请按系统指令输出JSON标注。"
    )


def _deepseek_client():
    settings = llm_judge.config()
    if not settings["api_key"]:
        raise RuntimeError(
            "缺少 DEEPSEEK_API_KEY 环境变量（参见 backend/.env.example）"
        )
    if llm_judge.OpenAI is None:
        raise RuntimeError(
            "openai SDK 未安装，请先: pip install -r backend/requirements.txt"
        )
    return llm_judge.OpenAI(api_key=settings["api_key"], base_url=settings["base_url"])


def deepseek_annotate(client, model, text, entities, frame_index):
    """One constrained-JSON annotation call. Raises on transport errors so the
    caller can decide whether to skip the report (cache stays cold)."""
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": ANNOTATION_SYSTEM_PROMPT},
            {"role": "user", "content": _annotation_user_prompt(text, entities, frame_index)},
        ],
        temperature=0,
        max_tokens=2000,
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content or ""
    try:
        return json.loads(content)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Stage 3-4: rdflib KG (FrameAnnotation + PROV-O) and SPARQL conflict queries
# ---------------------------------------------------------------------------

_SPARQL_PREFIXES = (
    "PREFIX doris: <http://kgexplorer.org/ontology/doris/>\n"
    "PREFIX prov: <http://www.w3.org/ns/prov#>\n"
)

# Strict DORIS rule: same frame, different frame elements, different sources.
Q_CONFLICT_FE = _SPARQL_PREFIXES + """
SELECT DISTINCT ?entityId ?frame ?trig ?doc1 ?doc2 ?fe1 ?fe2 ?sent1 ?sent2 WHERE {
  ?ann1 doris:frame ?frame ; doris:trigger ?trig ; doris:document ?doc1 ;
        doris:hasElement ?el1 .
  ?el1 doris:feName ?fe1 ; doris:aboutEntity ?ent1 ; doris:sentence ?sent1 .
  ?ann2 doris:frame ?frame ; doris:document ?doc2 ;
        doris:hasElement ?el2 .
  ?el2 doris:feName ?fe2 ; doris:aboutEntity ?ent2 ; doris:sentence ?sent2 .
  ?ent1 doris:entityId ?entityId .
  ?ent2 doris:entityId ?entityId .
  FILTER (STR(?doc1) < STR(?doc2))
  FILTER (STR(?fe1) != STR(?fe2))
}
"""

# Narrative role divergence: same entity cast into different frames by
# different sources (e.g. Assailant in Attack vs Participant_1 in Meet_with).
Q_CONFLICT_NARRATIVE = _SPARQL_PREFIXES + """
SELECT DISTINCT ?entityId ?frame1 ?frame2 ?doc1 ?doc2 ?fe1 ?fe2 ?sent1 ?sent2 WHERE {
  ?ann1 doris:frame ?frame1 ; doris:document ?doc1 ; doris:hasElement ?el1 .
  ?el1 doris:feName ?fe1 ; doris:aboutEntity ?ent1 ; doris:sentence ?sent1 .
  ?ann2 doris:frame ?frame2 ; doris:document ?doc2 ; doris:hasElement ?el2 .
  ?el2 doris:feName ?fe2 ; doris:aboutEntity ?ent2 ; doris:sentence ?sent2 .
  ?ent1 doris:entityId ?entityId .
  ?ent2 doris:entityId ?entityId .
  FILTER (STR(?doc1) < STR(?doc2))
  FILTER (STR(?frame1) != STR(?frame2))
}
"""

# Frame agreement: same frame, same FE, different sources.
Q_CONSISTENT = _SPARQL_PREFIXES + """
SELECT DISTINCT ?entityId ?frame ?trig ?doc1 ?doc2 ?fe1 ?sent1 ?sent2 WHERE {
  ?ann1 doris:frame ?frame ; doris:trigger ?trig ; doris:document ?doc1 ;
        doris:hasElement ?el1 .
  ?el1 doris:feName ?fe1 ; doris:aboutEntity ?ent1 ; doris:sentence ?sent1 .
  ?ann2 doris:frame ?frame ; doris:document ?doc2 ;
        doris:hasElement ?el2 .
  ?el2 doris:feName ?fe1 ; doris:aboutEntity ?ent2 ; doris:sentence ?sent2 .
  ?ent1 doris:entityId ?entityId .
  ?ent2 doris:entityId ?entityId .
  FILTER (STR(?doc1) < STR(?doc2))
}
"""


def build_event_kg(event_id, annotations_by_report, entity_meta):
    """Build the per-event framing KG.

    Classes: doris:Event, doris:SourceDocument, doris:FrameAnnotation,
    doris:FrameElementAssignment, doris:Entity. PROV-O records that the
    annotation activity (attributed to the DeepSeek agent) used each source
    document and generated every frame annotation.
    """
    if Graph is None:
        raise RuntimeError("rdflib 未安装，请先: pip install -r backend/requirements.txt")
    kg = Graph()
    kg.bind("doris", DORIS)
    kg.bind("prov", PROV)
    activity = DORIS["activity/framing-annotation-run"]
    agent = DORIS["agent/deepseek-chat"]
    kg.add((activity, RDF.type, PROV.Activity))
    kg.add((agent, RDF.type, PROV.SoftwareAgent))
    kg.add((activity, PROV.wasAttributedTo, agent))
    kg.add((activity, PROV.startedAtTime, Literal(utc_now_iso(), datatype=XSD.dateTime)))
    event_node = DORIS[f"event/{_safe_id(event_id)}"]
    kg.add((event_node, RDF.type, DORIS.Event))
    kg.add((event_node, DORIS.eventId, Literal(event_id or "")))
    kg.add((activity, PROV.used, event_node))
    for url_hash, record in (annotations_by_report or {}).items():
        if not record.get("frames"):
            continue
        doc = DORIS[f"doc/{url_hash}"]
        kg.add((doc, RDF.type, DORIS.SourceDocument))
        kg.add((doc, RDF.type, PROV.Entity))
        kg.add((doc, DORIS.reportId, Literal(record.get("report_id") or url_hash)))
        kg.add((doc, DORIS.domain, Literal(record.get("domain") or "")))
        kg.add((activity, PROV.used, doc))
        for index, frame in enumerate(record["frames"]):
            slug = f"{index}_{_safe_id(frame.get('trigger') or frame['frame'])}"
            ann = DORIS[f"ann/{url_hash}/{slug}"]
            kg.add((ann, RDF.type, DORIS.FrameAnnotation))
            kg.add((ann, DORIS.frame, Literal(frame["frame"])))
            kg.add((ann, DORIS.trigger, Literal(frame.get("trigger") or "")))
            kg.add((ann, DORIS.sentence, Literal(frame.get("sentence") or "")))
            kg.add((ann, DORIS.document, doc))
            kg.add((ann, DORIS.aboutEvent, event_node))
            kg.add((ann, PROV.wasGeneratedBy, activity))
            for findex, element in enumerate(frame.get("elements") or []):
                fe_node = URIRef(str(ann) + f"/element/{findex}")
                kg.add((ann, DORIS.hasElement, fe_node))
                kg.add((fe_node, RDF.type, DORIS.FrameElementAssignment))
                kg.add((fe_node, DORIS.feName, Literal(element["fe"])))
                kg.add((fe_node, DORIS.surface, Literal(element.get("surface") or "")))
                # The element carries its own sentence copy so the SPARQL
                # queries can read evidence straight from the FE assignment.
                kg.add((fe_node, DORIS.sentence, Literal(frame.get("sentence") or "")))
                entity = DORIS[f"entity/{_safe_id(element['entity'])}"]
                kg.add((fe_node, DORIS.aboutEntity, entity))
                kg.add((entity, RDF.type, DORIS.Entity))
                kg.add((entity, DORIS.entityId, Literal(element["entity"])))
                label = (entity_meta or {}).get(element["entity"], {}).get("label")
                if label:
                    kg.add((entity, RDFS.label, Literal(label)))
    return kg


def _doc_hash(doc_uri) -> str:
    return str(doc_uri).rsplit("/doc/", 1)[-1]


def query_framing_rows(kg):
    """Run the three SPARQL queries. Returns (fe_conflicts, narrative_conflicts,
    consistent_rows) as plain dicts; doc URIs are reduced to url hashes."""
    fe_conflicts = [
        {
            "kind": "fe",
            "entity": str(row.entityId),
            "frame": str(row.frame),
            "trigger": str(row.trig or ""),
            "docs": (_doc_hash(row.doc1), _doc_hash(row.doc2)),
            "frames": (str(row.frame), str(row.frame)),
            "fe": (str(row.fe1), str(row.fe2)),
            "sentences": (str(row.sent1 or ""), str(row.sent2 or "")),
        }
        for row in kg.query(Q_CONFLICT_FE, initNs={"doris": DORIS, "prov": PROV})
    ]
    narrative_conflicts = [
        {
            "kind": "narrative",
            "entity": str(row.entityId),
            "frame": str(row.frame1),
            "trigger": "",
            "docs": (_doc_hash(row.doc1), _doc_hash(row.doc2)),
            "frames": (str(row.frame1), str(row.frame2)),
            "fe": (str(row.fe1), str(row.fe2)),
            "sentences": (str(row.sent1 or ""), str(row.sent2 or "")),
        }
        for row in kg.query(Q_CONFLICT_NARRATIVE, initNs={"doris": DORIS, "prov": PROV})
    ]
    consistent_rows = [
        {
            "kind": "consistent",
            "entity": str(row.entityId),
            "frame": str(row.frame),
            "trigger": str(row.trig or ""),
            "docs": (_doc_hash(row.doc1), _doc_hash(row.doc2)),
            "frames": (str(row.frame), str(row.frame)),
            "fe": (str(row.fe1), str(row.fe1)),
            "sentences": (str(row.sent1 or ""), str(row.sent2 or "")),
        }
        for row in kg.query(Q_CONSISTENT, initNs={"doris": DORIS, "prov": PROV})
    ]
    return fe_conflicts, narrative_conflicts, consistent_rows


# ---------------------------------------------------------------------------
# Stage 5: verdict aggregation (entity level -> comparison slot level)
# ---------------------------------------------------------------------------

def effective_rule_status(status, reports):
    """Mirror of oldStatus() in frontend/src/data/store.js (additive metadata
    only - this never replaces the rule status in the UI)."""
    if status == "different":
        return "differ"
    if status in ("partial_overlap", "missing_in_some_reports"):
        return "overlap"
    if status == "consensus":
        return "consensus"
    present = sum(1 for values in (reports or {}).values() if values)
    return "overlap" if present > 1 else "single_source"


def _mentions_entity(text, meta):
    """Word-boundary match of an entity label/alias inside slot evidence."""
    surfaces = [meta.get("label") or ""] + list(meta.get("aliases") or [])
    for surface in surfaces:
        surface = str(surface).strip()
        if len(surface) < 2:
            continue
        pattern = r"(?<![A-Za-z])" + re.escape(surface) + r"(?![A-Za-z])"
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


def slot_entity_ids(event, comparison, entity_meta):
    """Canonical entities attributable to one comparison slot.

    roles: the entity part of `entity_id::role` canonical values (direct).
    attitude (and any future slot): entities whose label/alias surface appears
    in the slot's evidence sentences.
    """
    attribute = comparison.get("attribute")
    ids = []
    if attribute == "roles":
        for items in (comparison.get("reports") or {}).values():
            for item in items or []:
                entity_id = str((item or {}).get("canonical") or "").split("::", 1)[0]
                if entity_id in entity_meta and entity_id not in ids:
                    ids.append(entity_id)
        return ids
    for report in event.get("reports", []):
        items = (comparison.get("reports") or {}).get(report.get("report_id")) or []
        text = " ".join(str((item or {}).get("evidence") or "") for item in items)
        if not text:
            continue
        for entity_id, meta in entity_meta.items():
            if entity_id not in ids and _mentions_entity(text, meta):
                ids.append(entity_id)
    return ids


def _evidence_from_row(row, domain_by_hash, report_id_by_hash):
    entries = []
    for index in (0, 1):
        url_hash = row["docs"][index]
        entries.append({
            "report_id": report_id_by_hash.get(url_hash, url_hash),
            "url_hash": url_hash,
            "domain": domain_by_hash.get(url_hash) or url_hash,
            "frame": row["frames"][index],
            "fe": row["fe"][index],
            "trigger": row.get("trigger") or "",
            "sentence": row["sentences"][index],
        })
    return entries


def _conflict_reason(entity_label, row):
    if row["kind"] == "fe":
        return (
            f"框架冲突: {entity_label} 在 {row['frame']} 框架中被不同来源赋予不同框架元素"
            f"（{row['fe'][0]} vs {row['fe'][1]}）。"
        )
    return (
        f"框架冲突: {entity_label} 被不同来源框架化为不同角色"
        f"（{row['frames'][0]}/{row['fe'][0]} vs {row['frames'][1]}/{row['fe'][1]}）。"
    )


def _consistent_reason(entity_label, row):
    return (
        f"框架一致: {entity_label} 在 {row['frame']} 框架中被多个来源一致标注为 {row['fe'][0]}。"
    )


def aggregate_entity_verdicts(fe_conflicts, narrative_conflicts, consistent_rows, coverage, entity_meta):
    """Per-entity DORIS verdict: conflict > consistent > insufficient."""
    results = {}
    for entity_id, meta in entity_meta.items():
        label = meta.get("label") or entity_id
        fe_rows = [row for row in fe_conflicts if row["entity"] == entity_id]
        narrative_rows = [row for row in narrative_conflicts if row["entity"] == entity_id]
        consistent_for = [row for row in consistent_rows if row["entity"] == entity_id]
        if fe_rows or narrative_rows:
            row = (fe_rows + narrative_rows)[0]
            results[entity_id] = {
                "verdict": "framing_conflict",
                "reason": _conflict_reason(label, row),
                "rows": (fe_rows + narrative_rows)[:MAX_EVIDENCE_ROWS],
            }
        elif consistent_for:
            results[entity_id] = {
                "verdict": "framing_consistent",
                "reason": _consistent_reason(label, consistent_for[0]),
                "rows": consistent_for[:MAX_EVIDENCE_ROWS],
            }
        else:
            doc_count = len(coverage.get(entity_id) or ())
            if doc_count:
                reason = (
                    f"框架证据不足: {label} 仅在 {doc_count} 个来源中出现框架标注，"
                    "无法跨来源对比。"
                )
            else:
                reason = f"框架证据不足: {label} 在本事件的报道中没有可用的框架标注。"
            results[entity_id] = {"verdict": "insufficient", "reason": reason, "rows": []}
    return results


def compute_event_verdicts(event, annotations_by_report, entity_meta, model, judged_at=None):
    """Run the full KG + SPARQL + aggregation for one event.

    Returns (verdicts, kg) where verdicts maps llm_judge-compatible slot keys
    to additive framing verdict entries.
    """
    judged_at = judged_at or utc_now_iso()
    event_id = event.get("event_id") or ""
    domain_by_hash = {
        report.get("url_hash") or "": report.get("domain") or ""
        for report in event.get("reports", [])
    }
    report_id_by_hash = {
        report.get("url_hash") or "": report.get("report_id") or ""
        for report in event.get("reports", [])
    }
    kg = build_event_kg(event_id, annotations_by_report, entity_meta)
    fe_conflicts, narrative_conflicts, consistent_rows = query_framing_rows(kg)

    coverage = defaultdict(set)
    for url_hash, record in (annotations_by_report or {}).items():
        for frame in record.get("frames") or []:
            for element in frame.get("elements") or []:
                coverage[element["entity"]].add(url_hash)

    entity_results = aggregate_entity_verdicts(
        fe_conflicts, narrative_conflicts, consistent_rows, coverage, entity_meta
    )

    verdicts = {}
    for comparison in event.get("comparisons", []):
        attribute = comparison.get("attribute")
        if attribute not in TARGET_ATTRIBUTES:
            continue
        slot_entities = slot_entity_ids(event, comparison, entity_meta)
        if not slot_entities:
            continue
        chosen = None
        for entity_id in slot_entities:
            result = entity_results.get(entity_id) or {}
            if result.get("verdict") == "framing_conflict":
                chosen = (entity_id, result)
                break
        if chosen is None:
            for entity_id in slot_entities:
                result = entity_results.get(entity_id) or {}
                if result.get("verdict") == "framing_consistent":
                    chosen = (entity_id, result)
                    break
        if chosen is None:
            covered = sorted(
                (entity_id for entity_id in slot_entities if coverage.get(entity_id)),
                key=lambda entity_id: -len(coverage.get(entity_id) or ()),
            )
            if not covered:
                continue  # no framing signal at all for this slot
            chosen = (covered[0], entity_results.get(covered[0]) or {})
        entity_id, result = chosen
        # SPARQL joins can emit the same (report, frame, FE, sentence) tuple
        # from several annotation-pair bindings; keep first occurrence only.
        evidence = []
        seen_rows = set()
        for row in result.get("rows") or []:
            for entry in _evidence_from_row(row, domain_by_hash, report_id_by_hash):
                dedupe_key = (
                    entry.get("report_id"),
                    entry.get("frame"),
                    entry.get("fe"),
                    entry.get("sentence"),
                )
                if dedupe_key in seen_rows:
                    continue
                seen_rows.add(dedupe_key)
                evidence.append(entry)
        verdicts[verdict_key(event_id, attribute, comparison.get("reports"))] = {
            "verdict": result.get("verdict") or "insufficient",
            "reason": result.get("reason") or "",
            "rule_status": effective_rule_status(
                comparison.get("status"), comparison.get("reports")
            ),
            "method": METHOD,
            "annotator": ANNOTATOR,
            "model": model,
            "event_id": event_id,
            "attribute": attribute,
            "entity": entity_id,
            "entity_label": (entity_meta.get(entity_id) or {}).get("label") or entity_id,
            "evidence": evidence[: MAX_EVIDENCE_ROWS * 2],
            "judged_at": judged_at,
        }
    return verdicts, kg


# ---------------------------------------------------------------------------
# Sidecar IO + CLI orchestration
# ---------------------------------------------------------------------------

def load_existing_verdicts():
    """Existing sidecar verdicts (kept so runs RESUME and other events survive)."""
    if not OUTPUT_PATH.exists():
        return {}
    try:
        payload = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload.get("verdicts") or {}


def event_hashes(event):
    return {report.get("url_hash") or "" for report in event.get("reports", [])}


def collect_target_events(comparisons, only_ids, limit):
    """Multi-source events only: a framing conflict needs >= 2 sources."""
    events = comparisons.get("events", [])
    if only_ids:
        wanted = set(only_ids)
        events = [event for event in events if event.get("event_id") in wanted]
        missing = wanted - {event.get("event_id") for event in events}
        if missing:
            print(f"warning: unknown event ids ignored: {sorted(missing)}")
    events = [event for event in events if len(event.get("reports") or []) > 1]
    if limit and limit > 0:
        events = events[:limit]
    return events


def annotate_reports(events, entity_meta_by_event, frame_index, model, refresh):
    """Cache-first annotation pass. Reports already cached (or --refresh misses)
    need no API key. Returns (annotations, pending, report_entities) where
    pending entries are (url_hash, report, text, cache_path)."""
    report_index = {}
    report_entities = defaultdict(dict)
    for event in events:
        for report in event.get("reports", []):
            url_hash = report.get("url_hash") or ""
            if url_hash:
                report_index.setdefault(url_hash, report)
        for entity_id, meta in entity_meta_by_event.get(event.get("event_id"), {}).items():
            for report in event.get("reports", []):
                report_entities[report.get("url_hash") or ""].setdefault(entity_id, meta)
    annotations, pending = {}, []
    for url_hash, report in report_index.items():
        text = report_text(report)
        if text is None:
            print(f"  [skip] no body text for {url_hash} ({report.get('domain')})")
            continue
        cache = cache_path(model, text)
        cached = None if refresh else load_cached_annotation(cache)
        if cached is not None:
            annotations[url_hash] = {
                "report_id": report.get("report_id") or url_hash,
                "domain": report.get("domain") or "",
                "frames": cached if isinstance(cached, list) else [],
                "from_cache": True,
            }
        else:
            pending.append((url_hash, report, text, cache))
    return annotations, pending, report_entities


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--limit", type=int, default=0, help="process at most N multi-source events (0 = all)")
    parser.add_argument("--events", default="", help="comma/space separated event ids to process")
    parser.add_argument("--dry-run", action="store_true", help="list targets and pending annotations without calling the LLM")
    parser.add_argument("--refresh", action="store_true", help="re-annotate every report and recompute target verdicts")
    parser.add_argument("--dump-kg", action="store_true", help="write per-event Turtle KGs to processed/framing_kg/")
    args = parser.parse_args()

    frame_index = load_framenet_index()
    graph, comparisons = load_inputs()
    only_ids = [part for part in re.split(r"[,\s]+", args.events.strip()) if part]
    events = collect_target_events(comparisons, only_ids, args.limit)
    if not events:
        sys.exit("no multi-source events to process")
    entity_meta_by_event = {
        event.get("event_id"): event_entity_meta(event, graph.get("nodes", []))
        for event in events
    }
    print(
        f"target events: {len(events)} | attributes: {', '.join(TARGET_ATTRIBUTES)} | "
        f"frames in vocabulary: {len(frame_index)}"
    )

    model = llm_judge.config()["model"]
    annotations, pending, report_entities = annotate_reports(
        events, entity_meta_by_event, frame_index, model, args.refresh
    )
    cache_hits = sum(1 for record in annotations.values() if record.get("from_cache"))
    print(f"reports: {cache_hits} cached | {len(pending)} pending annotation")

    if args.dry_run:
        for event in events:
            meta = entity_meta_by_event.get(event.get("event_id"), {})
            annotated = event_hashes(event) & set(annotations)
            print(
                f"[dry-run] {event.get('event_id')} | "
                f"{len(event.get('reports', []))} report(s) | {len(meta)} entities | "
                f"{len(annotated)} annotated"
            )
        return

    if pending:
        if not llm_judge.is_configured():
            sys.exit(
                "framing annotator not configured. Set DEEPSEEK_API_KEY (see "
                "backend/.env.example); if the openai SDK is missing run: "
                "pip install -r backend/requirements.txt"
            )
        client = _deepseek_client()
        for index, (url_hash, report, text, cache) in enumerate(pending, 1):
            try:
                raw = deepseek_annotate(
                    client, model, text, report_entities.get(url_hash, {}), frame_index
                )
            except Exception as exc:  # degrade: the report simply stays unannotated
                print(
                    f"  [{index}/{len(pending)}] {url_hash} annotation failed: "
                    f"{type(exc).__name__}: {exc}"
                )
                continue
            normalized = normalize_annotation(raw, report_entities.get(url_hash, {}), frame_index)
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(normalized, ensure_ascii=False), encoding="utf-8")
            annotations[url_hash] = {
                "report_id": report.get("report_id") or url_hash,
                "domain": report.get("domain") or "",
                "frames": normalized,
                "from_cache": False,
            }
            element_total = sum(len(frame.get("elements") or []) for frame in normalized)
            print(
                f"  [{index}/{len(pending)}] {url_hash} ({report.get('domain')}) -> "
                f"{len(normalized)} frame(s), {element_total} element(s)"
            )

    if Graph is None:
        sys.exit("rdflib 未安装，请先: pip install -r backend/requirements.txt")

    target_ids = {event.get("event_id") for event in events}
    existing = load_existing_verdicts()
    if args.refresh:
        existing = {
            key: entry for key, entry in existing.items()
            if entry.get("event_id") not in target_ids
        }
    verdict_counts = Counter()
    for event in events:
        event_id = event.get("event_id")
        event_annotations = {
            url_hash: record
            for url_hash, record in annotations.items()
            if url_hash in event_hashes(event)
        }
        verdicts_for_event, kg = compute_event_verdicts(
            event, event_annotations, entity_meta_by_event.get(event_id, {}), model
        )
        if args.dump_kg:
            KG_DIR.mkdir(parents=True, exist_ok=True)
            kg_target = KG_DIR / f"{_safe_id(event_id)}.ttl"
            kg.serialize(destination=str(kg_target), format="turtle")
            print(f"kg dumped: {kg_target}")
        for key, entry in verdicts_for_event.items():
            existing[key] = entry
            verdict_counts[entry["verdict"]] += 1
        print(f"{event_id} -> {len(verdicts_for_event)} slot verdict(s)")

    payload = {
        "schema_version": SIDECAR_SCHEMA,
        "annotator": ANNOTATOR,
        "method": METHOD,
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "framenet_index": "scripts/framenet_index.json",
        "generated_at": utc_now_iso(),
        "stats": {
            "events": len(events),
            "reports_annotated": len(annotations),
            "annotation_cache_hits": cache_hits,
            "verdicts": dict(verdict_counts),
        },
        "verdicts": existing,
    }
    atomic_write_json(OUTPUT_PATH, payload)
    print(
        f"done. verdicts written: {sum(verdict_counts.values())} {dict(verdict_counts)} | "
        f"sidecar: {OUTPUT_PATH}"
    )


if __name__ == "__main__":
    main()










