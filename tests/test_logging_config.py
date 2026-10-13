import json
import logging

from app.logging_config import (
    _CorrelationIdFilter,
    _JsonFormatter,
    current_correlation_id,
    new_correlation_id,
)


def test_new_correlation_id_is_unique_and_retrievable():
    first = new_correlation_id()
    second = new_correlation_id()

    assert first != second
    assert current_correlation_id() == second


def test_correlation_id_filter_attaches_current_id():
    new_correlation_id()
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "msg", None, None)

    _CorrelationIdFilter().filter(record)

    assert record.correlation_id == current_correlation_id()


def test_json_formatter_produces_valid_json_with_expected_fields():
    new_correlation_id()
    record = logging.LogRecord("app.agent.nodes", logging.WARNING, __file__, 1, "something happened", None, None)
    _CorrelationIdFilter().filter(record)

    formatted = _JsonFormatter().format(record)
    payload = json.loads(formatted)

    assert payload["level"] == "WARNING"
    assert payload["logger"] == "app.agent.nodes"
    assert payload["message"] == "something happened"
    assert payload["correlation_id"] == current_correlation_id()
