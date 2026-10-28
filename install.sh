#!/usr/bin/env bash
# Installa Pastura nella home dell'utente (nessun privilegio di root necessario).
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
APP_DIR="$DATA_HOME/pastura-app"
BIN_DIR="$HOME/.local/bin"
DESK_DIR="$DATA_HOME/applications"
ICON_DIR="$DATA_HOME/icons/hicolor/scalable/apps"

command -v python3 >/dev/null || { echo "python3 non trovato"; exit 1; }

# vecchia installazione col nome "Ollama GUI" (kde-ollama-gui): programma, comando e voce di menu.
# Impostazioni e chat (~/.config/ollama-gui, ~/.local/share/ollama-gui) non si toccano:
# Pastura le sposta nelle nuove cartelle al primo avvio.
if [ -e "$DATA_HOME/ollama-gui-app" ] || [ -e "$BIN_DIR/ollama-gui" ] || [ -e "$DESK_DIR/ollama-gui.desktop" ]; then
    echo "→ Rimuovo la vecchia installazione di Ollama GUI…"
    rm -rf "$DATA_HOME/ollama-gui-app"
    rm -f "$BIN_DIR/ollama-gui" "$DESK_DIR/ollama-gui.desktop"
fi

mkdir -p "$APP_DIR" "$BIN_DIR" "$DESK_DIR" "$ICON_DIR"
cp "$SRC/pastura.py" "$SRC/requirements.txt" "$APP_DIR/"
cp "$SRC/pastura.svg" "$ICON_DIR/pastura.svg"

echo "→ Creo l'ambiente virtuale e installo le dipendenze…"
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

cat > "$BIN_DIR/pastura" <<EOF
#!/usr/bin/env bash
exec "$APP_DIR/venv/bin/python" "$APP_DIR/pastura.py" "\$@"
EOF
chmod +x "$BIN_DIR/pastura"

cat > "$DESK_DIR/pastura.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Pastura
Comment=A native Qt desktop client for Ollama
Exec=$BIN_DIR/pastura
Icon=pastura
Terminal=false
Categories=Utility;Development;
StartupWMClass=pastura
EOF

command -v update-desktop-database >/dev/null && update-desktop-database "$DESK_DIR" 2>/dev/null || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t "$DATA_HOME/icons/hicolor" 2>/dev/null || true

echo
echo "✔ Installato. Avvia con:  pastura   (oppure dal menu delle applicazioni)"
case ":$PATH:" in *":$BIN_DIR:"*) ;; *) echo "  Nota: aggiungi $BIN_DIR al PATH per usare il comando 'pastura'." ;; esac
