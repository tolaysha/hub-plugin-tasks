"""Assignments that grew from an owner's message — by the core's explicit link, no guessing: an assignment's `root` is the
owner's word the chain started from (core/chain.py: task_root; older ones have `for`). The core sets it: a task on the
owner's word, an executor `hub task run` — the turn it was asked in, an executor's subtask — the start of its task.
(09.10: the earlier guess "an assignment of the same session, given later" pulled the agent's unrelated assignments
into the task and did not find an executor `hub task run` without --for.)"""


LIVE = ("working", "waiting-owner")   # the assignment is in work (core/machines.py: DELEGATION)


def task_root(d):
    return d["root"] if "root" in d else d.get("for")   # as core/chain.py: task_root


def inherit(state, mid):
    dels = (state.get("delegations") or {}).values()
    return [d["id"] for d in sorted((d for d in dels if task_root(d) == mid), key=lambda d: d["created"])]
