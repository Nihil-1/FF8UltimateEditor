"""Run something on the next event-loop tick, tied to the widget that asked for it.

Several editors need to finish a job one tick later, once Qt has laid the widgets out and they
have real sizes: restore a scroll position after a rebuild, size a splitter from its true width,
clear a "still loading" flag. The obvious way to write that is QTimer.singleShot(0, callback).

It has a trap. The pending call outlives the widget: close the file (or let the tests drop it, or
tear the window down) before the tick arrives and the callback still runs, on Python wrappers
whose C++ objects Qt has already deleted. That raises RuntimeError inside a Qt slot, and PyQt does
not report those - it aborts the whole process, with no traceback and no message. It cost a test
suite that died mid-run for no visible reason, and it can equally take the editor down while
someone is using it.

Qt has a version of singleShot that takes a context object and drops the call when that object
dies, but PyQt6 does not expose it. What it does honour is ownership: a QTimer created as a CHILD
of the widget is destroyed with it, and a destroyed timer never fires.

Ownership alone is not enough, though. The C++ timer dies with its C++ owner - but the CALLBACK
is a Python object, and when the widget tree that scheduled it becomes cyclic garbage the garbage
collector can tear the callback apart (tp_clear) while the C++ side is still alive: the owner
sits in a C++ parent chain whose owning top-level wrapper the (incremental, since Python 3.14)
collector simply hasn't reached yet. The still-armed C++ timer then fires on the next event pump
and calls a function whose globals/closure have been nulled - an access violation inside the
interpreter, no Python traceback, blamed on whatever unrelated code happened to pump events (this
was the tests/test_single_pane.py mid-run crash). The cure: every pending call is pinned in
_PENDING, a module-level GC root, until it either fires or its owner is destroyed - so the
collector can never clear a callback out from under an armed timer. The pin keeps the callback's
cycle alive at most one tick (or until the owner dies), exactly the lifetime the call needs.
"""
from PyQt6.QtCore import QTimer

# Pending calls, id(timer) -> (timer, fire-slot, gone-slot). Module-level on purpose: entries
# must be reachable from a GC root (see the module doc), not just from the timer's connection.
_PENDING = {}


def defer(owner, callback, msec: int = 0) -> QTimer:
    """Call `callback` after `msec`, unless `owner` is destroyed first.

    `owner` must be the QObject whose C++ objects the callback touches - usually the widget that
    schedules it. Returns the timer, which callers can keep to cancel the call early; ignoring it
    is fine, `owner` owns it.
    """
    timer = QTimer(owner)
    timer.setSingleShot(True)
    key = id(timer)
    pending = _PENDING   # captured: the slots must not do a module-global lookup, the module
                         # dict may already be cleared when a timer dies at interpreter shutdown

    def _fire():
        pending.pop(key, None)
        callback()

    def _gone():
        pending.pop(key, None)

    timer.timeout.connect(_fire)
    timer.destroyed.connect(_gone)   # the owner's destruction destroys its child timer too
    _PENDING[key] = (timer, _fire, _gone)
    timer.start(msec)
    return timer
