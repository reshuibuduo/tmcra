from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from tmcra_service.provider_pool import ProviderKeyPool, ProviderPoolExhausted
from tmcra_service.writer import LeasedDeepSeekClient


class ProviderKeyPoolTests(unittest.TestCase):
    def test_leases_are_bounded_and_secrets_are_not_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "control.sqlite3"
            pool = ProviderKeyPool(
                database,
                pool="deepseek",
                keys=["secret-a", "secret-b"],
                max_concurrency_per_key=1,
            )
            first = pool.acquire(owner="one")
            second = pool.acquire(owner="two")
            with self.assertRaises(ProviderPoolExhausted):
                pool.acquire(owner="three")
            self.assertNotEqual(first.key_id, second.key_id)
            self.assertNotIn(b"secret-a", database.read_bytes())
            self.assertNotIn(b"secret-b", database.read_bytes())
            pool.release(first, outcome="success")
            third = pool.acquire(owner="three")
            self.assertEqual(first.key_id, third.key_id)

    def test_rate_limit_outcome_cools_down_one_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pool = ProviderKeyPool(
                Path(directory) / "control.sqlite3",
                pool="deepseek",
                keys=["secret-a", "secret-b"],
                max_concurrency_per_key=1,
            )
            first = pool.acquire(owner="one")
            pool.release(first, outcome="rate_limited", retry_after_seconds=60)
            second = pool.acquire(owner="two")
            self.assertNotEqual(first.key_id, second.key_id)
            stats = pool.stats()
            self.assertEqual(stats["healthy_keys"], 1)

    def test_pool_can_restart_with_reordered_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "control.sqlite3"
            ProviderKeyPool(
                database,
                pool="deepseek",
                keys=["secret-a", "secret-b"],
                max_concurrency_per_key=1,
            )
            reordered = ProviderKeyPool(
                database,
                pool="deepseek",
                keys=["secret-b", "secret-a"],
                max_concurrency_per_key=1,
            )
            first = reordered.acquire(owner="first-after-restart")
            second = reordered.acquire(owner="second-after-restart")
            self.assertEqual(first.secret, "secret-b")
            self.assertEqual(second.secret, "secret-a")
            reordered.release(first, outcome="success")
            reordered.release(second, outcome="success")

    def test_expired_release_is_idempotent_and_does_not_record_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pool = ProviderKeyPool(
                Path(directory) / "control.sqlite3",
                pool="deepseek",
                keys=["secret-a"],
                max_concurrency_per_key=1,
                lease_seconds=0.03,
            )
            lease = pool.acquire(owner="slow-request")
            time.sleep(0.06)
            pool.release(lease, outcome="success")
            pool.release(lease, outcome="success")
            stats = pool.stats()
            self.assertEqual(stats["successes"], 0)
            self.assertEqual(stats["active_leases"], 0)
            self.assertEqual(pool.acquire(owner="next-request").key_id, lease.key_id)

    def test_heartbeat_keeps_a_long_request_exclusive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "control.sqlite3"
            pool = ProviderKeyPool(
                database,
                pool="deepseek",
                keys=["secret-a"],
                max_concurrency_per_key=1,
                lease_seconds=0.2,
            )
            lease = pool.acquire(owner="slow-request")
            stop = threading.Event()

            def renew() -> None:
                while not stop.wait(0.01):
                    self.assertIsNotNone(pool.heartbeat(lease))

            thread = threading.Thread(target=renew)
            thread.start()
            try:
                time.sleep(0.65)
                with self.assertRaises(ProviderPoolExhausted):
                    pool.acquire(owner="contender")
            finally:
                stop.set()
                thread.join()
                pool.release(lease, outcome="success")

    def test_concurrent_pool_instances_respect_per_key_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "control.sqlite3"
            pools = [
                ProviderKeyPool(
                    database,
                    pool="deepseek",
                    keys=["secret-a", "secret-b"],
                    max_concurrency_per_key=1,
                    lease_seconds=2,
                )
                for _ in range(2)
            ]
            barrier = threading.Barrier(8)
            lock = threading.Lock()
            active: dict[str, int] = {}
            max_active = 0
            errors: list[BaseException] = []

            def worker(index: int) -> None:
                nonlocal max_active
                lease = None
                try:
                    barrier.wait()
                    deadline = time.time() + 5
                    while lease is None:
                        try:
                            lease = pools[index % 2].acquire(owner=f"worker-{index}")
                        except ProviderPoolExhausted:
                            if time.time() >= deadline:
                                raise
                            time.sleep(0.01)
                    with lock:
                        active[lease.key_id] = active.get(lease.key_id, 0) + 1
                        max_active = max(max_active, max(active.values()))
                    time.sleep(0.02)
                except BaseException as exc:
                    errors.append(exc)
                finally:
                    if lease is not None:
                        with lock:
                            active[lease.key_id] -= 1
                        pools[index % 2].release(lease, outcome="success")

            threads = [threading.Thread(target=worker, args=(index,)) for index in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(errors, [])
            self.assertLessEqual(max_active, 1)

    def test_writer_heartbeat_does_not_turn_long_success_into_failure(self) -> None:
        class SlowClient:
            def __init__(self, **_: object) -> None:
                pass

            def complete(self, payload: object) -> object:
                time.sleep(0.65)
                return payload

        with tempfile.TemporaryDirectory() as directory:
            pool = ProviderKeyPool(
                Path(directory) / "control.sqlite3",
                pool="deepseek",
                keys=["secret-a"],
                max_concurrency_per_key=1,
                lease_seconds=0.2,
            )
            client = LeasedDeepSeekClient(
                v4=SimpleNamespace(DeepSeekBatchClient=SlowClient),
                pool=pool,
                operation_id="operation-1",
                base_url="https://provider.invalid",
                model="deepseek-v4-flash",
                timeout=1.0,
                max_tokens=32,
            )
            payload = {"result": "ok"}
            self.assertEqual(client.complete(payload), payload)
            stats = pool.stats()
            self.assertEqual(stats["successes"], 1)
            self.assertEqual(stats["active_leases"], 0)


if __name__ == "__main__":
    unittest.main()
