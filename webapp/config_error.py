"""Report a stored automation config that the server refused to load.

Each controller's `_load()` falls back to its `DEFAULT_CONFIG` when the file
on disk does not validate, and every `DEFAULT_CONFIG` has `enabled: False`.
That is the safe direction for `battery_window` -- disabled means it writes
no POP at all, so the loads stay on the grid. It is **not** the safe
direction for `grid_charge`: a disabled charger means export goes
unabsorbed, which is the one thing this installation must not do.

The fallback used to be silent. Its only visible effect was the automation
showing as "desativada" on the dashboard, indistinguishable from someone
having turned it off deliberately. And it discards the window hours, the
thresholds and the pump window along with the enabled flag, so one mistyped
value silently loses the entire configuration.

Both halves of this matter: stderr so it reaches the journal, and a string
the controller hands back through its API so the dashboard can say it out
loud. Nobody reads the journal -- that is the whole reason notify.py exists.

The returned string is pt-PT, like `_pop_warning()` and the battery window's
`detail`: it is prose for a person, not part of the machine contract. The
reason codes and field names around it stay English.
"""

from __future__ import annotations

import sys


def report(path: str, exc: BaseException) -> str:
    """Log the rejection and return the message to surface in the API."""
    message = (f"a configuração {os_basename(path)} foi recusada ({exc}) — "
               f"esta automação está DESATIVADA e a correr com os valores "
               f"por omissão até o ficheiro ser corrigido")
    print(f"[config] rejected {path}: {exc} -- falling back to defaults, "
          f"this automation is DISABLED", file=sys.stderr, flush=True)
    return message


def os_basename(path: str) -> str:
    """Just the filename. The full path is in the journal line; the
    dashboard only needs to say which file to go and fix."""
    return path.rsplit("/", 1)[-1] or path
