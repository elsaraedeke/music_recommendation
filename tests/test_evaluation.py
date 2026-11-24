"""Hand-calculated evaluation checks; no database connection is required."""

import unittest
from datetime import datetime, timedelta, timezone
from math import log2

from evaluation.recommendations import listening_targets, ranking_metrics, summarize


def listener(uid, tracks, relevant, cold=False, k=2):
    return {"user_id": uid, "cold_start": cold, "relevant_tracks": relevant,
            "methods": {"fixture": {"tracks": tracks, **ranking_metrics(tracks, relevant, k)}}}


class EvaluationTests(unittest.TestCase):
    def test_precision_recall_and_discounted_rank_are_hand_calculated(self):
        result = ranking_metrics(["a", "miss", "b"], ["a", "b", "c"], k=4)
        self.assertEqual(result["hits"], 2)
        self.assertEqual(result["precision"], 0.5)  # Divide by requested K, not returned length.
        self.assertAlmostEqual(result["recall"], 2 / 3)
        self.assertAlmostEqual(result["ndcg"], 1.5 / (1 + 1 / log2(3) + 0.5))

    def test_empty_results_and_no_targets_have_distinct_meanings(self):
        self.assertEqual(ranking_metrics([], ["a"], k=2),
                         {"hits": 0, "precision": 0, "recall": 0, "ndcg": 0})
        self.assertEqual(ranking_metrics(["a"], [], k=2),
                         {"hits": 0, "precision": 0, "recall": None, "ndcg": None})

    def test_duplicate_oversized_and_invalid_k_lists_are_rejected(self):
        for tracks, k in [(["a", "a"], 2), (["a", "b"], 1), ([], 0)]:
            with self.subTest(tracks=tracks, k=k), self.assertRaises(ValueError):
                ranking_metrics(tracks, ["a"], k)

    def test_quality_excludes_no_target_users_but_coverage_and_fullness_include_them(self):
        rows = [listener(1, ["a", "b"], ["a"]),
                listener(2, ["b", "c"], [], cold=True),
                listener(3, [], ["d"])]
        result = summarize(rows, ["fixture"], catalog_tracks=10, k=2)
        all_users = result["all"]["fixture"]
        self.assertEqual((all_users["users"], all_users["eligible_users"], all_users["excluded_users"]), (3, 2, 1))
        self.assertEqual((all_users["hits"], all_users["precision"], all_users["recall"], all_users["ndcg"]),
                         (1, 0.25, 0.5, 0.5))
        self.assertEqual(all_users["distinct_recommended_tracks"], 3)
        self.assertEqual(all_users["catalog_coverage"], 0.3)
        self.assertAlmostEqual(all_users["full_list_rate"], 2 / 3)
        self.assertAlmostEqual(all_users["mean_returned"], 4 / 3)
        self.assertEqual(result["warm"]["fixture"]["full_list_rate"], 0.5)
        cold = result["cold_start"]["fixture"]
        self.assertIsNone(cold["precision"])
        self.assertEqual((cold["catalog_coverage"], cold["full_list_rate"], cold["mean_returned"]), (0.2, 1, 2))

    def test_short_lists_count_against_requested_k(self):
        result = summarize([listener(1, ["a"], ["a"])], ["fixture"], 10, 2)["all"]["fixture"]
        self.assertEqual((result["precision"], result["full_list_rate"], result["mean_returned"]), (0.5, 0, 1))

    def test_empty_cohort_and_empty_catalog_do_not_divide_by_zero(self):
        result = summarize([listener(1, [], [])], ["fixture"], 0, 2)
        warm = result["warm"]["fixture"]
        self.assertIsNone(warm["catalog_coverage"])
        self.assertEqual((warm["full_list_rate"], warm["mean_returned"]), (0, 0))
        empty = result["cold_start"]["fixture"]
        self.assertEqual((empty["users"], empty["distinct_recommended_tracks"]), (0, 0))
        for metric in ("precision", "recall", "ndcg", "catalog_coverage", "full_list_rate", "mean_returned"):
            self.assertIsNone(empty[metric])
        populated_catalog = summarize([listener(1, [], [])], ["fixture"], 10, 2)
        self.assertEqual(populated_catalog["all"]["fixture"]["catalog_coverage"], 0)

    def test_targets_respect_cutoff_end_completion_deduplication_and_prior_history(self):
        cutoff = datetime(2026, 7, 1, tzinfo=timezone.utc)
        end = datetime(2026, 9, 1, tzinfo=timezone.utc)
        events = [(1, "heard", cutoff - timedelta(microseconds=1), 0),
                  (1, "heard", cutoff, 1000),
                  (1, "boundary", cutoff, 800),
                  (1, "boundary", cutoff + timedelta(days=1), 1000),
                  (1, "late", end - timedelta(microseconds=1), 1000),
                  (1, "outside", end, 1000),
                  (1, "partial", cutoff, 799),
                  (1, "offset", datetime.fromisoformat("2026-06-30T20:00:00-04:00"), 800)]
        durations = {track: 1000 for _, track, _, _ in events}
        seen, targets = listening_targets([1, 2], durations, events, cutoff, end)
        self.assertEqual(seen, {1: {"heard"}, 2: set()})
        self.assertEqual(targets, {1: {"boundary", "late", "offset"}, 2: set()})


if __name__ == "__main__":
    unittest.main()
