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
# Certificado RSA dedicado al listener 8883 (dispositivos IoT con almacén de CA
# antiguo). Ver §"Cert RSA para dispositivos con almacén de CA antiguo" en
# CLAUDE.md: los Nanoenvi rechazan la cadena ECDSA del multi-SAN porque pasa por
# ISRG Root X2. Este va por RSA y termina en ISRG Root X1.
LE_RSA_DIR="/etc/letsencrypt/live/mqtt-iaq-rsa"
DEST="$(cd "$(dirname "$0")/.." && pwd)/mosquitto/certs"

mkdir -p "$DEST"
# -L deref: los ficheros de live/ son symlinks a archive/; copiamos el contenido real.
cp -L "$LE_DIR/fullchain.pem" "$DEST/fullchain.pem"
cp -L "$LE_DIR/privkey.pem"   "$DEST/privkey.pem"

# uid/gid 1883 = usuario mosquitto dentro del contenedor.
chown 1883:1883 "$DEST/fullchain.pem" "$DEST/privkey.pem"
chmod 0644 "$DEST/fullchain.pem"
chmod 0600 "$DEST/privkey.pem"

# Cert RSA del listener 8883 (opcional: si no existe, no se toca nada y el
# listener seguirá usando el multi-SAN — el script no debe fallar por esto).
if [[ -d "$LE_RSA_DIR" ]]; then
  cp -L "$LE_RSA_DIR/fullchain.pem" "$DEST/rsa_fullchain.pem"
  cp -L "$LE_RSA_DIR/privkey.pem"   "$DEST/rsa_privkey.pem"
  chown 1883:1883 "$DEST/rsa_fullchain.pem" "$DEST/rsa_privkey.pem"
  chmod 0644 "$DEST/rsa_fullchain.pem"
  chmod 0600 "$DEST/rsa_privkey.pem"
  echo "cert RSA (mqtt-iaq-rsa) copiado para el listener 8883"
else
  echo "AVISO: $LE_RSA_DIR no existe — el listener 8883 usará el cert multi-SAN"
fi

echo "certs copiados a $DEST (owner 1883:1883)"

if [[ "${1:-}" == "--restart" ]]; then
  # Valida en un bróker efímero, reinicia, recarga nginx (gotcha #6) y revierte
  # si no arranca (Regla #9 de /root/CLAUDE.md, incidente 2026-10-08).
  /root/.aglabs/mosquitto_apply.sh restart
fi
