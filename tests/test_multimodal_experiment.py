import runpy
import unittest
from pathlib import Path

import numpy as np


class MultimodalExperimentTest(unittest.TestCase):
    def test_view_deduplication_common_pool_and_invalid_scores(self):
        module = runpy.run_path(
            str(Path(__file__).parents[1] / "scripts/compare_multimodal_embeddings.py")
        )
        rank = module["rank_objects"]
        rows = [dict(object_id=oid) for oid in ["a", "b", "a", "excluded", "c"]]
        self.assertEqual(
            rank(np.array([0.1, 0.5, 0.9, 1.0, 0.5]), rows, {"a", "b", "c"}),
            ["a", "b", "c"],
        )
        with self.assertRaisesRegex(ValueError, "Invalid scores"):
            rank(np.array([np.nan] * 5), rows, {"a"})


if __name__ == "__main__":
    unittest.main()
