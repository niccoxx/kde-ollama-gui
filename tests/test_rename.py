"""Nome dell'app (Pastura), icona e migrazione dalle cartelle di kde-ollama-gui."""
from pathlib import Path

import pytest
from PySide6.QtGui import QColor

import pastura as g

ROOT = Path(g.__file__).resolve().parent


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    paths = {
        "old_config": tmp_path / "config" / "ollama-gui",
        "old_data": tmp_path / "share" / "ollama-gui",
        "new_config": tmp_path / "config" / "pastura",
        "new_data": tmp_path / "share" / "pastura",
    }
    monkeypatch.setattr(g, "LEGACY_CONFIG_DIR", paths["old_config"])
    monkeypatch.setattr(g, "LEGACY_DATA_DIR", paths["old_data"])
    monkeypatch.setattr(g, "CONFIG_DIR", paths["new_config"])
    monkeypatch.setattr(g, "DATA_DIR", paths["new_data"])
    monkeypatch.setattr(g, "CHATS_DIR", paths["new_data"] / "chats")
    return paths


def _legacy(dirs):
    dirs["old_config"].mkdir(parents=True)
    (dirs["old_config"] / "settings.json").write_text('{"theme": 2, "accent": "#ef4444"}', "utf-8")
    (dirs["old_data"] / "chats").mkdir(parents=True)
    chat = g.new_chat("llama3.2")
    chat["messages"] = [{"role": "user", "content": "ciao"}]
    (dirs["old_data"] / "chats" / f"{chat['id']}.json").write_text(__import__("json").dumps(chat), "utf-8")
    return chat


def test_migration_moves_settings_and_chats(dirs):
    chat = _legacy(dirs)
    assert g.migrate_legacy_dirs() == [dirs["new_config"], dirs["new_data"]]
    assert not dirs["old_config"].exists() and not dirs["old_data"].exists()
    s = g.Settings()
    assert s["theme"] == 2 and s["accent"] == "#ef4444"
    assert [c["id"] for c in g.ChatStore().load_all()] == [chat["id"]]


def test_migration_does_not_overwrite_existing_new_dirs(dirs):
    _legacy(dirs)
    dirs["new_config"].mkdir(parents=True)
    (dirs["new_config"] / "settings.json").write_text('{"theme": 1}', "utf-8")
    assert g.migrate_legacy_dirs() == [dirs["new_data"]]  # solo i dati, la config nuova c'era gia'
    assert dirs["old_config"].exists()  # la vecchia resta intatta
    assert g.Settings()["theme"] == 1


def test_migration_without_legacy_dirs_does_nothing(dirs):
    assert g.migrate_legacy_dirs() == []
    assert not dirs["new_config"].exists() and not dirs["new_data"].exists()


def test_paths_use_new_name():
    assert g.APP_NAME == "Pastura" and g.APP_ID == "pastura"
    assert g.XDG_CONFIG / "pastura" == Path(str(g.LEGACY_CONFIG_DIR).replace("ollama-gui", "pastura"))
    assert g.LEGACY_DATA_DIR.name == "ollama-gui"


def test_installed_svg_matches_app_icon():
    assert (ROOT / "pastura.svg").read_text("utf-8") == g.icon_svg()


def test_icon_follows_accent(app):
    img = g.make_icon("#ef4444", "#ffffff").pixmap(64, 64).toImage()
    assert img.pixelColor(6, 32).name() == "#ef4444"  # bordo del cerchio: colore d'accento
    assert img.pixelColor(0, 0).alpha() == 0  # angolo trasparente


def test_main_window_uses_new_name_and_icon(win):
    texts = {lbl.text() for lbl in win.findChildren(g.QLabel)}
    assert "Pastura" in texts and "Ollama" not in texts
    assert win.windowTitle() == "Pastura"
    accent = QColor(win.c["accent"]).name()
    for label in (win.side_logo, win.hero_logo):
        img = label.pixmap().toImage().scaled(64, 64)
        assert img.pixelColor(6, 32).name() == accent


def test_icon_updates_with_accent(win):
    from conftest import wait
    g.apply_appearance(2, "#10b981")
    wait(100)
    img = win.windowIcon().pixmap(64, 64).toImage()
    assert img.pixelColor(6, 32).name() == "#10b981"


def test_install_script_uses_new_names():
    sh = (ROOT / "install.sh").read_text("utf-8")
    for needle in ("pastura-app", '"$BIN_DIR/pastura"', "pastura.desktop", "Icon=pastura",
                   "Comment=A native Qt desktop client for Ollama", "icons/hicolor/scalable/apps", "pastura.svg"):
        assert needle in sh
    # rimuove la vecchia installazione, ma non impostazioni e chat (servono alla migrazione)
    assert 'rm -rf "$DATA_HOME/ollama-gui-app"' in sh
    assert 'rm -f "$BIN_DIR/ollama-gui" "$DESK_DIR/ollama-gui.desktop"' in sh
    assert 'rm -rf "$DATA_HOME/ollama-gui"\n' not in sh
