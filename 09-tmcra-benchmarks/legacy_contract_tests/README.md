# Archived contract-test snapshots

These files preserve pre-extraction V3 and memory-service contract tests for
design provenance. They target older internal entry points, job-state schemas,
and storage fixtures, so they are not part of the V4 benchmark release gate.

The supported benchmark suite lives one directory above and is run with
`python -m unittest discover -s . -p "test*.py"`. The authoritative memory API
release checks live in component `02-tmcra-memory-api`.
