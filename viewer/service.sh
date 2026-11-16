#!/bin/sh
# Visor de artifacts como servicio permanente de macOS (LaunchAgent): arranca al iniciar sesión,
# se reinicia si se cae y recarga solo cuando cambian los manifests. Corre dentro del sandbox del kernel
# (viewer/sandbox.sb): solo lee el repo de artifacts y el visor, solo escribe _db, _comments, la caché de
# fuentes y su log, solo atiende 127.0.0.1:PORT y no puede lanzar otros programas. Solo macOS.
#   LIENZO_ROOT=/ruta/a/artifacts [LIENZO_AUTHOR=Nombre] [PORT=8765] viewer/service.sh install
#   viewer/service.sh uninstall | restart | status
set -eu
LABEL=com.cognikas.lienzo
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
HERE=$(cd "$(dirname "$0")" && pwd)
PY=$(command -v python3)
PYPREFIX=$(printf '%s' "$PY" | cut -d/ -f1-3)   # /opt/homebrew: cubre el enlace opt/ y el Cellar/ real
PORT=${PORT:-8765}
AUTHOR=$(printf '%s' "${LIENZO_AUTHOR:-Local}" | sed 's/&/\&amp;/g; s/</\&lt;/g; s/>/\&gt;/g')  # va dentro del plist (XML)
LOG="$HOME/Library/Logs/lienzo.log"
# El perfil se copia fuera de ~/Documents: macOS (TCC) no deja que sandbox-exec, lanzado por launchd, lea ahí.
SUPPORT="$HOME/Library/Application Support/lienzo"

case "${1:-status}" in
  install)
    [ -n "${LIENZO_ROOT:-}" ] || { echo "falta LIENZO_ROOT (carpeta de artifacts)"; exit 2; }
    ART=$(cd "$LIENZO_ROOT" && pwd -P)
    ARTRE=$(printf '%s' "$ART" | sed 's/[.[\*^$()+?{|]/\\&/g')
    mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs" "$SUPPORT"
    cp "$HERE/sandbox.sb" "$SUPPORT/sandbox.sb"
    cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>/usr/bin/sandbox-exec</string><string>-f</string><string>$SUPPORT/sandbox.sb</string>
    <string>-D</string><string>HOME=$HOME</string><string>-D</string><string>ART=$ART</string>
    <string>-D</string><string>ARTRE=$ARTRE</string><string>-D</string><string>VIEWER=$HERE</string>
    <string>-D</string><string>PY=$PYPREFIX</string><string>-D</string><string>LOG=$LOG</string>
    <string>-D</string><string>PORT=$PORT</string>
    <string>$PY</string><string>-B</string><string>$HERE/serve.py</string>
    <string>--root</string><string>$ART</string><string>--port</string><string>$PORT</string>
    <string>--author</string><string>$AUTHOR</string>
  </array>
  <key>WorkingDirectory</key><string>$HERE</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict></plist>
PL
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST"
    echo "instalado: http://127.0.0.1:$PORT/  (log: $LOG)" ;;
  uninstall)
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"; rm -rf "$SUPPORT"; echo "desinstalado" ;;
  restart)
    launchctl kickstart -k "gui/$(id -u)/$LABEL"; echo "reiniciado" ;;
  status)
    launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | grep -E '^\s*(state|pid|last exit code)' || echo "no instalado" ;;
  *) echo "uso: $0 install|uninstall|restart|status"; exit 2 ;;
esac
