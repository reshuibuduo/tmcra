import json
from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path

from ops.prepare_tmcra_v4_fresh_slow_copy import (
    FreshSlowCopyError,
    prepare_copy,
)


class FreshSlowCopyTests(unittest.TestCase):
    def make_db(self, path: Path) -> None:
        with closing(sqlite3.connect(path)) as con:
            con.executescript(
                """
                CREATE TABLE records(
                    scope_id TEXT,memory_id TEXT,category TEXT,slot_key TEXT,value TEXT,
                    relation TEXT,anchor_concepts_json TEXT,evidence_anchors_json TEXT,
                    salience REAL,confidence REAL,source_kind TEXT,turn_index INTEGER,
                    state TEXT,supersedes_json TEXT,metadata_json TEXT,
                    PRIMARY KEY(scope_id,memory_id));
                CREATE TABLE memory_edges(
                    scope_id TEXT,edge_id TEXT,source_memory_id TEXT,target_memory_id TEXT,
                    edge_type TEXT,score REAL,model_score REAL,evidence_turn INTEGER,
                    evidence TEXT,metadata_json TEXT,PRIMARY KEY(scope_id,edge_id));
                CREATE TABLE slow_graph_jobs(job_id TEXT);
                CREATE TABLE slow_graph_patches(patch_id TEXT);
                CREATE TABLE audit_retrieval_log(row_id TEXT);
                CREATE TABLE slot_heads(
                    scope_id TEXT,slot_key TEXT,memory_id TEXT,
                    PRIMARY KEY(scope_id,slot_key));
                CREATE TABLE slot_history(
                    scope_id TEXT,slot_key TEXT,ordinal INTEGER,memory_id TEXT,
                    PRIMARY KEY(scope_id,slot_key,ordinal));
                """
            )
            base = ("scope", "fact", "slot", "value", "states", "[]", "[]", .7, .8, "writer", 1, "active", "[]")
            con.execute(
                "INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (base[0], "fast.1", *base[1:], json.dumps({"memory_layer": "fast", "content_variant": "product_semantic_memory"})),
            )
            con.execute(
                "INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (base[0], "slow.1", *base[1:], json.dumps({"memory_layer": "slow", "content_variant": "slow_memory_capsule"})),
            )
            con.execute(
                "INSERT INTO memory_edges VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("scope", "edge.fast", "fast.1", "source.1", "grounded_in", 1, 0, 1, "x", "{}"),
            )
            con.execute(
                "INSERT INTO memory_edges VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("scope", "edge.slow", "slow.1", "fast.1", "supported_by", 1, 0, 1, "x", "{}"),
            )
            con.execute("INSERT INTO slow_graph_jobs VALUES('job.1')")
            con.execute("INSERT INTO slow_graph_patches VALUES('patch.1')")
            con.execute("INSERT INTO audit_retrieval_log VALUES('row.1')")
            con.execute("INSERT INTO slot_heads VALUES('scope','slot','fast.1')")
            con.execute("INSERT INTO slot_heads VALUES('scope','slow.cap.1','slow.1')")
            con.execute("INSERT INTO slot_heads VALUES('scope','slow.orphan','slow.missing')")
            con.execute("INSERT INTO slot_history VALUES('scope','slot',0,'fast.1')")
            con.execute("INSERT INTO slot_history VALUES('scope','slow.cap.1',0,'slow.1')")
            con.execute("INSERT INTO slot_history VALUES('scope','slow.orphan',0,'slow.missing')")
            con.commit()

    def test_copy_removes_only_slow_state(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source.sqlite3"
            output = Path(temp) / "output.sqlite3"
            self.make_db(source)
            report = prepare_copy(source, output)
            self.assertEqual(report["removed_slow_records"], 1)
            self.assertEqual(report["removed_slow_edges"], 1)
            self.assertEqual(report["removed_slow_slot_heads"], 2)
            self.assertEqual(report["removed_slow_slot_history_rows"], 2)
            self.assertEqual(report["physical_api_calls"], 0)
            self.assertEqual(report["quick_check"], "ok")
            with closing(sqlite3.connect(output)) as con:
                self.assertEqual(
                    con.execute("SELECT memory_id FROM records").fetchall(),
                    [("fast.1",)],
                )
                self.assertEqual(
                    con.execute("SELECT edge_id FROM memory_edges").fetchall(),
                    [("edge.fast",)],
                )
                self.assertEqual(
                    con.execute("SELECT count(*) FROM slow_graph_jobs").fetchone()[0],
                    0,
                )
                self.assertEqual(
                    con.execute("SELECT * FROM slot_heads").fetchall(),
                    [("scope", "slot", "fast.1")],
                )
                self.assertEqual(
                    con.execute("SELECT * FROM slot_history").fetchall(),
                    [("scope", "slot", 0, "fast.1")],
                )

    def test_refuses_in_place_or_existing_output(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source.sqlite3"
            output = Path(temp) / "output.sqlite3"
            self.make_db(source)
            with self.assertRaisesRegex(FreshSlowCopyError, "must differ"):
                prepare_copy(source, source)
            output.write_text("occupied", encoding="utf-8")
            with self.assertRaisesRegex(FreshSlowCopyError, "already exists"):
                prepare_copy(source, output)

    def test_refuses_reserved_slow_slot_pointing_to_fast_record(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source.sqlite3"
            output = Path(temp) / "output.sqlite3"
            self.make_db(source)
            with closing(sqlite3.connect(source)) as con:
                con.execute(
                    "INSERT INTO slot_heads VALUES('scope','slow.invalid','fast.1')"
                )
                con.commit()
            with self.assertRaisesRegex(
                FreshSlowCopyError, "reserved Slow slot for a non-Slow record"
            ):
                prepare_copy(source, output)


if __name__ == "__main__":
    unittest.main()
