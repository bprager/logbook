import unittest
from unittest.mock import Mock

from tests.graph_fixture_guard import require_fixture


class GraphFixtureGuardTests(unittest.TestCase):
    def test_requires_unique_valid_marker_before_any_test_write(self):
        session = Mock()
        for token in (None, '', 'x'*64, 'a'*63):
            with self.subTest(token=token), self.assertRaises(ValueError):
                require_fixture(session, token)
        session.run.assert_not_called()
        session.run.return_value.single.return_value = {'n': 0}
        with self.assertRaisesRegex(ValueError, 'fixture'):
            require_fixture(session, 'a'*64)
        session.run.return_value.single.return_value = {'n': 2}
        with self.assertRaisesRegex(ValueError, 'fixture'):
            require_fixture(session, 'a'*64)
        session.run.return_value.single.return_value = {'n': 1}
        require_fixture(session, 'a'*64)
        query = session.run.call_args.args[0]
        self.assertIn('MATCH', query)
        self.assertNotIn('CREATE', query)
        self.assertNotIn('DELETE', query)
