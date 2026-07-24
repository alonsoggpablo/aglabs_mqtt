#!/usr/bin/env bash
# Copia el cert Let's Encrypt multi-SAN (boreas.aglabs.es, incluye la SAN
# mqtt.aglabs.es) a mosquitto/certs/ con owner 1883:1883 para que el listener
# 8883 TLS de aglabs_mosquitto pueda leerlo.
#
# Por qué copiar en vez de montar /etc/letsencrypt: mosquitto corre como uid 1883
# dentro de eclipse-mosquitto:2 y la privkey de LE es root:root 0600 → un mount
# directo daría "unable to load server key file". La copia dedicada resuelve
# permisos sin relajar los del store LE.
#
# Uso:
#   sudo scripts/sync_certs.sh            # copia + fija permisos
#   sudo scripts/sync_certs.sh --restart  # además reinicia el broker (aplica el cert)
#
# Renovación automática: enlazado como deploy-hook de certbot (ver README §TLS 8883).
set -euo pipefail

LE_DIR="/etc/letsencrypt/live/boreas.aglabs.es"
DEST="$(cd "$(dirname "$0")/.." && pwd)/mosquitto/certs"

mkdir -p "$DEST"
# -L deref: los ficheros de live/ son symlinks a archive/; copiamos el contenido real.
cp -L "$LE_DIR/fullchain.pem" "$DEST/fullchain.pem"
cp -L "$LE_DIR/privkey.pem"   "$DEST/privkey.pem"

# uid/gid 1883 = usuario mosquitto dentro del contenedor.
chown 1883:1883 "$DEST/fullchain.pem" "$DEST/privkey.pem"
chmod 0644 "$DEST/fullchain.pem"
chmod 0600 "$DEST/privkey.pem"

echo "certs copiados a $DEST (owner 1883:1883)"

if [[ "${1:-}" == "--restart" ]]; then
  docker restart aglabs_mosquitto
  # Gotcha #6: tras restart del broker, nginx cachea el upstream WSS muerto.
  docker exec boreas_nginx nginx -s reload || true
  echo "aglabs_mosquitto reiniciado + nginx recargado"
fi
