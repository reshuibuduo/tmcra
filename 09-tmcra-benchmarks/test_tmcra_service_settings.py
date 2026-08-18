from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from tmcra_service.settings import SettingsError, ServiceSettings


class ServiceSettingsWorkerTests(unittest.TestCase):
    def test_worker_concurrency_defaults_to_four(self) -> None:
        with patch.dict(
            os.environ,
            {"TMCRA_SERVICE_PUBLIC_BASE_URL": "https://example.invalid"},
            clear=True,
        ):
            self.assertEqual(ServiceSettings.from_env().worker_concurrency, 4)

    def test_worker_concurrency_reads_positive_integer(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TMCRA_SERVICE_PUBLIC_BASE_URL": "https://example.invalid",
                "TMCRA_SERVICE_WORKER_CONCURRENCY": "3",
            },
            clear=True,
        ):
            self.assertEqual(ServiceSettings.from_env().worker_concurrency, 3)

    def test_worker_concurrency_rejects_non_positive_values(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TMCRA_SERVICE_PUBLIC_BASE_URL": "https://example.invalid",
                "TMCRA_SERVICE_WORKER_CONCURRENCY": "0",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(
                SettingsError, "TMCRA_SERVICE_WORKER_CONCURRENCY must be positive"
            ):
                ServiceSettings.from_env()

    def test_evolution_and_index_defaults(self) -> None:
        with patch.dict(
            os.environ,
            {"TMCRA_SERVICE_PUBLIC_BASE_URL": "https://example.invalid"},
            clear=True,
        ):
            settings = ServiceSettings.from_env()
        self.assertEqual(settings.slow_dirty_token_threshold, 32_000)
        self.assertEqual(settings.slow_dirty_user_turn_threshold, 64)
        self.assertEqual(settings.slow_max_age_seconds, 86_400.0)
        self.assertEqual(settings.slow_min_token_threshold, 4_000)
        self.assertEqual(settings.slow_min_user_turn_threshold, 8)
        self.assertEqual(settings.slow_min_interval_seconds, 1_800.0)
        self.assertEqual(settings.index_dirty_threshold, 16)
        self.assertEqual(settings.index_max_age_seconds, 2.0)
        self.assertEqual(settings.scheduler_interval_seconds, 1.0)
        self.assertTrue(settings.preload_online_engine)

    def test_online_engine_preload_can_be_disabled(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TMCRA_SERVICE_PUBLIC_BASE_URL": "https://example.invalid",
                "TMCRA_SERVICE_PRELOAD_ONLINE_ENGINE": "off",
            },
            clear=True,
        ):
            self.assertFalse(ServiceSettings.from_env().preload_online_engine)

    def test_online_engine_preload_rejects_invalid_boolean(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TMCRA_SERVICE_PUBLIC_BASE_URL": "https://example.invalid",
                "TMCRA_SERVICE_PRELOAD_ONLINE_ENGINE": "sometimes",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(
                SettingsError, "TMCRA_SERVICE_PRELOAD_ONLINE_ENGINE must be a boolean"
            ):
                ServiceSettings.from_env()


if __name__ == "__main__":
    unittest.main()
