#!/usr/bin/env python3
"""
Ollama GUI per Linux
====================
Interfaccia desktop (Qt / PySide6) per Ollama, con le stesse funzioni
dell'app ufficiale per Windows:

  * chat in streaming con rendering Markdown e blocchi di codice
  * cronologia delle conversazioni (ricerca, rinomina, elimina, esporta)
  * scelta del modello, download (pull) con barra di avanzamento, eliminazione
  * allegati: immagini (modelli con visione), file di testo/codice, PDF
    - trascinando i file nella finestra, con il pulsante o incollando un'immagine
  * modalita' "ragionamento" (thinking) per i modelli che la supportano
  * impostazioni: host, finestra di contesto, temperatura, prompt di sistema,
    keep_alive, icona nella barra di sistema
  * rigenera risposta, ferma generazione, copia risposta, statistiche token/s
  * avvio del server Ollama se non e' in esecuzione
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
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
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, Qt, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QGuiApplication,
    QIcon,
    QImage,
    QKeySequence,
    QPainter,
    QPixmap,
    QShortcut,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
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
    QSpinBox,
    QSplitter,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
    QAbstractItemView,
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

THINK_RE = re.compile(r"<think>(.*?)(?:</think>|$)", re.S)


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
        raise RuntimeError("Per allegare i PDF serve il pacchetto 'pypdf' (pip install pypdf).") from e
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
    return {"id": uuid.uuid4().hex, "title": "Nuova chat", "model": model, "created": now, "updated": now, "messages": []}


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


def friendly_error(e: Exception | str) -> str:
    s = str(e)
    if "Connection refused" in s or "Max retries" in s or "ConnectionError" in s or "Failed to establish" in s:
        return "Impossibile contattare Ollama. Il server e' in esecuzione? (menu Server → Avvia Ollama)"
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
    progress = Signal(str, int, int)
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


# --------------------------------------------------------------------------- widget
class InputEdit(QPlainTextEdit):
    submit = Signal()
    image_pasted = Signal(QImage)
    files_pasted = Signal(list)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(False)
        self.setPlaceholderText("Scrivi un messaggio…  (Invio per inviare, Maiusc+Invio per andare a capo)")
        self.document().contentsChanged.connect(self._fit)
        self._fit()

    def _fit(self):
        h = int(self.document().size().height()) + 14
        self.setFixedHeight(max(46, min(h, 170)))

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (e.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            self.submit.emit()
            return
        super().keyPressEvent(e)

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
        self.setWindowTitle("Modelli")
        self.resize(780, 480)
        lay = QVBoxLayout(self)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Modello", "Dimensione", "Parametri", "Quantizzazione", "Modificato"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        lay.addWidget(self.table)

        row = QHBoxLayout()
        self.btn_refresh = QPushButton("Aggiorna")
        self.btn_unload = QPushButton("Scarica dalla memoria")
        self.btn_delete = QPushButton("Elimina")
        for b in (self.btn_refresh, self.btn_unload, self.btn_delete):
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)

        box = QHBoxLayout()
        self.name = QComboBox()
        self.name.setEditable(True)
        self.name.addItems(self.SUGGESTED)
        self.name.setCurrentText("")
        self.name.lineEdit().setPlaceholderText("Nome del modello da scaricare, es. llama3.2:3b")
        self.btn_pull = QPushButton("Scarica")
        self.btn_cancel = QPushButton("Annulla")
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
        self.refresh()

    def selected(self) -> str | None:
        r = self.table.currentRow()
        return self.table.item(r, 0).text() if r >= 0 else None

    def refresh(self):
        def ok(models):
            ok_ps = {}

            def fill(ps):
                loaded = {m.get("name") for m in ps}
                self._fill(models, loaded)

            run_async(self.win.api.ps, fill, lambda _m: self._fill(models, set()))

        run_async(self.win.api.tags, ok, lambda m: self.lbl.setText(m))

    def _fill(self, models, loaded):
        self.table.setRowCount(0)
        for m in sorted(models, key=lambda m: m.get("name", "")):
            d = m.get("details") or {}
            name = m.get("name", "")
            if name in loaded:
                name_txt = name
            else:
                name_txt = name
            vals = [
                name_txt,
                human_size(m.get("size", 0)),
                d.get("parameter_size", ""),
                d.get("quantization_level", ""),
                (m.get("modified_at") or "")[:16].replace("T", " "),
            ]
            r = self.table.rowCount()
            self.table.insertRow(r)
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if name in loaded:
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                    if c == 0:
                        it.setToolTip("In memoria")
                self.table.setItem(r, c, it)
        self.lbl.setText(f"{len(models)} modelli installati" + (f" · in memoria (grassetto): {', '.join(sorted(loaded))}" if loaded else ""))

    def delete_selected(self):
        n = self.selected()
        if not n:
            return
        if QMessageBox.question(self, "Elimina modello", f"Eliminare «{n}» dal disco?") != QMessageBox.StandardButton.Yes:
            return

        def ok(_):
            self.refresh()
            self.changed.emit()

        run_async(lambda: self.win.api.delete(n), ok, lambda m: QMessageBox.warning(self, "Errore", m))

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
        self.lbl.setText(f"Download di {name}…")
        self.puller.start()

    @Slot(str, int, int)
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
            self.lbl.setText("Download annullato.")
        else:
            self.lbl.setText("Download completato.")
        self.refresh()
        self.changed.emit()

    @Slot(str)
    def on_pull_failed(self, msg):
        self.lbl.setText("Errore: " + msg)

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
        self.setWindowTitle("Impostazioni")
        self.resize(560, 520)
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        lay.addWidget(tabs)

        # --- generale
        g = QWidget()
        f = QFormLayout(g)
        self.host = QLineEdit(s["host"])
        self.btn_test = QPushButton("Verifica")
        self.test_lbl = QLabel("")
        hrow = QHBoxLayout()
        hrow.addWidget(self.host, 1)
        hrow.addWidget(self.btn_test)
        f.addRow("Indirizzo del server:", hrow)
        f.addRow("", self.test_lbl)
        self.ctx = QSpinBox()
        self.ctx.setRange(0, 2_097_152)
        self.ctx.setSingleStep(1024)
        self.ctx.setSpecialValueText("Predefinito del modello")
        self.ctx.setValue(int(s["num_ctx"]))
        f.addRow("Lunghezza del contesto:", self.ctx)
        self.temp = QDoubleSpinBox()
        self.temp.setDecimals(1)
        self.temp.setRange(-0.1, 2.0)
        self.temp.setSingleStep(0.1)
        self.temp.setSpecialValueText("Predefinita del modello")
        self.temp.setValue(float(s["temperature"]))
        f.addRow("Temperatura:", self.temp)
        self.keep = QLineEdit(s["keep_alive"])
        self.keep.setPlaceholderText("vuoto = 5m · -1 = sempre in memoria · 0 = scarica subito · 30m")
        f.addRow("Mantieni in memoria:", self.keep)
        self.show_think = QCheckBox("Mostra il ragionamento dei modelli (thinking)")
        self.show_think.setChecked(bool(s["show_thinking"]))
        f.addRow("", self.show_think)
        self.tray = QCheckBox("Alla chiusura riduci nella barra di sistema")
        self.tray.setChecked(bool(s["close_to_tray"]))
        self.tray.setEnabled(win.tray is not None)
        f.addRow("", self.tray)
        tabs.addTab(g, "Generale")

        # --- prompt
        p = QWidget()
        pl = QVBoxLayout(p)
        pl.addWidget(QLabel("Prompt di sistema (inviato all'inizio di ogni conversazione):"))
        self.sysprompt = QPlainTextEdit(s["system_prompt"])
        pl.addWidget(self.sysprompt)
        tabs.addTab(p, "Prompt di sistema")

        # --- server
        sv = QTextBrowser()
        sv.setOpenExternalLinks(True)
        sv.setHtml(
            """
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
"""
        )
        tabs.addTab(sv, "Server")

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.btn_test.clicked.connect(self.test)

    def test(self):
        api = Ollama(self.host.text())
        self.test_lbl.setText("Verifica in corso…")
        run_async(api.version, lambda v: self.test_lbl.setText(f"✔ Connesso — Ollama {v}"), lambda m: self.test_lbl.setText("✘ " + m))

    def accept(self):
        s = self.win.settings
        s["host"] = normalize_host(self.host.text())
        s["num_ctx"] = self.ctx.value()
        s["temperature"] = self.temp.value()
        s["keep_alive"] = self.keep.text().strip()
        s["show_thinking"] = self.show_think.isChecked()
        s["close_to_tray"] = self.tray.isChecked()
        s["system_prompt"] = self.sysprompt.toPlainText()
        s.save()
        super().accept()


# --------------------------------------------------------------------------- finestra principale
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(make_icon())
        self.resize(1180, 760)
        self.setAcceptDrops(True)

        self.settings = Settings()
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
        self._html_cache: dict = {}
        self._img_cache: dict = {}
        self._dirty = False
        self._really_quit = False
        self.tray: QSystemTrayIcon | None = None

        self.c = self._colors()
        self._build_ui()
        self._build_menu()
        self._build_tray()
        self._restore_geometry()

        self.render_timer = QTimer(self)
        self.render_timer.setInterval(60)
        self.render_timer.timeout.connect(self._tick)
        self.render_timer.start()
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(8000)
        self.poll_timer.timeout.connect(self.check_server)
        self.poll_timer.start()

        QShortcut(QKeySequence("Ctrl+N"), self, self.new_chat_action)
        QShortcut(QKeySequence("Ctrl+,"), self, self.open_settings)
        QShortcut(QKeySequence("Ctrl+L"), self, lambda: self.input.setFocus())
        QShortcut(QKeySequence("Esc"), self, self.stop_generation)

        self.refresh_sidebar()
        self._render()
        self.check_server()
        self.refresh_models()

    # ---- costruzione UI
    def _colors(self) -> dict:
        dark = QApplication.palette().window().color().lightness() < 128
        if dark:
            return dict(dark=True, bubble="#303541", think="#262a33", muted="#9aa0ab", err="#f87171",
                        codebg="#1b1d23", codefg="#e4e4e7", link="#7cb7ff")
        return dict(dark=False, bubble="#e8ebf2", think="#f1f3f6", muted="#6b7280", err="#b91c1c",
                    codebg="#f3f4f6", codefg="#111827", link="#1d4ed8")

    def _build_ui(self):
        split = QSplitter(Qt.Orientation.Horizontal)
        self.setCentralWidget(split)

        # barra laterale
        side = QWidget()
        sl = QVBoxLayout(side)
        sl.setContentsMargins(8, 8, 8, 8)
        self.btn_new = QPushButton("＋  Nuova chat")
        self.btn_new.clicked.connect(self.new_chat_action)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Cerca nelle chat…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh_sidebar)
        self.chat_list = QListWidget()
        self.chat_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.chat_list.customContextMenuRequested.connect(self.chat_menu)
        self.chat_list.currentItemChanged.connect(self.on_chat_item)
        row = QHBoxLayout()
        b1 = QPushButton("Modelli")
        b1.clicked.connect(self.open_models)
        b2 = QPushButton("Impostazioni")
        b2.clicked.connect(self.open_settings)
        row.addWidget(b1)
        row.addWidget(b2)
        sl.addWidget(self.btn_new)
        sl.addWidget(self.search)
        sl.addWidget(self.chat_list, 1)
        sl.addLayout(row)
        split.addWidget(side)

        # area chat
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(10, 8, 10, 8)
        top = QHBoxLayout()
        self.model_combo = QComboBox()
        self.model_combo.setMinimumWidth(240)
        self.model_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.model_combo.currentTextChanged.connect(self.on_model_changed)
        self.think_combo = QComboBox()
        self.think_combo.addItems(["Ragionamento: auto", "Ragionamento: attivo", "Ragionamento: disattivo"])
        self.think_combo.setCurrentIndex(int(self.settings["think_mode"]))
        self.think_combo.currentIndexChanged.connect(self.on_think_changed)
        top.addWidget(QLabel("Modello:"))
        top.addWidget(self.model_combo)
        top.addWidget(self.think_combo)
        top.addStretch(1)
        rl.addLayout(top)

        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)
        self.view.setAcceptDrops(False)
        f = self.view.font()
        f.setPointSize(max(f.pointSize(), 11))
        self.view.setFont(f)
        self.view.document().setDefaultStyleSheet(
            f"pre {{ background-color:{self.c['codebg']}; color:{self.c['codefg']}; font-family:monospace; }}"
            f"code {{ font-family:monospace; background-color:{self.c['codebg']}; }}"
            f"a {{ color:{self.c['link']}; }}"
        )
        rl.addWidget(self.view, 1)

        arow = QHBoxLayout()
        self.btn_regen = QPushButton("↻ Rigenera")
        self.btn_copy = QPushButton("⧉ Copia risposta")
        self.btn_regen.clicked.connect(self.regenerate)
        self.btn_copy.clicked.connect(self.copy_last)
        arow.addStretch(1)
        arow.addWidget(self.btn_regen)
        arow.addWidget(self.btn_copy)
        rl.addLayout(arow)

        self.chips = QWidget()
        self.chips_lay = QHBoxLayout(self.chips)
        self.chips_lay.setContentsMargins(0, 0, 0, 0)
        self.chips.setVisible(False)
        rl.addWidget(self.chips)

        irow = QHBoxLayout()
        irow.setAlignment(Qt.AlignmentFlag.AlignBottom)
        self.btn_attach = QPushButton("📎")
        self.btn_attach.setToolTip("Allega file o immagini (si possono anche trascinare nella finestra)")
        self.btn_attach.setFixedSize(46, 46)
        self.btn_attach.clicked.connect(self.pick_files)
        self.input = InputEdit()
        self.input.submit.connect(self.send_or_ignore)
        self.input.image_pasted.connect(lambda img: self.add_qimage(img, "immagine-incollata.png"))
        self.input.files_pasted.connect(lambda fs: [self.add_file(x) for x in fs])
        self.btn_send = QPushButton("Invia ➤")
        self.btn_send.setFixedHeight(46)
        self.btn_send.setMinimumWidth(90)
        self.btn_send.clicked.connect(self.send_or_stop)
        irow.addWidget(self.btn_attach)
        irow.addWidget(self.input, 1)
        irow.addWidget(self.btn_send)
        rl.addLayout(irow)
        split.addWidget(right)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes(self.settings["splitter"])
        self.splitter = split

        # barra di stato
        self.conn_lbl = QLabel("")
        self.btn_start = QPushButton("Avvia Ollama")
        self.btn_start.setFlat(True)
        self.btn_start.clicked.connect(self.start_server)
        self.btn_start.setVisible(False)
        self.statusBar().addPermanentWidget(self.btn_start)
        self.statusBar().addPermanentWidget(self.conn_lbl)

    def _build_menu(self):
        mb = self.menuBar()
        m = mb.addMenu("&File")
        a = QAction("Nuova chat", self, shortcut="Ctrl+N", triggered=self.new_chat_action)
        m.addAction(a)
        m.addAction(QAction("Esporta chat in Markdown…", self, triggered=lambda: self.export_chat(self.chat)))
        m.addSeparator()
        m.addAction(QAction("Esci", self, shortcut="Ctrl+Q", triggered=self.quit_app))
        s = mb.addMenu("&Server")
        s.addAction(QAction("Avvia Ollama", self, triggered=self.start_server))
        s.addAction(QAction("Modelli…", self, triggered=self.open_models))
        s.addAction(QAction("Impostazioni…", self, triggered=self.open_settings))
        h = mb.addMenu("&Aiuto")
        h.addAction(QAction("Informazioni", self, triggered=self.about))

    def _build_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        menu = QMenu()
        menu.addAction("Apri", self.show_normal)
        menu.addAction("Nuova chat", lambda: (self.show_normal(), self.new_chat_action()))
        menu.addSeparator()
        menu.addAction("Esci", self.quit_app)
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
        self.settings["splitter"] = self.splitter.sizes()
        self.settings.save()
        if self.gen_chat:
            self.store.save(self.gen_chat)
        e.accept()
        QApplication.quit()

    def about(self):
        QMessageBox.about(self, APP_NAME, f"<b>{APP_NAME}</b><br>Interfaccia desktop per Ollama su Linux.<br>Server: {self.api.base}")

    # ---- server
    def check_server(self):
        run_async(self.api.version, self._srv_ok, self._srv_fail)

    def _srv_ok(self, v):
        first = not self.connected
        self.connected = True
        self.conn_lbl.setText(f'<span style="color:#22c55e">●</span> Ollama {v} · {self.api.base}')
        self.btn_start.setVisible(False)
        if first:
            self.refresh_models()

    def _srv_fail(self, _m):
        self.connected = False
        self.conn_lbl.setText(f'<span style="color:#ef4444">●</span> Ollama non raggiungibile · {self.api.base}')
        host = urlparse(self.api.base).hostname or ""
        self.btn_start.setVisible(host in ("127.0.0.1", "localhost", "::1"))

    def start_server(self):
        exe = shutil.which("ollama")
        if not exe:
            QMessageBox.information(self, "Ollama non trovato",
                                    "Il comando 'ollama' non e' installato.\n\nInstallalo con:\n  curl -fsSL https://ollama.com/install.sh | sh")
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
            QMessageBox.warning(self, "Errore", str(e))
            return
        self.statusBar().showMessage("Avvio di Ollama…", 4000)
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
            self.statusBar().showMessage("Nessun modello installato: apri «Modelli» per scaricarne uno.", 8000)

    def on_model_changed(self, name: str):
        if not name:
            return
        self.chat["model"] = name
        self.settings["last_model"] = name
        self.caps = None
        self.think_combo.setEnabled(True)
        self.btn_attach.setToolTip("Allega file o immagini (si possono anche trascinare nella finestra)")

        def ok(info):
            if self.model_combo.currentText() != name:
                return
            caps = info.get("capabilities")
            if caps is None:
                return
            self.caps = set(caps)
            self.think_combo.setEnabled("thinking" in self.caps)
            tip = "Allega file" + (" o immagini" if "vision" in self.caps else " (questo modello non supporta le immagini)")
            self.btn_attach.setToolTip(tip)

        run_async(lambda: self.api.show(name), ok, lambda _m: None)

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
            self._html_cache.clear()
            self._render()

    # ---- sidebar / cronologia
    def refresh_sidebar(self):
        q = self.search.text().strip().lower()
        self.chat_list.blockSignals(True)
        self.chat_list.clear()
        for c in sorted(self.chats, key=lambda c: -c.get("updated", 0)):
            if q and q not in c["title"].lower() and not any(q in (m.get("display") or m.get("content", "")).lower() for m in c["messages"]):
                continue
            it = QListWidgetItem(c["title"])
            it.setData(Qt.ItemDataRole.UserRole, c["id"])
            it.setToolTip(f"{c.get('model', '')} · {time.strftime('%d/%m/%Y %H:%M', time.localtime(c.get('updated', 0)))}")
            self.chat_list.addItem(it)
            if c["id"] == self.chat["id"]:
                self.chat_list.setCurrentItem(it)
        self.chat_list.blockSignals(False)

    def on_chat_item(self, cur, _prev):
        if not cur:
            return
        cid = cur.data(Qt.ItemDataRole.UserRole)
        for c in self.chats:
            if c["id"] == cid:
                self.select_chat(c)
                return

    def chat_by_item(self, item) -> dict | None:
        cid = item.data(Qt.ItemDataRole.UserRole)
        return next((c for c in self.chats if c["id"] == cid), None)

    def chat_menu(self, pos):
        item = self.chat_list.itemAt(pos)
        if not item:
            return
        c = self.chat_by_item(item)
        if not c:
            return
        m = QMenu(self)
        a1 = m.addAction("Rinomina…")
        a2 = m.addAction("Esporta in Markdown…")
        a3 = m.addAction("Elimina")
        act = m.exec(self.chat_list.mapToGlobal(pos))
        if act == a1:
            t, ok = QInputDialog.getText(self, "Rinomina", "Titolo:", text=c["title"])
            if ok and t.strip():
                c["title"] = t.strip()
                self.store.save(c)
                self.refresh_sidebar()
        elif act == a2:
            self.export_chat(c)
        elif act == a3:
            self.delete_chat(c)

    def delete_chat(self, c: dict):
        if QMessageBox.question(self, "Elimina chat", f"Eliminare «{c['title']}»?") != QMessageBox.StandardButton.Yes:
            return
        if c is self.gen_chat and self.worker:
            self.worker.cancel()
            self.worker.wait(3000)
        self.store.delete(c)
        self.chats = [x for x in self.chats if x is not c]
        if c is self.chat:
            self.chat = new_chat(self.model_combo.currentText())
            self._html_cache.clear()
        self.refresh_sidebar()
        self._render()

    def export_chat(self, c: dict):
        if not c["messages"]:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Esporta chat", f"{c['title'][:40]}.md", "Markdown (*.md)")
        if not path:
            return
        out = [f"# {c['title']}", f"*Modello: {c.get('model', '')}*", ""]
        for m in c["messages"]:
            if m["role"] == "user":
                out += ["**Tu:**", "", m.get("display") or m["content"], ""]
            else:
                out += ["**Assistente:**", "", split_think(m["content"])[1], ""]
        Path(path).write_text("\n".join(out), "utf-8")

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
        self._html_cache.clear()
        m = c.get("model")
        if m and self.model_combo.findText(m) >= 0 and self.model_combo.currentText() != m:
            self.model_combo.setCurrentText(m)
        elif not m:
            c["model"] = self.model_combo.currentText()
        self._render()
        self._update_buttons()

    # ---- allegati
    def pick_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Allega file")
        for f in files:
            self.add_file(f)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
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
        self.attachments.append({"name": name, "kind": "image", "b64": base64.b64encode(bytes(ba)).decode()})
        self._rebuild_chips()

    def add_file(self, path: str):
        p = Path(path)
        try:
            ext = p.suffix.lower()
            if ext in IMAGE_EXT:
                img = QImage(str(p))
                if img.isNull():
                    raise RuntimeError("Immagine non leggibile.")
                self.add_qimage(img, p.name)
                return
            if ext == ".pdf":
                text = pdf_text(str(p))
            else:
                raw = p.read_bytes()[: MAX_TEXT_CHARS * 4]
                if b"\0" in raw[:4096]:
                    raise RuntimeError("Tipo di file non supportato (non e' testo).")
                text = raw.decode("utf-8", errors="replace")
            if len(text) > MAX_TEXT_CHARS:
                text = text[:MAX_TEXT_CHARS] + "\n[… file troncato …]"
            if not text.strip():
                raise RuntimeError("Il file non contiene testo.")
            self.attachments.append({"name": p.name, "kind": "text", "text": text})
            self._rebuild_chips()
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Allegato non valido", f"{p.name}: {e}")

    def _rebuild_chips(self):
        while self.chips_lay.count():
            it = self.chips_lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        for i, a in enumerate(self.attachments):
            b = QPushButton(f"{'🖼' if a['kind'] == 'image' else '📄'} {a['name']}  ✕")
            b.setFlat(False)
            b.clicked.connect(lambda _=False, i=i: self._remove_attachment(i))
            self.chips_lay.addWidget(b)
        self.chips_lay.addStretch(1)
        self.chips.setVisible(bool(self.attachments))

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
                parts.append(f"File allegato «{a['name']}»:\n```\n{a['text']}\n```")
        if text:
            parts.append(text)
        msg = {"role": "user", "content": "\n\n".join(parts), "display": text, "images": images, "attachments": names}
        if images and self.caps is not None and "vision" not in self.caps:
            QMessageBox.information(self, "Immagini non supportate",
                                    f"Il modello «{self.model_combo.currentText()}» non sembra supportare le immagini. Scegline uno con visione (es. llava, gemma3, qwen2.5vl).")
            return
        self.chat["messages"].append(msg)
        if self.chat["title"] == "Nuova chat":
            t = (text or (names[0] if names else "Nuova chat")).replace("\n", " ")
            self.chat["title"] = t[:42] + ("…" if len(t) > 42 else "")
        if self.chat not in self.chats:
            self.chats.append(self.chat)
        self.input.clear()
        self.attachments.clear()
        self._rebuild_chips()
        self.error_text = ""
        if not self._start_generation():
            self.chat["messages"].pop()
            if not self.chat["messages"] and self.chat in self.chats:
                self.chats.remove(self.chat)
            self.input.setPlainText(text)
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
        self._html_cache.clear()
        self._start_generation()

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
            QMessageBox.information(self, "Nessun modello", "Non c'e' nessun modello installato. Scaricane uno dalla finestra «Modelli».")
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
        self._set_busy(True)
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
        chat = self.gen_chat
        if chat is not None:
            chat["updated"] = time.time()
            if self.gen_msg is not None and not self.gen_msg["content"].strip() and not self.gen_msg.get("thinking"):
                chat["messages"].remove(self.gen_msg)
            self.store.save(chat)
        ec, ed = stats.get("eval_count"), stats.get("eval_duration")
        if ec and ed:
            note = " (interrotta)" if stats.get("cancelled") else ""
            self.statusBar().showMessage(f"{ec} token · {ec / (ed / 1e9):.1f} token/s{note}", 15000)

    @Slot(str)
    def on_failed(self, msg: str):
        chat = self.gen_chat
        if chat is not None and self.gen_msg is not None and not self.gen_msg["content"].strip() and not self.gen_msg.get("thinking"):
            chat["messages"].remove(self.gen_msg)
        if chat is not None:
            self.store.save(chat)
        if "not found" in msg.lower():
            msg += "\nSuggerimento: scarica il modello dalla finestra «Modelli»."
        self.error_text = msg
        if chat is not self.chat:
            self.statusBar().showMessage("Errore: " + msg, 10000)

    @Slot()
    def on_worker_finished(self):
        self.worker = None
        self.gen_msg = None
        self.gen_chat = None
        self._set_busy(False)
        self._html_cache.clear()
        self._render()
        self.refresh_sidebar()

    def _set_busy(self, busy: bool):
        self.btn_send.setText("■ Stop" if busy else "Invia ➤")
        self._update_buttons()

    def _update_buttons(self):
        busy = self.worker is not None
        has_user = any(m["role"] == "user" for m in self.chat["messages"])
        has_ans = any(m["role"] == "assistant" and m["content"].strip() for m in self.chat["messages"])
        self.btn_regen.setEnabled(not busy and has_user)
        self.btn_copy.setEnabled(has_ans)

    def copy_last(self):
        for m in reversed(self.chat["messages"]):
            if m["role"] == "assistant" and m["content"].strip():
                QGuiApplication.clipboard().setText(split_think(m["content"])[1])
                self.statusBar().showMessage("Risposta copiata negli appunti.", 3000)
                return

    # ---- rendering
    def _tick(self):
        if self._dirty:
            self._dirty = False
            self._render()

    def _img_url(self, b64: str) -> str:
        key = "img:" + hashlib.md5(b64[:4000].encode()).hexdigest() + str(len(b64))
        img = self._img_cache.get(key)
        if img is None:
            img = QImage.fromData(base64.b64decode(b64))
            if img.width() > 260:
                img = img.scaledToWidth(260, Qt.TransformationMode.SmoothTransformation)
            self._img_cache[key] = img
        self.view.document().addResource(QTextDocument.ResourceType.ImageResource, QUrl(key), img)
        return key

    def _render_msg(self, m: dict, streaming: bool) -> str:
        c = self.c
        gap = '<p style="margin:0">&nbsp;</p>'
        if m["role"] == "user":
            imgs = "".join(f'<img src="{self._img_url(b)}"> ' for b in m.get("images", []))
            if imgs:
                imgs += "<br>"
            # le immagini sono gia' mostrate come anteprima: si elencano solo gli altri file
            atts = "".join(
                f'<span style="color:{c["muted"]}">📄 {html.escape(n)}</span><br>'
                for n in m.get("attachments", [])
                if Path(n).suffix.lower() not in IMAGE_EXT
            )
            body = esc_br(m.get("display") or "")
            return (
                f'<table width="100%" cellspacing="0" cellpadding="10"><tr><td width="22%"></td>'
                f'<td bgcolor="{c["bubble"]}">{imgs}{atts}{body}</td></tr></table>{gap}'
            )
        # assistente
        think_inline, body = split_think(m.get("content", ""))
        thinking = (m.get("thinking", "") + ("\n" + think_inline if think_inline else "")).strip()
        out = ""
        if thinking and self.settings["show_thinking"]:
            out += (
                f'<table width="100%" cellspacing="0" cellpadding="8"><tr><td bgcolor="{c["think"]}">'
                f'<span style="color:{c["muted"]}"><i>💭 Ragionamento</i><br>{esc_br(thinking)}</span></td></tr></table>{gap}'
            )
        if body:
            out += md_to_html(body)
        elif streaming and not thinking:
            out += f'<span style="color:{c["muted"]}">…</span>'
        return out + gap

    def _build_html(self) -> str:
        msgs = self.chat["messages"]
        c = self.c
        if not msgs and not self.error_text:
            model = self.model_combo.currentText() or "nessun modello selezionato"
            return (
                f'<div style="margin-top:140px" align="center"><h2>Come posso aiutarti?</h2>'
                f'<span style="color:{c["muted"]}">Modello: {html.escape(model)}<br>'
                f"Puoi trascinare qui file, immagini e PDF.</span></div>"
            )
        parts = []
        for m in msgs:
            streaming = m is self.gen_msg and self.worker is not None
            key = (len(m.get("content", "")), len(m.get("thinking", "")), bool(self.settings["show_thinking"]), streaming)
            ent = self._html_cache.get(id(m))
            if ent and ent[0] == key:
                parts.append(ent[1])
                continue
            h = self._render_msg(m, streaming)
            self._html_cache[id(m)] = (key, h)
            parts.append(h)
        if self.error_text:
            parts.append(f'<p style="color:{c["err"]}">⚠ {esc_br(self.error_text)}</p>')
        return "".join(parts)

    def _render(self):
        sb = self.view.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 6
        old = sb.value()
        self.view.setHtml(self._build_html())
        sb.setValue(sb.maximum() if at_bottom else old)
        self._update_buttons()


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
