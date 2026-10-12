"""Поручения задачи — по `root` ядра (core/chain.py), не по догадке о сессиях и времени."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import chain


def d(did, session, created, **kw):
    return {"id": did, "session": session, "for": None, "for_session": None, "created": created, **kw}


def st(*ds):
    return {"delegations": {x["id"]: x for x in ds}}


class Chain(unittest.TestCase):
    def test_by_root_in_time_order(self):
        s = st(d("d2", "exec-1", 20, root="m1", for_session="hub"), d("d1", "backend", 10, root="m1", **{"for": "m1"}),
               d("d9", "frontend", 15, root="m9"))
        self.assertEqual(chain.inherit(s, "m1"), ["d1", "d2"])

    def test_old_delegation_without_root_uses_for(self):
        self.assertEqual(chain.inherit(st(d("d1", "backend", 10, **{"for": "m1"})), "m1"), ["d1"])

    def test_root_wins_over_for(self):
        self.assertEqual(chain.inherit(st(d("d1", "backend", 10, root=None, **{"for": "m1"})), "m1"), [])

    def test_later_task_of_same_session_with_other_root_not_taken(self):
        # агент выдал поручение через дни по другому делу — у него свой root
        s = st(d("d1", "hub-dev", 10, root="m1"), d("d2", "visible-failures", 99, root="m7", for_session="hub-dev"))
        self.assertEqual(chain.inherit(s, "m1"), ["d1"])

    def test_no_delegations(self):
        self.assertEqual(chain.inherit({"delegations": {}}, "m1"), [])


if __name__ == "__main__":
    unittest.main()
