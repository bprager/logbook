"""Verify a pre-seeded isolated graph before destructive integration tests."""
import re


def require_fixture(session, token):
    if not isinstance(token, str) or re.fullmatch(r'[a-f0-9]{64}', token) is None:
        raise ValueError('a unique isolated graph fixture token is required')
    row = session.run(
        'MATCH (n:LogbookIsolatedTestFixture {id:$token}) RETURN count(n) AS n', token=token
    ).single()
    if row is None or row['n'] != 1:
        raise ValueError('isolated graph fixture marker not found or ambiguous')
