import sys
import unittest
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app import app  # noqa: E402


class GuqinExplorerApiTest(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_case_data_includes_machine_audit(self):
        response = self.client.get("/api/case-data/guqin")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["stats"]["records"], 154)
        self.assertEqual(data["stats"]["conflicts"], 74)
        self.assertEqual(data["evidence_audit"]["ready_for_expert_review"], 23)
        audited_slots = [
            slot
            for artifact in data["artifacts"]
            for slot in artifact.get("caus", [])
            if slot.get("audit")
        ]
        self.assertEqual(len(audited_slots), 74)

    def test_case_data_includes_lens_projection(self):
        response = self.client.get("/api/case-data/guqin")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["projection"]["ready"])
        self.assertEqual(data["projection"]["point_count"], len(data["artifacts"]))
        sample = next(item for item in data["artifacts"] if item.get("projection"))
        self.assertIn("x", sample["projection"]["conflict"])
        self.assertIn("y", sample["projection"]["attribute"])

    def test_explorer_filters_ready_gq_evidence(self):
        response = self.client.get(
            "/api/guqin/explorer?readiness=P1_expert_review&source=Gq"
        )
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertGreater(data["result_counts"]["conflicts"], 0)
        for conflict in data["conflicts"]:
            self.assertEqual(conflict["review_readiness"], "P1_expert_review")
            self.assertIn("Gq", conflict["sources"])

    def test_quality_issue_is_attached_to_record(self):
        response = self.client.get("/api/guqin/records/Yjzq00033")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["record_id"], "Yjzq00033")
        self.assertEqual(data["quality_issues"][0]["issue_type"], "probable_missing_digit")

    def test_conflict_detail_preserves_source_assertions(self):
        explorer = self.client.get(
            "/api/guqin/explorer?readiness=P1_expert_review"
        ).get_json()
        conflict = explorer["conflicts"][0]
        response = self.client.get(
            f"/api/guqin/conflicts/{conflict['conflict_id']}"
        )
        self.assertEqual(response.status_code, 200)
        detail = response.get_json()
        self.assertEqual(detail["conflict_id"], conflict["conflict_id"])
        self.assertGreaterEqual(len(detail["assertions"]), 2)
        self.assertTrue(all(item["text_url"] for item in detail["assertions"]))


if __name__ == "__main__":
    unittest.main()
