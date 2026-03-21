from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_IMPL_PATH = (
    Path(__file__).resolve().parents[2]
    / "modeling"
    / "src"
    / "koris_api"
    / "win_probability.py"
)
_SPEC = importlib.util.spec_from_file_location(
    "koris_api_modeling_win_probability",
    _IMPL_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError(f"Unable to load win_probability implementation from {_IMPL_PATH}")

_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules.setdefault(_SPEC.name, _MODULE)
_SPEC.loader.exec_module(_MODULE)

for _name, _value in vars(_MODULE).items():
    if _name.startswith("__") and _name not in {"__doc__", "__all__"}:
        continue
    globals()[_name] = _value

__all__ = getattr(
    _MODULE,
    "__all__",
    [name for name in vars(_MODULE) if not name.startswith("__")],
)
