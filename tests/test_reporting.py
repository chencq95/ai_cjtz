from data_market_probe.models import Platform
from data_market_probe.query import coverage_matrix


def test_coverage_matrix_contains_auditable_metrics(db_session) -> None:
    db_session.add(Platform(id=1, name="测试平台", onboarding_status="blocked"))
    db_session.flush()
    rows = coverage_matrix(db_session)
    assert rows[0]["platform_id"] == 1
    assert rows[0]["conclusion"] == "BLOCKED"
    assert rows[0]["collections"] == []
