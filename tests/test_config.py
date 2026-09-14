from novera.config import Settings


def test_defaults() -> None:
    s = Settings(_env_file=None)
    assert s.platform_name == "Novera"
    assert s.reporting_currency == "USD"


def test_brand_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("NOVERA_PLATFORM_NAME", "Valyra")
    monkeypatch.setenv("NOVERA_REPORTING_CURRENCY", "EUR")
    s = Settings(_env_file=None)
    assert s.platform_name == "Valyra"
    assert s.reporting_currency == "EUR"
