#!/usr/bin/env python3
"""
Ollama GUI per Linux
====================
Interfaccia desktop (Qt / PySide6) per Ollama, con le stesse funzioni
dell'app ufficiale per Windows:

  * chat in streaming con rendering Markdown e blocchi di codice copiabili
  * cronologia delle conversazioni raggruppata per data (ricerca, rinomina, elimina, esporta)
  * scelta del modello, download (pull) con barra di avanzamento, eliminazione
  * libreria di ollama.com integrata: ricerca, filtri, varianti (tag) e download con un clic
  * allegati: immagini (modelli con visione), file di testo/codice, PDF
    - trascinando i file nella finestra, con il pulsante o incollando un'immagine
  * modalita' "ragionamento" (thinking) per i modelli che la supportano
  * impostazioni: host, finestra di contesto, temperatura, prompt di sistema,
    keep_alive, icona nella barra di sistema
  * rigenera risposta, ferma generazione, copia risposta, statistiche token/s
  * avvio del server Ollama se non e' in esecuzione

L'aspetto segue i colori del tema di sistema (chiaro/scuro e colore d'accento di KDE).
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
import math
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import requests
from PySide6.QtCore import (
    QBuffer,
    QByteArray,
    QEvent,
    QIODevice,
    QLocale,
    QObject,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    QThread,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QDesktopServices,
    QFont,
    QFontDatabase,
    QFontMetrics,
    QGuiApplication,
    QIcon,
    QImage,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPen,
    QPalette,
    QPixmap,
    QShortcut,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

try:  # rendering Markdown piu' fedele; se manca si usa quello di Qt
    import markdown as _md
except ImportError:  # pragma: no cover
    _md = None

APP_NAME = "Ollama GUI"
XDG_CONFIG = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
XDG_DATA = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
CONFIG_DIR = XDG_CONFIG / "ollama-gui"
DATA_DIR = XDG_DATA / "ollama-gui"
CHATS_DIR = DATA_DIR / "chats"

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
MAX_TEXT_CHARS = 400_000
MAX_IMAGE_SIDE = 2048
COLUMN_WIDTH = 820  # larghezza massima della colonna dei messaggi

THINK_RE = re.compile(r"<think>(.*?)(?:</think>|$)", re.S)

# --------------------------------------------------------------------------- lingua
# I testi dell'interfaccia sono scritti in italiano e passano da tr(); EN contiene le traduzioni.
LANGUAGES = [("", "Automatica (sistema)"), ("it", "Italiano"), ("en", "English")]
_LANG = "it"


def system_language() -> str:
    """Lingua preferita dal sistema ("it" o "en")."""
    langs = []
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        v = os.environ.get(var, "")
        if v and not v.startswith(("C.", "POSIX")) and v != "C":
            langs += v.split(":")
            break
    if not langs and sys.platform == "darwin":
        # Qt avviato da Python (fuori da un'app .app) riporta l'inglese: si legge la preferenza di macOS
        try:
            out = subprocess.run(["defaults", "read", "-g", "AppleLanguages"], capture_output=True, text=True, timeout=2).stdout
            langs += re.findall(r'"?([A-Za-z]{2})[-_A-Za-z]*"?', out)
        except (OSError, subprocess.SubprocessError):
            pass
    langs += QLocale.system().uiLanguages()
    return "it" if langs and langs[0].lower().startswith("it") else "en"


def set_language(code: str):
    """Imposta la lingua dell'interfaccia: "it", "en" o "" (dalla lingua del sistema)."""
    global _LANG
    _LANG = code if code in ("it", "en") else system_language()


def tr(s: str) -> str:
    return s if _LANG == "it" else EN.get(s, s)


EN = {
    # generale
    "Nuova chat": "New chat",
    "  Nuova chat": "  New chat",
    "Nuova chat (Ctrl+N)": "New chat (Ctrl+N)",
    "Impossibile contattare Ollama. Il server e' in esecuzione? (pulsante «Avvia» in basso a sinistra)":
        "Can't reach Ollama. Is the server running? (“Start” button at the bottom left)",
    "Per allegare i PDF serve il pacchetto 'pypdf' (pip install pypdf).":
        "Attaching PDFs requires the 'pypdf' package (pip install pypdf).",
    "Errore": "Error",
    "Errore: ": "Error: ",
    "Annulla": "Cancel",
    "Elimina": "Delete",
    "Copia": "Copy",
    "Copiato ✓": "Copied ✓",
    "Rigenera": "Regenerate",
    "Riprova": "Retry",
    "Modello": "Model",
    "Modelli": "Models",
    "Impostazioni": "Settings",
    "Menu": "Menu",
    "Apri": "Open",
    "Esci": "Quit",
    "Avvia": "Start",
    "Server": "Server",
    "Aggiorna": "Refresh",
    "Cerca": "Search",
    "Scarica": "Download",
    # ollama.com
    "Impossibile raggiungere ollama.com: controlla la connessione a Internet.":
        "Can't reach ollama.com: check your Internet connection.",
    "ollama.com ha risposto con l'errore {}.": "ollama.com responded with error {}.",
    "Modello non trovato su ollama.com.": "Model not found on ollama.com.",
    # colori
    "Blu": "Blue", "Viola": "Purple", "Verde": "Green", "Arancione": "Orange",
    "Rosa": "Pink", "Rosso": "Red", "Grafite": "Graphite",
    # messaggi
    "Scrivi un messaggio…": "Write a message…",
    "Sta ragionando…": "Thinking…",
    "Ragionamento": "Reasoning",
    "Assistente": "Assistant",
    "codice": "code",
    "⧉ Copia": "⧉ Copy",
    # finestra Modelli
    "Scarica dalla memoria": "Unload from memory",
    "Installati": "Installed",
    "Dimensione": "Size",
    "Parametri": "Parameters",
    "Quantizzazione": "Quantization",
    "Modificato": "Modified",
    "Cerca nella libreria di Ollama, es. qwen, gemma, coder…": "Search the Ollama library, e.g. qwen, gemma, coder…",
    "Tutte le funzioni": "All capabilities",
    "Strumenti (tools)": "Tools",
    "Visione": "Vision",
    "Più popolari": "Most popular",
    "Più recenti": "Newest",
    "Nascondi i modelli disponibili solo nel cloud": "Hide cloud-only models",
    "Descrizione": "Description",
    "Funzioni": "Capabilities",
    "Dimensioni": "Sizes",
    "Aggiornato": "Updated",
    "Altri risultati": "More results",
    "Seleziona un modello per vederne le varianti (tag).": "Select a model to see its variants (tags).",
    "Contesto": "Context",
    "Libreria": "Library",
    "<b>Scarica un modello</b> — sceglilo dalla scheda «Libreria» o scrivi il nome":
        "<b>Download a model</b> — pick it from the “Library” tab or type its name",
    "Nome del modello da scaricare, es. llama3.2:3b": "Name of the model to download, e.g. llama3.2:3b",
    "Ricerca su ollama.com…": "Searching ollama.com…",
    "Installato (almeno una variante)": "Installed (at least one variant)",
    "Modello caricato da un utente della community": "Model uploaded by a community user",
    "Aggiornato il ": "Updated ",
    "Nessun modello trovato.": "No models found.",
    "{} modelli": "{} models",
    "Varianti di <b>{}</b>: caricamento…": "Variants of <b>{}</b>: loading…",
    "Varianti di <b>{}</b>: {}": "Variants of <b>{}</b>: {}",
    "Varianti di <b>{}</b> ({}) — doppio clic per scaricare": "Variants of <b>{}</b> ({}) — double-click to download",
    "Già installato": "Already installed",
    "Eseguito sui server di Ollama: richiede l'accesso con «ollama signin»":
        "Runs on Ollama's servers: requires signing in with “ollama signin”",
    "In memoria": "In memory",
    "{} modelli installati": "{} models installed",
    " · in memoria (●): {}": " · in memory (●): {}",
    "Elimina modello": "Delete model",
    "Eliminare «{}» dal disco?": "Delete “{}” from disk?",
    "Download di {}…": "Downloading {}…",
    "Download annullato.": "Download cancelled.",
    "Download completato.": "Download complete.",
    # Impostazioni
    "Verifica": "Test",
    "Indirizzo del server:": "Server address:",
    "Predefinito del modello": "Model default",
    "Lunghezza del contesto:": "Context length:",
    "Predefinita del modello": "Model default",
    "Temperatura:": "Temperature:",
    "vuoto = 5m · -1 = sempre in memoria · 0 = scarica subito · 30m":
        "empty = 5m · -1 = always in memory · 0 = unload immediately · 30m",
    "Mantieni in memoria:": "Keep in memory:",
    "Mostra il ragionamento dei modelli (thinking)": "Show the models' reasoning (thinking)",
    "Alla chiusura riduci nella barra di sistema": "Minimize to the system tray on close",
    "Generale": "General",
    "Lingua:": "Language:",
    "Automatica (sistema)": "Automatic (system)",
    "La lingua viene applicata al prossimo avvio.": "The language is applied on next start.",
    "Tema:": "Theme:",
    "Segui il sistema": "Follow the system",
    "Chiaro": "Light",
    "Scuro": "Dark",
    "Sistema": "System",
    "Usa il colore d'accento del sistema": "Use the system accent color",
    "Personalizzato…": "Custom…",
    "Colore d'accento:": "Accent color:",
    "Colore d'accento": "Accent color",
    "Aspetto": "Appearance",
    "Prompt di sistema (inviato all'inizio di ogni conversazione):":
        "System prompt (sent at the start of every conversation):",
    "Prompt di sistema": "System prompt",
    "Verifica in corso…": "Testing…",
    "✔ Connesso — Ollama {}": "✔ Connected — Ollama {}",
    # schermata iniziale
    "Come posso aiutarti?": "How can I help you?",
    "Modello: {}": "Model: {}",
    "nessun modello installato": "no model installed",
    "Spiegami un concetto": "Explain a concept",
    "come funziona la crittografia a chiave pubblica": "how public-key cryptography works",
    "Spiegami in modo semplice come funziona la crittografia a chiave pubblica.":
        "Explain in simple terms how public-key cryptography works.",
    "Scrivi del codice": "Write some code",
    "uno script Python che ordina i file per data": "a Python script that sorts files by date",
    "Scrivi uno script Python che sposta i file di una cartella in sottocartelle per anno e mese.":
        "Write a Python script that moves the files in a folder into subfolders by year and month.",
    "Riassumi un testo": "Summarize a text",
    "incolla un testo o trascina qui un PDF": "paste some text or drop a PDF here",
    "Riassumi il testo seguente in pochi punti chiave:\n\n": "Summarize the following text in a few key points:\n\n",
    "Dammi qualche idea": "Give me some ideas",
    "per un weekend fuori porta in autunno": "for an autumn weekend getaway",
    "Dammi cinque idee per un weekend fuori porta in autunno, con pro e contro.":
        "Give me five ideas for an autumn weekend getaway, with pros and cons.",
    # finestra principale
    "Ollama": "Ollama",
    "Nascondi la barra laterale (Ctrl+B)": "Hide the sidebar (Ctrl+B)",
    "Mostra la barra laterale (Ctrl+B)": "Show the sidebar (Ctrl+B)",
    "Cerca nelle chat…": "Search chats…",
    "Avvia il server Ollama": "Start the Ollama server",
    "Nessun modello": "No model",
    "Gestisci modelli…": "Manage models…",
    "Allega file o immagini (si possono anche trascinare nella finestra)":
        "Attach files or images (you can also drag them into the window)",
    "💭 Ragionamento: {}": "💭 Reasoning: {}",
    "Auto": "Auto",
    "Attivo": "On",
    "Disattivo": "Off",
    "Modalità ragionamento (per i modelli che la supportano)": "Reasoning mode (for models that support it)",
    "Invio per inviare · Maiusc+Invio per andare a capo · trascina qui file, immagini e PDF":
        "Enter to send · Shift+Enter for a new line · drop files, images and PDFs here",
    "Rinomina chat…": "Rename chat…",
    "Esporta chat in Markdown…": "Export chat as Markdown…",
    "Elimina chat": "Delete chat",
    "Rigenera risposta": "Regenerate response",
    "Copia ultima risposta": "Copy last response",
    "Modelli…": "Models…",
    "Impostazioni…": "Settings…",
    "Avvia server Ollama": "Start Ollama server",
    "Mostra/nascondi barra laterale": "Show/hide sidebar",
    "Informazioni": "About",
    "<b>{}</b><br>Interfaccia desktop per Ollama su Linux.<br>Server: {}":
        "<b>{}</b><br>Desktop interface for Ollama on Linux.<br>Server: {}",
    "Connesso a {}": "Connected to {}",
    "Non connesso": "Not connected",
    "Ollama non raggiungibile su {}": "Ollama not reachable at {}",
    "Ollama non trovato": "Ollama not found",
    "Il comando 'ollama' non e' installato.\n\nInstallalo con:\n  curl -fsSL https://ollama.com/install.sh | sh":
        "The 'ollama' command is not installed.\n\nInstall it with:\n  curl -fsSL https://ollama.com/install.sh | sh",
    "Avvio di Ollama…": "Starting Ollama…",
    "Nessun modello installato: apri «Modelli» per scaricarne uno.":
        "No model installed: open “Models” to download one.",
    "Allega file": "Attach files",
    " o immagini": " or images",
    " (questo modello non supporta le immagini)": " (this model doesn't support images)",
    # cronologia
    "Oggi": "Today",
    "Ieri": "Yesterday",
    "Ultimi 7 giorni": "Previous 7 days",
    "Ultimi 30 giorni": "Previous 30 days",
    "Meno recenti": "Older",
    "● sta scrivendo…": "● writing…",
    "Rinomina…": "Rename…",
    "Esporta in Markdown…": "Export as Markdown…",
    "Rinomina": "Rename",
    "Titolo:": "Title:",
    "Eliminare «{}»?": "Delete “{}”?",
    "Esporta chat": "Export chat",
    "*Modello: {}*": "*Model: {}*",
    "**Tu:**": "**You:**",
    "**Assistente:**": "**Assistant:**",
    "Chat esportata.": "Chat exported.",
    # allegati e invio
    "Immagine non leggibile.": "Unreadable image.",
    "Tipo di file non supportato (non e' testo).": "Unsupported file type (not text).",
    "\n[… file troncato …]": "\n[… file truncated …]",
    "Il file non contiene testo.": "The file contains no text.",
    "Allegato non valido": "Invalid attachment",
    "{} — clic per rimuovere": "{} — click to remove",
    "File allegato «{}»:\n```\n{}\n```": "Attached file “{}”:\n```\n{}\n```",
    "Immagini non supportate": "Images not supported",
    "Il modello «{}» non sembra supportare le immagini. Scegline uno con visione (es. llava, gemma3, qwen2.5vl).":
        "The model “{}” doesn't seem to support images. Pick one with vision (e.g. llava, gemma3, qwen2.5vl).",
    "Non c'e' nessun modello installato. Scaricane uno dalla finestra «Modelli».":
        "No model is installed. Download one from the “Models” window.",
    " · interrotta": " · stopped",
    "{} token · {:.1f} token/s{}": "{} tokens · {:.1f} tokens/s{}",
    "\nSuggerimento: scarica il modello dalla finestra «Modelli».": "\nTip: download the model from the “Models” window.",
    "Ferma la generazione (Esc)": "Stop generating (Esc)",
    "Invia (Invio)": "Send (Enter)",
    "Risposta copiata negli appunti.": "Response copied to the clipboard.",
    "Codice copiato negli appunti.": "Code copied to the clipboard.",
    # guida alle impostazioni del server
    '\n<h3>Impostazioni del server Ollama</h3>\n<p>Alcune opzioni (cartella dei modelli, accesso dalla rete) appartengono al\n<b>server</b>, non a questa interfaccia. Su Linux si impostano con systemd:</p>\n<pre>sudo systemctl edit ollama</pre>\n<p>e si aggiunge, ad esempio:</p>\n<pre>[Service]\nEnvironment="OLLAMA_HOST=0.0.0.0:11434"\nEnvironment="OLLAMA_MODELS=/percorso/dei/modelli"\nEnvironment="OLLAMA_CONTEXT_LENGTH=8192"\nEnvironment="OLLAMA_KEEP_ALIVE=30m"</pre>\n<p>poi:</p>\n<pre>sudo systemctl daemon-reload\nsudo systemctl restart ollama</pre>\n<ul>\n<li><b>OLLAMA_HOST=0.0.0.0:11434</b> espone Ollama alla rete locale (apri la porta nel firewall).</li>\n<li><b>OLLAMA_MODELS</b>: la cartella deve appartenere all\'utente <i>ollama</i>\n(<code>sudo chown -R ollama:ollama /percorso</code>).</li>\n</ul>\n<p>Per usare un Ollama su un altro computer, scrivi qui sopra il suo indirizzo,\nad esempio <code>http://192.168.1.50:11434</code>.</p>\n':
        '\n<h3>Ollama server settings</h3>\n<p>Some options (models folder, network access) belong to the\n<b>server</b>, not to this interface. On Linux they are set with systemd:</p>\n<pre>sudo systemctl edit ollama</pre>\n<p>and add, for example:</p>\n<pre>[Service]\nEnvironment="OLLAMA_HOST=0.0.0.0:11434"\nEnvironment="OLLAMA_MODELS=/path/to/models"\nEnvironment="OLLAMA_CONTEXT_LENGTH=8192"\nEnvironment="OLLAMA_KEEP_ALIVE=30m"</pre>\n<p>then:</p>\n<pre>sudo systemctl daemon-reload\nsudo systemctl restart ollama</pre>\n<ul>\n<li><b>OLLAMA_HOST=0.0.0.0:11434</b> exposes Ollama to the local network (open the port in the firewall).</li>\n<li><b>OLLAMA_MODELS</b>: the folder must belong to the <i>ollama</i> user\n(<code>sudo chown -R ollama:ollama /path</code>).</li>\n</ul>\n<p>To use Ollama on another computer, enter its address above,\nfor example <code>http://192.168.1.50:11434</code>.</p>\n',
}

# --------------------------------------------------------------------------- utilita'
def normalize_host(h: str) -> str:
    h = (h or "").strip() or "127.0.0.1:11434"
    if "://" not in h:
        h = "http://" + h
    h = h.replace("0.0.0.0", "127.0.0.1")
    try:
        u = urlparse(h)
        if u.port is None and u.scheme == "http":
            h = h.rstrip("/") + ":11434"
    except ValueError:
        pass
    return h.rstrip("/")


def default_host() -> str:
    return normalize_host(os.environ.get("OLLAMA_HOST", ""))


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def split_think(text: str) -> tuple[str, str]:
    """Separa i blocchi <think>…</think> dal testo. Ritorna (ragionamento, contenuto)."""
    thinking = "\n".join(t.strip() for t in THINK_RE.findall(text or "") if t.strip())
    content = THINK_RE.sub("", text or "").strip()
    return thinking, content


def md_to_html(text: str) -> str:
    if _md is not None:
        h = _md.markdown(text, extensions=["fenced_code", "tables", "sane_lists", "nl2br"])
        return h.replace("<table>", '<table border="1" cellspacing="0" cellpadding="5">')
    d = QTextDocument()
    d.setMarkdown(text)
    h = d.toHtml()
    m = re.search(r"<body[^>]*>(.*)</body>", h, re.S)
    return m.group(1) if m else html.escape(text).replace("\n", "<br>")


def esc_br(text: str) -> str:
    return html.escape(text).replace("\n", "<br>")


def make_icon() -> QIcon:
    pm = QPixmap(128, 128)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#111827"))
    p.drawRoundedRect(8, 8, 112, 112, 28, 28)
    p.setPen(QColor("white"))
    f = QFont()
    f.setBold(True)
    f.setPixelSize(74)
    p.setFont(f)
    p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, "O")
    p.end()
    return QIcon.fromTheme("ollama", QIcon(pm))


def pdf_text(path: str) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise RuntimeError(tr("Per allegare i PDF serve il pacchetto 'pypdf' (pip install pypdf).")) from e
    reader = PdfReader(path)
    return "\n\n".join((p.extract_text() or "") for p in reader.pages).strip()


# --------------------------------------------------------------------------- impostazioni e storico
class Settings(dict):
    DEFAULTS = {
        "host": "",
        "system_prompt": "",
        "num_ctx": 0,
        "temperature": -0.1,  # < 0 = predefinito del modello
        "keep_alive": "",
        "show_thinking": True,
        "close_to_tray": False,
        "last_model": "",
        "think_mode": 0,  # 0 auto, 1 attivo, 2 disattivo
        "geometry": "",
        "splitter": [260, 900],
        "sidebar": True,
        "theme": 0,  # 0 sistema, 1 chiaro, 2 scuro
        "accent": "",  # vuoto = colore d'accento del sistema
        "language": "",  # vuoto = lingua del sistema, "it" o "en"
    }

    def __init__(self):
        super().__init__(self.DEFAULTS)
        self.path = CONFIG_DIR / "settings.json"
        try:
            self.update(json.loads(self.path.read_text("utf-8")))
        except Exception:
            pass
        if not self["host"]:
            self["host"] = default_host()

    def save(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self, ensure_ascii=False, indent=2), "utf-8")
        tmp.replace(self.path)


def new_chat(model: str = "") -> dict:
    now = time.time()
    return {"id": uuid.uuid4().hex, "title": tr("Nuova chat"), "model": model, "created": now, "updated": now, "messages": []}


class ChatStore:
    def load_all(self) -> list[dict]:
        CHATS_DIR.mkdir(parents=True, exist_ok=True)
        chats = []
        for f in CHATS_DIR.glob("*.json"):
            try:
                c = json.loads(f.read_text("utf-8"))
                if c.get("messages"):
                    chats.append(c)
            except Exception:
                continue
        chats.sort(key=lambda c: -c.get("updated", 0))
        return chats

    def save(self, chat: dict):
        if not chat["messages"]:
            return
        CHATS_DIR.mkdir(parents=True, exist_ok=True)
        p = CHATS_DIR / f"{chat['id']}.json"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(chat, ensure_ascii=False), "utf-8")
        tmp.replace(p)

    def delete(self, chat: dict):
        try:
            (CHATS_DIR / f"{chat['id']}.json").unlink()
        except FileNotFoundError:
            pass


# --------------------------------------------------------------------------- API Ollama
class Ollama:
    def __init__(self, host: str):
        self.base = normalize_host(host)

    def set_host(self, host: str):
        self.base = normalize_host(host)

    def _get(self, path: str, timeout: float = 5):
        r = requests.get(self.base + path, timeout=timeout)
        r.raise_for_status()
        return r.json()

    def version(self) -> str:
        return self._get("/api/version")["version"]

    def tags(self) -> list[dict]:
        return self._get("/api/tags", timeout=10).get("models", [])

    def ps(self) -> list[dict]:
        return self._get("/api/ps", timeout=5).get("models", [])

    def show(self, name: str) -> dict:
        r = requests.post(self.base + "/api/show", json={"model": name}, timeout=15)
        r.raise_for_status()
        return r.json()

    def delete(self, name: str):
        r = requests.delete(self.base + "/api/delete", json={"model": name}, timeout=60)
        r.raise_for_status()

    def unload(self, name: str):
        requests.post(self.base + "/api/generate", json={"model": name, "keep_alive": 0}, timeout=30).raise_for_status()


class Library:
    """Catalogo di ollama.com/library. Non esiste un'API pubblica: si leggono le pagine HTML."""

    SITE = "https://ollama.com"
    CAPS = ["", "tools", "thinking", "vision", "embedding", "audio"]
    _TAG = re.compile(r"<[^>]+>")

    @classmethod
    def _text(cls, s: str) -> str:
        return " ".join(html.unescape(cls._TAG.sub("", s)).split())

    @classmethod
    def _page(cls, path: str, params: dict | None = None, fragment: bool = False) -> str:
        headers = {"User-Agent": f"{APP_NAME} (Linux)"}
        if fragment:  # le pagine successive alla prima arrivano come frammento htmx
            headers["HX-Request"] = "true"
        try:
            r = requests.get(cls.SITE + path, params=params, timeout=15, headers=headers)
        except requests.RequestException:
            raise RuntimeError(tr("Impossibile raggiungere ollama.com: controlla la connessione a Internet.")) from None
        if r.status_code == 404:
            raise RuntimeError(tr("Modello non trovato su ollama.com."))
        if not r.ok:
            raise RuntimeError(tr("ollama.com ha risposto con l'errore {}.").format(r.status_code))
        return r.text

    @classmethod
    def _badges(cls, blk: str, color: str) -> list[str]:
        pat = r'<span[^>]*class="[^"]*%s[^"]*"[^>]*>(.*?)</span>' % re.escape(color)
        return [cls._text(x) for x in re.findall(pat, blk, re.S)]

    @classmethod
    def search(cls, query: str = "", cap: str = "", newest: bool = False, page: int = 1) -> tuple[list[dict], bool]:
        """Ritorna (modelli, ci_sono_altre_pagine). I modelli della community hanno nome «utente/modello»."""
        params = {k: v for k, v in (("q", query), ("c", cap), ("o", "newest" if newest else ""), ("page", page if page > 1 else "")) if v}
        text = cls._page("/search", params, fragment=page > 1)
        out = []
        for blk in re.split(r"<li\b", text)[1:]:
            m = re.search(r'<a href="/([\w.-]+)/([\w.-]+)"', blk)
            if not m or m.group(1) in ("search", "blog", "download", "settings"):
                continue
            community = m.group(1) != "library"
            desc = re.search(r"<p\b[^>]*>(.*?)</p>", blk, re.S)
            upd = re.search(r'title="Updated ([^"]+)"[^>]*>([^<]*)<', blk)
            dl = re.search(r'title="([\d,.]+) downloads"', blk)
            out.append({
                "name": f"{m.group(1)}/{m.group(2)}" if community else m.group(2),
                "community": community,
                "description": cls._text(desc.group(1)) if desc else "",
                "capabilities": cls._badges(blk, "indigo"),
                "sizes": cls._badges(blk, "ddf4ff"),
                "cloud": "cloud" in cls._badges(blk, "cyan"),
                "downloads": int(re.sub(r"\D", "", dl.group(1))) if dl else 0,
                "updated": cls._text(upd.group(2)) if upd else "",
                "updated_full": upd.group(1) if upd else "",
            })
        return out, 'data-has-more="true"' in text or re.search(rf"[?&;]page={page + 1}\b", text) is not None

    @classmethod
    def tags(cls, name: str) -> list[dict]:
        path = name if "/" in name else "library/" + name
        prefix = name.split(":")[0].rsplit("/", 1)[0] + "/" if "/" in name else ""
        text = cls._page(f"/{path}/tags")
        out, seen = [], set()
        pat = r'<a href="/(%s:[^"]+)" class="md:hidden[^"]*">(.*?)</a>' % re.escape(path)
        for m in re.finditer(pat, text, re.S):
            tag, info = prefix + m.group(1).rsplit("/", 1)[1], cls._text(m.group(2))
            if tag in seen:
                continue
            seen.add(tag)
            size = re.search(r"•\s*([\d.]+\s*[KMGT]?B)\b", info)
            ctx = re.search(r"([\d.]+[KM]?)\s+context window", info)
            inp = re.search(r"context window\s*•\s*(.+?)\s+input", info)
            out.append({"name": tag, "size": size.group(1) if size else "",
                        "context": ctx.group(1) if ctx else "", "input": inp.group(1) if inp else ""})
        if not out:  # struttura della pagina cambiata: almeno i nomi dei tag
            for tag in dict.fromkeys(re.findall(r'href="/(%s:[^"]+)"' % re.escape(path), text)):
                out.append({"name": prefix + tag.rsplit("/", 1)[1], "size": "", "context": "", "input": ""})
        return out


def short_count(n: int) -> str:
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if n >= div:
            return f"{n / div:.1f}".rstrip("0").rstrip(".") + suf
    return str(n) if n else ""


def friendly_error(e: Exception | str) -> str:
    s = str(e)
    if "Connection refused" in s or "Max retries" in s or "ConnectionError" in s or "Failed to establish" in s:
        return tr("Impossibile contattare Ollama. Il server e' in esecuzione? (pulsante «Avvia» in basso a sinistra)")
    return s


class CallWorker(QThread):
    ok = Signal(object)
    fail = Signal(str)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        try:
            self.ok.emit(self.fn())
        except Exception as e:  # noqa: BLE001
            self.fail.emit(friendly_error(e))


class CallbackRelay(QObject):
    """Vive nel thread principale: esegue i callback in modo sicuro rispetto ai thread."""

    def __init__(self, ok, fail):
        super().__init__()
        self._ok, self._fail = ok, fail

    @Slot(object)
    def on_ok(self, r):
        try:
            if self._ok:
                self._ok(r)
        except RuntimeError:
            pass

    @Slot(str)
    def on_fail(self, msg):
        try:
            if self._fail:
                self._fail(msg)
        except RuntimeError:
            pass


_live_workers: set = set()


def run_async(fn, ok=None, fail=None) -> CallWorker:
    w = CallWorker(fn)
    relay = CallbackRelay(ok, fail)
    w.relay = relay  # type: ignore[attr-defined]
    w.ok.connect(relay.on_ok)
    w.fail.connect(relay.on_fail)
    _live_workers.add(w)
    w.finished.connect(lambda: _live_workers.discard(w))
    w.start()
    return w


def _kill_response(r):
    if r is None:
        return
    try:
        r.raw._fp.fp.raw._sock.shutdown(socket.SHUT_RDWR)
    except Exception:
        pass
    try:
        r.close()
    except Exception:
        pass


class ChatWorker(QThread):
    token = Signal(str)
    thinking = Signal(str)
    done = Signal(dict)
    failed = Signal(str)

    def __init__(self, base: str, payload: dict):
        super().__init__()
        self.base, self.payload = base, payload
        self._cancel = False
        self._resp = None

    def cancel(self):
        self._cancel = True
        _kill_response(self._resp)

    @staticmethod
    def _err(r) -> str:
        try:
            return r.json().get("error") or r.text
        except Exception:
            return r.text or f"HTTP {r.status_code}"

    def run(self):
        stats: dict = {}
        try:
            payload = self.payload
            r = None
            for attempt in range(2):
                r = requests.post(f"{self.base}/api/chat", json=payload, stream=True, timeout=(5, None))
                self._resp = r
                if r.status_code == 200:
                    break
                msg = self._err(r)
                if attempt == 0 and "think" in payload and "think" in msg.lower():
                    payload = {k: v for k, v in payload.items() if k != "think"}
                    continue
                self.failed.emit(msg)
                return
            for line in r.iter_lines():
                if self._cancel:
                    break
                if not line:
                    continue
                d = json.loads(line)
                if "error" in d:
                    self.failed.emit(str(d["error"]))
                    return
                m = d.get("message") or {}
                if m.get("thinking"):
                    self.thinking.emit(m["thinking"])
                if m.get("content"):
                    self.token.emit(m["content"])
                if d.get("done"):
                    stats = d
        except Exception as e:  # noqa: BLE001
            if not self._cancel:
                self.failed.emit(friendly_error(e))
                return
        finally:
            _kill_response(self._resp)
        stats = dict(stats)
        stats["cancelled"] = self._cancel
        self.done.emit(stats)


class PullWorker(QThread):
    progress = Signal(str, "qint64", "qint64")
    done = Signal()
    failed = Signal(str)

    def __init__(self, base: str, name: str):
        super().__init__()
        self.base, self.name = base, name
        self._cancel = False
        self._resp = None

    def cancel(self):
        self._cancel = True
        _kill_response(self._resp)

    def run(self):
        try:
            r = requests.post(f"{self.base}/api/pull", json={"model": self.name, "stream": True}, stream=True, timeout=(5, None))
            self._resp = r
            if r.status_code != 200:
                try:
                    self.failed.emit(r.json().get("error") or r.text)
                except Exception:
                    self.failed.emit(r.text)
                return
            for line in r.iter_lines():
                if self._cancel:
                    break
                if not line:
                    continue
                d = json.loads(line)
                if "error" in d:
                    self.failed.emit(str(d["error"]))
                    return
                self.progress.emit(d.get("status", ""), int(d.get("completed") or 0), int(d.get("total") or 0))
        except Exception as e:  # noqa: BLE001
            if not self._cancel:
                self.failed.emit(friendly_error(e))
                return
        finally:
            _kill_response(self._resp)
        self.done.emit()



# --------------------------------------------------------------------------- tema
def _mix(a: QColor, b: QColor, t: float) -> str:
    """Colore intermedio tra a e b (t=0 → a, t=1 → b)."""
    return QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t),
    ).name()


ACCENTS = [
    ("Blu", "#3b82f6"), ("Viola", "#8b5cf6"), ("Verde", "#10b981"), ("Arancione", "#f97316"),
    ("Rosa", "#ec4899"), ("Rosso", "#ef4444"), ("Grafite", "#64748b"),
]

# palette usate quando il tema chiaro/scuro e' forzato (toni simili a Breeze)
_SCHEMES = {
    False: dict(window="#eff0f1", base="#ffffff", alt="#f7f7f7", text="#232629", button="#fcfcfc",
                mid="#bdc3c7", disabled="#a0a4a8", highlight="#3daee9"),
    True: dict(window="#202326", base="#1b1e20", alt="#232629", text="#fcfcfc", button="#292c30",
               mid="#4d5257", disabled="#6e7378", highlight="#3daee9"),
}


def _scheme_palette(dark: bool) -> QPalette:
    k = _SCHEMES[dark]
    R = QPalette.ColorRole
    p = QPalette()
    for role, key in ((R.Window, "window"), (R.Base, "base"), (R.AlternateBase, "alt"), (R.Button, "button"),
                      (R.ToolTipBase, "base"), (R.Mid, "mid"), (R.Highlight, "highlight")):
        p.setColor(role, QColor(k[key]))
    for role in (R.WindowText, R.Text, R.ButtonText, R.ToolTipText, R.BrightText):
        p.setColor(role, QColor(k["text"]))
    p.setColor(R.PlaceholderText, QColor(k["disabled"]))
    p.setColor(R.HighlightedText, QColor("white"))
    for role in (R.WindowText, R.Text, R.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor(k["disabled"]))
    return p


_SYS_ACCENT: list[str] = []


def _system_accent() -> str:
    """Colore d'accento del sistema, letto prima che l'app imposti una palette propria."""
    if not _SYS_ACCENT:
        _SYS_ACCENT.append(QGuiApplication.palette().highlight().color().name())
    return _SYS_ACCENT[0]


def apply_appearance(theme: int, accent: str):
    """Applica tema (0 sistema, 1 chiaro, 2 scuro) e colore d'accento (vuoto = di sistema) a tutta l'app."""
    app = QApplication.instance()
    _system_accent()
    hints = app.styleHints()
    if hasattr(hints, "setColorScheme"):  # Qt >= 6.8: anche le parti native (es. barra del titolo)
        hints.setColorScheme((Qt.ColorScheme.Unknown, Qt.ColorScheme.Light, Qt.ColorScheme.Dark)[theme])
    # una palette vuota torna a quella del sistema; si forzano solo i ruoli necessari
    pal = _scheme_palette(theme == 2) if theme in (1, 2) else QPalette()
    if accent:
        acc = QColor(accent)
        pal.setColor(QPalette.ColorRole.Highlight, acc)
        light = 0.2126 * acc.redF() + 0.7152 * acc.greenF() + 0.0722 * acc.blueF() > 0.6
        pal.setColor(QPalette.ColorRole.HighlightedText, QColor("black" if light else "white"))
    elif theme in (1, 2):  # tema forzato: si tiene comunque l'accento del sistema
        pal.setColor(QPalette.ColorRole.Highlight, QColor(_system_accent()))
    app.setPalette(pal)


def theme_colors() -> dict:
    """Palette dell'interfaccia derivata dal tema di sistema (chiaro/scuro e colore d'accento)."""
    pal = QApplication.palette()
    win, base = pal.window().color(), pal.base().color()
    text, acc = pal.windowText().color(), pal.highlight().color()
    dark = win.lightness() < 128
    white = QColor("white")
    err = QColor("#f87171" if dark else "#dc2626")
    return dict(
        dark=dark,
        bg=base.name(),
        side=win.name(),
        text=text.name(),
        muted=_mix(text, base, 0.45),
        border=_mix(base, text, 0.16 if dark else 0.13),
        hover=_mix(base, text, 0.08 if dark else 0.06),
        surface=_mix(base, text, 0.06 if dark else 0.025),
        side_hover=_mix(win, text, 0.07),
        side_sel=_mix(win, acc, 0.28 if dark else 0.16),
        accent=acc.name(),
        accent_hover=(acc.lighter(115) if dark else acc.darker(110)).name(),
        accent_fg=pal.highlightedText().color().name(),
        bubble=_mix(base, acc, 0.24 if dark else 0.12),
        think=_mix(base, text, 0.05 if dark else 0.03),
        codebg=_mix(base, text, 0.08 if dark else 0.045),
        codehead=_mix(base, text, 0.13 if dark else 0.085),
        codefg=text.name(),
        link=_mix(acc, white, 0.3) if dark else acc.name(),
        err=err.name(),
        errbg=_mix(base, err, 0.12),
    )


def build_qss(c: dict) -> str:
    # le regole sono limitate (tramite objectName) ai widget della finestra principale:
    # le finestre di dialogo mantengono l'aspetto nativo del desktop
    scrollbars = "".join(
        f"""
{s} QScrollBar:vertical {{ background:transparent; width:10px; margin:0; }}
{s} QScrollBar::handle:vertical {{ background:{c['border']}; border-radius:4px; min-height:32px; margin:2px; }}
{s} QScrollBar::handle:vertical:hover {{ background:{c['muted']}; }}
{s} QScrollBar::add-line:vertical, {s} QScrollBar::sub-line:vertical {{ height:0; }}
{s} QScrollBar::add-page:vertical, {s} QScrollBar::sub-page:vertical {{ background:none; }}"""
        for s in ("#chatScroll", "#chatList", "#input")
    )
    return f"""
#main, #header, #composerArea, #hero, #chatCanvas {{ background:{c['bg']}; }}
#chatScroll {{ background:{c['bg']}; border:none; }}
#sidebar {{ background:{c['side']}; }}
QSplitter::handle {{ background:{c['border']}; }}
QLabel#brand {{ font-size:15px; font-weight:700; color:{c['text']}; }}
QPushButton#newChat {{
    background:{c['accent']}; color:{c['accent_fg']}; border:none; border-radius:10px;
    padding:9px 12px; font-weight:600; text-align:left;
}}
QPushButton#newChat:hover {{ background:{c['accent_hover']}; }}
QLineEdit#search {{
    background:{c['bg']}; color:{c['text']}; border:1px solid {c['border']};
    border-radius:10px; padding:6px 10px;
}}
QLineEdit#search:focus {{ border-color:{c['accent']}; }}
QListWidget#chatList {{ background:transparent; border:none; outline:0; }}
QPushButton#sideBtn {{
    background:transparent; color:{c['text']}; border:none; border-radius:8px;
    padding:7px 8px; text-align:left;
}}
QPushButton#sideBtn:hover {{ background:{c['side_hover']}; }}
QLabel#conn {{ color:{c['muted']}; }}
QPushButton#startBtn {{
    background:transparent; color:{c['accent']}; border:1px solid {c['accent']};
    border-radius:8px; padding:3px 10px;
}}
QPushButton#startBtn:hover {{ background:{c['side_sel']}; }}
QToolButton#pill {{
    background:transparent; color:{c['text']}; border:none; border-radius:10px;
    padding:6px 10px; font-weight:600;
}}
QToolButton#pill:hover, QToolButton#pill:pressed {{ background:{c['hover']}; }}
QToolButton#pill:disabled {{ color:{c['muted']}; }}
QToolButton#pill::menu-indicator, QToolButton#iconBtn::menu-indicator {{ image:none; width:0; }}
QToolButton#iconBtn {{ background:transparent; color:{c['text']}; border:none; border-radius:8px; padding:6px; }}
QToolButton#iconBtn:hover {{ background:{c['hover']}; }}
QTextBrowser#rich {{
    background:transparent; color:{c['text']}; border:none;
    selection-background-color:{c['accent']}; selection-color:{c['accent_fg']};
}}
QFrame#userBubble {{ background:{c['bubble']}; border-radius:18px; }}
QLabel#files {{ color:{c['muted']}; }}
QLabel#modelTag {{ color:{c['text']}; font-weight:600; }}
QToolButton#thinkToggle {{
    background:transparent; color:{c['muted']}; border:none; border-radius:8px; padding:3px 6px;
}}
QToolButton#thinkToggle:hover {{ background:{c['hover']}; color:{c['text']}; }}
QFrame#thinkBox {{ background:{c['think']}; border:1px solid {c['border']}; border-radius:12px; }}
QToolButton#msgBtn {{
    background:transparent; color:{c['muted']}; border:none; border-radius:7px; padding:4px 7px;
}}
QToolButton#msgBtn:hover {{ background:{c['hover']}; color:{c['text']}; }}
QLabel#stats {{ color:{c['muted']}; }}
QFrame#errorBox {{ background:{c['errbg']}; border:1px solid {c['err']}; border-radius:12px; }}
QLabel#errorText {{ color:{c['err']}; }}
QPushButton#retry {{
    background:transparent; color:{c['err']}; border:1px solid {c['err']}; border-radius:8px; padding:4px 12px;
}}
QFrame#composer {{ background:{c['surface']}; border:1px solid {c['border']}; border-radius:22px; }}
QFrame#composer[active="true"] {{ border:1px solid {c['accent']}; }}
QPlainTextEdit#input {{
    background:transparent; color:{c['text']}; border:none;
    selection-background-color:{c['accent']}; selection-color:{c['accent_fg']};
}}
QToolButton#send {{ background:{c['accent']}; border:none; border-radius:17px; }}
QToolButton#send:hover {{ background:{c['accent_hover']}; }}
QToolButton#send:disabled {{ background:{c['border']}; }}
QPushButton#chip {{
    background:{c['bg']}; color:{c['text']}; border:1px solid {c['border']};
    border-radius:12px; padding:4px 10px;
}}
QPushButton#chip:hover {{ border-color:{c['err']}; }}
QLabel#hint {{ color:{c['muted']}; }}
QLabel#heroTitle {{ font-size:30px; font-weight:700; color:{c['text']}; }}
QLabel#heroSub {{ color:{c['muted']}; }}
QFrame#suggestion {{ background:{c['surface']}; border:1px solid {c['border']}; border-radius:16px; }}
QFrame#suggestion:hover {{ background:{c['hover']}; border-color:{c['accent']}; }}
QLabel#sugTitle {{ color:{c['text']}; font-weight:600; background:transparent; }}
QLabel#sugSub {{ color:{c['muted']}; background:transparent; }}
{scrollbars}
"""


def doc_css(c: dict) -> str:
    """Foglio di stile dei documenti rich text (messaggi)."""
    mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family()
    return (
        "p, li { line-height:150%; }"
        f"pre {{ font-family:'{mono}'; color:{c['codefg']}; margin:0; line-height:135%; }}"
        f"code {{ font-family:'{mono}'; background-color:{c['codebg']}; }}"
        f"a {{ color:{c['link']}; text-decoration:none; }}"
        f"th {{ background-color:{c['codebg']}; }}"
        f"blockquote {{ color:{c['muted']}; }}"
    )


def theme_icon(*names: str) -> QIcon:
    for n in names:
        if QIcon.hasThemeIcon(n):
            return QIcon.fromTheme(n)
    return QIcon()


def glyph_icon(kind: str, color: str, size: int = 64) -> QIcon:
    """Icone disegnate a mano per il pulsante di invio (indipendenti dal tema)."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = float(size)
    if kind == "send":
        p.setPen(QPen(QColor(color), s * 0.1, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        top = QPointF(s / 2, s * 0.24)
        p.drawLine(QPointF(s / 2, s * 0.78), top)
        p.drawLine(QPointF(s * 0.27, s * 0.47), top)
        p.drawLine(QPointF(s * 0.73, s * 0.47), top)
    else:  # stop
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color))
        p.drawRoundedRect(QRectF(s * 0.3, s * 0.3, s * 0.4, s * 0.4), s * 0.07, s * 0.07)
    p.end()
    return QIcon(pm)


def rounded_pixmap(img: QImage, max_side: int, radius: float, dpr: float = 1.0) -> QPixmap:
    side = int(max_side * dpr)
    if max(img.width(), img.height()) > side:
        img = img.scaled(side, side, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    pm = QPixmap(img.size())
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(pm.rect()), radius * dpr, radius * dpr)
    p.setClipPath(path)
    p.drawImage(0, 0, img)
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def scaled_font(f: QFont, k: float) -> QFont:
    f = QFont(f)
    if f.pointSizeF() > 0:
        f.setPointSizeF(f.pointSizeF() * k)
    else:
        f.setPixelSize(max(8, round(f.pixelSize() * k)))
    return f


# --------------------------------------------------------------------------- widget
class InputEdit(QPlainTextEdit):
    submit = Signal()
    image_pasted = Signal(QImage)
    files_pasted = Signal(list)
    focus_changed = Signal(bool)

    def __init__(self):
        super().__init__()
        self.setObjectName("input")
        self.setAcceptDrops(False)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setPlaceholderText(tr("Scrivi un messaggio…"))
        self.document().contentsChanged.connect(self._fit)
        self._fit()

    def _fit(self):
        h = int(self.document().size().height() * self.fontMetrics().lineSpacing()) + 12
        self.setFixedHeight(max(34, min(h, 220)))

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (e.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            self.submit.emit()
            return
        super().keyPressEvent(e)

    def focusInEvent(self, e):
        super().focusInEvent(e)
        self.focus_changed.emit(True)

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self.focus_changed.emit(False)

    def insertFromMimeData(self, src):
        if src.hasImage():
            img = src.imageData()
            if isinstance(img, QImage) and not img.isNull():
                self.image_pasted.emit(img)
                return
        if src.hasUrls():
            files = [u.toLocalFile() for u in src.urls() if u.isLocalFile()]
            if files:
                self.files_pasted.emit(files)
                return
        super().insertFromMimeData(src)


class MenuPicker(QToolButton):
    """Pulsante "a pillola" con menu di scelta; espone un'API simile a QComboBox."""

    currentTextChanged = Signal(str)
    currentIndexChanged = Signal(int)

    def __init__(self, fmt=None, extra=None):
        super().__init__()
        self.setObjectName("pill")
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMenu(QMenu(self))
        self._fmt = fmt or (lambda t: t)
        self._extra = extra or []  # [(etichetta, callback)] in fondo al menu
        self._items: list[str] = []
        self._index = -1
        self._sync()

    def count(self) -> int:
        return len(self._items)

    def findText(self, t: str) -> int:
        return self._items.index(t) if t in self._items else -1

    def currentText(self) -> str:
        return self._items[self._index] if 0 <= self._index < len(self._items) else ""

    def currentIndex(self) -> int:
        return self._index

    def clear(self):
        self._items = []
        self._set(-1)

    def addItems(self, items):
        self._items += list(items)
        if self._index < 0 and self._items:
            self._set(0)
        else:
            self._sync()

    def setCurrentText(self, t: str):
        i = self.findText(t)
        if i >= 0:
            self._set(i)

    def setCurrentIndex(self, i: int):
        if 0 <= i < len(self._items):
            self._set(i)

    def _set(self, i: int):
        changed = i != self._index
        self._index = i
        self._sync()
        if changed:
            self.currentIndexChanged.emit(i)
            self.currentTextChanged.emit(self.currentText())

    def _sync(self):
        self.setText(f"{self._fmt(self.currentText())}  ▾")
        m = self.menu()
        m.clear()
        for i, t in enumerate(self._items):
            a = m.addAction(t)
            a.setCheckable(True)
            a.setChecked(i == self._index)
            a.triggered.connect(lambda _=False, i=i: self._set(i))
        if self._extra:
            if self._items:
                m.addSeparator()
            for label, cb in self._extra:
                m.addAction(label, cb)


ROLE_ID = Qt.ItemDataRole.UserRole
ROLE_SUB = Qt.ItemDataRole.UserRole + 1
ROLE_HEADER = Qt.ItemDataRole.UserRole + 2


class ChatItemDelegate(QStyledItemDelegate):
    """Voci della cronologia su due righe (titolo + modello/ora) e intestazioni dei gruppi."""

    def __init__(self, win: "MainWindow"):
        super().__init__(win)
        self.win = win

    def sizeHint(self, opt, idx):
        h = opt.fontMetrics.height()
        return QSize(10, h + 18 if idx.data(ROLE_HEADER) else h * 2 + 18)

    def paint(self, p, opt, idx):
        c = self.win.c
        r = opt.rect
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if idx.data(ROLE_HEADER):
            f = scaled_font(opt.font, 0.85)
            f.setWeight(QFont.Weight.DemiBold)
            p.setFont(f)
            p.setPen(QColor(c["muted"]))
            p.drawText(r.adjusted(10, 0, -8, -4), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, idx.data())
            p.restore()
            return
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        if selected or opt.state & QStyle.StateFlag.State_MouseOver:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(c["side_sel"] if selected else c["side_hover"]))
            p.drawRoundedRect(QRectF(r).adjusted(1, 1, -1, -1), 9, 9)
        tr = r.adjusted(11, 8, -10, -8)
        f = QFont(opt.font)
        if selected:
            f.setWeight(QFont.Weight.DemiBold)
        fm = QFontMetrics(f)
        p.setFont(f)
        p.setPen(QColor(c["text"]))
        p.drawText(QRect(tr.left(), tr.top(), tr.width(), fm.height()), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   fm.elidedText(idx.data() or "", Qt.TextElideMode.ElideRight, tr.width()))
        f2 = scaled_font(opt.font, 0.85)
        fm2 = QFontMetrics(f2)
        p.setFont(f2)
        p.setPen(QColor(c["muted"]))
        p.drawText(QRect(tr.left(), tr.bottom() - fm2.height() + 1, tr.width(), fm2.height()),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   fm2.elidedText(idx.data(ROLE_SUB) or "", Qt.TextElideMode.ElideRight, tr.width()))
        p.restore()


class SuggestionCard(QFrame):
    """Scheda cliccabile della schermata iniziale: titolo in evidenza e descrizione attenuata."""

    def __init__(self, title: str, sub: str, slot):
        super().__init__(objectName="suggestion")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._slot = slot
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 12, 16, 12)
        v.setSpacing(2)
        v.addWidget(QLabel(title, objectName="sugTitle"))
        s = QLabel(sub, objectName="sugSub")
        s.setWordWrap(True)
        s.setFont(scaled_font(s.font(), 0.92))
        v.addWidget(s)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self._slot()


class RichView(QTextBrowser):
    """Testo rich text che si adatta in altezza al contenuto (lo scorrimento e' della lista messaggi)."""

    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.setObjectName("rich")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setOpenLinks(False)
        self.setAcceptDrops(False)
        self.viewport().setAutoFillBackground(False)
        self.anchorClicked.connect(win.on_anchor)
        self.document().setDocumentMargin(2)
        self.document().setDefaultStyleSheet(win.doc_css)
        self.document().documentLayout().documentSizeChanged.connect(self._fit)
        self.setFixedHeight(24)

    def _fit(self, *_):
        self.document().setTextWidth(self.viewport().width())
        h = math.ceil(self.document().size().height())
        if h != self.height():
            self.setFixedHeight(h)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit()

    def wheelEvent(self, e):
        e.ignore()  # lascia scorrere l'area dei messaggi


class UserMessage(QWidget):
    def __init__(self, win: "MainWindow", m: dict):
        super().__init__()
        self.win, self.m = win, m
        self._done = False
        self._natural = 0
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addStretch(1)
        self.bubble = QFrame(objectName="userBubble")
        self.bubble.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        bl = QVBoxLayout(self.bubble)
        bl.setContentsMargins(14, 10, 14, 10)
        bl.setSpacing(6)
        self.thumbs = QHBoxLayout()
        self.thumbs.setSpacing(6)
        bl.addLayout(self.thumbs)
        self.files = QLabel(objectName="files")
        self.files.setWordWrap(True)
        bl.addWidget(self.files)
        self.text = RichView(win)
        bl.addWidget(self.text)
        lay.addWidget(self.bubble, 1000)

    def update_content(self, streaming: bool, is_last: bool):
        if self._done:  # i messaggi dell'utente non cambiano
            return
        self._done = True
        m = self.m
        fm = QFontMetrics(self.text.font())
        widths = [0]
        thumbs_w = 0
        for b64 in m.get("images", []):
            lbl = QLabel()
            pm = self.win.thumbnail(b64, 200)
            lbl.setPixmap(pm)
            self.thumbs.addWidget(lbl)
            thumbs_w += int(pm.width() / pm.devicePixelRatio()) + 6
        self.thumbs.addStretch(1)
        widths.append(thumbs_w)
        # le immagini sono gia' mostrate come anteprima: si elencano solo gli altri file
        files = [n for n in m.get("attachments", []) if Path(n).suffix.lower() not in IMAGE_EXT]
        self.files.setText("<br>".join(f"📄 {html.escape(n)}" for n in files))
        self.files.setVisible(bool(files))
        widths += [fm.horizontalAdvance(f"📄 {n}") for n in files]
        display = m.get("display") or ""
        self.text.setHtml(esc_br(display))
        self.text.setVisible(bool(display))
        widths += [fm.horizontalAdvance(line) + 10 for line in display.split("\n")]
        self._natural = max(widths) + 30
        self._fit_width()

    def _fit_width(self):
        self.bubble.setMaximumWidth(max(90, min(self._natural, int(self.width() * 0.8))))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit_width()


class AssistantMessage(QWidget):
    def __init__(self, win: "MainWindow", m: dict):
        super().__init__()
        self.win, self.m = win, m
        self._key = None
        self._thinking = ""
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(8)
        avatar = QLabel()
        avatar.setPixmap(win.windowIcon().pixmap(22, 22))
        self.model_lbl = QLabel(objectName="modelTag")
        head.addWidget(avatar)
        head.addWidget(self.model_lbl)
        head.addStretch(1)
        v.addLayout(head)

        self.think_btn = QToolButton(objectName="thinkToggle")
        self.think_btn.setCheckable(True)
        self.think_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.think_btn.toggled.connect(self._toggle_think)
        v.addWidget(self.think_btn)
        self.think_box = QFrame(objectName="thinkBox")
        tl = QVBoxLayout(self.think_box)
        tl.setContentsMargins(12, 8, 12, 8)
        self.think_view = RichView(win)
        self.think_view.setFont(scaled_font(self.think_view.font(), 0.92))
        tl.addWidget(self.think_view)
        self.think_box.setVisible(False)
        v.addWidget(self.think_box)

        self.body = RichView(win)
        v.addWidget(self.body)

        self.actions = QWidget()
        al = QHBoxLayout(self.actions)
        al.setContentsMargins(0, 0, 0, 0)
        al.setSpacing(2)
        self.btn_copy = self._action_button(("edit-copy",), tr("Copia"), self.copy)
        self.btn_regen = self._action_button(("view-refresh",), tr("Rigenera"), win.regenerate)
        self.stats = QLabel(objectName="stats")
        self.stats.setFont(scaled_font(self.stats.font(), 0.9))
        al.addWidget(self.btn_copy)
        al.addWidget(self.btn_regen)
        al.addSpacing(6)
        al.addWidget(self.stats)
        al.addStretch(1)
        v.addWidget(self.actions)

    def _action_button(self, icons, text, slot) -> QToolButton:
        b = QToolButton(objectName="msgBtn")
        b.setText(text)
        b.setIcon(theme_icon(*icons))
        b.setIconSize(QSize(14, 14))
        b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(slot)
        return b

    def _think_label(self, streaming: bool, has_body: bool):
        arrow = "▾" if self.think_btn.isChecked() else "▸"
        word = tr("Sta ragionando…") if streaming and not has_body else tr("Ragionamento")
        self.think_btn.setText(f"{arrow}  💭 {word}")

    def _toggle_think(self, on: bool):
        self.think_box.setVisible(on)
        if on:
            self.think_view.setHtml(esc_br(self._thinking))
        self._key = None
        self.win._render()

    def update_content(self, streaming: bool, is_last: bool):
        m = self.m
        show = bool(self.win.settings["show_thinking"])
        key = (len(m.get("content", "")), len(m.get("thinking", "")), streaming, is_last, show,
               m.get("stats"), self.think_btn.isChecked())
        if key == self._key:
            return
        self._key = key
        think_inline, body = split_think(m.get("content", ""))
        self._thinking = (m.get("thinking", "") + ("\n" + think_inline if think_inline else "")).strip()
        has_think = bool(self._thinking) and show
        self.model_lbl.setText(html.escape(m.get("model") or tr("Assistente")))
        self.think_btn.setVisible(has_think)
        self.think_box.setVisible(has_think and self.think_btn.isChecked())
        self._think_label(streaming, bool(body))
        if self.think_box.isVisible():
            self.think_view.setHtml(f'<span style="color:{self.win.c["muted"]}">{esc_br(self._thinking)}</span>')
        if body:
            self.body.setHtml(self.win.render_markdown(body))
        elif streaming and not has_think:
            self.body.setHtml(f'<span style="color:{self.win.c["muted"]}">●●●</span>')
        self.body.setVisible(bool(body) or (streaming and not has_think))
        self.actions.setVisible(not streaming and bool(body))
        self.btn_regen.setVisible(is_last)
        self.stats.setText(m.get("stats") or "")

    def copy(self):
        QGuiApplication.clipboard().setText(split_think(self.m.get("content", ""))[1])
        self.btn_copy.setText(tr("Copiato ✓"))
        QTimer.singleShot(1500, lambda: self.btn_copy.setText(tr("Copia")))


class ModelsDialog(QDialog):
    changed = Signal()
    SUGGESTED = [
        "llama3.2", "llama3.2:1b", "llama3.1:8b", "qwen3:8b", "qwen3:4b", "gemma3:4b", "gemma3:12b",
        "mistral", "phi4", "deepseek-r1:8b", "qwen2.5-coder:7b", "llava", "gpt-oss:20b",
    ]

    def __init__(self, win: "MainWindow"):
        super().__init__(win)
        self.win = win
        self.puller: PullWorker | None = None
        self.installed: set[str] = set()
        self.lib_page = 1
        self.lib_seq = 0  # scarta le risposte arrivate dopo una ricerca piu' recente
        self.tags_seq = 0
        self.setWindowTitle(tr("Modelli"))
        self.resize(940, 680)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs, 1)

        # --- scheda "Installati"
        page = QWidget()
        inst = QVBoxLayout(page)
        self.table = self._make_table([tr("Modello"), tr("Dimensione"), tr("Parametri"), tr("Quantizzazione"), tr("Modificato")])
        inst.addWidget(self.table)
        row = QHBoxLayout()
        self.btn_refresh = QPushButton(theme_icon("view-refresh"), tr("Aggiorna"))
        self.btn_unload = QPushButton(theme_icon("media-eject", "go-down"), tr("Scarica dalla memoria"))
        self.btn_delete = QPushButton(theme_icon("edit-delete"), tr("Elimina"))
        for b in (self.btn_refresh, self.btn_unload, self.btn_delete):
            row.addWidget(b)
        row.addStretch(1)
        inst.addLayout(row)
        self.tabs.addTab(page, theme_icon("drive-harddisk"), tr("Installati"))

        # --- scheda "Libreria" (ollama.com/library)
        page = QWidget()
        lib = QVBoxLayout(page)
        row = QHBoxLayout()
        self.lib_query = QLineEdit(placeholderText=tr("Cerca nella libreria di Ollama, es. qwen, gemma, coder…"), clearButtonEnabled=True)
        self.lib_cap = QComboBox()
        for c in Library.CAPS:
            self.lib_cap.addItem({"": tr("Tutte le funzioni"), "tools": tr("Strumenti (tools)"), "thinking": tr("Ragionamento"),
                                  "vision": tr("Visione"), "embedding": "Embedding", "audio": "Audio"}[c], c)
        self.lib_order = QComboBox()
        self.lib_order.addItems([tr("Più popolari"), tr("Più recenti")])
        self.btn_search = QPushButton(theme_icon("edit-find", "system-search"), tr("Cerca"))
        row.addWidget(self.lib_query, 1)
        row.addWidget(self.lib_cap)
        row.addWidget(self.lib_order)
        row.addWidget(self.btn_search)
        lib.addLayout(row)
        self.lib_local_only = QCheckBox(tr("Nascondi i modelli disponibili solo nel cloud"), checked=True)
        lib.addWidget(self.lib_local_only)

        split = QSplitter(Qt.Orientation.Vertical)
        top = QWidget()
        tl = QVBoxLayout(top)
        tl.setContentsMargins(0, 0, 0, 0)
        self.lib_table = self._make_table([tr("Modello"), tr("Descrizione"), tr("Funzioni"), tr("Dimensioni"), "Download", tr("Aggiornato")])
        self.lib_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.lib_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.lib_table.setWordWrap(False)
        tl.addWidget(self.lib_table)
        row = QHBoxLayout()
        self.lib_status = QLabel("")
        self.btn_more = QPushButton(tr("Altri risultati"))
        self.btn_more.setVisible(False)
        row.addWidget(self.lib_status, 1)
        row.addWidget(self.btn_more)
        tl.addLayout(row)
        split.addWidget(top)

        bottom = QWidget()
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(0, 0, 0, 0)
        self.tags_title = QLabel(tr("Seleziona un modello per vederne le varianti (tag)."), wordWrap=True)
        bl.addWidget(self.tags_title)
        self.tags_table = self._make_table(["Tag", tr("Dimensione"), tr("Contesto"), "Input"])
        bl.addWidget(self.tags_table)
        split.addWidget(bottom)
        split.setSizes([360, 220])
        lib.addWidget(split, 1)
        self.tabs.addTab(page, theme_icon("internet-services", "applications-internet", "globe"), tr("Libreria"))

        # --- download (comune alle due schede)
        lay.addWidget(QLabel(tr("<b>Scarica un modello</b> — sceglilo dalla scheda «Libreria» o scrivi il nome")))
        box = QHBoxLayout()
        self.name = QComboBox()
        self.name.setEditable(True)
        self.name.addItems(self.SUGGESTED)
        self.name.setCurrentText("")
        self.name.lineEdit().setPlaceholderText(tr("Nome del modello da scaricare, es. llama3.2:3b"))
        self.btn_pull = QPushButton(theme_icon("download", "folder-download"), tr("Scarica"))
        self.btn_cancel = QPushButton(tr("Annulla"))
        self.btn_cancel.setEnabled(False)
        box.addWidget(self.name, 1)
        box.addWidget(self.btn_pull)
        box.addWidget(self.btn_cancel)
        lay.addLayout(box)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setVisible(False)
        self.lbl = QLabel("")
        lay.addWidget(self.bar)
        lay.addWidget(self.lbl)

        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_delete.clicked.connect(self.delete_selected)
        self.btn_unload.clicked.connect(self.unload_selected)
        self.btn_pull.clicked.connect(self.start_pull)
        self.btn_cancel.clicked.connect(self.cancel_pull)
        self.name.lineEdit().returnPressed.connect(self.start_pull)
        self.btn_search.clicked.connect(self.lib_search)
        self.lib_query.returnPressed.connect(self.lib_search)
        self.lib_cap.currentIndexChanged.connect(self.lib_search)
        self.lib_order.currentIndexChanged.connect(self.lib_search)
        self.lib_local_only.toggled.connect(self.lib_search)
        self.btn_more.clicked.connect(lambda: self.lib_search(more=True))
        self.lib_table.itemSelectionChanged.connect(self.lib_load_tags)
        self.lib_table.itemDoubleClicked.connect(lambda _it: self._pull_selected_model())
        self.tags_table.itemSelectionChanged.connect(self._tag_selected)
        self.tags_table.itemDoubleClicked.connect(lambda _it: self.start_pull())
        self.tabs.currentChanged.connect(lambda i: i == 1 and self.lib_table.rowCount() == 0 and self.lib_search())
        self.refresh()

    def _make_table(self, headers: list[str]) -> QTableWidget:
        t = QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.setShowGrid(False)
        t.setAlternatingRowColors(True)
        t.verticalHeader().setVisible(False)
        t.verticalHeader().setDefaultSectionSize(self.fontMetrics().height() + 14)
        t.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        t.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        return t

    def _is_installed(self, tag: str) -> bool:
        return tag in self.installed or (":" not in tag and tag + ":latest" in self.installed)

    # --- libreria
    def lib_search(self, *_a, more: bool = False):
        self.lib_page = self.lib_page + 1 if more else 1
        self.lib_seq += 1
        seq, page = self.lib_seq, self.lib_page
        if not more:
            self.lib_table.setRowCount(0)
            self.tags_table.setRowCount(0)
            self.tags_title.setText(tr("Seleziona un modello per vederne le varianti (tag)."))
        self.btn_more.setEnabled(False)
        self.lib_status.setText(tr("Ricerca su ollama.com…"))
        q, cap, newest = self.lib_query.text().strip(), self.lib_cap.currentData(), self.lib_order.currentIndex() == 1

        def ok(res):
            if seq == self.lib_seq:
                self._lib_fill(*res)

        def fail(msg):
            if seq == self.lib_seq:
                self.lib_status.setText(msg)
                self.btn_more.setEnabled(True)

        run_async(lambda: Library.search(q, cap, newest, page), ok, fail)

    def _lib_fill(self, models: list[dict], has_more: bool):
        hide_cloud = self.lib_local_only.isChecked()
        for m in models:
            if hide_cloud and m["cloud"] and not m["sizes"]:
                continue
            name = m["name"]
            caps = list(m["capabilities"]) + (["cloud"] if m["cloud"] else [])
            vals = [name, m["description"], ", ".join(caps), " ".join(m["sizes"]), short_count(m["downloads"]), m["updated"]]
            r = self.lib_table.rowCount()
            self.lib_table.insertRow(r)
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 0:
                    it.setData(Qt.ItemDataRole.UserRole, name)
                    if any(i.split(":")[0] == name for i in self.installed):
                        it.setText("✓ " + name)
                        it.setToolTip(tr("Installato (almeno una variante)"))
                        it.setForeground(QColor("#22c55e"))
                    elif m["community"]:
                        it.setToolTip(tr("Modello caricato da un utente della community"))
                        it.setForeground(self.lib_table.palette().placeholderText().color())
                elif c == 1:
                    it.setToolTip(f"<p>{html.escape(v)}</p>")
                elif c == 5 and m["updated_full"]:
                    it.setToolTip(tr("Aggiornato il ") + m["updated_full"])
                self.lib_table.setItem(r, c, it)
        n = self.lib_table.rowCount()
        self.lib_status.setText(tr("{} modelli").format(n) if n else tr("Nessun modello trovato."))
        self.btn_more.setVisible(has_more)
        self.btn_more.setEnabled(True)
        # se una pagina e' fatta solo di modelli cloud nascosti, passa alla successiva
        if has_more and n == 0 and models:
            self.lib_search(more=True)

    def _lib_selected(self) -> str | None:
        r = self.lib_table.currentRow()
        return self.lib_table.item(r, 0).data(Qt.ItemDataRole.UserRole) if r >= 0 else None

    def lib_load_tags(self):
        name = self._lib_selected()
        if not name:
            return
        self.name.setCurrentText(name)
        self.tags_seq += 1
        seq = self.tags_seq
        self.tags_table.setRowCount(0)
        self.tags_title.setText(tr("Varianti di <b>{}</b>: caricamento…").format(html.escape(name)))

        def ok(tags):
            if seq == self.tags_seq:
                self._tags_fill(name, tags)

        def fail(msg):
            if seq == self.tags_seq:
                self.tags_title.setText(tr("Varianti di <b>{}</b>: {}").format(html.escape(name), html.escape(msg)))

        run_async(lambda: Library.tags(name), ok, fail)

    def _tags_fill(self, name: str, tags: list[dict]):
        if self.lib_local_only.isChecked():
            tags = [t for t in tags if not re.search(r"[:-]cloud$", t["name"])] or tags
        for t in tags:
            tag = t["name"]
            r = self.tags_table.rowCount()
            self.tags_table.insertRow(r)
            for c, v in enumerate((tag, t["size"], t["context"], t["input"])):
                it = QTableWidgetItem(v)
                if c == 0:
                    it.setData(Qt.ItemDataRole.UserRole, tag)
                    if self._is_installed(tag):
                        it.setText("✓ " + tag)
                        it.setToolTip(tr("Già installato"))
                        it.setForeground(QColor("#22c55e"))
                    elif tag.endswith("cloud") or "-cloud" in tag:
                        it.setToolTip(tr("Eseguito sui server di Ollama: richiede l'accesso con «ollama signin»"))
                self.tags_table.setItem(r, c, it)
        self.tags_title.setText(tr("Varianti di <b>{}</b> ({}) — doppio clic per scaricare").format(html.escape(name), len(tags)))

    def _tag_selected(self):
        r = self.tags_table.currentRow()
        if r >= 0:
            self.name.setCurrentText(self.tags_table.item(r, 0).data(Qt.ItemDataRole.UserRole))

    def _pull_selected_model(self):
        if self._lib_selected():
            self.name.setCurrentText(self._lib_selected())
            self.start_pull()

    def selected(self) -> str | None:
        r = self.table.currentRow()
        return self.table.item(r, 0).data(Qt.ItemDataRole.UserRole) if r >= 0 else None

    def refresh(self):
        def ok(models):
            def fill(ps):
                self._fill(models, {m.get("name") for m in ps})

            run_async(self.win.api.ps, fill, lambda _m: self._fill(models, set()))

        run_async(self.win.api.tags, ok, lambda m: self.lbl.setText(m))

    def _fill(self, models, loaded):
        self.installed = {m.get("name", "") for m in models}
        for r in range(self.tags_table.rowCount()):
            it = self.tags_table.item(r, 0)
            tag = it.data(Qt.ItemDataRole.UserRole)
            ok = self._is_installed(tag)
            it.setText(("✓ " if ok else "") + tag)
            it.setForeground(QColor("#22c55e") if ok else self.tags_table.palette().text().color())
        self.table.setRowCount(0)
        for m in sorted(models, key=lambda m: m.get("name", "")):
            d = m.get("details") or {}
            name = m.get("name", "")
            vals = [
                ("● " if name in loaded else "") + name,
                human_size(m.get("size", 0)),
                d.get("parameter_size", ""),
                d.get("quantization_level", ""),
                (m.get("modified_at") or "")[:16].replace("T", " "),
            ]
            r = self.table.rowCount()
            self.table.insertRow(r)
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 0:
                    it.setData(Qt.ItemDataRole.UserRole, name)
                    if name in loaded:
                        it.setToolTip(tr("In memoria"))
                        it.setForeground(QColor("#22c55e"))
                self.table.setItem(r, c, it)
        self.lbl.setText(tr("{} modelli installati").format(len(models)) + (tr(" · in memoria (●): {}").format(", ".join(sorted(loaded))) if loaded else ""))

    def delete_selected(self):
        n = self.selected()
        if not n:
            return
        if QMessageBox.question(self, tr("Elimina modello"), tr("Eliminare «{}» dal disco?").format(n)) != QMessageBox.StandardButton.Yes:
            return

        def ok(_):
            self.refresh()
            self.changed.emit()

        run_async(lambda: self.win.api.delete(n), ok, lambda m: QMessageBox.warning(self, tr("Errore"), m))

    def unload_selected(self):
        n = self.selected()
        if n:
            run_async(lambda: self.win.api.unload(n), lambda _r: self.refresh(), lambda m: self.lbl.setText(m))

    def start_pull(self):
        name = self.name.currentText().strip()
        if not name or self.puller:
            return
        self.puller = PullWorker(self.win.api.base, name)
        self.puller.progress.connect(self.on_progress)
        self.puller.done.connect(self.on_pull_done)
        self.puller.failed.connect(self.on_pull_failed)
        self.puller.finished.connect(self._pull_finished)
        self.bar.setVisible(True)
        self.bar.setValue(0)
        self.bar.setRange(0, 0)
        self.btn_pull.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.lbl.setText(tr("Download di {}…").format(name))
        self.puller.start()

    @Slot(str, "qint64", "qint64")
    def on_progress(self, status, done, total):
        if total > 0:
            self.bar.setRange(0, 100)
            self.bar.setValue(int(done * 100 / total))
            self.lbl.setText(f"{status} — {human_size(done)} / {human_size(total)}")
        else:
            self.bar.setRange(0, 0)
            self.lbl.setText(status)

    @Slot()
    def on_pull_done(self):
        if self.puller and self.puller._cancel:
            self.lbl.setText(tr("Download annullato."))
        else:
            self.lbl.setText(tr("Download completato."))
        self.refresh()
        self.changed.emit()

    @Slot(str)
    def on_pull_failed(self, msg):
        self.lbl.setText(tr("Errore: ") + msg)

    @Slot()
    def _pull_finished(self):
        self.puller = None
        self.bar.setVisible(False)
        self.btn_pull.setEnabled(True)
        self.btn_cancel.setEnabled(False)

    def cancel_pull(self):
        if self.puller:
            self.puller.cancel()

    def closeEvent(self, e):
        if self.puller:
            self.puller.cancel()
            self.puller.wait(2000)
        super().closeEvent(e)


class SettingsDialog(QDialog):
    def __init__(self, win: "MainWindow"):
        super().__init__(win)
        self.win = win
        s = win.settings
        self.setWindowTitle(tr("Impostazioni"))
        self.resize(580, 520)
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        lay.addWidget(tabs)

        # --- generale
        g = QWidget()
        f = QFormLayout(g)
        f.setVerticalSpacing(10)
        self.host = QLineEdit(s["host"])
        self.btn_test = QPushButton(tr("Verifica"))
        self.test_lbl = QLabel("")
        hrow = QHBoxLayout()
        hrow.addWidget(self.host, 1)
        hrow.addWidget(self.btn_test)
        f.addRow(tr("Indirizzo del server:"), hrow)
        f.addRow("", self.test_lbl)
        self.ctx = QSpinBox()
        self.ctx.setRange(0, 2_097_152)
        self.ctx.setSingleStep(1024)
        self.ctx.setSpecialValueText(tr("Predefinito del modello"))
        self.ctx.setValue(int(s["num_ctx"]))
        f.addRow(tr("Lunghezza del contesto:"), self.ctx)
        self.temp = QDoubleSpinBox()
        self.temp.setDecimals(1)
        self.temp.setRange(-0.1, 2.0)
        self.temp.setSingleStep(0.1)
        self.temp.setSpecialValueText(tr("Predefinita del modello"))
        self.temp.setValue(float(s["temperature"]))
        f.addRow(tr("Temperatura:"), self.temp)
        self.keep = QLineEdit(s["keep_alive"])
        self.keep.setPlaceholderText(tr("vuoto = 5m · -1 = sempre in memoria · 0 = scarica subito · 30m"))
        f.addRow(tr("Mantieni in memoria:"), self.keep)
        self.show_think = QCheckBox(tr("Mostra il ragionamento dei modelli (thinking)"))
        self.show_think.setChecked(bool(s["show_thinking"]))
        f.addRow("", self.show_think)
        self.tray = QCheckBox(tr("Alla chiusura riduci nella barra di sistema"))
        self.tray.setChecked(bool(s["close_to_tray"]))
        self.tray.setEnabled(win.tray is not None)
        f.addRow("", self.tray)
        tabs.addTab(g, tr("Generale"))

        # --- aspetto (anteprima immediata; Annulla ripristina)
        self._orig_look = (int(s["theme"]), s["accent"])
        self._accent = s["accent"]
        a = QWidget()
        af = QFormLayout(a)
        af.setVerticalSpacing(14)
        self.theme = QComboBox()
        self.theme.addItems([tr("Segui il sistema"), tr("Chiaro"), tr("Scuro")])
        self.theme.setCurrentIndex(int(s["theme"]))
        self.theme.currentIndexChanged.connect(self._preview)
        af.addRow(tr("Tema:"), self.theme)
        self.language = QComboBox()
        for code, label in LANGUAGES:
            self.language.addItem(tr(label), code)
        self.language.setCurrentIndex(max(0, self.language.findData(s["language"])))
        af.addRow(tr("Lingua:"), self.language)
        self.language_note = QLabel(tr("La lingua viene applicata al prossimo avvio."))
        self.language_note.setVisible(False)
        self.language.currentIndexChanged.connect(
            lambda _i: self.language_note.setVisible(self.language.currentData() != s["language"]))
        af.addRow("", self.language_note)
        sw = QHBoxLayout()
        sw.setSpacing(8)
        self.accent_group = QButtonGroup(self)
        b_sys = QPushButton(tr("Sistema"))
        b_sys.setCheckable(True)
        b_sys.setToolTip(tr("Usa il colore d'accento del sistema"))
        self.accent_group.addButton(b_sys)
        sw.addWidget(b_sys)
        self._swatches = {"": b_sys}
        for name, col in ACCENTS:
            b = self._swatch(col, tr(name))
            self._swatches[col] = b
            sw.addWidget(b)
        self.btn_custom = self._swatch(s["accent"] if s["accent"] and s["accent"] not in self._swatches else "", tr("Personalizzato…"))
        sw.addWidget(self.btn_custom)
        sw.addStretch(1)
        (self._swatches.get(s["accent"]) or self.btn_custom).setChecked(True)
        self.accent_group.buttonClicked.connect(self._on_swatch)
        af.addRow(tr("Colore d'accento:"), sw)
        tabs.addTab(a, tr("Aspetto"))

        # --- prompt
        p = QWidget()
        pl = QVBoxLayout(p)
        pl.addWidget(QLabel(tr("Prompt di sistema (inviato all'inizio di ogni conversazione):")))
        self.sysprompt = QPlainTextEdit(s["system_prompt"])
        pl.addWidget(self.sysprompt)
        tabs.addTab(p, tr("Prompt di sistema"))

        # --- server
        sv = QTextBrowser()
        sv.setOpenExternalLinks(True)
        sv.setHtml(
            tr("""
<h3>Impostazioni del server Ollama</h3>
<p>Alcune opzioni (cartella dei modelli, accesso dalla rete) appartengono al
<b>server</b>, non a questa interfaccia. Su Linux si impostano con systemd:</p>
<pre>sudo systemctl edit ollama</pre>
<p>e si aggiunge, ad esempio:</p>
<pre>[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
Environment="OLLAMA_MODELS=/percorso/dei/modelli"
Environment="OLLAMA_CONTEXT_LENGTH=8192"
Environment="OLLAMA_KEEP_ALIVE=30m"</pre>
<p>poi:</p>
<pre>sudo systemctl daemon-reload
sudo systemctl restart ollama</pre>
<ul>
<li><b>OLLAMA_HOST=0.0.0.0:11434</b> espone Ollama alla rete locale (apri la porta nel firewall).</li>
<li><b>OLLAMA_MODELS</b>: la cartella deve appartenere all'utente <i>ollama</i>
(<code>sudo chown -R ollama:ollama /percorso</code>).</li>
</ul>
<p>Per usare un Ollama su un altro computer, scrivi qui sopra il suo indirizzo,
ad esempio <code>http://192.168.1.50:11434</code>.</p>
""")
        )
        tabs.addTab(sv, tr("Server"))

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.btn_test.clicked.connect(self.test)

    def _swatch(self, color: str, tip: str) -> QToolButton:
        b = QToolButton()
        b.setCheckable(True)
        b.setFixedSize(28, 28)
        b.setToolTip(tip)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setProperty("color", color)
        self._paint_swatch(b, color)
        self.accent_group.addButton(b)
        return b

    def _paint_swatch(self, b: QToolButton, color: str):
        pal = QApplication.palette()  # quella del dialogo si aggiorna in ritardo
        ring = pal.windowText().color().name()
        fill = color or "transparent"
        b.setText("" if color else "+")
        b.setStyleSheet(
            f"QToolButton {{ background:{fill}; border:2px solid {pal.mid().color().name() if not color else fill};"
            f" border-radius:14px; }} QToolButton:checked {{ border:2px solid {ring}; }}")

    def _on_swatch(self, b):
        if b is self.btn_custom:
            col = QColorDialog.getColor(QColor(self._accent or _system_accent()), self, tr("Colore d'accento"))
            if not col.isValid():
                (self._swatches.get(self._accent) or self.btn_custom).setChecked(True)
                return
            self._accent = col.name()
            self.btn_custom.setProperty("color", self._accent)
            self._paint_swatch(self.btn_custom, self._accent)
        else:
            self._accent = b.property("color") or ""
        self._preview()

    def _preview(self):
        apply_appearance(self.theme.currentIndex(), self._accent)
        for b in self.accent_group.buttons():  # l'anello di selezione segue il tema
            if b is not self._swatches[""]:
                self._paint_swatch(b, b.property("color") or "")

    def reject(self):
        apply_appearance(*self._orig_look)
        super().reject()

    def test(self):
        api = Ollama(self.host.text())
        self.test_lbl.setText(tr("Verifica in corso…"))
        run_async(api.version, lambda v: self.test_lbl.setText(tr("✔ Connesso — Ollama {}").format(v)), lambda m: self.test_lbl.setText("✘ " + m))

    def accept(self):
        s = self.win.settings
        s["host"] = normalize_host(self.host.text())
        s["num_ctx"] = self.ctx.value()
        s["temperature"] = self.temp.value()
        s["keep_alive"] = self.keep.text().strip()
        s["show_thinking"] = self.show_think.isChecked()
        s["close_to_tray"] = self.tray.isChecked()
        s["system_prompt"] = self.sysprompt.toPlainText()
        s["theme"] = self.theme.currentIndex()
        s["accent"] = self._accent
        s["language"] = self.language.currentData()
        s.save()
        super().accept()


# --------------------------------------------------------------------------- finestra principale
SUGGESTIONS = [
    ("Spiegami un concetto", "come funziona la crittografia a chiave pubblica",
     "Spiegami in modo semplice come funziona la crittografia a chiave pubblica."),
    ("Scrivi del codice", "uno script Python che ordina i file per data",
     "Scrivi uno script Python che sposta i file di una cartella in sottocartelle per anno e mese."),
    ("Riassumi un testo", "incolla un testo o trascina qui un PDF",
     "Riassumi il testo seguente in pochi punti chiave:\n\n"),
    ("Dammi qualche idea", "per un weekend fuori porta in autunno",
     "Dammi cinque idee per un weekend fuori porta in autunno, con pro e contro."),
]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(make_icon())
        self.resize(1180, 780)
        self.setAcceptDrops(True)

        self.settings = Settings()
        set_language(self.settings["language"])
        self.api = Ollama(self.settings["host"])
        self.store = ChatStore()
        self.chats: list[dict] = self.store.load_all()
        self.chat: dict = new_chat()
        self.worker: ChatWorker | None = None
        self.gen_chat: dict | None = None
        self.gen_msg: dict | None = None
        self.attachments: list[dict] = []
        self.caps: set[str] | None = None
        self.connected = False
        self.error_text = ""
        self._rows: list[QWidget] = []  # widget dei messaggi della chat mostrata
        self._rendered_chat: dict | None = None
        self._img_cache: dict = {}
        self._codes: dict = {}  # blocchi di codice copiabili, per chiave
        self._stick = True  # la vista segue la fine della chat
        self._dirty = False
        self._dragging = False
        self._really_quit = False
        self.tray: QSystemTrayIcon | None = None

        apply_appearance(int(self.settings["theme"]), self.settings["accent"])
        self.c = theme_colors()
        self.doc_css = doc_css(self.c)
        self._build_ui()
        self._build_actions()
        self._build_tray()
        self._restore_geometry()
        self._apply_theme()

        self.render_timer = QTimer(self)
        self.render_timer.setInterval(60)
        self.render_timer.timeout.connect(self._tick)
        self.render_timer.start()
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(8000)
        self.poll_timer.timeout.connect(self.check_server)
        self.poll_timer.start()

        self.refresh_sidebar()
        self._render()
        self.check_server()
        self.refresh_models()
        self.input.setFocus()

    # ---- costruzione UI
    def _icon_button(self, icons, fallback: str, tip: str, slot=None) -> QToolButton:
        b = QToolButton(objectName="iconBtn")
        ic = theme_icon(*icons)
        if ic.isNull():
            b.setText(fallback)
        else:
            b.setIcon(ic)
            b.setIconSize(QSize(18, 18))
        b.setToolTip(tip)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        if slot:
            b.clicked.connect(slot)
        return b

    def _side_button(self, icons, text: str, slot) -> QPushButton:
        b = QPushButton(theme_icon(*icons), "  " + text, objectName="sideBtn")
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(slot)
        return b

    def _build_ui(self):
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setHandleWidth(1)
        split.setChildrenCollapsible(False)
        self.setCentralWidget(split)
        self.splitter = split

        # --- barra laterale
        self.sidebar = side = QWidget(objectName="sidebar")
        side.setMinimumWidth(220)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(12, 14, 12, 12)
        sl.setSpacing(10)
        brand = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(self.windowIcon().pixmap(26, 26))
        brand.addWidget(logo)
        brand.addWidget(QLabel(tr("Ollama"), objectName="brand"))
        brand.addStretch(1)
        brand.addWidget(self._icon_button(("sidebar-collapse-left", "view-left-close", "go-previous"), "«",
                                          tr("Nascondi la barra laterale (Ctrl+B)"), self.toggle_sidebar))
        sl.addLayout(brand)
        self.btn_new = QPushButton(theme_icon("list-add", "document-new"), tr("  Nuova chat"), objectName="newChat")
        self.btn_new.setToolTip(tr("Nuova chat (Ctrl+N)"))
        self.btn_new.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_new.clicked.connect(self.new_chat_action)
        sl.addWidget(self.btn_new)
        self.search = QLineEdit(objectName="search")
        self.search.setPlaceholderText(tr("Cerca nelle chat…"))
        self.search.setClearButtonEnabled(True)
        self.search.addAction(theme_icon("edit-find", "search"), QLineEdit.ActionPosition.LeadingPosition)
        self.search.textChanged.connect(self.refresh_sidebar)
        sl.addWidget(self.search)
        self.chat_list = QListWidget(objectName="chatList")
        self.chat_list.setItemDelegate(ChatItemDelegate(self))
        self.chat_list.setMouseTracking(True)
        self.chat_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.chat_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.chat_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.chat_list.customContextMenuRequested.connect(self.chat_menu)
        self.chat_list.currentItemChanged.connect(self.on_chat_item)
        sl.addWidget(self.chat_list, 1)
        sl.addWidget(self._side_button(("run-build", "package-x-generic", "applications-other"), tr("Modelli"), self.open_models))
        sl.addWidget(self._side_button(("configure", "preferences-system", "settings-configure"), tr("Impostazioni"), self.open_settings))
        srow = QHBoxLayout()
        srow.setContentsMargins(8, 4, 0, 0)
        self.conn_lbl = QLabel("", objectName="conn")
        self.conn_lbl.setFont(scaled_font(self.conn_lbl.font(), 0.9))
        self.btn_start = QPushButton(tr("Avvia"), objectName="startBtn")
        self.btn_start.setToolTip(tr("Avvia il server Ollama"))
        self.btn_start.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_start.clicked.connect(self.start_server)
        self.btn_start.setVisible(False)
        srow.addWidget(self.conn_lbl, 1)
        srow.addWidget(self.btn_start)
        sl.addLayout(srow)
        split.addWidget(side)

        # --- area principale
        main = QWidget(objectName="main")
        ml = QVBoxLayout(main)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(0)

        header = QWidget(objectName="header")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(10, 8, 10, 4)
        hl.setSpacing(4)
        self.btn_show_side = self._icon_button(("sidebar-expand-left", "view-left-new", "go-next"), "»",
                                               tr("Mostra la barra laterale (Ctrl+B)"), self.toggle_sidebar)
        self.btn_new_hdr = self._icon_button(("document-new", "list-add"), "＋", tr("Nuova chat (Ctrl+N)"), self.new_chat_action)
        self.model_combo = MenuPicker(lambda t: t or tr("Nessun modello"), [(tr("Gestisci modelli…"), self.open_models)])
        self.model_combo.setToolTip(tr("Modello"))
        self.model_combo.currentTextChanged.connect(self.on_model_changed)
        self.btn_menu = self._icon_button(("application-menu", "open-menu", "overflow-menu"), "⋮", tr("Menu"))
        self.btn_menu.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        hl.addWidget(self.btn_show_side)
        hl.addWidget(self.btn_new_hdr)
        hl.addWidget(self.model_combo)
        hl.addStretch(1)
        hl.addWidget(self.btn_menu)
        ml.addWidget(header)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_hero())
        self.scroll = QScrollArea(objectName="chatScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        canvas = QWidget(objectName="chatCanvas")
        cl = QHBoxLayout(canvas)
        cl.setContentsMargins(20, 12, 20, 24)
        self.column = QWidget()
        self.column.setMaximumWidth(COLUMN_WIDTH)
        self.col_lay = QVBoxLayout(self.column)
        self.col_lay.setContentsMargins(0, 0, 0, 0)
        self.col_lay.setSpacing(22)
        self.error_box = QFrame(objectName="errorBox")
        el = QHBoxLayout(self.error_box)
        el.setContentsMargins(14, 10, 10, 10)
        self.error_lbl = QLabel(objectName="errorText")
        self.error_lbl.setWordWrap(True)
        self.error_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        retry = QPushButton(tr("Riprova"), objectName="retry")
        retry.setCursor(Qt.CursorShape.PointingHandCursor)
        retry.clicked.connect(self.regenerate)
        el.addWidget(self.error_lbl, 1)
        el.addWidget(retry, 0, Qt.AlignmentFlag.AlignTop)
        self.error_box.setVisible(False)
        self.col_lay.addWidget(self.error_box)
        self.col_lay.addStretch(1)
        cl.addStretch(1)
        cl.addWidget(self.column, 100)
        cl.addStretch(1)
        self.scroll.setWidget(canvas)
        sb = self.scroll.verticalScrollBar()
        sb.valueChanged.connect(lambda v: setattr(self, "_stick", v >= sb.maximum() - 40))
        sb.rangeChanged.connect(lambda _lo, hi: sb.setValue(hi) if self._stick else None)
        self.stack.addWidget(self.scroll)
        ml.addWidget(self.stack, 1)
        ml.addWidget(self._build_composer())

        split.addWidget(main)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes(self.settings["splitter"])
        self.sidebar.setVisible(bool(self.settings["sidebar"]))
        self._sync_sidebar_buttons()

    def _build_hero(self) -> QWidget:
        hero = QWidget(objectName="hero")
        hv = QVBoxLayout(hero)
        hv.setContentsMargins(20, 20, 20, 20)
        hv.addStretch(2)
        logo = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        logo.setPixmap(self.windowIcon().pixmap(60, 60))
        hv.addWidget(logo)
        hv.addSpacing(10)
        hv.addWidget(QLabel(tr("Come posso aiutarti?"), objectName="heroTitle", alignment=Qt.AlignmentFlag.AlignCenter))
        self.hero_sub = QLabel(objectName="heroSub", alignment=Qt.AlignmentFlag.AlignCenter)
        hv.addWidget(self.hero_sub)
        hv.addSpacing(24)
        grid_w = QWidget()
        grid_w.setMaximumWidth(640)
        grid = QGridLayout(grid_w)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(10)
        for i, (title, sub, prompt) in enumerate(SUGGESTIONS):
            grid.addWidget(SuggestionCard(tr(title), tr(sub), lambda p=prompt: self.use_prompt(tr(p))), i // 2, i % 2)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(grid_w, 100)
        row.addStretch(1)
        hv.addLayout(row)
        hv.addStretch(3)
        return hero

    def _build_composer(self) -> QWidget:
        area = QWidget(objectName="composerArea")
        outer = QHBoxLayout(area)
        outer.setContentsMargins(20, 4, 20, 10)
        col = QWidget()
        col.setMaximumWidth(COLUMN_WIDTH)
        cv = QVBoxLayout(col)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(6)

        self.composer = QFrame(objectName="composer")
        v = QVBoxLayout(self.composer)
        v.setContentsMargins(14, 10, 10, 8)
        v.setSpacing(4)
        self.chips = QWidget()
        self.chips_lay = QHBoxLayout(self.chips)
        self.chips_lay.setContentsMargins(0, 0, 0, 4)
        self.chips_lay.setSpacing(6)
        self.chips.setVisible(False)
        v.addWidget(self.chips)
        self.input = InputEdit()
        self.input.submit.connect(self.send_or_ignore)
        self.input.image_pasted.connect(lambda img: self.add_qimage(img, "immagine-incollata.png"))
        self.input.files_pasted.connect(lambda fs: [self.add_file(x) for x in fs])
        self.input.focus_changed.connect(lambda _f: self._sync_composer())
        self.input.textChanged.connect(self._update_send)
        v.addWidget(self.input)
        bottom = QHBoxLayout()
        bottom.setSpacing(4)
        self.btn_attach = self._icon_button(("mail-attachment", "paperclip", "document-open"), "📎",
                                            tr("Allega file o immagini (si possono anche trascinare nella finestra)"), self.pick_files)
        self.think_combo = MenuPicker(lambda t: tr("💭 Ragionamento: {}").format(t.lower()))
        self.think_combo.addItems([tr("Auto"), tr("Attivo"), tr("Disattivo")])
        self.think_combo.setCurrentIndex(int(self.settings["think_mode"]))
        self.think_combo.setToolTip(tr("Modalità ragionamento (per i modelli che la supportano)"))
        self.think_combo.currentIndexChanged.connect(self.on_think_changed)
        self.btn_send = QToolButton(objectName="send")
        self.btn_send.setFixedSize(34, 34)
        self.btn_send.setIconSize(QSize(20, 20))
        self.btn_send.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_send.clicked.connect(self.send_or_stop)
        bottom.addWidget(self.btn_attach)
        bottom.addWidget(self.think_combo)
        bottom.addStretch(1)
        bottom.addWidget(self.btn_send)
        v.addLayout(bottom)
        cv.addWidget(self.composer)

        self._hint_default = tr("Invio per inviare · Maiusc+Invio per andare a capo · trascina qui file, immagini e PDF")
        self.hint = QLabel(self._hint_default, objectName="hint", alignment=Qt.AlignmentFlag.AlignCenter)
        self.hint.setFont(scaled_font(self.hint.font(), 0.88))
        self.hint_timer = QTimer(self, singleShot=True)
        self.hint_timer.timeout.connect(lambda: self.hint.setText(self._hint_default))
        cv.addWidget(self.hint)

        outer.addStretch(1)
        outer.addWidget(col, 100)
        outer.addStretch(1)
        return area

    def _build_actions(self):
        def act(text, slot, shortcut=None, icons=()):
            a = QAction(theme_icon(*icons), text, self)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            a.triggered.connect(slot)
            self.addAction(a)  # le scorciatoie funzionano anche a menu chiuso
            return a

        m = QMenu(self)
        m.addAction(act(tr("Nuova chat"), self.new_chat_action, "Ctrl+N", ("document-new",)))
        m.addAction(act(tr("Rinomina chat…"), lambda: self.rename_chat(self.chat), "F2", ("edit-rename",)))
        m.addAction(act(tr("Esporta chat in Markdown…"), lambda: self.export_chat(self.chat), "Ctrl+E", ("document-export", "document-save-as")))
        m.addAction(act(tr("Elimina chat"), lambda: self.delete_chat(self.chat), None, ("edit-delete",)))
        m.addSeparator()
        m.addAction(act(tr("Rigenera risposta"), self.regenerate, "Ctrl+R", ("view-refresh",)))
        m.addAction(act(tr("Copia ultima risposta"), self.copy_last, "Ctrl+Shift+C", ("edit-copy",)))
        m.addSeparator()
        m.addAction(act(tr("Modelli…"), self.open_models, "Ctrl+M", ("run-build", "package-x-generic")))
        m.addAction(act(tr("Impostazioni…"), self.open_settings, "Ctrl+,", ("configure", "preferences-system")))
        m.addAction(act(tr("Avvia server Ollama"), self.start_server, None, ("media-playback-start",)))
        m.addAction(act(tr("Mostra/nascondi barra laterale"), self.toggle_sidebar, "Ctrl+B", ("view-split-left-right",)))
        m.addSeparator()
        m.addAction(act(tr("Informazioni"), self.about, None, ("help-about",)))
        m.addAction(act(tr("Esci"), self.quit_app, "Ctrl+Q", ("application-exit",)))
        self.btn_menu.setMenu(m)
        QShortcut(QKeySequence("Ctrl+L"), self, lambda: self.input.setFocus())
        QShortcut(QKeySequence("Esc"), self, self.stop_generation)

    def _build_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        menu = QMenu()
        menu.addAction(tr("Apri"), self.show_normal)
        menu.addAction(tr("Nuova chat"), lambda: (self.show_normal(), self.new_chat_action()))
        menu.addSeparator()
        menu.addAction(tr("Esci"), self.quit_app)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda r: self.show_normal() if r == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.setToolTip(APP_NAME)
        self.tray.show()
        QApplication.setQuitOnLastWindowClosed(False)

    def _restore_geometry(self):
        g = self.settings["geometry"]
        if g:
            try:
                self.restoreGeometry(QByteArray.fromBase64(g.encode()))
            except Exception:
                pass

    # ---- tema
    def _apply_theme(self):
        self.setStyleSheet(build_qss(self.c))
        shadow = QGraphicsDropShadowEffect(self.composer)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(0, 0, 0, 110 if self.c["dark"] else 28))
        self.composer.setGraphicsEffect(shadow)
        self._update_send()

    def changeEvent(self, e):
        if e.type() in (QEvent.Type.ApplicationPaletteChange, QEvent.Type.PaletteChange) and hasattr(self, "stack"):
            QTimer.singleShot(0, self._on_palette_changed)
        super().changeEvent(e)

    def _on_palette_changed(self):
        c = theme_colors()
        if c == self.c:
            return
        self.c = c
        self.doc_css = doc_css(c)
        self._apply_theme()
        self._clear_rows()
        self._render()
        self.chat_list.viewport().update()

    def _sync_composer(self):
        active = self.input.hasFocus() or self._dragging
        if self.composer.property("active") != active:
            self.composer.setProperty("active", active)
            self.composer.style().unpolish(self.composer)
            self.composer.style().polish(self.composer)

    def notify(self, msg: str, ms: int = 4000):
        self.hint.setText(msg)
        self.hint_timer.start(ms)

    # ---- finestra / uscita
    def show_normal(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def quit_app(self):
        self._really_quit = True
        self.close()

    def closeEvent(self, e):
        if self.settings["close_to_tray"] and self.tray and not self._really_quit:
            self.hide()
            e.ignore()
            return
        if self.worker:
            self.worker.cancel()
            self.worker.wait(3000)
        self.settings["geometry"] = bytes(self.saveGeometry().toBase64()).decode()
        if not self.sidebar.isHidden():
            self.settings["splitter"] = self.splitter.sizes()
        self.settings.save()
        if self.gen_chat:
            self.store.save(self.gen_chat)
        e.accept()
        QApplication.quit()

    def about(self):
        QMessageBox.about(self, APP_NAME, tr("<b>{}</b><br>Interfaccia desktop per Ollama su Linux.<br>Server: {}").format(APP_NAME, self.api.base))

    def toggle_sidebar(self):
        visible = not self.sidebar.isHidden()
        if visible:
            self.settings["splitter"] = self.splitter.sizes()
        self.sidebar.setVisible(not visible)
        self.settings["sidebar"] = not visible
        self._sync_sidebar_buttons()

    def _sync_sidebar_buttons(self):
        hidden = self.sidebar.isHidden()
        self.btn_show_side.setVisible(hidden)
        self.btn_new_hdr.setVisible(hidden)

    # ---- server
    def check_server(self):
        run_async(self.api.version, self._srv_ok, self._srv_fail)

    def _srv_ok(self, v):
        first = not self.connected
        self.connected = True
        self.conn_lbl.setText(f'<span style="color:#22c55e">●</span>&nbsp; Ollama {v}')
        self.conn_lbl.setToolTip(tr("Connesso a {}").format(self.api.base))
        self.btn_start.setVisible(False)
        if first:
            self.refresh_models()

    def _srv_fail(self, _m):
        self.connected = False
        self.conn_lbl.setText('<span style="color:#ef4444">●</span>&nbsp; ' + tr("Non connesso"))
        self.conn_lbl.setToolTip(tr("Ollama non raggiungibile su {}").format(self.api.base))
        host = urlparse(self.api.base).hostname or ""
        self.btn_start.setVisible(host in ("127.0.0.1", "localhost", "::1"))

    def start_server(self):
        exe = shutil.which("ollama")
        if not exe:
            QMessageBox.information(self, tr("Ollama non trovato"),
                                    tr("Il comando 'ollama' non e' installato.\n\nInstallalo con:\n  curl -fsSL https://ollama.com/install.sh | sh"))
            return
        try:
            has_unit = subprocess.run(["systemctl", "cat", "ollama"], capture_output=True).returncode == 0
        except FileNotFoundError:
            has_unit = False
        try:
            if has_unit and shutil.which("pkexec"):
                subprocess.Popen(["pkexec", "systemctl", "start", "ollama"])
            else:
                subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, tr("Errore"), str(e))
            return
        self.notify(tr("Avvio di Ollama…"))
        QTimer.singleShot(2500, self.check_server)

    # ---- modelli
    def refresh_models(self):
        run_async(self.api.tags, self._models_loaded, lambda _m: None)

    def _models_loaded(self, models):
        names = sorted(m["name"] for m in models)
        want = (self.chat.get("model") or self.settings["last_model"] or self.model_combo.currentText())
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self.model_combo.addItems(names)
        if want in names:
            self.model_combo.setCurrentText(want)
        elif names:
            self.model_combo.setCurrentIndex(0)
        self.model_combo.blockSignals(False)
        self.on_model_changed(self.model_combo.currentText())
        if not names:
            self.notify(tr("Nessun modello installato: apri «Modelli» per scaricarne uno."), 8000)
        self._render()

    def on_model_changed(self, name: str):
        if not name:
            return
        self.chat["model"] = name
        self.settings["last_model"] = name
        self.caps = None
        self.think_combo.setVisible(True)
        self.btn_attach.setToolTip(tr("Allega file o immagini (si possono anche trascinare nella finestra)"))

        def ok(info):
            if self.model_combo.currentText() != name:
                return
            caps = info.get("capabilities")
            if caps is None:
                return
            self.caps = set(caps)
            self.think_combo.setVisible("thinking" in self.caps)
            tip = tr("Allega file") + (tr(" o immagini") if "vision" in self.caps else tr(" (questo modello non supporta le immagini)"))
            self.btn_attach.setToolTip(tip)

        run_async(lambda: self.api.show(name), ok, lambda _m: None)
        self._render()

    def on_think_changed(self, i: int):
        self.settings["think_mode"] = i

    def open_models(self):
        d = ModelsDialog(self)
        d.changed.connect(self.refresh_models)
        d.exec()

    def open_settings(self):
        d = SettingsDialog(self)
        if d.exec():
            self.api.set_host(self.settings["host"])
            self.connected = False
            self.check_server()
            self.refresh_models()
            self._clear_rows()
            self._render()

    # ---- sidebar / cronologia
    @staticmethod
    def _day_group(ts: float, today: float) -> str:
        if ts >= today:
            return tr("Oggi")
        if ts >= today - 86400:
            return tr("Ieri")
        if ts >= today - 6 * 86400:
            return tr("Ultimi 7 giorni")
        if ts >= today - 29 * 86400:
            return tr("Ultimi 30 giorni")
        return tr("Meno recenti")

    def refresh_sidebar(self):
        q = self.search.text().strip().lower()
        lt = time.localtime()
        today = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
        self.chat_list.blockSignals(True)
        self.chat_list.clear()
        group = None
        for c in sorted(self.chats, key=lambda c: -c.get("updated", 0)):
            if q and q not in c["title"].lower() and not any(q in (m.get("display") or m.get("content", "")).lower() for m in c["messages"]):
                continue
            ts = c.get("updated", 0)
            g = self._day_group(ts, today)
            if g != group:
                group = g
                h = QListWidgetItem(g)
                h.setFlags(Qt.ItemFlag.NoItemFlags)
                h.setData(ROLE_HEADER, True)
                self.chat_list.addItem(h)
            when = time.strftime("%H:%M" if ts >= today - 86400 else "%d/%m/%Y", time.localtime(ts))
            sub = tr("● sta scrivendo…") if c is self.gen_chat else f"{c.get('model', '')} · {when}"
            it = QListWidgetItem(c["title"])
            it.setData(ROLE_ID, c["id"])
            it.setData(ROLE_SUB, sub)
            it.setToolTip(f"{c['title']}\n{c.get('model', '')} · {time.strftime('%d/%m/%Y %H:%M', time.localtime(ts))}")
            self.chat_list.addItem(it)
            if c["id"] == self.chat["id"]:
                self.chat_list.setCurrentItem(it)
        self.chat_list.blockSignals(False)

    def on_chat_item(self, cur, _prev):
        if not cur:
            return
        c = self.chat_by_item(cur)
        if c:
            self.select_chat(c)

    def chat_by_item(self, item) -> dict | None:
        cid = item.data(ROLE_ID)
        return next((c for c in self.chats if c["id"] == cid), None) if cid else None

    def chat_menu(self, pos):
        item = self.chat_list.itemAt(pos)
        c = self.chat_by_item(item) if item else None
        if not c:
            return
        m = QMenu(self)
        a1 = m.addAction(theme_icon("edit-rename"), tr("Rinomina…"))
        a2 = m.addAction(theme_icon("document-export", "document-save-as"), tr("Esporta in Markdown…"))
        m.addSeparator()
        a3 = m.addAction(theme_icon("edit-delete"), tr("Elimina"))
        act = m.exec(self.chat_list.viewport().mapToGlobal(pos))
        if act == a1:
            self.rename_chat(c)
        elif act == a2:
            self.export_chat(c)
        elif act == a3:
            self.delete_chat(c)

    def rename_chat(self, c: dict):
        if c not in self.chats:
            return
        t, ok = QInputDialog.getText(self, tr("Rinomina"), tr("Titolo:"), text=c["title"])
        if ok and t.strip():
            c["title"] = t.strip()
            self.store.save(c)
            self.refresh_sidebar()
            self._update_title()

    def delete_chat(self, c: dict):
        if c not in self.chats:
            return
        if QMessageBox.question(self, tr("Elimina chat"), tr("Eliminare «{}»?").format(c["title"])) != QMessageBox.StandardButton.Yes:
            return
        if c is self.gen_chat and self.worker:
            self.worker.cancel()
            self.worker.wait(3000)
        self.store.delete(c)
        self.chats = [x for x in self.chats if x is not c]
        if c is self.chat:
            self.chat = new_chat(self.model_combo.currentText())
            self.error_text = ""
        self.refresh_sidebar()
        self._render()

    def export_chat(self, c: dict):
        if not c["messages"]:
            return
        path, _ = QFileDialog.getSaveFileName(self, tr("Esporta chat"), f"{c['title'][:40]}.md", "Markdown (*.md)")
        if not path:
            return
        out = [f"# {c['title']}", tr("*Modello: {}*").format(c.get("model", "")), ""]
        for m in c["messages"]:
            if m["role"] == "user":
                out += [tr("**Tu:**"), "", m.get("display") or m["content"], ""]
            else:
                out += [tr("**Assistente:**"), "", split_think(m["content"])[1], ""]
        Path(path).write_text("\n".join(out), "utf-8")
        self.notify(tr("Chat esportata."))

    def new_chat_action(self):
        if not self.chat["messages"]:
            self.input.setFocus()
            return
        self.select_chat(new_chat(self.model_combo.currentText()))
        self.refresh_sidebar()
        self.input.setFocus()

    def select_chat(self, c: dict):
        self.chat = c
        self.error_text = ""
        m = c.get("model")
        if m and self.model_combo.findText(m) >= 0 and self.model_combo.currentText() != m:
            self.model_combo.setCurrentText(m)
        elif not m:
            c["model"] = self.model_combo.currentText()
        self._render()

    def use_prompt(self, prompt: str):
        self.input.setPlainText(prompt)
        cur = self.input.textCursor()
        cur.movePosition(cur.MoveOperation.End)
        self.input.setTextCursor(cur)
        self.input.setFocus()

    # ---- allegati
    def pick_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, tr("Allega file"))
        for f in files:
            self.add_file(f)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._dragging = True
            self._sync_composer()

    def dragLeaveEvent(self, e):
        self._dragging = False
        self._sync_composer()

    def dropEvent(self, e):
        self._dragging = False
        self._sync_composer()
        for u in e.mimeData().urls():
            if u.isLocalFile():
                self.add_file(u.toLocalFile())

    def add_qimage(self, img: QImage, name: str):
        if max(img.width(), img.height()) > MAX_IMAGE_SIDE:
            img = img.scaled(MAX_IMAGE_SIDE, MAX_IMAGE_SIDE, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        img.save(buf, "PNG")
        thumb = rounded_pixmap(img, 20, 4, self.devicePixelRatioF())
        self.attachments.append({"name": name, "kind": "image", "b64": base64.b64encode(bytes(ba)).decode(), "thumb": thumb})
        self._rebuild_chips()

    def add_file(self, path: str):
        p = Path(path)
        try:
            ext = p.suffix.lower()
            if ext in IMAGE_EXT:
                img = QImage(str(p))
                if img.isNull():
                    raise RuntimeError(tr("Immagine non leggibile."))
                self.add_qimage(img, p.name)
                return
            if ext == ".pdf":
                text = pdf_text(str(p))
            else:
                raw = p.read_bytes()[: MAX_TEXT_CHARS * 4]
                if b"\0" in raw[:4096]:
                    raise RuntimeError(tr("Tipo di file non supportato (non e' testo)."))
                text = raw.decode("utf-8", errors="replace")
            if len(text) > MAX_TEXT_CHARS:
                text = text[:MAX_TEXT_CHARS] + tr("\n[… file troncato …]")
            if not text.strip():
                raise RuntimeError(tr("Il file non contiene testo."))
            self.attachments.append({"name": p.name, "kind": "text", "text": text})
            self._rebuild_chips()
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, tr("Allegato non valido"), f"{p.name}: {e}")

    def _rebuild_chips(self):
        while self.chips_lay.count():
            it = self.chips_lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        for i, a in enumerate(self.attachments):
            name = a["name"] if len(a["name"]) <= 28 else a["name"][:25] + "…"
            b = QPushButton(f"{'' if a.get('thumb') else '📄 '}{name}   ✕", objectName="chip")
            if a.get("thumb"):
                b.setIcon(QIcon(a["thumb"]))
            b.setToolTip(tr("{} — clic per rimuovere").format(a["name"]))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, i=i: self._remove_attachment(i))
            self.chips_lay.addWidget(b)
        self.chips_lay.addStretch(1)
        self.chips.setVisible(bool(self.attachments))
        self._update_send()

    def _remove_attachment(self, i: int):
        if 0 <= i < len(self.attachments):
            del self.attachments[i]
            self._rebuild_chips()

    # ---- invio / generazione
    def send_or_ignore(self):
        if not self.worker:
            self.send()

    def send_or_stop(self):
        if self.worker:
            self.stop_generation()
        else:
            self.send()

    def stop_generation(self):
        if self.worker:
            self.worker.cancel()

    def send(self):
        if self.worker:
            return
        text = self.input.toPlainText().strip()
        if not text and not self.attachments:
            return
        parts, images, names = [], [], []
        for a in self.attachments:
            names.append(a["name"])
            if a["kind"] == "image":
                images.append(a["b64"])
            else:
                parts.append(tr("File allegato «{}»:\n```\n{}\n```").format(a["name"], a["text"]))
        if text:
            parts.append(text)
        msg = {"role": "user", "content": "\n\n".join(parts), "display": text, "images": images, "attachments": names}
        if images and self.caps is not None and "vision" not in self.caps:
            QMessageBox.information(self, tr("Immagini non supportate"),
                                    tr("Il modello «{}» non sembra supportare le immagini. Scegline uno con visione (es. llava, gemma3, qwen2.5vl).").format(self.model_combo.currentText()))
            return
        self.chat["messages"].append(msg)
        if self.chat["title"] == tr("Nuova chat"):
            t = (text or (names[0] if names else tr("Nuova chat"))).replace("\n", " ")
            self.chat["title"] = t[:42] + ("…" if len(t) > 42 else "")
        if self.chat not in self.chats:
            self.chats.append(self.chat)
        self.input.clear()
        self.attachments.clear()
        self._rebuild_chips()
        self.error_text = ""
        self._stick = True
        if not self._start_generation():
            self.chat["messages"].pop()
            if not self.chat["messages"] and self.chat in self.chats:
                self.chats.remove(self.chat)
            self.input.setPlainText(text)
            self._render()
            return
        self.refresh_sidebar()

    def regenerate(self):
        if self.worker:
            return
        msgs = self.chat["messages"]
        while msgs and msgs[-1]["role"] != "user":
            msgs.pop()
        if not msgs:
            return
        self.error_text = ""
        self._stick = True
        self._start_generation()
        self.refresh_sidebar()

    def _payload_messages(self, chat: dict) -> list[dict]:
        out = []
        sp = self.settings["system_prompt"].strip()
        if sp:
            out.append({"role": "system", "content": sp})
        for m in chat["messages"]:
            if m["role"] not in ("user", "assistant"):
                continue
            content = m["content"] if m["role"] == "user" else split_think(m["content"])[1]
            d = {"role": m["role"], "content": content}
            if m.get("images"):
                d["images"] = m["images"]
            out.append(d)
        return out

    def _start_generation(self) -> bool:
        model = self.model_combo.currentText().strip()
        if not model:
            QMessageBox.information(self, tr("Nessun modello"), tr("Non c'e' nessun modello installato. Scaricane uno dalla finestra «Modelli»."))
            self.open_models()
            return False
        s = self.settings
        chat = self.chat
        chat["model"] = model
        payload: dict = {"model": model, "messages": self._payload_messages(chat), "stream": True}
        opts = {}
        if int(s["num_ctx"]) > 0:
            opts["num_ctx"] = int(s["num_ctx"])
        if float(s["temperature"]) >= 0:
            opts["temperature"] = round(float(s["temperature"]), 2)
        if opts:
            payload["options"] = opts
        ka = str(s["keep_alive"]).strip()
        if ka:
            payload["keep_alive"] = int(ka) if re.fullmatch(r"-?\d+", ka) else ka
        mode = int(s["think_mode"])
        if mode in (1, 2) and (self.caps is None or "thinking" in self.caps):
            payload["think"] = (mode == 1)

        msg = {"role": "assistant", "content": "", "thinking": "", "model": model}
        chat["messages"].append(msg)
        self.gen_chat, self.gen_msg = chat, msg
        w = ChatWorker(self.api.base, payload)
        w.token.connect(self.on_token)
        w.thinking.connect(self.on_thinking)
        w.done.connect(self.on_done)
        w.failed.connect(self.on_failed)
        w.finished.connect(self.on_worker_finished)
        self.worker = w
        self._update_send()
        self._render()
        w.start()
        return True

    @Slot(str)
    def on_token(self, t: str):
        if self.gen_msg is not None:
            self.gen_msg["content"] += t
            self._dirty = self.chat is self.gen_chat or self._dirty

    @Slot(str)
    def on_thinking(self, t: str):
        if self.gen_msg is not None:
            self.gen_msg["thinking"] += t
            self._dirty = self.chat is self.gen_chat or self._dirty

    @Slot(dict)
    def on_done(self, stats: dict):
        chat, msg = self.gen_chat, self.gen_msg
        ec, ed = stats.get("eval_count"), stats.get("eval_duration")
        if msg is not None and ec and ed:
            note = tr(" · interrotta") if stats.get("cancelled") else ""
            msg["stats"] = tr("{} token · {:.1f} token/s{}").format(ec, ec / (ed / 1e9), note)
        if chat is not None:
            chat["updated"] = time.time()
            if msg is not None and not msg["content"].strip() and not msg.get("thinking"):
                chat["messages"].remove(msg)
            self.store.save(chat)

    @Slot(str)
    def on_failed(self, msg: str):
        chat = self.gen_chat
        if chat is not None and self.gen_msg is not None and not self.gen_msg["content"].strip() and not self.gen_msg.get("thinking"):
            chat["messages"].remove(self.gen_msg)
        if chat is not None:
            self.store.save(chat)
        if "not found" in msg.lower():
            msg += tr("\nSuggerimento: scarica il modello dalla finestra «Modelli».")
        if chat is self.chat:
            self.error_text = msg
        else:
            self.notify(tr("Errore: ") + msg.splitlines()[0], 10000)

    @Slot()
    def on_worker_finished(self):
        self.worker = None
        self.gen_msg = None
        self.gen_chat = None
        self._update_send()
        self._render()
        self.refresh_sidebar()

    def _update_send(self):
        busy = self.worker is not None
        has_input = bool(self.input.toPlainText().strip() or self.attachments)
        self.btn_send.setIcon(glyph_icon("stop" if busy else "send", self.c["accent_fg"]))
        self.btn_send.setToolTip(tr("Ferma la generazione (Esc)") if busy else tr("Invia (Invio)"))
        self.btn_send.setEnabled(busy or has_input)

    def copy_last(self):
        for m in reversed(self.chat["messages"]):
            if m["role"] == "assistant" and m["content"].strip():
                QGuiApplication.clipboard().setText(split_think(m["content"])[1])
                self.notify(tr("Risposta copiata negli appunti."), 3000)
                return

    # ---- rendering
    def on_anchor(self, url):
        if url.scheme() == "copycode":
            code = self._codes.get(url.path())
            if code is not None:
                QGuiApplication.clipboard().setText(code)
                self.notify(tr("Codice copiato negli appunti."), 3000)
        else:
            QDesktopServices.openUrl(url)

    def render_markdown(self, text: str) -> str:
        """Markdown → HTML, con i blocchi di codice incorniciati e un link per copiarli."""
        c = self.c

        def code_block(mt):
            inner = re.sub(r"\n+(</code>)?\s*$", r"\1", mt.group(1))
            lang = re.search(r'class="language-([\w+#.-]+)"', inner)
            code = html.unescape(re.sub(r"<[^>]+>", "", inner))
            key = hashlib.md5(code.encode()).hexdigest()[:16]
            self._codes[key] = code
            label = html.escape(lang.group(1)) if lang else tr("codice")
            return (
                f'<table width="100%" cellspacing="0" cellpadding="10" border="0" bgcolor="{c["codebg"]}">'
                f'<tr bgcolor="{c["codehead"]}"><td><span style="color:{c["muted"]}; font-size:small">{label}</span></td>'
                f'<td align="right"><a href="copycode:{key}"><span style="color:{c["muted"]}; font-size:small">'
                f'⧉ {tr("Copia")}</span></a></td></tr>'
                f'<tr><td colspan="2"><pre>{inner}</pre></td></tr></table>'
            )

        h = md_to_html(text).replace('<table border="1"', f'<table border="1" style="border-color:{c["border"]}"')
        return re.sub(r"<pre[^>]*>(.*?)</pre>", code_block, h, flags=re.S)

    def thumbnail(self, b64: str, side: int) -> QPixmap:
        key = (hashlib.md5(b64[:4000].encode()).hexdigest(), len(b64), side)
        pm = self._img_cache.get(key)
        if pm is None:
            pm = rounded_pixmap(QImage.fromData(base64.b64decode(b64)), side, 12, self.devicePixelRatioF())
            self._img_cache[key] = pm
        return pm

    def _tick(self):
        if self._dirty:
            self._dirty = False
            self._render()

    def _clear_rows(self):
        for w in self._rows:
            self.col_lay.removeWidget(w)
            w.hide()
            w.deleteLater()
        self._rows = []
        self._rendered_chat = None

    def _update_title(self):
        msgs = self.chat["messages"]
        self.setWindowTitle(f"{self.chat['title']} — {APP_NAME}" if msgs else APP_NAME)

    def _render(self):
        if self._rendered_chat is not self.chat:
            self._clear_rows()
            self._codes.clear()
            self._rendered_chat = self.chat
            self._stick = True
        msgs = self.chat["messages"]
        # si riusano i widget dei messaggi gia' mostrati, si aggiungono/tolgono solo le differenze
        i = 0
        while i < len(self._rows) and i < len(msgs) and self._rows[i].m is msgs[i]:
            i += 1
        for w in self._rows[i:]:
            self.col_lay.removeWidget(w)
            w.hide()
            w.deleteLater()
        del self._rows[i:]
        for m in msgs[i:]:
            w = UserMessage(self, m) if m["role"] == "user" else AssistantMessage(self, m)
            self.col_lay.insertWidget(len(self._rows), w)
            self._rows.append(w)
        busy = self.worker is not None
        last = msgs[-1] if msgs else None
        for w in self._rows:
            streaming = busy and w.m is self.gen_msg
            w.update_content(streaming, w.m is last and not busy)
        self.error_lbl.setText("⚠  " + self.error_text)
        self.error_box.setVisible(bool(self.error_text))
        empty = not msgs and not self.error_text
        if empty:
            model = self.model_combo.currentText() or tr("nessun modello installato")
            self.hero_sub.setText(tr("Modello: {}").format(html.escape(model)))
        self.stack.setCurrentIndex(0 if empty else 1)
        self._update_title()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("ollama-gui")
    app.setApplicationDisplayName(APP_NAME)
    app.setDesktopFileName("ollama-gui")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
