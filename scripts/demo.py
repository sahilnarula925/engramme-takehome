"""A repeatable, narrated API demo. Each run uses a fresh user and asserts its claims."""
import argparse
import json
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "starter-kit"))
from harness_lib.checks import compare_user
from harness_lib.reference import Reference
from harness_lib.service import ServiceClient
from harness_lib.workload import Event

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--pause", action="store_true", help="wait before each stage for screen recording")
args = parser.parse_args()

service, reference = ServiceClient("http://localhost:8080"), Reference("permanent")
user = "demo-" + uuid.uuid4().hex[:8]
lead = "Ana is leading the Atlas project."
joined = "Ben joined the Atlas project."
budget = "The Atlas project budget is 120000 dollars."
deadline = "The Atlas project deadline is March 3."
other_facts = [deadline, "Chen joined the Atlas project.", "Dana joined the Atlas project."]
new_budget = "The Atlas project budget is 150000 dollars."


def say(text, pause=False):
    print("\n" + text, flush=True)
    if pause and args.pause:
        input("Press Enter to run this stage... ")


def send(source, item, version, facts):
    event = Event(user, source, item, version, "upsert", " ".join(facts), "2026-01-01T00:00:00Z")
    service.send_event(event)
    reference.apply_event(event)


def converged():
    end = time.monotonic() + 10
    while True:
        memories = service.list_memories(user)
        errors = compare_user(user, reference.user_state(user), memories)
        if not errors:
            return memories
        if time.monotonic() > end:
            raise RuntimeError(errors)
        time.sleep(0.1)


def show(memories):
    for memory in sorted(memories, key=lambda m: (m["kind"], m["text"])):
        print(json.dumps({k: memory[k] for k in ("id", "kind", "text", "lineage")}, indent=2))


say(f"Demo user: {user}\n1. Two sources state the same fact; distinct topic facts produce a summary.", pause=True)
send("gmail", "email", 1, [lead, joined, budget])
send("calendar", "invite", 1, [lead])
send("gmail", "notes", 1, other_facts)
before = converged()
show(before)
shared = next(m for m in before if m["text"] == lead)
assert len(shared["lineage"]["items"]) == 2

say("2. Edit the email: budget changes to 150000; the lead and joined facts retain their IDs.", pause=True)
send("gmail", "email", 2, [lead, joined, new_budget])
after = converged()
for text in (lead, joined):
    assert next(m["id"] for m in before if m["text"] == text) == next(m["id"] for m in after if m["text"] == text)
show(after)
say("Exact-query recall:")
print(json.dumps(service.recall(user, lead, 1), indent=2))

say("3. Forget the calendar item. The shared fact stays with the same ID and email lineage.", pause=True)
service.user_delete((user, "calendar", "invite"))
reference.user_delete((user, "calendar", "invite"))
visible = service.get_memory(user, shared["id"])
assert visible["lineage"]["items"] == [{"source_id": "gmail", "item_id": "email"}]
show(converged())

say("4. Forget the email. Its unbacked facts disappear immediately; the summary is re-derived from the three notes facts.", pause=True)
service.user_delete((user, "gmail", "email"))
reference.user_delete((user, "gmail", "email"))
assert service.get_memory(user, shared["id"]) is None
assert all(lead not in m["text"] for m in service.list_memories(user))
print(json.dumps({"memories": service.list_memories(user)}))
remaining = converged()
assert len(remaining) == 4 and any(m["kind"] == "summary" for m in remaining)
show(remaining)

say("5. Disconnect/reconnect, then replay both items with newer versions: permanent forgetting wins.", pause=True)
for source in ("gmail", "calendar"):
    service.disconnect(user, source)
    reference.disconnect(user, source)
    service.connect(user, source)
    reference.connect(user, source)
send("gmail", "email", 99, [lead, joined, new_budget])
send("calendar", "invite", 99, [lead])
assert converged() == remaining
print(json.dumps({"memories": service.list_memories(user)}))
say("Demo assertions passed. Run the backfill and crash harness scenarios for the remaining live demonstrations.")
