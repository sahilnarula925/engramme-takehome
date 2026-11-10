from collections import Counter
import unittest

from service.scheduling import FairOrder


class SchedulingTests(unittest.TestCase):
    def test_many_sources_do_not_buy_one_user_more_capacity(self):
        candidates = [{"id": i, "user_id": "busy", "lane": f"source-{i}"} for i in range(10)]
        candidates.append({"id": 11, "user_id": "small", "lane": "mail"})
        order, counts = FairOrder(), Counter()
        for _ in range(200):
            selected = order.choose(candidates)
            counts[selected["user_id"]] += 1
            order.served(selected)
        self.assertEqual(counts["busy"], counts["small"])

    def test_live_and_summary_lanes_progress_during_backfill(self):
        candidates = [{"id": i, "user_id": "same-user", "lane": lane}
                      for i, lane in enumerate(("backfill", "mail", "~summary"))]
        order, counts = FairOrder(), Counter()
        for _ in range(90):
            selected = order.choose(candidates)
            counts[selected["lane"]] += 1
            order.served(selected)
        self.assertEqual(set(counts.values()), {30})

    def test_idle_capacity_is_available_and_new_lane_gets_a_turn(self):
        backlog = {"id": 1, "user_id": "user", "lane": "backfill"}
        live = {"id": 2, "user_id": "user", "lane": "mail"}
        order = FairOrder()
        for _ in range(100):
            self.assertEqual(order.choose([backlog]), backlog)
            order.served(backlog)
        self.assertEqual(order.choose([backlog, live]), live)

    def test_empty_cached_lane_does_not_idle_ready_work(self):
        empty = {"id": 1, "user_id": "user", "lane": "empty"}
        ready = {"id": 2, "user_id": "user", "lane": "mail"}
        candidates = [empty, ready]
        selected = FairOrder().next_ready(candidates, lambda lane: ready if lane == ready else None)
        self.assertEqual(selected, ready)
        self.assertEqual(candidates, [ready])
