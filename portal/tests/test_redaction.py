import logging

from portal.app import _RedactingFilter, _TOKEN_QS


def test_token_query_param_redacted_in_log_messages():
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, "x", 1,
        'GET /sign-in?token=ABCDEF HTTP/1.1 200',
        (), None,
    )
    f = _RedactingFilter()
    f.filter(record)
    assert "token=<redacted>" in record.getMessage()
    assert "ABCDEF" not in record.getMessage()


def test_no_token_unchanged():
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, "x", 1,
        'GET /healthz HTTP/1.1 200', (), None,
    )
    f = _RedactingFilter()
    f.filter(record)
    assert "/healthz" in record.getMessage()
