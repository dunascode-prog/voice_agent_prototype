"""The Alembic migrations must build exactly the schema the models describe."""

import subprocess
import sys
from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from voice_agent.storage.models import Base

ROOT = Path(__file__).resolve().parents[2]


def test_upgrade_head_matches_models(tmp_path):
    db_file = (tmp_path / "migrated.db").as_posix()
    subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-x",
            f"url=sqlite+aiosqlite:///{db_file}",
            "upgrade",
            "head",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    engine = create_engine(f"sqlite:///{db_file}")
    with engine.connect() as conn:
        diffs = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    engine.dispose()
    assert diffs == []


def test_downgrade_base_runs(tmp_path):
    db_file = (tmp_path / "down.db").as_posix()
    url = f"url=sqlite+aiosqlite:///{db_file}"
    for step in (["upgrade", "head"], ["downgrade", "base"]):
        subprocess.run(
            [sys.executable, "-m", "alembic", "-x", url, *step],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
