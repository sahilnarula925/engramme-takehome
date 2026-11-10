"""Serial fault-injection tests. Do not run alongside the harness: both configure the sidecar."""
import time
import unittest

import test_service as support

FACTS, http = support.FACTS, support.http


class FailureTests(unittest.TestCase):
    setUp = support.ServiceTests.setUp
    event = support.ServiceTests.event
    converge = support.ServiceTests.converge
    forget = support.ServiceTests.forget

    def configure(self, **values):
        response = http("POST", "http://localhost:7070/admin/config", values)
        self.assertEqual(response.status, 200)

    def tearDown(self):
        self.configure(latency_min_ms=50, latency_max_ms=200, failure_rate=0.02)

    def counter(self, name):
        return http("GET", "http://localhost:7070/admin/stats").body[name]

    def wait_counter(self, name, baseline):
        deadline = time.monotonic() + 5
        while self.counter(name) <= baseline:
            if time.monotonic() > deadline:
                self.fail(f"{name} did not advance")
            time.sleep(0.02)

    def test_delete_during_extraction_discards_stale_result(self):
        self.configure(latency_min_ms=800, latency_max_ms=800, failure_rate=0)
        started, completed = self.counter("extract_calls"), self.counter("extract_completed")
        self.event("one")
        self.wait_counter("extract_calls", started)
        self.forget("one")
        self.assertEqual(self.service.list_memories(self.user), [])
        self.wait_counter("extract_completed", completed)
        time.sleep(0.15)
        self.assertEqual(self.converge(), [])

    def test_delete_during_consolidation_discards_stale_summary(self):
        self.configure(latency_min_ms=600, latency_max_ms=600, failure_rate=0)
        started, completed = self.counter("consolidate_calls"), self.counter("consolidate_completed")
        self.event("one", FACTS[:2])
        self.event("two", FACTS[2:3])
        self.wait_counter("consolidate_calls", started)
        self.forget("two")
        self.assertFalse(any(m["kind"] == "summary" for m in self.service.list_memories(self.user)))
        self.wait_counter("consolidate_completed", completed)
        time.sleep(0.15)
        self.assertEqual(len(self.converge()), 2)

    def test_retryable_failure_remains_durable_until_recovery(self):
        self.configure(latency_min_ms=50, latency_max_ms=50, failure_rate=1)
        failed = self.counter("transient_failures")
        self.event("one")
        self.wait_counter("transient_failures", failed)
        self.assertEqual(self.service.list_memories(self.user), [])
        self.configure(failure_rate=0)
        self.assertEqual(len(self.converge()), 5)


if __name__ == "__main__":
    unittest.main()
