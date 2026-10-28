"""Widget e finestre: impostazioni, schede dei suggerimenti, rendering dei messaggi."""
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

import ollama_gui as g
from conftest import wait


# ---- dialogo Impostazioni → Aspetto
def test_settings_dialog_preview_and_accept(win):
    d = g.SettingsDialog(win)
    d.theme.setCurrentIndex(2)
    d._swatches["#8b5cf6"].click()
    wait(100)
    assert win.c["dark"] is True and win.c["accent"] == "#8b5cf6"  # anteprima immediata
    d.accept()
    saved = g.Settings()
    assert saved["theme"] == 2 and saved["accent"] == "#8b5cf6"


def test_settings_dialog_cancel_restores(win):
    before = dict(win.c)
    d = g.SettingsDialog(win)
    d.theme.setCurrentIndex(2 if not before["dark"] else 1)
    d._swatches["#ef4444"].click()
    wait(100)
    assert win.c != before
    d.reject()
    wait(100)
    assert win.c == before
    assert g.Settings()["accent"] == ""  # niente salvato


def test_settings_dialog_system_swatch_clears_accent(win):
    win.settings["accent"] = "#10b981"
    d = g.SettingsDialog(win)
    assert d._swatches["#10b981"].isChecked()
    d._swatches[""].click()
    d.accept()
    assert g.Settings()["accent"] == ""


def test_settings_dialog_shows_saved_custom_color(win):
    win.settings["accent"] = "#123456"  # non e' tra i colori predefiniti
    d = g.SettingsDialog(win)
    assert d.btn_custom.isChecked()
    assert d.btn_custom.property("color") == "#123456"


# ---- schermata iniziale
def test_suggestion_card_click_fills_input(win):
    card = win.findChildren(g.SuggestionCard)[0]
    QTest.mouseClick(card, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))
    assert win.input.toPlainText() == g.SUGGESTIONS[0][2]


def test_suggestion_card_ignores_right_click(win):
    card = win.findChildren(g.SuggestionCard)[0]
    QTest.mouseClick(card, Qt.MouseButton.RightButton, pos=QPoint(10, 10))
    assert win.input.toPlainText() == ""


def test_one_card_per_suggestion(win):
    assert len(win.findChildren(g.SuggestionCard)) == len(g.SUGGESTIONS)


# ---- messaggi
def test_render_markdown_code_block_is_copyable(win):
    h = win.render_markdown("```python\nprint('ciao')\n```")
    assert "copycode:" in h and "python" in h
    assert f'bgcolor="{win.c["codehead"]}"' in h  # intestazione distinta dal corpo
    assert "print('ciao')" in list(win._codes.values())


def test_chat_renders_messages(win):
    c = g.new_chat("llama3.2")
    c["messages"] = [
        {"role": "user", "content": "ciao", "display": "ciao"},
        {"role": "assistant", "model": "llama3.2", "content": "<think>ok</think>Salve!"},
    ]
    win.select_chat(c)
    assert win.stack.currentIndex() == 1  # vista della chat, non la schermata iniziale
    assert [type(w).__name__ for w in win._rows] == ["UserMessage", "AssistantMessage"]
    assistant = win._rows[1]
    assert "Salve!" in assistant.body.toPlainText()
    assert "ok" not in assistant.body.toPlainText()  # il ragionamento resta separato


def test_empty_chat_shows_hero(win):
    win.select_chat(g.new_chat())
    assert win.stack.currentIndex() == 0


# ---- MenuPicker
def test_menu_picker_api(app):
    picker = g.MenuPicker(lambda t: t or "vuoto")
    seen = []
    picker.currentTextChanged.connect(seen.append)
    picker.addItems(["a", "b"])
    assert picker.currentText() == "a" and picker.count() == 2
    picker.setCurrentText("b")
    picker.setCurrentText("inesistente")
    assert picker.currentIndex() == 1
    assert seen == ["a", "b"]
    picker.clear()
    assert picker.currentText() == "" and picker.text().startswith("vuoto")
