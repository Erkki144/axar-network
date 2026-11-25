"""Migration history guard: exactly one Alembic head and a linear chain.

Two branches that each add a migration with the same parent (as #26 and #49
both did with 0005) produce two heads; `alembic upgrade head` then fails in
deployment. This test fails in CI first instead. Revisions already in `main`
are never renamed - a later branch renumbers ITS migration onto the head.
"""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[1]


def _script() -> ScriptDirectory:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "axar_pos" / "migrations"))
    return ScriptDirectory.from_config(cfg)


def test_single_head_and_linear_chain():
    script = _script()
    heads = script.get_heads()
    assert len(heads) == 1, f"multiple Alembic heads {heads}: renumber the newer branch's migration"
    chain = [rev.revision for rev in script.walk_revisions()]      # head -> base
    assert len(chain) == len(set(chain))
    for rev in script.walk_revisions():
        assert not isinstance(rev.down_revision, (tuple, list)), f"merge revision {rev.revision} not expected"


def test_revisions_already_released_in_main_are_unchanged():
    """0001..0004 are applied wherever main ran; they must keep their ids and parents."""
    script = _script()
    expected = {"0001": None, "0002": "0001", "0003": "0002", "0004": "0003"}
    for rev, parent in expected.items():
        assert script.get_revision(rev).down_revision == parent
