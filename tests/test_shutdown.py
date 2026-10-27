"""Offline regression tests. All DLL loading and power requests are replaced."""
import ctypes
import importlib
import io
import logging
import os
from pathlib import Path
import sys
import threading
import types
import unittest
from unittest.mock import Mock, patch
from contextlib import redirect_stdout, redirect_stderr

# Import backend modules without loading ComfyUI or registering HTTP handlers.
package = types.ModuleType('_zznodes_shutdown_tests')
package.__path__ = [str(Path(__file__).resolve().parents[1] / 'nodes' / 'shutdown')]
sys.modules[package.__name__] = package
power = importlib.import_module(package.__name__ + '.windows_power')
controller = importlib.import_module(package.__name__ + '.controller')


class MemoryLogTests(unittest.TestCase):
    def setUp(self):
        self.sink = controller.MemoryLogHandler()
        for target, name, value in ((controller, '_handler', self.sink),
                                    (controller.logger, 'handlers', [self.sink])):
            override = patch.object(target, name, value)
            override.start()
            self.addCleanup(override.stop)

    def test_events_and_errors_are_visible_in_page_snapshot_without_console_output(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        root_sink = Mock(spec=logging.Handler)
        root_sink.level = logging.NOTSET
        with redirect_stdout(stdout), redirect_stderr(stderr), patch.object(logging.getLogger(), 'handlers', [root_sink]):
            node = controller.Controller(Mock())
            controller.logger.info('test runtime event')
            try:
                raise ValueError('test error private-test-token')
            except ValueError:
                controller.logger.exception('test Windows API failure')
            first = node.snapshot()['logs']
            self.assertEqual(first, node.snapshot()['logs'])
        self.assertEqual(stdout.getvalue(), '')
        self.assertEqual(stderr.getvalue(), '')
        root_sink.handle.assert_not_called()
        self.assertTrue(any('test runtime event' in item for item in first))
        self.assertIn('test Windows API failure', first[-1])
        self.assertIn('test error private-test-token', first[-1])
        self.assertFalse(any(isinstance(handler, logging.StreamHandler) for handler in controller.logger.handlers))

    def test_last_100_records_only_and_new_buffer_starts_empty(self):
        for number in range(105):
            controller.logger.info('event %s', number)
        saved = self.sink.snapshot()
        self.assertEqual(len(saved), 100)
        self.assertTrue(saved[0].endswith('event 5'))
        self.assertTrue(saved[-1].endswith('event 104'))
        saved.clear()
        self.assertEqual(len(self.sink.snapshot()), 100)
        self.assertEqual(controller.MemoryLogHandler().snapshot(), [])


@unittest.skipUnless(os.name == 'nt', 'Windows ctypes error handling')
class WindowsPowerTests(unittest.TestCase):
    def setUp(self):
        self.log_error = Mock()
        self.kernel = Mock()
        self.security = Mock()
        for dll, names in ((self.kernel, ('CloseHandle',)),
                           (self.security, ('OpenProcessToken', 'LookupPrivilegeValueW',
                                            'InitiateSystemShutdownExW'))):
            for name in names:
                getattr(dll, name).return_value = True
        self.kernel.GetCurrentProcess.return_value = -1
        self.security.AdjustTokenPrivileges.side_effect = self.adjust
        loader = patch.object(ctypes, 'WinDLL', side_effect=[self.kernel, self.security])
        loader.start()
        self.addCleanup(loader.stop)
        ctypes.set_last_error(0)

    @staticmethod
    def adjust(token, disable_all, new, length, previous, size):
        if previous is not None:
            old = ctypes.cast(previous, ctypes.POINTER(power.TOKEN_PRIVILEGES)).contents
            old.PrivilegeCount = 1
            old.Privileges[0].Luid.LowPart = 19
            old.Privileges[0].Attributes = 0
        return True

    def test_success_is_local_nonforced_shutdown_and_restores_privilege(self):
        power.shutdown_windows(self.log_error)
        self.security.InitiateSystemShutdownExW.assert_called_once_with(
            None, None, 0, False, False, power.SHUTDOWN_REASON)
        self.assertEqual(self.security.AdjustTokenPrivileges.call_count, 2)
        previous = self.security.AdjustTokenPrivileges.call_args.args[2]._obj
        self.assertEqual(previous.Privileges[0].Attributes, 0)
        self.assertEqual(previous.Privileges[0].Luid.LowPart, 19)
        self.kernel.CloseHandle.assert_called_once()
        self.assertEqual(self.kernel.GetCurrentProcess.restype, power.wintypes.HANDLE)

    def test_missing_privilege_never_requests_shutdown(self):
        def not_assigned(*args):
            ctypes.set_last_error(1300)
            return True
        self.security.AdjustTokenPrivileges.side_effect = not_assigned
        with self.assertRaises(OSError) as error:
            power.shutdown_windows(self.log_error)
        self.assertEqual(error.exception.winerror, 1300)
        self.security.InitiateSystemShutdownExW.assert_not_called()
        self.kernel.CloseHandle.assert_called_once()

    def test_rejected_shutdown_restores_privilege_and_preserves_error(self):
        def rejected(*args):
            ctypes.set_last_error(5)
            return False
        self.security.InitiateSystemShutdownExW.side_effect = rejected
        with self.assertRaises(OSError) as error:
            power.shutdown_windows(self.log_error)
        self.assertEqual(error.exception.winerror, 5)
        self.assertEqual(self.security.AdjustTokenPrivileges.call_count, 2)
        self.kernel.CloseHandle.assert_called_once()

    def test_lookup_failure_closes_token_without_shutdown(self):
        self.security.LookupPrivilegeValueW.return_value = False
        with self.assertRaises(OSError):
            power.shutdown_windows(self.log_error)
        self.security.InitiateSystemShutdownExW.assert_not_called()
        self.security.AdjustTokenPrivileges.assert_not_called()
        self.kernel.CloseHandle.assert_called_once()

    def test_open_failure_does_not_close_unopened_handle(self):
        self.security.OpenProcessToken.return_value = False
        with self.assertRaises(OSError):
            power.shutdown_windows(self.log_error)
        self.security.InitiateSystemShutdownExW.assert_not_called()
        self.kernel.CloseHandle.assert_not_called()

    def test_cleanup_failure_does_not_report_accepted_shutdown_as_failure(self):
        def adjust(*args):
            if args[4] is None:
                ctypes.set_last_error(5)
                return False
            return self.adjust(*args)
        self.security.AdjustTokenPrivileges.side_effect = adjust
        power.shutdown_windows(self.log_error)
        self.log_error.assert_called_once_with('恢复关机权限失败：WinError=%s', 5)
        self.kernel.CloseHandle.assert_called_once()


class QueueGateTests(unittest.TestCase):
    def setUp(self):
        self.queue = Mock()
        self.queue.mutex = threading.RLock()
        self.queue.get_tasks_remaining.return_value = 0
        self.queue.get_history.return_value = {}
        self.node = controller.Controller(lambda: self.queue)
        self.node.stop_file = Mock()
        self.node.stop_file.exists.return_value = False
        self.node.token = 'test-consent'
        self.node.state = 'waiting'
        shutdown = patch.object(controller, 'shutdown_windows')
        self.shutdown = shutdown.start()
        self.addCleanup(shutdown.stop)
        # Guard against accidental future calls bypassing the mock above.
        dll = patch.object(ctypes, 'WinDLL', create=True,
                           side_effect=AssertionError('Real DLL loading forbidden in tests'))
        dll.start()
        self.addCleanup(dll.stop)

    def test_exactly_30_seconds_idle_then_shutdown_once_without_countdown(self):
        self.assertTrue(self.node._tick(0, 100))
        self.assertEqual(self.node.state, 'idle')
        self.assertTrue(self.node._tick(0, 129.9))
        self.assertEqual(self.node.state, 'idle')
        self.assertEqual(self.node.remaining, 1)
        self.shutdown.assert_not_called()
        self.assertFalse(self.node._tick(0, 130))
        self.shutdown.assert_called_once_with(controller.logger.error)
        self.assertEqual(self.node.state, 'committing')
        self.assertFalse(self.node._tick(0, 131))
        self.shutdown.assert_called_once_with(controller.logger.error)

    def test_arm_needs_no_timing_settings(self):
        with patch.object(controller, 'os', types.SimpleNamespace(name='nt')):
            self.node.arm(0)
        self.assertEqual(self.node.state, 'armed')
        self.assertIn('30 秒后直接关机', self.node.snapshot()['logs'][-1])
        self.shutdown.assert_not_called()

    def test_new_submission_restarts_full_30_seconds(self):
        self.node._tick(0, 100)
        self.node.submitted()
        self.node._tick(0, 129)
        self.assertTrue(self.node._tick(0, 158.9))
        self.shutdown.assert_not_called()
        self.assertFalse(self.node._tick(0, 159))
        self.shutdown.assert_called_once_with(controller.logger.error)

    def test_cancel_invalidates_previous_monitor(self):
        self.node._tick(0, 100)
        self.node.cancel('test cancel')
        self.assertFalse(self.node._tick(0, 200))
        self.shutdown.assert_not_called()

    def test_new_task_resets_wait(self):
        self.node._tick(0, 100)
        self.queue.get_tasks_remaining.return_value = 1
        self.node._tick(0, 120)
        self.assertIsNone(self.node.idle_since)
        self.queue.get_tasks_remaining.return_value = 0
        self.node._tick(0, 121)
        self.assertEqual(self.node.state, 'idle')
        self.shutdown.assert_not_called()

    def test_final_queue_check_stops_request(self):
        self.node.idle_since = 100
        # Initial check, final locked check.
        self.queue.get_tasks_remaining.side_effect = [0, 1]
        self.assertTrue(self.node._tick(0, 130))
        self.shutdown.assert_not_called()

    def test_failed_job_cancels_request(self):
        self.queue.get_history.return_value = {'job': {'status': {'status_str': 'error'}}}
        self.assertFalse(self.node._tick(0, 120))
        self.assertEqual(self.node.state, 'cancelled')
        self.shutdown.assert_not_called()

    def test_api_failure_becomes_visible_error_and_invalidates_consent(self):
        self.node.idle_since = 0
        self.shutdown.side_effect = OSError('test: access denied')
        self.node._monitor(0)
        self.assertEqual(self.node.state, 'error')
        self.assertEqual(self.node.token, '')
        self.assertIn('access denied', self.node.message)


if __name__ == '__main__':
    unittest.main()
