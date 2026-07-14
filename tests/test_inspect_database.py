from src.utils.db_inspection import build_inspection_report, get_connection_url


def test_get_connection_url_prefers_environment_values(monkeypatch):
    monkeypatch.setenv("POSTGRES_HOST", "db")
    monkeypatch.setenv("POSTGRES_PORT", "5433")
    monkeypatch.setenv("POSTGRES_USER", "alice")
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret")
    monkeypatch.setenv("POSTGRES_DB", "research")

    assert get_connection_url() == "postgresql+psycopg://alice:secret@db:5433/research"


def test_build_inspection_report_organizes_tables_and_row_counts():
    report = build_inspection_report(
        "research",
        [("public", "orders", 123), ("public", "trades", 0)],
        "postgresql+psycopg://postgres:postgres@localhost:5432/research",
    )

    assert report["database"] == "research"
    assert report["table_count"] == 2
    assert report["tables"][0]["schema"] == "public"
    assert report["tables"][0]["name"] == "orders"
    assert report["tables"][0]["row_count"] == 123
    assert report["tables"][1]["row_count"] == 0
