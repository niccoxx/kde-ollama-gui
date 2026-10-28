"""Funzioni di utilita' senza interfaccia."""
import pytest

import ollama_gui as g


@pytest.mark.parametrize("given, expected", [
    ("", "http://127.0.0.1:11434"),
    (None, "http://127.0.0.1:11434"),
    ("localhost", "http://localhost:11434"),
    ("192.168.1.50:8080", "http://192.168.1.50:8080"),
    ("0.0.0.0:11434", "http://127.0.0.1:11434"),
    ("http://server/", "http://server:11434"),
    ("https://ollama.example.com", "https://ollama.example.com"),
    ("  http://host:1234/  ", "http://host:1234"),
])
def test_normalize_host(given, expected):
    assert g.normalize_host(given) == expected


def test_default_host_uses_env(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "0.0.0.0:9999")
    assert g.default_host() == "http://127.0.0.1:9999"


@pytest.mark.parametrize("n, expected", [
    (0, "0 B"),
    (512, "512 B"),
    (2048, "2.0 KB"),
    (5 * 1024 ** 3, "5.0 GB"),
    (3 * 1024 ** 5, "3072.0 TB"),
])
def test_human_size(n, expected):
    assert g.human_size(n) == expected


def test_split_think_separates_reasoning():
    assert g.split_think("<think> piano </think>Risposta") == ("piano", "Risposta")


def test_split_think_unclosed_block_while_streaming():
    assert g.split_think("<think>sto pensando") == ("sto pensando", "")


def test_split_think_multiple_blocks_and_empty():
    assert g.split_think("<think>a</think>x<think> </think><think>b</think>y") == ("a\nb", "xy")
    assert g.split_think(None) == ("", "")
    assert g.split_think("solo testo") == ("", "solo testo")


def test_esc_br():
    assert g.esc_br("<b>&\nriga") == "&lt;b&gt;&amp;<br>riga"


def test_md_to_html_renders_code_and_tables():
    h = g.md_to_html("**grassetto**\n\n```python\nprint(1)\n```\n\n| a | b |\n|---|---|\n| 1 | 2 |")
    assert "<strong>grassetto</strong>" in h
    assert "<pre>" in h and "print(1)" in h
    assert '<table border="1"' in h


def test_friendly_error():
    assert "Impossibile contattare Ollama" in g.friendly_error(Exception("Connection refused"))
    assert g.friendly_error("model not found") == "model not found"


def test_new_chat_defaults():
    c = g.new_chat("llama3.2")
    assert c["model"] == "llama3.2" and c["messages"] == [] and c["title"] == "Nuova chat"
    assert g.new_chat()["id"] != c["id"]
