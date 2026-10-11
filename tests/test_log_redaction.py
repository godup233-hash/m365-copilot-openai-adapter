"""测试 10：日志不泄露敏感凭据。"""
import logging

from app.core.logging import RedactFilter, setup_logging


def test_bearer_token_redacted(capsys):
    setup_logging("INFO", extra_secrets=["test-key-secret-value"])
    logger = logging.getLogger("m365_adapter.test_redact")
    logger.info("upstream call failed with Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9abcdef")
    logger.info("adapter key was test-key-secret-value ok?")
    text = capsys.readouterr().err
    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9abcdef" not in text
    assert "Bearer ***" in text
    assert "test-key-secret-value" not in text


def test_redact_filter_directly():
    f = RedactFilter(extra_secrets=[])
    record = logging.LogRecord("x", logging.INFO, __file__, 1,
                               "token=sk-abc123&access_token=deadbeef", (), None)
    f.filter(record)
    msg = record.getMessage()
    assert "deadbeef" not in msg
