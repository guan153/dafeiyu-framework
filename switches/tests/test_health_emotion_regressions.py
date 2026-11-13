"""Offline regressions for probe expectations and emotion write ownership."""

import asyncio
import os
import sys
import uuid
import unittest
from pathlib import Path
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(os.environ.get("WHALE_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
# Import after selecting the isolated source or deployment tree.
from whale_core import health  # noqa: E402
from whale_core.emotion import EmotionHub  # noqa: E402


class HttpExpectationTests(unittest.TestCase):
    def probe(self, ok, status, expect=200):
        with patch.object(
            health, "_http_get", return_value=(ok, f"HTTP {status}", status)
        ):
            return asyncio.run(
                health.http_probe("fixture", "http://offline.invalid", expect=expect)
            )

    def test_exact_200_passes(self):
        self.assertTrue(self.probe(True, 200).ok)

    def test_unexpected_204_fails(self):
        result = self.probe(True, 204)
        self.assertFalse(result.ok)
        self.assertIn("expected 200", result.detail)

    def test_explicit_204_passes(self):
        self.assertTrue(self.probe(True, 204, 204).ok)

    def test_unexpected_200_fails(self):
        self.assertFalse(self.probe(True, 200, 204).ok)

    def test_http_error_fails(self):
        self.assertFalse(self.probe(False, 401).ok)
        self.assertFalse(self.probe(False, 503).ok)

    def test_transport_failure_fails(self):
        self.assertFalse(self.probe(False, 0).ok)

    def test_disabled_expectation_keeps_success(self):
        self.assertTrue(self.probe(True, 204, 0).ok)


class EmotionOwnershipTests(unittest.TestCase):
    def setUp(self):
        test_dir = (
            Path(__file__).resolve().parents[1]
            / ".test_tmp"
            / "health-emotion-regressions"
        )
        test_dir.mkdir(parents=True, exist_ok=True)
        self.path = test_dir / f"{uuid.uuid4().hex}.json"
        self.hub = EmotionHub(state_path=self.path)

    def test_unclaimed_writer_is_rejected_without_mutation(self):
        before = self.hub.snapshot()
        self.assertIsNone(self.hub.update("unclaimed", {"mood": "开心"}))
        self.assertEqual(before["version"], self.hub.snapshot()["version"])
        self.assertFalse(self.path.exists())

    def test_claimed_writer_can_update(self):
        self.assertTrue(self.hub.claim("owner")[0])
        self.assertEqual(self.hub.update("owner", {"mood": "开心"})["mood"], "开心")

    def test_other_writer_is_rejected(self):
        self.hub.claim("owner")
        self.assertIsNone(self.hub.update("other", {"mood": "低落"}))

    def test_release_requires_new_claim(self):
        self.hub.claim("owner")
        self.hub.update("owner", {"mood": "开心"})
        self.assertTrue(self.hub.release("owner"))
        self.assertIsNone(self.hub.update("owner", {"mood": "低落"}))
        self.assertIsNone(self.hub.update("other", {"mood": "低落"}))
        self.assertTrue(self.hub.claim("other")[0])
        self.assertEqual(self.hub.update("other", {"mood": "平静"})["source"], "other")

    def test_observation_does_not_mutate_emotion(self):
        self.hub.observe("observer", "synthetic observation", mood_hint="低落")
        self.assertEqual(self.hub.snapshot()["version"], 0)
        self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
