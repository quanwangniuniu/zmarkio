"""Shared test helpers for the agent app."""
from unittest.mock import MagicMock


def _returning(value):
    return value
    yield  # makes this a generator


class IterAnalysisMock(MagicMock):
    """Mock for ``agent.services.analysis._iter_analysis``.

    Configure it like a plain function mock (``return_value``, ``side_effect``,
    ``call_args``); each call returns a generator that yields nothing and
    returns that value, so ``analysis = yield from _iter_analysis(...)`` works.

        @patch('agent.services.analysis._iter_analysis', new_callable=IterAnalysisMock)
    """

    def __call__(self, *args, **kwargs):
        return _returning(super().__call__(*args, **kwargs))

    def _get_child_mock(self, **kwargs):
        return MagicMock(**kwargs)
