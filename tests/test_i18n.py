"""Traduzioni dell'interfaccia (italiano → inglese)."""
import ast
import re
import string
from pathlib import Path

import pytest

import pastura as g

SOURCE = Path(g.__file__).read_text("utf-8")


def _tr_literals() -> list[str]:
    """Tutti i testi passati a tr("...") come costante nel sorgente."""
    out = []
    for n in ast.walk(ast.parse(SOURCE)):
        if (isinstance(n, ast.Call) and getattr(n.func, "id", None) == "tr" and n.args
                and isinstance(n.args[0], ast.Constant)):
            out.append(n.args[0].value)
    return out


def _fields(s: str) -> list:
    return [(name, spec) for _, name, spec, _ in string.Formatter().parse(s) if name is not None]


def test_every_tr_literal_has_english_translation():
    missing = sorted(set(_tr_literals()) - set(g.EN))
    assert not missing, f"testi senza traduzione: {missing}"


def test_module_level_texts_have_translations():
    # questi vengono tradotti dove si usano (tr(title), tr(name)…)
    texts = [t for row in g.SUGGESTIONS for t in row] + [n for n, _ in g.ACCENTS] + [lbl for code, lbl in g.LANGUAGES if not code]  # "Italiano"/"English" restano invariati
    assert not [t for t in texts if t not in g.EN]


@pytest.mark.parametrize("key", sorted(g.EN))
def test_placeholders_match(key):
    assert _fields(key) == _fields(g.EN[key])


def test_no_untranslated_fstrings_with_italian_text():
    """Le f-string non passano da tr(): non devono contenere frasi visibili in italiano."""
    italian = re.compile(r"\b(il|la|di|per|non|nessun|clic|modello|modelli|chat)\b", re.I)
    bad = []
    for n in ast.walk(ast.parse(SOURCE)):
        if isinstance(n, ast.JoinedStr):
            text = "".join(v.value for v in n.values if isinstance(v, ast.Constant))
            if italian.search(re.sub(r"<[^>]+>|[\w-]+:[^;]+;|/[\w/]+", " ", text)):
                bad.append((n.lineno, text[:60]))
    assert not bad


def test_tr_switches_language():
    g.set_language("en")
    assert g.tr("Nuova chat") == "New chat"
    assert g.tr("testo sconosciuto") == "testo sconosciuto"  # senza traduzione resta l'originale
    g.set_language("it")
    assert g.tr("Nuova chat") == "Nuova chat"


@pytest.mark.parametrize("env, expected", [
    ({"LANG": "it_IT.UTF-8"}, "it"),
    ({"LANG": "en_US.UTF-8"}, "en"),
    ({"LANG": "de_DE.UTF-8"}, "en"),  # lingue non tradotte → inglese
    ({"LANGUAGE": "it:en", "LANG": "en_US.UTF-8"}, "it"),  # LANGUAGE ha la precedenza
    ({"LC_ALL": "en_GB.UTF-8", "LANG": "it_IT.UTF-8"}, "en"),
])
def test_system_language_from_env(monkeypatch, env, expected):
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(var, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert g.system_language() == expected


def test_auto_language_follows_system(monkeypatch):
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    g.set_language("")
    assert g.tr("Impostazioni") == "Settings"


def test_settings_default_language_is_auto():
    assert g.Settings()["language"] == ""


def test_main_window_in_english(app, monkeypatch, reset_appearance):
    s = g.Settings()
    s["language"] = "en"
    s.save()
    monkeypatch.setattr(g.MainWindow, "check_server", lambda self: None)
    monkeypatch.setattr(g.MainWindow, "refresh_models", lambda self: None)
    w = g.MainWindow()
    try:
        texts = {lbl.text() for lbl in w.findChildren(g.QLabel)}
        assert "How can I help you?" in texts
        assert w.btn_new.text().strip() == "New chat"
        assert w.input.placeholderText() == "Write a message…"
        assert w.search.placeholderText() == "Search chats…"
        assert {c.findChild(g.QLabel, "sugTitle").text() for c in w.findChildren(g.SuggestionCard)} == \
            {g.EN[t] for t, _, _ in g.SUGGESTIONS}
        assert w.think_combo.text().startswith("💭 Reasoning: auto")
        w.use_prompt(g.tr(g.SUGGESTIONS[0][2]))
        assert w.input.toPlainText() == "Explain in simple terms how public-key cryptography works."
        d = g.SettingsDialog(w)
        tabs = d.findChild(g.QTabWidget)
        assert [tabs.tabText(i) for i in range(tabs.count())] == ["General", "Appearance", "System prompt", "Server"]
    finally:
        w.poll_timer.stop()
        w.render_timer.stop()
        w.deleteLater()


def test_settings_dialog_saves_language(win):
    d = g.SettingsDialog(win)
    assert d.language.currentData() == ""
    assert d.language_note.isHidden()
    d.language.setCurrentIndex(d.language.findData("en"))
    assert not d.language_note.isHidden()  # avvisa che serve un riavvio
    d.accept()
    assert g.Settings()["language"] == "en"
