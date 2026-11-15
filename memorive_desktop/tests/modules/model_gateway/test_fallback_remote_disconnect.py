"""RemoteDisconnected retry classification and bounded fallback contract."""
from __future__ import annotations

import http.client
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from model_gateway.errors import ApiKeyMissing, DependencyError, SlotDisabled
from model_gateway.fallback import (
    CLASS_ANALYSIS_VERIFY,
    ROOT_TRANSIENT,
    ROOT_TRANSIENT_EXHAUSTED,
    FallbackPolicy,
    _root_class,
)


class RemoteDisconnectedFallbackTest(unittest.TestCase):
    def setUp(self) -> None:
        self.slot = SimpleNamespace(
            provider="anthropic",
            model_id="claude-sonnet-5",
        )

    @staticmethod
    def _disconnect() -> http.client.RemoteDisconnected:
        return http.client.RemoteDisconnected(
            "Remote end closed connection without response"
        )

    @staticmethod
    def _incomplete_read() -> http.client.IncompleteRead:
        return http.client.IncompleteRead(b'{"partial":', 128)

    def test_remote_disconnected_is_transient(self) -> None:
        self.assertEqual(
            _root_class(self._disconnect()),
            ROOT_TRANSIENT,
        )

    def test_incomplete_read_is_transient(self) -> None:
        self.assertEqual(
            _root_class(self._incomplete_read()),
            ROOT_TRANSIENT,
        )

    def test_incomplete_read_retries_until_success(self) -> None:
        calls = 0

        def dispatch():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise self._incomplete_read()
            return {"text": "ok"}

        with patch("model_gateway.fallback.log_error") as mocked_log:
            result, retries = FallbackPolicy(backoff=(0, 0)).run(
                "distill",
                self.slot,
                dispatch,
            )

        self.assertEqual(result, {"text": "ok"})
        self.assertEqual(retries, 1)
        self.assertEqual(calls, 2)
        self.assertEqual(mocked_log.call_count, 1)
        self.assertEqual(mocked_log.call_args.kwargs["context"]["action"], "retry")

    def test_non_connection_errors_are_not_retried(self) -> None:
        cases = (
            (
                "api_key",
                ApiKeyMissing("analysis", "MEMORIVE_TEST_KEY"),
            ),
            ("slot_disabled", SlotDisabled("disabled")),
            ("unknown", ValueError("unknown")),
            ("plain_os_error", OSError("disk read failed")),
        )
        for label, error in cases:
            with self.subTest(label=label):
                calls = 0

                def dispatch(error=error):
                    nonlocal calls
                    calls += 1
                    raise error

                with (
                    patch("model_gateway.fallback.log_error") as mocked_log,
                    self.assertRaises(DependencyError),
                ):
                    FallbackPolicy(backoff=(0, 0)).run(
                        "analysis",
                        self.slot,
                        dispatch,
                    )

                contexts = [
                    call.kwargs["context"]
                    for call in mocked_log.call_args_list
                ]
                self.assertEqual(calls, 1)
                self.assertEqual(len(contexts), 1)
                self.assertEqual(contexts[0]["action"], "block")
                self.assertEqual(contexts[0]["attempt"], 1)
                self.assertNotEqual(
                    contexts[0]["root_class"],
                    ROOT_TRANSIENT,
                )

    def test_remote_disconnected_retries_until_success(self) -> None:
        calls = 0

        def dispatch():
            nonlocal calls
            calls += 1
            if calls < 3:
                raise self._disconnect()
            return {"text": "ok"}

        with patch("model_gateway.fallback.log_error") as mocked_log:
            result, retries = FallbackPolicy(backoff=(0, 0)).run(
                "analysis",
                self.slot,
                dispatch,
            )

        contexts = [
            call.kwargs["context"]
            for call in mocked_log.call_args_list
        ]
        self.assertEqual(result, {"text": "ok"})
        self.assertEqual(retries, 2)
        self.assertEqual(calls, 3)
        self.assertEqual(
            [context["action"] for context in contexts],
            ["retry", "retry"],
        )
        self.assertEqual(
            [context["attempt"] for context in contexts],
            [1, 2],
        )
        self.assertTrue(
            all(
                context["root_class"] == ROOT_TRANSIENT
                for context in contexts
            )
        )

    def test_remote_disconnected_exhausts_after_three_attempts(self) -> None:
        calls = 0

        def dispatch():
            nonlocal calls
            calls += 1
            raise self._disconnect()

        with (
            patch("model_gateway.fallback.log_error") as mocked_log,
            self.assertRaises(DependencyError) as caught,
        ):
            FallbackPolicy(backoff=(0, 0)).run(
                "analysis",
                self.slot,
                dispatch,
            )

        contexts = [
            call.kwargs["context"]
            for call in mocked_log.call_args_list
        ]
        self.assertEqual(calls, 3)
        self.assertEqual(caught.exception.error_class, CLASS_ANALYSIS_VERIFY)
        self.assertEqual(
            caught.exception.root_class,
            ROOT_TRANSIENT_EXHAUSTED,
        )
        self.assertEqual(
            [context["action"] for context in contexts],
            ["retry", "retry", "block"],
        )
        self.assertEqual(
            [context["attempt"] for context in contexts],
            [1, 2, 3],
        )
        self.assertEqual(
            contexts[-1]["root_class"],
            ROOT_TRANSIENT_EXHAUSTED,
        )

    def test_call_specific_retry_ceiling_one_stops_after_two_attempts(self) -> None:
        calls = 0

        def dispatch():
            nonlocal calls
            calls += 1
            raise self._disconnect()

        with (
            patch("model_gateway.fallback.log_error") as mocked_log,
            self.assertRaises(DependencyError),
        ):
            FallbackPolicy(backoff=(0,), max_retries=1).run(
                "verify",
                self.slot,
                dispatch,
            )

        self.assertEqual(calls, 2)
        self.assertEqual(
            [call.kwargs["context"]["action"] for call in mocked_log.call_args_list],
            ["retry", "block"],
        )
        self.assertTrue(
            all(call.kwargs["context"]["max_attempts"] == 2 for call in mocked_log.call_args_list)
        )


if __name__ == "__main__":
    unittest.main()
