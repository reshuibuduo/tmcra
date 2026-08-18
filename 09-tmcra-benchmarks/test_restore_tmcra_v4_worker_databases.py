import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from ops.restore_tmcra_v4_worker_databases import (
    SLOW_CONTROL_PLANE_TABLES,
    TABLES,
    inspect_database,
)


class RestoreWorkerDatabaseTests(unittest.TestCase):
    def test_inspection_covers_the_complete_slow_control_plane(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "native_memory.sqlite3"
            with closing(sqlite3.connect(path)) as con:
                for table in TABLES:
                    con.execute(f'CREATE TABLE "{table}" (id TEXT)')
                    con.execute(f'INSERT INTO "{table}" VALUES (?)', (table,))
                con.commit()
            state = inspect_database(path)

        self.assertEqual(set(state["counts"]), set(TABLES))
        self.assertEqual(
            set(state["slow_control_plane_counts"]), set(SLOW_CONTROL_PLANE_TABLES)
        )
        self.assertEqual(set(state["table_sha256"]), set(TABLES))
        self.assertTrue(state["slow_control_plane_sha256"])


if __name__ == "__main__":
    unittest.main()
