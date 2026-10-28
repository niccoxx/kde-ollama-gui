"""Configurazione comune: Qt senza finestre, cartelle temporanee e niente rete."""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import ollama_gui as g


@pytest.fixture(scope="session")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    """Impostazioni e chat vanno in una cartella temporanea, mai in quelle dell'utente."""
    monkeypatch.setattr(g, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(g, "CHATS_DIR", tmp_path / "chats")
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    return tmp_path


@pytest.fixture
def reset_appearance(app):
    yield
    g.apply_appearance(0, "")
    QTest.qWait(50)


@pytest.fixture
def win(app, monkeypatch, reset_appearance):
    """Finestra principale senza chiamate al server Ollama."""
    monkeypatch.setattr(g.MainWindow, "check_server", lambda self: None)
    monkeypatch.setattr(g.MainWindow, "refresh_models", lambda self: None)
    monkeypatch.setattr(g.QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: False))
    w = g.MainWindow()
    w.show()
    QTest.qWait(20)
    yield w
    w.poll_timer.stop()
    w.render_timer.stop()
    w.hide()  # close() chiamerebbe QApplication.quit()
    w.deleteLater()
    QTest.qWait(20)


def wait(ms: int = 50):
    """Lascia propagare gli eventi (es. cambio di palette) prima delle verifiche."""
    QTest.qWait(ms)
