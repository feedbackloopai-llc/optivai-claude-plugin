"""test_log_writer_parity.py - the two log_writer.py copies must stay byte-identical.

There are two hand-duplicated copies of the activity logger:

  * scripts/log_writer.py        - the copy the tests import (scripts/ on sys.path)
  * scripts/hooks/log_writer.py  - the copy upgrade.sh deploys to ~/.claude/hooks/,
                                   and the copy the live hooks import at runtime
                                   (pre-tool-use.py / user-prompt-submit.py do
                                   `from log_writer import AgentActivityLogger`).

Because they are hand-duplicated, a fix applied to one but not the other silently
ships a stale hook. That is exactly what happened: the dup-session bare-except fix
(test_log_writer_session_dedup.py) landed only on scripts/log_writer.py, so the
tested copy was correct while the deployed hook kept the bug. This test fails if
they drift, forcing every change to be applied to both.
"""

from pathlib import Path

_SCRIPTS = Path(__file__).parent.parent


def test_log_writer_copies_are_byte_identical() -> None:
    tested = (_SCRIPTS / "log_writer.py").read_bytes()
    deployed = (_SCRIPTS / "hooks" / "log_writer.py").read_bytes()
    assert tested == deployed, (
        "scripts/log_writer.py and scripts/hooks/log_writer.py have drifted. "
        "Apply the same change to BOTH: the runtime hooks import the scripts/hooks/ "
        "copy, while the tests import the scripts/ copy. A fix to only one ships a "
        "stale live hook."
    )
