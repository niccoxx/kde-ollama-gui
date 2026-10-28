"""Impostazioni e archivio delle chat (su cartelle temporanee, vedi conftest)."""
import json

import pastura as g


def test_settings_defaults_include_theme():
    s = g.Settings()
    assert s["theme"] == 0
    assert s["accent"] == ""
    assert s["host"] == "http://127.0.0.1:11434"


def test_settings_roundtrip():
    s = g.Settings()
    s["theme"] = 2
    s["accent"] = "#ef4444"
    s.save()
    again = g.Settings()
    assert again["theme"] == 2 and again["accent"] == "#ef4444"


def test_settings_keeps_defaults_for_missing_keys():
    g.CONFIG_DIR.mkdir(parents=True)
    (g.CONFIG_DIR / "settings.json").write_text(json.dumps({"temperature": 0.7}), "utf-8")
    s = g.Settings()
    assert s["temperature"] == 0.7
    assert s["theme"] == 0  # chiave nuova: le vecchie configurazioni continuano a funzionare


def test_settings_ignores_corrupted_file():
    g.CONFIG_DIR.mkdir(parents=True)
    (g.CONFIG_DIR / "settings.json").write_text("{non json", "utf-8")
    assert g.Settings()["theme"] == 0


def _chat(title, updated, messages=True):
    c = g.new_chat("m")
    c["title"], c["updated"] = title, updated
    if messages:
        c["messages"] = [{"role": "user", "content": "ciao"}]
    return c


def test_chat_store_save_load_sorted_and_delete():
    store = g.ChatStore()
    old, new = _chat("vecchia", 1), _chat("nuova", 2)
    store.save(old)
    store.save(new)
    store.save(_chat("vuota", 3, messages=False))  # le chat vuote non si salvano
    assert [c["title"] for c in store.load_all()] == ["nuova", "vecchia"]
    store.delete(old)
    store.delete(old)  # eliminare due volte non e' un errore
    assert [c["title"] for c in store.load_all()] == ["nuova"]


def test_chat_store_skips_broken_files():
    store = g.ChatStore()
    store.save(_chat("ok", 1))
    (g.CHATS_DIR / "rotto.json").write_text("{", "utf-8")
    assert [c["title"] for c in store.load_all()] == ["ok"]
