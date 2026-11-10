class FairOrder:
    """Hierarchical round-robin order for the ready head of each durable lane."""

    def __init__(self):
        self.user_turn = {}
        self.lane_turn = {}
        self.clock = 0

    def choose(self, candidates):
        return min(candidates, key=lambda job: (self.user_turn.get(job["user_id"], 0),
                   self.lane_turn.get((job["user_id"], job["lane"]), 0), job["id"]))

    def served(self, job):
        self.clock += 1
        self.user_turn[job["user_id"]] = self.clock
        self.lane_turn[(job["user_id"], job["lane"])] = self.clock

    def next_ready(self, candidates, lookup):
        """Skip stale lane heads immediately instead of idling other ready work."""
        while candidates:
            lane = self.choose(candidates)
            job = lookup(lane)
            if job:
                return job
            candidates.remove(lane)
        return None
