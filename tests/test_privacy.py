from data_market_probe.privacy import redact_sensitive, redact_text


def test_redacts_common_contact_and_identity_values() -> None:
    result = redact_text("电话13812345678 邮箱alice@example.com 身份证110101199001011234")
    assert "138****5678" in result
    assert "al***@example.com" in result
    assert "110101********1234" in result
    assert "13812345678" not in result


def test_redaction_walks_nested_api_payloads() -> None:
    result = redact_sensitive({"contacts": ["13900001111"], "count": 2})
    assert result == {"contacts": ["139****1111"], "count": 2}
