"""Поручения, выросшие из сообщения владельца, — по явной связи ядра, без догадок: у поручения `root` — слово
владельца, с которого началась цепочка (core/chain.py: task_root; у старых — `for`). Его ставит ядро: задача по слову
владельца, исполнитель `hub task run` — ход, в котором его просили, подзадача исполнителя — начало его задачи.
(09.10: прежняя догадка «поручение той же сессии, выданное позже» тянула в задачу чужие поручения агента, а
исполнителя `hub task run` без --for не находила.)"""


LIVE = ("working", "waiting-owner")   # поручение в работе (core/machines.py: DELEGATION)


def task_root(d):
    return d["root"] if "root" in d else d.get("for")   # как core/chain.py: task_root


def inherit(state, mid):
    dels = (state.get("delegations") or {}).values()
    return [d["id"] for d in sorted((d for d in dels if task_root(d) == mid), key=lambda d: d["created"])]
