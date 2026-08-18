import json
from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path

from ops.prepare_tmcra_v4_fresh_slow_run import prepare_run


class FreshSlowRunTests(unittest.TestCase):
    def _database(self, path: Path) -> None:
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
                CREATE TABLE v4_batch_journal(status TEXT);
                CREATE TABLE slow_graph_jobs(job_id TEXT);
                """
            )
            base = (
                "scope", "fact", "slot", "value", "states", "[]", "[]", .7, .8,
                "writer", 1, "active", "[]",
            )
            con.execute(
                "INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (*base[:1], "fast.1", *base[1:], json.dumps({"memory_layer": "fast"})),
            )
            con.execute(
                "INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (*base[:1], "slow.1", *base[1:], json.dumps({"memory_layer": "slow"})),
            )
            con.execute("INSERT INTO v4_batch_journal VALUES('committed')")
            con.execute("INSERT INTO slow_graph_jobs VALUES('old')")
            con.commit()

    def test_prepares_isolated_filtered_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            output = root / "output"
            worker = source / "writer" / "worker_003"
            worker.mkdir(parents=True)
            self._database(worker / "native_memory.sqlite3")
            for name in (
                "product_write_messages.jsonl",
                "product_writer_calls.jsonl",
                "product_writer_raw_responses.jsonl",
                "writer.log",
            ):
                (worker / name).write_text("{}\n", encoding="utf-8")
            (worker / "product_writer_revalidations.jsonl").write_text(
                json.dumps({"batch_id": "batch-1", "physical_api_calls": 0})
                + "\n",
                encoding="utf-8",
            )
            (worker / "input.json").write_text("{}\n", encoding="utf-8")
            (worker / "product_writer_report.json").write_text(
                json.dumps({"completed": True, "batches": 1}), encoding="utf-8"
            )
            (worker / "source_exclusions.json").write_text(
                json.dumps(
                    {
                        "schema_version": "tmcra.v4.source-exclusions.1",
                        "count": 0,
                        "messages": [],
                    }
                ),
                encoding="utf-8",
            )
            (worker / "writer_chain_audit.json").write_text(
                json.dumps({"passed": True}), encoding="utf-8"
            )
            qid = "qid3"
            (source / "writer_input.json").write_text(
                json.dumps([{"question_id": qid, "haystack_sessions": []}]),
                encoding="utf-8",
            )
            scope_row = {
                "question_id": qid,
                "scope_id": "scope",
                "db_path": "old",
                "index_path": "old",
            }
            query_row = {**scope_row, "question": "q"}
            (source / "scope_manifest.jsonl").write_text(
                json.dumps(scope_row) + "\n", encoding="utf-8"
            )
            (source / "query_manifest.jsonl").write_text(
                json.dumps(query_row) + "\n", encoding="utf-8"
            )
            manifest = {
                "status": "prepared",
                "row_count": 1,
                "workers": [
                    {
                        "worker_dir": str(worker),
                        "worker_index": 3,
                        "question_id": qid,
                        "scope_id": "scope",
                        "message_count": 0,
                        "nonempty_message_count": 0,
                        "empty_message_count": 0,
                        "duplicate_session_id_occurrence_count": 0,
                    }
                ],
            }
            (source / "input_manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )

            report = prepare_run(source, output, ["worker_003"])
            self.assertEqual(report["physical_api_calls"], 0)
            self.assertEqual(
                report["copies"][0]["output_db"],
                str(output / "writer" / "worker_003" / "native_memory.sqlite3"),
            )
            self.assertTrue((output / "FRESH_SLOW_COPY_COMPLETE.json").is_file())
            self.assertEqual(
                (
                    output
                    / "writer"
                    / "worker_003"
                    / "product_writer_revalidations.jsonl"
                ).read_text(encoding="utf-8"),
                (worker / "product_writer_revalidations.jsonl").read_text(
                    encoding="utf-8"
                ),
            )
            prepared = json.loads(
                (output / "input_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(prepared["qids"], [qid])
            self.assertEqual(prepared["row_count"], 1)
            self.assertEqual(
                prepared["workers"][0]["worker_dir"],
                str(output / "writer" / "worker_003"),
            )
            with closing(
                sqlite3.connect(
                    output / "writer" / "worker_003" / "native_memory.sqlite3"
                )
            ) as con:
                self.assertEqual(
                    con.execute("SELECT memory_id FROM records").fetchall(),
                    [("fast.1",)],
                )
                self.assertEqual(
                    con.execute("SELECT count(*) FROM slow_graph_jobs").fetchone()[0],
                    0,
                )


if __name__ == "__main__":
    unittest.main()
