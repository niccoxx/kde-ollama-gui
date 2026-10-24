#!/usr/bin/env bash
# Installa Ollama GUI nella home dell'utente (nessun privilegio di root necessario).
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/ollama-gui-app"
BIN_DIR="$HOME/.local/bin"
DESK_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"

command -v python3 >/dev/null || { echo "python3 non trovato"; exit 1; }

mkdir -p "$APP_DIR" "$BIN_DIR" "$DESK_DIR"
cp "$SRC/ollama_gui.py" "$SRC/requirements.txt" "$APP_DIR/"

echo "→ Creo l'ambiente virtuale e installo le dipendenze…"
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

cat > "$BIN_DIR/ollama-gui" <<EOF
#!/usr/bin/env bash
exec "$APP_DIR/venv/bin/python" "$APP_DIR/ollama_gui.py" "\$@"
EOF
chmod +x "$BIN_DIR/ollama-gui"

cat > "$DESK_DIR/ollama-gui.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Ollama GUI
Comment=Interfaccia grafica per Ollama
Exec=$BIN_DIR/ollama-gui
Icon=utilities-terminal
Terminal=false
Categories=Utility;Development;
StartupWMClass=ollama-gui
EOF

command -v update-desktop-database >/dev/null && update-desktop-database "$DESK_DIR" 2>/dev/null || true

echo
echo "✔ Installato. Avvia con:  ollama-gui   (oppure dal menu delle applicazioni)"
case ":$PATH:" in *":$BIN_DIR:"*) ;; *) echo "  Nota: aggiungi $BIN_DIR al PATH per usare il comando 'ollama-gui'." ;; esac
