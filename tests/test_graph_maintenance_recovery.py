import unittest
import json
from subprocess import CompletedProcess
from unittest.mock import Mock, call, patch

from logbook.graph_maintenance_recovery import DockerRecovery, recover


class GraphMaintenanceRecoveryTests(unittest.TestCase):
    def test_stops_worker_and_drains_transactions_before_unfencing(self):
        operations = Mock()
        operations.transactions.side_effect = [["123", "456"], []]
        recover(operations)
        self.assertEqual(operations.mock_calls, [
            call.verify_target(), call.stop_worker(), call.transactions(),
            call.terminate("123"), call.terminate("456"), call.transactions(),
            call.verify_target(), call.remove_fence(),
        ])

    def test_failure_to_terminate_keeps_fence(self):
        operations = Mock()
        operations.transactions.return_value = ["123"]
        operations.terminate.side_effect = RuntimeError("unavailable")
        with self.assertRaises(RuntimeError):
            recover(operations)
        operations.remove_fence.assert_not_called()

    def test_failure_to_drain_keeps_fence(self):
        operations = Mock()
        operations.transactions.return_value = ["123"]
        with self.assertRaisesRegex(RuntimeError, "did not drain"):
            recover(operations, attempts=2)
        operations.remove_fence.assert_not_called()

    def test_docker_target_and_worker_must_match(self):
        ops = DockerRecovery("a" * 64, 123, 7687, "logbook-graph-maintenance-test.service")
        fence = json.dumps({"nftables": [
            {"chain": {"name": "input", "hook": "input", "prio": -300, "policy": "accept"}},
            {"rule": {"expr": [
                {"match": {"op": "!=", "left": {"meta": {"key": "iifname"}}, "right": "lo"}},
                {"match": {"op": "==", "left": {"payload": {"protocol": "tcp", "field": "dport"}}, "right": 7687}},
                {"reject": {"type": "tcp reset"}},
            ]}},
        ]})
        ops.run = Mock(side_effect=[f'{"a" * 64} 123 true', fence, "loaded", "", "inactive"])
        ops.verify_target()
        ops.stop_worker()
        ops.run.assert_any_call(["systemctl", "stop", ops.unit])
        ops.run = Mock(return_value="different 123 true")
        with self.assertRaisesRegex(RuntimeError, "target container changed"):
            ops.verify_target()
        ops.run = Mock(side_effect=[f'{"a" * 64} 123 true', '{"nftables": []}'])
        with self.assertRaisesRegex(RuntimeError, "fence changed"):
            ops.verify_target()
        ops.run = Mock(return_value="not-found")
        with self.assertRaisesRegex(RuntimeError, "not loaded"):
            ops.stop_worker()
        ops.run = Mock(side_effect=["loaded", "", "active"])
        with self.assertRaisesRegex(RuntimeError, "did not stop"):
            ops.stop_worker()

    def test_transaction_listing_and_errors(self):
        ops = DockerRecovery("a" * 64, 123, 7687, "logbook-graph-maintenance-test.service")
        ops.query = Mock(return_value=[
            {"query": '["SHOW TRANSACTIONS"]'},
            {"query": '["CREATE (n)"]', "transaction_id": '"123"'},
        ])
        self.assertEqual(ops.transactions(), ["123"])
        ops.terminate("123")
        ops.query.assert_called_with('TERMINATE TRANSACTIONS "123"')
        with self.assertRaises(ValueError):
            ops.terminate('123"')
        ops.query.return_value = []
        with self.assertRaisesRegex(RuntimeError, "listing incomplete"):
            ops.transactions()
        ops.query.return_value = [{"query": '["CREATE (n)"]', "transaction_id": '"invalid"'}]
        with self.assertRaisesRegex(RuntimeError, "invalid transaction"):
            ops.transactions()

    def test_validation_and_query_execution(self):
        for args in [("x", 123, 7687, "logbook-graph-maintenance-test.service"),
                     ("a" * 64, 0, 7687, "logbook-graph-maintenance-test.service"),
                     ("a" * 64, 123, 0, "logbook-graph-maintenance-test.service"),
                     ("a" * 64, 123, 7687, "other.service")]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                DockerRecovery(*args)
        ops = DockerRecovery("a" * 64, 123, 7687, "logbook-graph-maintenance-test.service")
        with patch("logbook.graph_maintenance_recovery.subprocess.run", return_value=
                   CompletedProcess([], 0, '"n"\n1\n', "")) as run:
            self.assertEqual(ops.query("RETURN 1 AS n"), [{"n": "1"}])
            ops.remove_fence()
            self.assertEqual(run.call_count, 2)

    def test_fence_uses_one_atomic_ipv4_and_ipv6_table(self):
        ops = DockerRecovery("a" * 64, 123, 7687, "logbook-graph-maintenance-test.service")
        ops.run = Mock()
        ops.install_fence()
        self.assertEqual(ops.run.call_count, 1)
        args, rules = ops.run.call_args.args
        self.assertIn("nft", args)
        self.assertIn("add table inet", rules)
        self.assertIn('iifname != "lo"', rules)
        ops.remove_fence()
        self.assertIn("nft", ops.run.call_args.args[0])

    def test_transient_unusable_listing_is_retried_before_unfencing(self):
        from logbook.graph_maintenance_recovery import TransactionListingUnavailable
        operations = Mock()
        operations.transactions.side_effect = [TransactionListingUnavailable('bad ID'), []]
        with patch('logbook.graph_maintenance_recovery.time.sleep'):
            recover(operations, attempts=2)
        self.assertEqual(operations.transactions.call_count, 2)
        operations.remove_fence.assert_called_once()

    def test_persistent_unusable_listing_keeps_fence(self):
        from logbook.graph_maintenance_recovery import TransactionListingUnavailable
        operations = Mock()
        operations.transactions.side_effect = TransactionListingUnavailable('bad ID')
        with patch('logbook.graph_maintenance_recovery.time.sleep'):
            with self.assertRaisesRegex(RuntimeError, 'did not drain'):
                recover(operations, attempts=2)
        operations.remove_fence.assert_not_called()
