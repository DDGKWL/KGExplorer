"""Offline tests for the DORIS framing pipeline (scripts/run_doris_framing.py).

No network: DeepSeek annotation is bypassed entirely - tests feed normalized
fixture annotations straight into the KG/SPARQL/verdict stages and check that
sidecar keys stay isomorphic to llm_judge.verdict_key().
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import app as app_module  # noqa: E402  (path bootstrap must run first)
from app import app  # noqa: E402
import llm_judge  # noqa: E402
import run_doris_framing as doris  # noqa: E402

ENTITY_META = {
    "person:donald-trump": {
        "label": "Donald Trump",
        "aliases": ["Trump", "U.S. President"],
    },
    "person:iranian-mediators": {
        "label": "Iranian mediators",
        "aliases": ["mediators"],
    },
}

FRAME_INDEX = {
    "Attack": ["Assailant", "Victim", "Target"],
    "Meet_with": ["Participant_1", "Participant_2"],
    "Threatening": ["Speaker", "Addressee"],
}


def ann(frames):
    return {"report_id": "r", "domain": "d", "frames": frames, "from_cache": False}


def frame(name, trigger, sentence, elements):
    return {"frame": name, "trigger": trigger, "sentence": sentence, "elements": elements}


def fe(name, entity, surface):
    return {"fe": name, "entity": entity, "surface": surface}


def event_fixture():
    """Two reports, a roles slot (assailant vs participant) and an attitude slot
    whose evidence mentions Trump (report:a) and the mediators (report:b)."""
    return {
        "event_id": "event:test-1",
        "canonical_name": "Test event",
        "reports": [
            {"report_id": "report:a", "url_hash": "hash-a", "domain": "a.com"},
            {"report_id": "report:b", "url_hash": "hash-b", "domain": "b.com"},
        ],
        "comparisons": [
            {
                "attribute": "roles",
                "status": "different",
                "reports": {
                    "report:a": [
                        {"canonical": "person:donald-trump::assailant", "display": "Donald Trump · assailant"}
                    ],
                    "report:b": [
                        {"canonical": "person:donald-trump::participant", "display": "Donald Trump · participant"}
                    ],
                },
            },
            {
                "attribute": "attitude",
                "status": "consensus",
                "reports": {
                    "report:a": [{"canonical": "neutral", "evidence": "Trump threatened Iran."}],
                    "report:b": [{"canonical": "neutral", "evidence": "Mediators shuttled all day."}],
                },
            },
        ],
    }


class VerdictKeyTest(unittest.TestCase):
    def test_key_matches_llm_judge_and_expected_string(self):
        reports = {
            "report:a": [{"canonical": "Person:X::Role"}],
            "report:b": [{"canonical": "person:y::role"}],
        }
        key = llm_judge.verdict_key("event:e", "roles", reports)
        self.assertEqual(
            key,
            "event:e::roles::report:a:person:x::role;report:b:person:y::role",
        )

    def test_compute_event_verdicts_use_llm_judge_keys(self):
        event = event_fixture()
        annotations = {
            "hash-a": ann([frame("Attack", "annihilate", "s1", [fe("Assailant", "person:donald-trump", "Trump")])]),
            "hash-b": ann([frame("Meet_with", "met", "s2", [fe("Participant_1", "person:donald-trump", "Trump")])]),
        }
        verdicts, _ = doris.compute_event_verdicts(event, annotations, ENTITY_META, "deepseek-chat")
        for comparison in event["comparisons"]:
            key = llm_judge.verdict_key(
                event["event_id"], comparison["attribute"], comparison["reports"]
            )
            self.assertIn(key, verdicts)


class NormalizeAnnotationTest(unittest.TestCase):
    def test_fuzzy_vocabulary_and_entity_filtering(self):
        raw = {"annotations": [
            frame("Atack", "annihilate", "He may annihilate Iran.", [
                fe("Assailant", "person:donald-trump", "Trump"),
                fe("Bogus", "person:donald-trump", "Trump"),
                fe("Assailant", "person:unknown", "Mystery"),
            ]),
            frame("FooFrame", "x", "y", [fe("Assailant", "person:donald-trump", "Trump")]),
            frame("Threatening", "threatened", "Trump threatened Iran.", [
                fe("Speaker", "Trump", "Trump"),
            ]),
        ]}
        normalized = doris.normalize_annotation(raw, ENTITY_META, FRAME_INDEX)
        self.assertEqual([row["frame"] for row in normalized], ["Attack", "Threatening"])
        self.assertEqual(
            normalized[0]["elements"],
            [{"fe": "Assailant", "entity": "person:donald-trump", "surface": "Trump"}],
        )
        # entity resolved through the alias surface "Trump"
        self.assertEqual(normalized[1]["elements"][0]["entity"], "person:donald-trump")

    def test_malformed_payloads_degrade_to_empty(self):
        self.assertEqual(doris.normalize_annotation(None, ENTITY_META, FRAME_INDEX), [])
        self.assertEqual(doris.normalize_annotation({"annotations": "x"}, ENTITY_META, FRAME_INDEX), [])
        self.assertEqual(doris.normalize_annotation({}, ENTITY_META, FRAME_INDEX), [])


@unittest.skipIf(doris.Graph is None, "rdflib is not installed")
class KgQueryTest(unittest.TestCase):
    def test_strict_fe_conflict(self):
        annotations = {
            "hash-a": ann([frame("Attack", "annihilate", "s1", [fe("Assailant", "person:donald-trump", "Trump")])]),
            "hash-b": ann([frame("Attack", "annihilate", "s2", [fe("Victim", "person:donald-trump", "Trump")])]),
        }
        kg = doris.build_event_kg("event:test-1", annotations, ENTITY_META)
        fe_conflicts, narrative, consistent = doris.query_framing_rows(kg)
        self.assertEqual(len(fe_conflicts), 1)
        self.assertEqual(fe_conflicts[0]["entity"], "person:donald-trump")
        self.assertEqual(fe_conflicts[0]["fe"], ("Assailant", "Victim"))
        self.assertEqual(narrative, [])
        self.assertEqual(consistent, [])

    def test_narrative_role_divergence(self):
        annotations = {
            "hash-a": ann([frame("Attack", "annihilate", "s1", [fe("Assailant", "person:donald-trump", "Trump")])]),
            "hash-b": ann([frame("Meet_with", "met", "s2", [fe("Participant_1", "person:donald-trump", "Trump")])]),
        }
        kg = doris.build_event_kg("event:test-1", annotations, ENTITY_META)
        fe_conflicts, narrative, consistent = doris.query_framing_rows(kg)
        self.assertEqual(fe_conflicts, [])
        self.assertEqual(len(narrative), 1)
        self.assertEqual(narrative[0]["frames"], ("Attack", "Meet_with"))
        self.assertEqual(narrative[0]["fe"], ("Assailant", "Participant_1"))

    def test_same_frame_same_fe_is_consistent_not_conflict(self):
        annotations = {
            "hash-a": ann([frame("Threatening", "threatened", "s1", [fe("Speaker", "person:donald-trump", "Trump")])]),
            "hash-b": ann([frame("Threatening", "threatened", "s2", [fe("Speaker", "person:donald-trump", "Trump")])]),
        }
        kg = doris.build_event_kg("event:test-1", annotations, ENTITY_META)
        fe_conflicts, narrative, consistent = doris.query_framing_rows(kg)
        self.assertEqual(fe_conflicts, [])
        self.assertEqual(narrative, [])
        self.assertEqual(len(consistent), 1)
        self.assertEqual(consistent[0]["fe"], ("Speaker", "Speaker"))

    def test_provenance_is_recorded(self):
        kg = doris.build_event_kg("event:test-1", {
            "hash-a": ann([frame("Attack", "annihilate", "s", [fe("Assailant", "person:donald-trump", "Trump")])]),
        }, ENTITY_META)
        turtle = kg.serialize(format="turtle")
        self.assertIn("prov:wasAttributedTo", turtle)
        self.assertIn("deepseek-chat", turtle)


class ComputeVerdictsTest(unittest.TestCase):
    def test_conflict_end_to_end(self):
        event = event_fixture()
        annotations = {
            "hash-a": ann([frame(
                "Attack", "annihilate", "He may annihilate the Islamic Republic.",
                [fe("Assailant", "person:donald-trump", "Trump")])]),
            "hash-b": ann([frame(
                "Meet_with", "met", "Officials met on Tuesday.",
                [fe("Participant_1", "person:donald-trump", "Trump")])]),
        }
        verdicts, _ = doris.compute_event_verdicts(event, annotations, ENTITY_META, "deepseek-chat")
        roles_key = llm_judge.verdict_key("event:test-1", "roles", event["comparisons"][0]["reports"])
        entry = verdicts[roles_key]
        self.assertEqual(entry["verdict"], "framing_conflict")
        self.assertEqual(entry["annotator"], "deepseek")
        self.assertEqual(entry["rule_status"], "differ")
        self.assertEqual({item["domain"] for item in entry["evidence"]}, {"a.com", "b.com"})
        self.assertEqual({item["report_id"] for item in entry["evidence"]}, {"report:a", "report:b"})
        # SPARQL pair-joins must not leak duplicate evidence rows.
        dedupe_keys = [
            (item["report_id"], item["frame"], item["fe"], item["sentence"])
            for item in entry["evidence"]
        ]
        self.assertEqual(len(dedupe_keys), len(set(dedupe_keys)))
        attitude_key = llm_judge.verdict_key(
            "event:test-1", "attitude", event["comparisons"][1]["reports"]
        )
        self.assertEqual(verdicts[attitude_key]["verdict"], "framing_conflict")

    def test_consistent_and_insufficient(self):
        event = event_fixture()
        consistent_annotations = {
            "hash-a": ann([frame("Threatening", "threatened", "s1", [fe("Speaker", "person:donald-trump", "Trump")])]),
            "hash-b": ann([frame("Threatening", "threatened", "s2", [fe("Speaker", "person:donald-trump", "Trump")])]),
        }
        verdicts, _ = doris.compute_event_verdicts(event, consistent_annotations, ENTITY_META, "deepseek-chat")
        roles_key = llm_judge.verdict_key("event:test-1", "roles", event["comparisons"][0]["reports"])
        self.assertEqual(verdicts[roles_key]["verdict"], "framing_consistent")
        # single-source coverage -> insufficient with no evidence rows
        verdicts_single, _ = doris.compute_event_verdicts(
            event, {"hash-a": consistent_annotations["hash-a"]}, ENTITY_META, "deepseek-chat"
        )
        self.assertEqual(verdicts_single[roles_key]["verdict"], "insufficient")
        self.assertEqual(verdicts_single[roles_key]["evidence"], [])

    def test_no_annotations_means_no_verdicts(self):
        # Slots without any framing signal stay absent from the sidecar so the
        # frontend keeps them on rule colors (never a fabricated verdict).
        verdicts, _ = doris.compute_event_verdicts(
            event_fixture(), {"hash-a": ann([]), "hash-b": ann([])}, ENTITY_META, "deepseek-chat"
        )
        self.assertEqual(verdicts, {})


class SidecarIoTest(unittest.TestCase):
    def test_atomic_write_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "sub" / "out.json"
            doris.atomic_write_json(target, {"verdicts": {"a": 1}})
            self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["verdicts"]["a"], 1)
            self.assertEqual(list(target.parent.glob("*.tmp")), [])


class FramingEndpointTest(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self._original = app_module.NEWS_FRAMING_VERDICTS

    def tearDown(self):
        app_module.NEWS_FRAMING_VERDICTS = self._original

    def test_missing_sidecar_returns_empty_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            app_module.NEWS_FRAMING_VERDICTS = Path(tmp) / "missing.json"
            response = self.client.get("/api/news/framing-verdicts")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["schema_version"], "news-framing-verdicts-1")
        self.assertEqual(payload["verdicts"], {})

    def test_existing_sidecar_is_served(self):
        payload = {
            "schema_version": "news-framing-verdicts-1",
            "annotator": "deepseek",
            "verdicts": {"k": {"verdict": "framing_conflict", "evidence": []}},
        }
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "framing_verdicts.json"
            target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            app_module.NEWS_FRAMING_VERDICTS = target
            response = self.client.get("/api/news/framing-verdicts")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["verdicts"]["k"]["verdict"], "framing_conflict")


if __name__ == "__main__":
    unittest.main()



