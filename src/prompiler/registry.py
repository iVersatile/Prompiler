"""Public import shim for the in-process registry — sub-step 4a (PLAN.md L160).

PRD L185 and architecture.md L117 document ``prompiler.registry`` as the
public import path. architecture.md §2.4 places the canonical
implementation under ``prompiler.runtime.registry``. This module keeps the
public name working while the source of truth moves into the runtime
package.

Why ``sys.modules`` aliasing instead of a plain re-export
---------------------------------------------------------
``tests/test_registry_discovery.py`` runs
``monkeypatch.setattr(prompiler.registry, "compile_spec", forced)`` to
force a hash collision (line 267). A naive re-export ``from … import *``
would bind ``compile_spec`` on *this* module's globals, but ``discover()``
inside ``prompiler.runtime.registry`` would still resolve its own
``compile_spec`` via the runtime module's globals — the monkeypatch would
target the wrong namespace and silently fail.

Aliasing ``sys.modules[__name__]`` to the canonical module makes
``prompiler.registry`` and ``prompiler.runtime.registry`` point at the
same module object. ``setattr`` against either name mutates the same
``__dict__`` the runtime code reads from, so the monkeypatch contract
survives the move without test changes.

Limitations
-----------
After this module executes its alias, any subsequent ``import
prompiler.registry`` returns the canonical runtime module. Helpers like
``importlib.reload(prompiler.registry)`` would now reload
``prompiler.runtime.registry`` — that is the intended behavior; the two
names are deliberately one module.
"""

from __future__ import annotations

import sys as _sys

from prompiler.runtime import registry as _runtime_registry

_sys.modules[__name__] = _runtime_registry
