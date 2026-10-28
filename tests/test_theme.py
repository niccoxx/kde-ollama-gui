"""Tema, colore d'accento e reazione dell'interfaccia ai cambi di palette."""
import pytest
from PySide6.QtGui import QColor, QFontDatabase

import pastura as g
from conftest import wait


def _lightness(name: str) -> int:
    return QColor(name).lightness()


@pytest.mark.parametrize("theme, dark", [(1, False), (2, True)])
def test_apply_appearance_forces_scheme(app, reset_appearance, theme, dark):
    g.apply_appearance(theme, "")
    wait()
    c = g.theme_colors()
    assert c["dark"] is dark
    assert (_lightness(c["bg"]) < 128) is dark


def test_apply_appearance_custom_accent(app, reset_appearance):
    g.apply_appearance(2, "#8b5cf6")
    wait()
    assert g.theme_colors()["accent"] == "#8b5cf6"


@pytest.mark.parametrize("accent, fg", [("#1e3a8a", "#ffffff"), ("#fde047", "#000000")])
def test_accent_text_is_readable(app, reset_appearance, accent, fg):
    g.apply_appearance(1, accent)
    wait()
    assert g.theme_colors()["accent_fg"] == fg


def test_forced_theme_system_accent_text_is_readable(app, reset_appearance, monkeypatch):
    """Regressione: accento di sistema chiaro (es. #a5cdff su macOS) con tema forzato → testo nero."""
    monkeypatch.setattr(g, "_SYS_ACCENT", ["#a5cdff"])
    g.apply_appearance(2, "")
    wait()
    c = g.theme_colors()
    assert c["accent"] == "#a5cdff" and c["accent_fg"] == "#000000"


def test_forced_theme_keeps_system_accent(app, reset_appearance):
    system = g._system_accent()
    g.apply_appearance(2, "#10b981")
    wait()
    g.apply_appearance(1, "")  # torna all'accento di sistema, non a quello scelto prima
    wait()
    assert g.theme_colors()["accent"] == system


def test_system_theme_restores_platform_palette(app, reset_appearance):
    before = g.theme_colors()
    g.apply_appearance(2, "#ef4444")
    wait()
    g.apply_appearance(0, "")
    wait()
    assert g.theme_colors() == before


def test_doc_css_uses_real_monospace_font(app):
    mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family()
    css = g.doc_css(g.theme_colors())
    assert f"font-family:'{mono}'" in css
    assert "font-family:monospace" not in css


def test_main_window_follows_palette_change(win):
    """Regressione: la finestra riceve PaletteChange (non ApplicationPaletteChange) e deve aggiornarsi."""
    g.apply_appearance(2, "#ef4444")
    wait(100)
    assert win.c["dark"] is True
    assert win.c["accent"] == "#ef4444"
    assert "#ef4444" in win.styleSheet()
    g.apply_appearance(1, "#10b981")
    wait(100)
    assert win.c["dark"] is False
    assert win.c["accent"] == "#10b981"


def test_main_window_applies_saved_theme_on_start(app, monkeypatch, reset_appearance):
    s = g.Settings()
    s["theme"], s["accent"] = 2, "#f97316"
    s.save()
    monkeypatch.setattr(g.MainWindow, "check_server", lambda self: None)
    monkeypatch.setattr(g.MainWindow, "refresh_models", lambda self: None)
    w = g.MainWindow()
    try:
        assert w.c["dark"] is True and w.c["accent"] == "#f97316"
    finally:
        w.poll_timer.stop()
        w.render_timer.stop()
        w.deleteLater()
