# CLAUDE.md

Guida per Claude Code (e per chi sviluppa) sul progetto **kde-ollama-gui**: com'è fatto,
come si lavora e la storia dello sviluppo fino alla versione `v1.0.0`.

## Il progetto

Interfaccia desktop per [Ollama](https://ollama.com) su Linux, pensata per KDE Plasma ma
funzionante su qualsiasi desktop con Qt. Scritta in Python con **PySide6**.

- Tutto il programma sta in **un solo file**: `ollama_gui.py`. `install.sh` copia solo quel file
  (più `requirements.txt`) in `~/.local/share/ollama-gui-app/`, quindi niente moduli separati:
  traduzioni, temi e risorse restano dentro `ollama_gui.py`.
- Dipendenze: `PySide6`, `requests`, `markdown`, `pypdf` (`requirements.txt`); per i test anche
  `pytest` (`requirements-dev.txt`).
- Dati utente (specifiche XDG): impostazioni in `~/.config/ollama-gui/settings.json`,
  chat in `~/.local/share/ollama-gui/chats/*.json`.
- Licenza MIT (`LICENSE`).

## Comandi

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt   # ambiente di sviluppo
.venv/bin/python ollama_gui.py                                          # avvia l'app
LANGUAGE=en .venv/bin/python ollama_gui.py                              # avvia in inglese (solo questa volta)
.venv/bin/python -m pytest                                              # test
```

I test girano con `QT_QPA_PLATFORM=offscreen` (impostato in `tests/conftest.py`): niente finestre,
cartelle temporanee al posto di quelle dell'utente, nessuna chiamata a Ollama.

## Struttura di `ollama_gui.py`

Le sezioni sono separate da commenti `# ----...---- nome`:

| Sezione | Contenuto |
|---|---|
| lingua | `tr()`, `set_language()`, `system_language()`, dizionario `EN` |
| utilita' | `normalize_host`, `human_size`, `split_think` (blocchi `<think>`), `md_to_html`, `pdf_text` |
| impostazioni e storico | `Settings` (dict con `DEFAULTS`, salvato in JSON), `ChatStore`, `new_chat` |
| API Ollama | `Ollama` (REST locale), `Library` (ricerca su ollama.com), worker `QThread` per chat e pull, `run_async` |
| tema | `apply_appearance`, `theme_colors`, `build_qss` (foglio di stile), `doc_css` (stile dei messaggi) |
| widget | `InputEdit`, `MenuPicker`, `ChatItemDelegate`, `SuggestionCard`, `RichView`, `UserMessage`, `AssistantMessage`, `ModelsDialog`, `SettingsDialog` |
| finestra principale | `MainWindow`, `main()` |

## Convenzioni importanti

### Testi e traduzioni
- I testi dell'interfaccia si scrivono **in italiano** dentro `tr("…")`; la traduzione inglese va
  aggiunta al dizionario `EN` in cima al file. Se manca, `tests/test_i18n.py` fallisce.
- La funzione si chiama `tr` e **non** `_`, perché nel codice `_` è usato come variabile
  (es. `path, _ = QFileDialog.getSaveFileName(...)`).
- Testi con valori variabili: `tr("Download di {}…").format(name)`. **Mai f-string con frasi
  visibili**: non passerebbero dalla traduzione (c'è un test che le cerca). I segnaposto `{}`
  della chiave e della traduzione devono coincidere (anche questo è testato).
- Le liste a livello di modulo (`SUGGESTIONS`, `ACCENTS`, `LANGUAGES`) vengono valutate all'import,
  prima che la lingua sia impostata: restano in italiano e si traducono **dove si usano**
  (`tr(title)`, `tr(name)`).
- La lingua (`settings["language"]`: `""` automatica, `"it"`, `"en"`) si applica all'avvio
  (`set_language` in `MainWindow.__init__`); cambiarla richiede un riavvio.
- Rilevamento automatico: prima `LANGUAGE`/`LC_ALL`/`LC_MESSAGES`/`LANG`, poi su macOS
  `defaults read -g AppleLanguages` (Qt avviato da Python riporta sempre `en_US`), infine `QLocale`.
  Lingue diverse dall'italiano → inglese.

### Tema e colori
- I colori vengono dalla palette Qt: `theme_colors()` deriva tutti i colori dell'interfaccia
  (sfondo, bordi, accento, bolle, codice…) e `build_qss()` li usa nel foglio di stile.
  Le regole QSS sono limitate per `objectName` ai widget della finestra principale: i dialoghi
  mantengono l'aspetto nativo.
- `apply_appearance(theme, accent)`: tema 0 sistema / 1 chiaro / 2 scuro, accento `""` = di sistema.
  Con tema forzato usa palette in stile Breeze (`_SCHEMES`) e, se disponibile (Qt ≥ 6.8),
  `styleHints().setColorScheme`. L'accento di sistema è memorizzato al primo uso (`_system_accent`).
  Il colore del testo sull'accento (nero/bianco) si calcola sempre dalla luminosità.
- La finestra si aggiorna nel `changeEvent` su **`PaletteChange`** (oltre ad
  `ApplicationPaletteChange`): le finestre ricevono il primo, non il secondo.
- Il codice usa il font monospaziato di sistema (`QFontDatabase.SystemFont.FixedFont`):
  la famiglia generica `monospace` non esiste su tutti i sistemi.

### Test
- `tests/conftest.py`: fixture `app` (QApplication di sessione), `isolated_dirs` (autouse:
  cartelle temporanee, lingua fissata a italiano), `win` (MainWindow con `check_server` e
  `refresh_models` disattivati), `reset_appearance`. Helper `wait(ms)` per lasciar propagare
  gli eventi (i cambi di palette sono asincroni).
- Non chiamare `close()` sulla `MainWindow` nei test: `closeEvent` chiama `QApplication.quit()`.
- File: `test_utils.py`, `test_storage.py`, `test_theme.py`, `test_widgets.py`, `test_i18n.py`.

### Screenshot del README
In `docs/`: `screenshot-it-{light,dark}.png` (sezione italiana) e `screenshot-en-{light,dark}.png`
(sezione inglese), 1180×780. Si generano con uno script temporaneo che: imposta
`XDG_CONFIG_HOME`/`XDG_DATA_HOME` su cartelle temporanee (mai i dati veri dell'utente), scrive
`settings.json` con `theme`, `accent: "#3b82f6"` e `language`, disattiva `check_server`/`refresh_models`,
crea chat dimostrative, chiama `_srv_ok("0.12.6")` per lo stato "connesso" e salva `w.grab()`.

## Flusso di lavoro con git

- `main` riceve le modifiche **solo tramite pull request** da branch di lavoro dedicati;
  dopo il merge i branch di lavoro si possono cancellare.
- **`develop` è il branch di sviluppo personale: non va cancellato.** Al 28/10/2026 contiene
  `15337d6 fix install` (`install -m 644` al posto di `cp` in `install.sh`), non ancora su `main`.
- **Chiedere sempre conferma prima di ogni commit e push.**
- Identità dei commit: `niccoxx <45878511+niccoxx@users.noreply.github.com>`, impostata nella config
  del repository (`git config user.name/user.email`). Sul Mac non c'è una config globale: senza quella
  locale git userebbe il nome dell'account e della macchina. Verificarla prima di committare.
- Il comando `gh` non è installato: le PR si aprono dal link `…/pull/new/<branch>` stampato dal push.
- Tag delle versioni nel formato `v1.0.0` (senza punto dopo la `v`), con Release su GitHub.
- `.gitignore`: `.venv`, `__pycache__/`, `.pytest_cache/`.

## Storia dello sviluppo

### 24/10/2026 — prime versioni
- `7e7b647` primo commit: chat in streaming, cronologia, gestione modelli, allegati, impostazioni,
  icona nella barra di sistema, `install.sh`, README bilingue.
- `8bee4fb` nuova versione su `develop`, unita con la **PR #1**.

### 27/10/2026 — libreria dei modelli
- `5d384d9` elenco e ricerca dei modelli di ollama.com nella finestra Modelli. Con questo commit
  era finito nel repository anche `__pycache__/ollama_gui.cpython-314.pyc` (poi rimosso).

### 28/10/2026 — interfaccia moderna, temi, test, inglese e v1.0.0
1. **UI più moderna** (`49d12bd`, PR #2): blocchi di codice con font monospaziato vero
   (prima usavano un font proporzionale perché "Monospace" non esisteva) e intestazione colorata;
   schede dei suggerimenti (`SuggestionCard`) con titolo in grassetto e descrizione attenuata;
   ombra sul composer; titolo iniziale più grande.
2. **Impostazioni del tema** (stesso commit): scheda *Aspetto* con tema sistema/chiaro/scuro e colore
   d'accento (sistema, 7 preset, personalizzato), con anteprima immediata e ripristino su *Annulla*.
   Corretto un bug già presente: la finestra non si aggiornava al cambio di tema del sistema
   (gestiva solo `ApplicationPaletteChange`, mentre riceve `PaletteChange`).
3. **Test** (`e023c6e`, PR #3): suite pytest offscreen; verificato che il test di regressione sul
   tema fallisca reintroducendo il bug. Aggiunti `requirements-dev.txt` e la sezione Test nel README.
4. **`.gitignore`** (`a512ce4`, PR #3) e **rimozione di `__pycache__`** dal repository (`6cd1064`, PR #5).
5. **Screenshot nel README** (`067b6ca`, PR #6). Generandoli è emerso un bug: con tema forzato e
   accento di sistema il testo sui pulsanti era sempre bianco, illeggibile sull'azzurro chiaro di
   macOS; ora si calcola dalla luminosità (con test).
6. **Interfaccia in inglese** (`33893e5`, PR #7): circa 200 testi passati da `tr()`, dizionario `EN`,
   impostazione *Lingua*, rilevamento della lingua di sistema (con la correzione per macOS),
   `tests/test_i18n.py`.
7. **Preparazione al rilascio** (`ef73628`, PR #8): licenza MIT; tolta dal README inglese la nota
   "the user interface is currently in Italian"; nomi inglesi dei menu nella parte inglese;
   screenshot inglesi nella sezione English e italiani (rinominati `screenshot-it-*`) in quella
   italiana; impostazioni di aspetto e lingua nella documentazione.
8. **Rilascio `v1.0.0`**: la Release "Python Ollama GUI" è stata spostata dal tag `v.1.0.0` al tag
   `v1.0.0` (sul merge della PR #8, quindi con la licenza); il vecchio tag è stato cancellato.
   Cancellati anche i branch di lavoro già uniti; restano `main` e `develop`.
9. **`CLAUDE.md`** su `develop` e merge di `main` in `develop`.
10. **Riscrittura dell'identità dei commit**: alcuni commit erano usciti con il nome dell'account e
    della macchina locale invece di `niccoxx`. Tutta la storia di `main` e `develop` è stata riscritta
    (autore e committer `niccoxx` con email noreply di GitHub) con force-push e spostamento del tag
    `v1.0.0`; gli hash citati qui sono quelli nuovi. Le PR #2–#8 su GitHub mostrano ancora i commit
    vecchi (servono il supporto di GitHub per rimuoverli).

## Idee non ancora fatte
- Evidenziazione della sintassi nei blocchi di codice (es. con Pygments).
- Animazioni (comparsa dei messaggi, indicatore "sta scrivendo").
- Icone coerenti anche fuori da KDE (su macOS alcune icone del tema mancano, es. la ricerca).
- Cambio di lingua senza riavvio.
- Portare su `main` il `fix install` di `develop`.
