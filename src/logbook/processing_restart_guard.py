"""Block cooperating writers while fixture graph recovery is unresolved."""
import os
from pathlib import Path


def require_clear_guard(root: Path) -> None:
    if os.path.lexists(root / 'graph-maintenance.pending'):
        raise RuntimeError('graph maintenance unresolved; processing is blocked')
