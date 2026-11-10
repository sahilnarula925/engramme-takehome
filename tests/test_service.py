"""Black-box contract tests against the running Compose service (no host dependencies)."""
import concurrent.futures
import json
import sys
import time
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "starter-kit"))
from harness_lib.reference import Reference
from harness_lib.checks import compare_user
from harness_lib.service import ServiceClient, http
from harness_lib.workload import Event
from memkit import core

URL = "http://localhost:8080"
FACTS = ["Ana is leading the Atlas project.", "Ben joined the Atlas project.",
         "The Atlas project budget is 120000 dollars.", "The Atlas project deadline is March 3."]


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.user = "test-" + uuid.uuid4().hex
        self.service = ServiceClient(URL)
        self.reference = Reference("permanent")

    def event(self, item, facts=FACTS, version=1, source="gmail", kind="upsert"):
        event = Event(self.user, source, item, version, kind,
                      " ".join(facts) if kind == "upsert" else None, "2026-01-01T00:00:00Z")
        self.service.send_event(event)
        self.reference.apply_event(event)
        return event

    def converge(self, timeout=10):
        end = time.monotonic() + timeout
        while True:
            memories = self.service.list_memories(self.user)
            errors = compare_user(self.user, self.reference.user_state(self.user), memories)
            if not errors:
                return memories
            if time.monotonic() >= end:
                self.fail("; ".join(errors))
            time.sleep(0.1)

    def test_summary_recall_and_pagination(self):
        self.event("one")
        memories = self.converge()
        self.assertEqual(len(memories), 5)
        recalled = self.service.recall(self.user, FACTS[0], 1)
        self.assertEqual(recalled[0]["text"], FACTS[0])
        self.assertAlmostEqual(recalled[0]["score"], 1)
        seen, cursor = [], ""
        while True:
            page = http("GET", f"{URL}/v1/users/{self.user}/memories?limit=2&cursor={cursor}").body
            seen.extend(m["id"] for m in page["memories"])
            cursor = page["next_cursor"]
            if not cursor:
                break
        self.assertEqual(seen, [m["id"] for m in memories])
        for m in memories:
            self.assertEqual(self.service.get_memory(self.user, m["id"]), m)

    def test_duplicate_and_older_events_are_noops(self):
        event = self.event("one", FACTS[:1], version=4)
        before = self.converge()
        with concurrent.futures.ThreadPoolExecutor(16) as pool:
            list(pool.map(lambda _: self.service.send_event(event), range(32)))
        self.event("one", FACTS[1:], version=3)
        self.assertEqual(self.converge(), before)

    def test_cross_source_fact_is_stored_once(self):
        self.event("one", FACTS[:1])
        self.event("two", FACTS[:1], source="calendar")
        memories = self.converge()
        self.assertEqual(len(memories), 1)
        self.assertEqual(len(memories[0]["lineage"]["items"]), 2)

    def test_new_backer_updates_existing_summary_lineage(self):
        self.event("one", FACTS[:3])
        before = next(m for m in self.converge() if m["kind"] == "summary")
        self.event("two", FACTS[:1], source="calendar")
        after = next(m for m in self.converge() if m["kind"] == "summary")
        self.assertEqual(after["id"], before["id"])
        self.assertEqual(after["text"], before["text"])
        self.assertEqual(len(after["lineage"]["items"]), 2)

    def test_two_facts_have_no_summary_and_users_are_isolated(self):
        self.event("one", FACTS[:2])
        self.assertEqual(len(self.converge()), 2)
        self.assertEqual(self.service.list_memories(self.user + "-other"), [])

    def test_recall_matches_exact_cosine_oracle_and_k_limits(self):
        self.event("one")
        memories = self.converge()
        query = "Atlas budget deadline"
        scored = [(m, core.cosine(core.embed(query), core.embed(m["text"]))) for m in memories]
        expected = sorted(scored, key=lambda pair: (-pair[1], pair[0]["id"]))
        actual = self.service.recall(self.user, query, 100)
        self.assertEqual([m["id"] for m in actual], [m["id"] for m, _ in expected])
        for got, (_, score) in zip(actual, expected):
            self.assertAlmostEqual(got["score"], score)
        self.assertEqual(self.service.recall(self.user, query, 0), [])
        self.assertEqual(len(self.service.recall(self.user, query, 2)), 2)

    def test_invalid_event_is_rejected(self):
        for body in ({}, {"user_id": self.user}, [], {"version": True}):
            self.assertEqual(http("POST", URL + "/v1/events", body).status, 400)
        self.assertIsNone(self.service.get_memory(self.user, "missing"))

    def forget(self, item, source="gmail"):
        key = self.user, source, item
        self.service.user_delete(key)
        self.reference.user_delete(key)

    def assert_clean(self, forbidden, item, old_ids):
        responses = [self.service.list_memories(self.user), self.service.recall(self.user, forbidden, 100)]
        responses.append([m for mid in old_ids if (m := self.service.get_memory(self.user, mid))])
        for memories in responses:
            for memory in memories:
                self.assertNotIn(forbidden, memory["text"])
                self.assertNotIn({"source_id": "gmail", "item_id": item}, memory["lineage"]["items"])

    def test_edit_preserves_unchanged_ids_and_updates_summary(self):
        self.event("one", FACTS[:3])
        before = {m.get("fact_key"): m["id"] for m in self.converge() if m["kind"] == "atomic"}
        self.event("one", [FACTS[0], FACTS[1], FACTS[3]], version=2)
        after = {m.get("fact_key"): m["id"] for m in self.converge() if m["kind"] == "atomic"}
        for key in before.keys() & after.keys():
            self.assertEqual(before[key], after[key])

    def test_edit_to_zero_extracted_facts_removes_memories(self):
        self.event("one")
        self.assertEqual(len(self.converge()), 5)
        self.event("one", ["Thanks for the update."], version=2)
        self.assertEqual(self.converge(), [])

    def test_forget_shared_fact_then_last_backer_is_immediately_clean(self):
        self.event("one", FACTS[:3])
        self.event("two", FACTS[:1], source="calendar")
        before = self.converge()
        ids = [m["id"] for m in before]
        atomic_id = next(m["id"] for m in before if m["text"] == FACTS[0])
        self.forget("one")
        self.assert_clean(FACTS[1], "one", ids)
        memories = self.converge()
        self.assertEqual(memories[0]["id"], atomic_id)
        self.forget("two", "calendar")
        self.assertEqual(self.service.list_memories(self.user), [])
        self.converge()

    def test_unknown_forget_is_permanent_even_for_newer_version(self):
        self.forget("unknown")
        self.event("unknown", version=99)
        self.assertEqual(self.converge(), [])

    def test_forget_rebuilds_summary_from_remaining_three_facts(self):
        self.event("one", FACTS[:1])
        self.event("two", FACTS[1:])
        before = self.converge()
        summary_id = next(m["id"] for m in before if m["kind"] == "summary")
        self.forget("one")
        self.assert_clean(FACTS[0], "one", [m["id"] for m in before])
        after = self.converge()
        self.assertEqual(len(after), 4)
        self.assertEqual(next(m["id"] for m in after if m["kind"] == "summary"), summary_id)

    def test_disconnect_reconnect_restores_except_forgotten_items(self):
        first = self.event("one", FACTS[:2])
        self.event("two", FACTS[2:])
        before = self.converge()
        self.forget("two")
        self.service.disconnect(self.user, "gmail")
        self.reference.disconnect(self.user, "gmail")
        self.assertEqual(self.service.list_memories(self.user), [])
        for m in before:
            self.assertIsNone(self.service.get_memory(self.user, m["id"]))
        self.service.connect(self.user, "gmail")
        self.reference.connect(self.user, "gmail")
        self.service.send_event(first)
        self.event("two", FACTS[2:], version=10)
        self.assertEqual(len(self.converge()), 2)

    def test_concurrent_edit_and_forget_never_resurrect(self):
        self.event("one")
        self.converge()
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            edit = pool.submit(self.event, "one", FACTS[1:], 2)
            delete = pool.submit(self.forget, "one")
            edit.result()
            delete.result()
        self.assertEqual(self.service.list_memories(self.user), [])
        self.assertEqual(self.converge(), [])

    def test_upstream_delete_is_versioned_and_can_be_restored(self):
        self.event("one", version=2)
        self.converge()
        self.event("one", kind="delete", version=3)
        self.assertEqual(self.service.list_memories(self.user), [])
        self.converge()
        self.event("one", version=2)
        self.assertEqual(self.converge(), [])
        self.event("one", version=4)
        self.assertEqual(len(self.converge()), 5)


if __name__ == "__main__":
    unittest.main()
