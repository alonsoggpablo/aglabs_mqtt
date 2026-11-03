#!/usr/bin/env python3
"""Cola de altas y bajas de Shellys en el bloque `rg_shelly` del aclfile.

Boreas deja peticiones en <cola>/requests/*.json al dar de alta o de baja un
Shelly (ver boreas_mediacion/broker_acl.py). Este script, en el HOST y por cron
cada minuto, las aplica:

  1. Valida el Client ID con la misma expresión que Boreas. Nada más entra.
  2. Añade o quita SOLO la línea `topic readwrite shellies2/<cid>/#` (y su
     comentario) dentro del bloque `user rg_shelly`. Nunca toca otros usuarios.
     Un Client ID con usuario propio (Shellys migrados) se deja como está.
  3. Aplica con /root/.aglabs/mosquitto_apply.sh reload, que valida antes y deshace
     solo si el bróker lo rechaza. Si falla, se restaura el aclfile del repo.
  4. Commit + push del aclfile en este repo (Regla #10: nada vivo fuera de git).
  5. Escribe el resultado por Client ID en <cola>/state.json (lo lee Boreas).

Si el aclfile tiene cambios sin commitear de otra persona, no toca nada y deja
las peticiones para la siguiente pasada.

Uso: acl_queue.py [--dry-run]
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile

REPO = os.environ.get('ACL_QUEUE_REPO', '/root/aglabs_mqtt')
ACL = os.path.join(REPO, 'mosquitto/config/aclfile')
ACL_REL = 'mosquitto/config/aclfile'
QUEUE = os.environ.get('ACL_QUEUE_DIR', '/root/cortex/anomaly_reports/_broker')
REQ_DIR = os.path.join(QUEUE, 'requests')
STATE = os.path.join(QUEUE, 'state.json')
APPLY = os.environ.get('ACL_QUEUE_APPLY', '/root/.aglabs/mosquitto_apply.sh')
BLOCK_USER = 'rg_shelly'

CID_RE = re.compile(r'^shelly(pro|plus)[a-z0-9]*-[0-9a-f]{12}$')
DEV_RE = re.compile(r'^[A-Za-z0-9_.-]{1,40}$')


def now():
    return dt.datetime.now().astimezone().isoformat(timespec='seconds')


def log(msg):
    print(f'[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}', flush=True)


def git(*args, check=True):
    return subprocess.run(['git', '-C', REPO, *args], check=check,
                          capture_output=True, text=True)


def topic_line(cid):
    return f'topic readwrite shellies2/{cid}/#'


def block_bounds(lines):
    """(inicio, fin) del bloque `user rg_shelly`: inicio = índice de la línea user,
    fin = índice de la siguiente línea `user ` (o len)."""
    start = next((i for i, l in enumerate(lines) if l.strip() == f'user {BLOCK_USER}'), None)
    if start is None:
        raise RuntimeError(f'no encuentro `user {BLOCK_USER}` en el aclfile')
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith('user ')),
               len(lines))
    return start, end


def has_own_user(lines, cid):
    return any(l.strip() == f'user {cid}' for l in lines)


def apply_change(lines, req):
    """Devuelve (lineas_nuevas, estado, cambiado) para una petición."""
    cid, action = req['cid'], req['action']
    if has_own_user(lines, cid):
        return lines, 'propio', False
    start, end = block_bounds(lines)
    tl = topic_line(cid)
    idx = next((i for i in range(start + 1, end) if lines[i].strip() == tl), None)
    if action == 'add':
        if idx is not None:
            return lines, 'activo', False
        # Inserta tras la última línea no vacía del bloque.
        ins = end
        while ins > start + 1 and not lines[ins - 1].strip():
            ins -= 1
        dev = req.get('device_id') or ''
        actor = req.get('actor') or 'Boreas'
        new = [f'# {dev or cid} — alta {dt.date.today().isoformat()} ({actor}, desde Boreas)',
               tl]
        return lines[:ins] + new + lines[ins:], 'activo', True
    # remove
    if idx is None:
        return lines, 'retirado', False
    lo = idx
    if idx - 1 > start and lines[idx - 1].startswith('# ') and \
            (req.get('device_id') and req['device_id'] in lines[idx - 1] or cid in lines[idx - 1]):
        lo = idx - 1
    return lines[:lo] + lines[idx + 1:], 'retirado', True


def load_requests():
    try:
        names = sorted(n for n in os.listdir(REQ_DIR) if n.endswith('.json') and not n.startswith('.'))
    except FileNotFoundError:
        return []
    out = []
    for n in names:
        path = os.path.join(REQ_DIR, n)
        try:
            with open(path) as fh:
                req = json.load(fh)
        except (OSError, ValueError) as e:
            out.append((path, None, f'petición ilegible: {e}'))
            continue
        cid = str(req.get('cid', '')).strip().lower()
        dev = str(req.get('device_id', '')).strip()
        if req.get('action') not in ('add', 'remove') or not CID_RE.match(cid) \
                or (dev and not DEV_RE.match(dev)):
            out.append((path, None, 'petición no válida'))
            continue
        req['cid'], req['device_id'] = cid, dev
        req['actor'] = re.sub(r'[^\w@.\- ]', '', str(req.get('actor', '')))[:40]
        out.append((path, req, None))
    return out


def load_state():
    try:
        with open(STATE) as fh:
            return json.load(fh) or {}
    except (OSError, ValueError):
        return {}


def save_state(state):
    fd, tmp = tempfile.mkstemp(dir=QUEUE, prefix='.state_', suffix='.json')
    with os.fdopen(fd, 'w') as fh:
        json.dump(state, fh, indent=1, sort_keys=True)
    os.chmod(tmp, 0o644)
    os.replace(tmp, STATE)


def write_in_place(path, text):
    # Sobre el fichero existente: conserva inodo, dueño (1883) y permisos.
    with open(path, 'r+') as fh:
        fh.seek(0)
        fh.write(text)
        fh.truncate()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    reqs = load_requests()
    if not reqs:
        return 0
    state = load_state()

    dirty = git('status', '--porcelain', '--', ACL_REL).stdout.strip()
    if dirty:
        log('aclfile con cambios sin commitear: no toco nada (se reintenta en la siguiente pasada)')
        for _, req, _err in reqs:
            if req:
                state[req['cid']] = {**state.get(req['cid'], {}), 'state': 'error', 'ts': now(),
                                     'msg': 'Bróker ocupado con otro cambio; se reintenta solo.'}
        if not args.dry_run:
            save_state(state)
        return 1

    with open(ACL) as fh:
        original = fh.read()
    lines = original.split('\n')
    results, done_paths, summary = {}, [], []
    for path, req, err in reqs:
        done_paths.append(path)
        if err:
            log(f'{os.path.basename(path)}: {err} — descartada')
            continue
        try:
            lines, st, changed = apply_change(lines, req)
        except RuntimeError as e:
            log(f'ERROR: {e}')
            return 2
        results[req['cid']] = (st, req)
        if changed:
            summary.append(f"{req['action']} {req.get('device_id') or req['cid']}")
        log(f"{req['action']} {req['cid']} ({req.get('device_id')}) → {st}"
            f"{'' if changed else ' (sin cambios)'}")

    new_text = '\n'.join(lines)
    if args.dry_run:
        if new_text != original:
            import difflib
            sys.stdout.writelines(difflib.unified_diff(
                original.splitlines(True), new_text.splitlines(True), 'aclfile', 'aclfile'))
        return 0

    ok, msg = True, ''
    if new_text != original:
        write_in_place(ACL, new_text)
        r = subprocess.run([APPLY, 'reload'], capture_output=True, text=True)
        if r.returncode != 0:
            ok = False
            msg = 'El bróker rechazó el cambio; avisa al administrador.'
            log(f'mosquitto_apply.sh falló ({r.returncode}):\n{r.stdout[-800:]}{r.stderr[-400:]}')
            git('checkout', '--', ACL_REL, check=False)
            subprocess.run([APPLY, 'perms'], capture_output=True, text=True)
        else:
            git('add', '--', ACL_REL)
            c = git('commit', '-m', 'acl(auto): ' + ', '.join(summary) +
                    '\n\nAplicado por scripts/acl_queue.py desde Boreas.', check=False)
            if c.returncode != 0:
                log(f'commit falló: {c.stderr.strip()}')
            p = git('push', '-q', 'origin', 'HEAD', check=False)
            if p.returncode != 0:
                log(f'push falló (el commit queda local): {p.stderr.strip()}')

    for cid, (st, req) in results.items():
        state[cid] = {'state': st if ok else 'error', 'msg': '' if ok else msg, 'ts': now(),
                      'device_id': req.get('device_id', ''), 'action': req['action']}
    save_state(state)
    for p in done_paths:
        try:
            os.remove(p)
        except OSError:
            pass
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
