#!/usr/bin/env python3
"""
Genera el script de aprovisionamiento MQTT para Shellys (`mqtt-provision`) con
la contraseña de un usuario compartido, a partir de la plantilla de Diana
(`/root/diana/routers/shelly_scripts/provision/mqtt-provision.js.tmpl`).
Normalmente basta con descargarlo de Diana (Scripts Shelly → Aprovisionamiento,
solo staff); este generador queda para la línea de comandos.

    python3 scripts/make_provision_script.py rg_shelly
    → .secrets/mqtt-provision-rg_shelly.js  (0600, fuera de git)

El fichero generado LLEVA LA CONTRASEÑA: se entrega por canal privado, nunca por
el enlace público de Diana ni por correo en claro. La contraseña sale de
`.secrets/<usuario>`; el usuario debe existir en el passwordfile del bróker.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Fuente única de la plantilla: Diana (que también la sirve al staff).
TEMPLATE = Path('/root/diana/routers/shelly_scripts/provision/mqtt-provision.js.tmpl')


def main():
    if len(sys.argv) != 2:
        sys.exit('uso: make_provision_script.py <usuario_mqtt>')
    user = sys.argv[1]
    secret = ROOT / '.secrets' / user
    if not secret.exists():
        sys.exit(f'falta {secret}')
    password = secret.read_text().strip()
    if not password.isalnum():
        sys.exit('la contraseña debe ser alfanumérica (va dentro de una cadena JS)')
    pwfile = ROOT / 'mosquitto' / 'config' / 'passwordfile'
    if pwfile.exists() and not any(l.startswith(f'{user}:') for l in pwfile.read_text().splitlines()):
        sys.exit(f'{user} no está en el passwordfile del bróker')

    code = TEMPLATE.read_text().replace('@@USER@@', user).replace('@@PASS@@', password)
    if '@@' in code or not code.isascii():
        sys.exit('plantilla con marcadores sin sustituir o caracteres no ASCII')

    out = ROOT / '.secrets' / f'mqtt-provision-{user}.js'
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(code)
    print(out)


if __name__ == '__main__':
    main()
