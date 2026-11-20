import sys
from types import SimpleNamespace
from unittest.mock import Mock

from scripts import demo_singlestore


def test_shared_cluster_settings_preserve_literal_password_and_require_tls(monkeypatch):
    for name, value in {
        "HOST": "sql.example.test",
        "PORT": "3333",
        "USER": "db_user",
        "PASSWORD": "literal%40#${value}",
        "DATABASE": "db_demo",
    }.items():
        monkeypatch.setenv(f"SINGLESTORE_{name}", value)

    settings = demo_singlestore.database_settings()
    assert settings["port"] == 3333
    assert settings["database"] == "db_demo"
    assert settings["password"] == "literal%40#${value}"
    assert settings["ssl_verify_identity"] is True
    assert settings["ssl_verify_cert"] is True
    assert settings["ssl_disabled"] is False


def test_rejected_connection_keeps_demo_source_unopened(monkeypatch, capsys):
    class OperationalError(Exception):
        errno = 1045

    connect = Mock(side_effect=OperationalError("sensitive connection details"))
    read_source = Mock()
    monkeypatch.setitem(sys.modules, "singlestoredb", SimpleNamespace(connect=connect))
    monkeypatch.setattr(demo_singlestore, "demo_session", read_source)
    monkeypatch.setattr(demo_singlestore, "load_dotenv", Mock())
    monkeypatch.setattr(demo_singlestore.signal, "signal", Mock())
    monkeypatch.setattr(demo_singlestore.signal, "alarm", Mock())
    monkeypatch.setenv("SINGLESTORE_URL", "mysql://db_user:local-only@sql.example.test:3333/db_demo")
    monkeypatch.setattr(sys, "argv", ["demo_singlestore.py", "publish"])

    assert demo_singlestore.main() == 2
    captured = capsys.readouterr()
    assert "OperationalError 1045 (SQL authentication rejected)" in captured.err
    assert "sensitive connection details" not in captured.err
    connect.assert_called_once()
    read_source.assert_not_called()
