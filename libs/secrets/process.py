"""Keeping what a process holds from the processes it starts.

The document worker holds the key to every stored credential in its environment, and it starts other
people's programs: a browser, and the AI tools, one of which can run commands of its own and is handed the
text of a job posting, which anybody can write. A program started by another may read that one's environment
and memory, being the same user (`/proc/<pid>/environ`). So the worker says it is not to be read."""
from __future__ import annotations

import ctypes
import logging

logger = logging.getLogger(__name__)

# `prctl(PR_SET_DUMPABLE, 0)`: Linux then lets nobody but root read this process's environment or memory,
# its own children among them, and writes no core file of it.
_PR_SET_DUMPABLE = 4


def keep_to_itself() -> bool:
    """Make this process unreadable to the ones it starts. False, and said, where the system has no such
    thing (a laptop that is not Linux): the process runs on as before."""
    try:
        refused = ctypes.CDLL(None, use_errno=True).prctl(_PR_SET_DUMPABLE, 0, 0, 0, 0)
    except (OSError, AttributeError) as exc:
        logger.warning("secrets: this system cannot keep a process's environment from its children (%s)", exc)
        return False
    if refused:
        logger.warning("secrets: the system refused to keep this process's environment from its children "
                       "(errno %d)", ctypes.get_errno())
    return not refused
