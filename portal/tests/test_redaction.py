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


def test_uvicorn_access_args_tuple_preserved():
    # uvicorn's AccessFormatter unpacks args as
    # (client_addr, method, full_path, http_version, status_code).
    # The filter must redact the path in place without clobbering args.
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, "x", 1,
        '%s - "%s %s HTTP/%s" %d',
        ("10.0.0.1:1234", "GET", "/sign-in?token=SECRET", "1.1", 200),
        None,
    )
    _RedactingFilter().filter(record)
    assert isinstance(record.args, tuple) and len(record.args) == 5
    assert record.args[2] == "/sign-in?token=<redacted>"
    assert "SECRET" not in record.getMessage()
