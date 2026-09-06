# aglabs_mqtt — bróker MQTT propio + viewer

Eres el agente responsable de la infraestructura MQTT compartida de AGlabs:
Mosquitto en `aglabs_mosquitto` (boreas_net), bridge unidireccional al bróker
de Tactica (`mqtt.solutions-iot.es`), MQTTX Web como cliente genérico y un
dashboard estático propio (auto-conexión) en `https://mqtt.aglabs.es/dashboard/`.

Tu dominio es `/root/aglabs_mqtt`. No toques otros repos salvo los que aquí
se citan explícitamente como acoplamientos (`/root/boreas_rgiot/nginx.conf`,
`/root/nexus/.env`, `/root/ag` para el card).

Las reglas globales de `/root/CLAUDE.md` aplican siempre (en particular regla #1
sobre nombres de servicio docker en `boreas_net` y regla #4 sobre resolver
dinámico en vhosts).

## Por qué existe

Toda la telemetría que consume AGlabs (Shellys, Nanoenvi, Engelmann calor,
Teltonika) fluye hoy por el bróker MQTT de Tactica TIC. Tactica cobra
~500 €/mes por mantenerlo y es lenta en evolutivos; además la integración
actual con Boreas es **scraping HTML** del portal, no API — un cambio de UI
en su lado puede romper Boreas sin previo aviso.

Este proyecto es el primer paso del plan de sustitución por fases: levantar
infraestructura MQTT propia que conviva con la de Tactica, sin tocar todavía
el parque de dispositivos. Cuando Diana (`/root/diana`) reconfigure los
Shellys uno a uno para publicar a `mqtt.aglabs.es`, esta pieza ya está lista
para recibirlos. Mientras tanto el **bridge** copia en local todo el tráfico
del bróker de Tactica, así que Nexus y compañía pueden migrar su `.env` a
`aglabs_mosquitto:1883` sin perder ni un dato.

Resumen de la estrategia y el contexto en `/root/CLAUDE.md` y en las
conversaciones del agente Claude del 2026-05-16.

## Arquitectura

```
                                                          (browser pablo)
                                                                 │
                                                                 ▼  HTTPS 443
                       (dispositivos → futuro)             ┌─────────────┐
                              │                             │ boreas_nginx │
                              ▼  (8883 sin abrir todavía)   │  mqtt.aglabs.es
  ┌──────────────────┐  bridge in 0   ┌───────────────────┐ │              │
  │ mqtt.solutions-  │ ◄──────────── │ aglabs_mosquitto   │◄┤ /            │ → aglabs_mqttx:80  (MQTTX Web)
  │ iot.es:8883      │  TLS outbound  │ 1883 (TCP, intern) │ │ /dashboard/  │ → bind-mount estático
  │ (Tactica)        │                │ 9001 (WS, intern)  │◄┤ /mqtt        │ → WS proxy a :9001
  └──────────────────┘                └───────────────────┘ └─────────────┘
                                              ▲
                                              │ (Telegraf, Cortex,
                                              │  Amelia, futuros)
                                          boreas_net
```

- **`aglabs_mosquitto`** — eclipse-mosquitto:2. Listeners 1883 (TCP) y 9001
  (WebSockets), ambos solo dentro de `boreas_net`. Ninguno expuesto al host.
  Bridge outbound TLS al bróker de Tactica.
- **`aglabs_mqttx`** — emqx/mqttx-web:latest. SPA Vue para conexiones MQTT
  ad-hoc (publicar/suscribir/depurar). Sirve estático en su puerto 80
  interno. Sin estado, todo en IndexedDB del navegador. Bundle dev ~36 MB
  (sin minificar) — **imprescindible gzip en nginx**, sin él la primera
  carga es una página en blanco durante 30 s+.
- **Dashboard propio** (`web/dashboard/index.html`) — HTML+MQTT.js, auto-
  connect a `wss://mqtt.aglabs.es/mqtt`, árbol de topics, último payload
  por topic, firehose. Cero clicks. ~14 KB.

Nginx (en `boreas_nginx`, stack Boreas) hace el TLS termination en 443 y
proxea por path al SPA, al dashboard o al WebSocket de Mosquitto.

## Bridge — topics copiados desde Tactica

Configurados en `mosquitto/config/conf.d/bridge.conf` (gitignored — incluye
credenciales). Son los que ya consume Nexus + un margen:

- `gadget/#`     — UUID-indexado: IAQ (nanoenvi), calor (engelmann), gas (sigfox)
- `router/#`     — Teltonika señal/uptime

**`shellies/#` y `shellies2/#` se RETIRARON del bridge el 2026-08-31.** No es
limpieza cosmética: `nexus_telegraf` consume esos mismos topics **en los dos
brokers** (consumers `nexus-shelly-*` contra Tactica y contra aglabs). Mientras
el bridge los espejaba, cada mensaje de un Gen1/Gen2 que sigue en Tactica
llegaba a Nexus **dos veces** — una directa y otra por el espejo — inflando
`nexus_electrical` y falseando energía. No estalló antes solo porque el bridge
llevaba días caído; era una mina que se activaba al repararlo. Regla derivada:
**un topic que Nexus ya consume en ambos brokers NO se puede espejar aquí.**

**El bridge lleva caído desde ~11-ago** con
`error:0A000086: certificate verify failed`: el cert de `mqtt.solutions-iot.es`
caducó y el bridge sí valida la cadena (a diferencia de los Shelly, que van con
`ssl_ca:"*"`). No afecta a la ingesta — Telegraf lee Tactica directo con
`insecure_skip_verify` — así que el bridge hoy solo aporta el espejo de
`gadget/#` y `router/#`. Si se quiere revivir: `bridge_insecure true` (o esperar
a que Tactica renueve). **Efecto colateral a tener presente al diagnosticar:**
los `shellies2/<cid>/online` retained que quedan en aglabs son espejo viejo de
Tactica, NO verdad del equipo — un `online false` ahí no significa que el
dispositivo esté caído si nunca se migró.

Dirección: `in 0` (de remoto a local, QoS 0 — telemetría, no idempotencia).
Reconnect automático con `restart_timeout 30`. No publicamos NADA hacia
Tactica (`out` no está configurado): el bridge es estrictamente de lectura.

## Credenciales y secretos

| Fichero | Contiene | Origen |
|---------|----------|--------|
| `mosquitto/config/conf.d/bridge.conf` | usuario/password del bróker Tactica | copiado de `/root/nexus/.env` (`MQTT_USERNAME`, `MQTT_PASSWORD`) |
| `mosquitto/config/passwordfile` | hashes mosquitto `$7$` de publishers/subscribers MQTT-nativos | generado con `mosquitto_passwd -b` dentro del contenedor; ver §"Usuarios MQTT (password_file + ACL)" |
| `/root/boreas_rgiot/ssl/htpasswd_aglabs_mqtt` | hash apr1 del basic auth `pablo` (solo UI: `/`, `/dashboard/`) | generado con `openssl passwd -apr1`; password en memoria de Claude (`user_credentials.md`) |

**Ninguno se commitea**. El bridge.conf y el passwordfile están en
`.gitignore` local; el htpasswd está en `.gitignore` de boreas_rgiot
(patrón `ssl/htpasswd_*`).

Si se redespliega en otra máquina:
1. Copiar el .env de Nexus para tener las credenciales del bróker Tactica.
2. `set -a; . /root/nexus/.env; set +a` y regenerar `bridge.conf` con el
   patrón del template (heredoc en la conversación de despliegue).
3. `openssl passwd -apr1 '<password>'` para el htpasswd.

## Auth y exposición pública

- **Mosquitto**: `per_listener_settings true`, ambos listeners (1883 TCP y
  9001 WS) con `listener_allow_anonymous true` + `password_file` + `acl_file`.
  Coexisten anónimos (broad readwrite, preserva dashboard auto-connect y
  bridge a Tactica) con usuarios autenticados de ACL estricta (publishers
  AGlabs-nativos, subscriber de Telegraf). Detalle de usuarios y políticas
  en §"Usuarios MQTT (password_file + ACL)". Los listeners 1883/9001 no se
  publican al host — solo accesibles desde `boreas_net`; el 9001 se expone al
  exterior vía proxy nginx en `wss://mqtt.aglabs.es/mqtt`. **El listener 8883
  MQTT-TLS nativo SÍ se publica al host** (para los Shelly Gen2, que no hablan
  WSS) — ver §"Listener 8883 TLS nativo (Shellys)".

### Listener 8883 TLS nativo (Shellys)

Añadido 2026-07-24 para el piloto de migración de Shellys Tactica → aglabs.
Los **Shelly Gen2 (Pro 3EM / Pro EM50) solo tienen cliente MQTT nativo** — no
pueden usar la ruta WSS 443→9001 que usa radar_iot. Por eso necesitan un
listener MQTT-over-TLS en 8883, expuesto al exterior:

- **`mosquitto.conf`**: bloque `listener 8883` con `listener_allow_anonymous
  false` (este listener SIEMPRE exige auth; 1883/9001 siguen con anónimos para
  dashboard/bridge) + `certfile`/`keyfile` apuntando a `/mosquitto/certs/`.
- **Cert**: mismo multi-SAN Let's Encrypt de `boreas.aglabs.es` (incluye la SAN
  `mqtt.aglabs.es`). **No se monta `/etc/letsencrypt`**: mosquitto corre como
  uid 1883 y la privkey de LE es `root:root 0600`. Se copia a
  `mosquitto/certs/` con owner 1883 vía [`scripts/sync_certs.sh`](scripts/sync_certs.sh)
  (gitignored — contiene la privkey). Renovación: enlazar el script como
  deploy-hook de certbot (`/etc/letsencrypt/renewal-hooks/deploy/`), que re-copia
  y reinicia el broker en cada renovación.
- **compose**: `ports: ["8883:8883"]` + `./mosquitto/certs:/mosquitto/certs:ro`.
- **Firewall**: abrir 8883/tcp en el host (los Shelly conectan por la WAN de su
  RUT a `mqtt.aglabs.es:8883`).
- **Usuario por Shelly**: una credencial por dispositivo, ACL anclada a su
  prefijo nativo `shellies2/<cid>` con **`readwrite`** (el firmware publica
  status Y se suscribe a `.../rpc` + `.../command`). NO usa el prefijo
  `aglabs/<kind>/…` de radar — los Shelly conservan su prefijo nativo para no
  tocar los scripts mJS. Primer usuario: `shellypro3em-34987a673194` (Shelly
  DEMO Pablo Oviedo).
- **Validación sin tocar el broker vivo**: `docker run --rm` un
  `eclipse-mosquitto:2` efímero con la config montada y mirar que abre 8883 sin
  error de cert/ACL (ojo: reutiliza el `remote_clientid` del bridge → parpadea
  la conexión Tactica del broker vivo unos segundos; se reconecta solo).
- **Gotcha #7 aplica al `aclfile`/`passwordfile`**: editarlos con un editor
  resetea owner a `root` → mosquitto (uid 1883) no los puede leer y el próximo
  restart falla. Tras cualquier edición: `chown 1883:1883` + `chmod 0600`.

**Estado 2026-07-24:** config lista y validada en dry-run. Pendiente de Fase 3
(abrir firewall + `docker compose up -d` que recrea el broker con el listener +
`nginx -s reload`). Ingesta a Nexus sigue pendiente del transformador
`shellies2/# → gadget/<uuid>` (bloqueador funcional, scope Nexus/Boreas).
- **UI** (`/` y `/dashboard/` en `mqtt.aglabs.es`): protegidas con HTTP
  basic auth en nginx (location-scoped, NO server-scoped — ver más abajo).
  Usuario único `pablo`. Defensa en profundidad combinada con
  `AppItem.restricted_to_users=[pablo]` en AG (el card del portal solo lo
  ve pablo).
- **/mqtt** (WebSocket público en `mqtt.aglabs.es/mqtt`): **sin basic auth**.
  La autenticación es MQTT-nativa (CONNECT username/password) contra
  `password_file` + `acl_file` del bróker. Crítico para que publishers
  externos (radar_iot wrapper, futuros) conecten via WSS con SOLO sus
  credenciales MQTT (Paho `username_pw_set`) sin necesidad de inyectar
  cabeceras `Authorization: Basic` en el WS upgrade. Esto se gestiona en
  `/root/boreas_rgiot/nginx.conf` vhost `mqtt.aglabs.es` moviendo el
  `auth_basic` desde el server block a los `location / { ... }` y
  `location ^~ /dashboard/ { ... }`. **No revertir** o publishers no-browser
  dejan de funcionar (paho no soporta basic auth en WS sin custom headers).
- **No SSO**: AG generaría `/sso/?token=<jwt>` por defecto para apps con
  backend Django. Como mqtt.aglabs.es no tiene backend que valide JWT,
  el `AppItem` se registra con `kind='external'` para que AG redirija
  directamente al `full_url` sin pasar por `/sso/`. Si en el futuro se
  quiere single-click sin diálogo basic auth, construir un shim Python
  (~50 líneas) que valide el JWT y setee cookie; nginx haría `auth_request`
  contra el shim.

## Usuarios MQTT (password_file + ACL)

Desde 2026-05-28, Mosquitto autentica clientes con `password_file` y
ACL granular (`acl_file`) además de permitir tráfico anónimo amplio en
ambos listeners. Política: cada publisher AGlabs-nativo tiene su propio
usuario con permisos mínimos. El subscriber de Telegraf también es
autenticado.

| Usuario | Rol | Permisos ACL |
|---------|-----|--------------|
| `radar_iot_001` | Publisher — primer sensor del piloto radar_iot (uRAD 60 GHz, RPi en Oviedo c/ Independencia 3) | `write` en `aglabs/radar/concejo_oviedo_paso_independencia_3/radar_iot_001/#` |
| `nexus_telegraf` | Subscriber — Telegraf vuelca a `nexus_radar` **y** a `nexus_electrical` (Shellys migrados) | `read` en `aglabs/radar/#`, `$SYS/#` y **`shellies2/#`** |
| `shellypro3em-34987a673194` | Publisher — Shelly Pro 3EM DEMO Pablo Oviedo (piloto, en Tactica todavía) | `readwrite shellies2/shellypro3em-34987a673194/#` |
| `shellyproem50-08f9e0e7d058` | Publisher — Shelly Pro EM-50 CONSH60134 DEMO A.P. (**migrado a aglabs 2026-08-14**) | `readwrite shellies2/shellyproem50-08f9e0e7d058/#` |
| `aglabs_shelly_rpc` | Operador RPC — envía comandos a Shellys Gen2 migrados y lee respuestas | `write shellies2/+/rpc`, `read shellies2/+/+/rpc`, `read shellies2/+/online` |
| `*` (anónimo) | Legacy: dashboard, MQTTX Web, bridge a Tactica | `readwrite #` |

**Semántica importante de la ACL Mosquitto**: con `password_file` +
`acl_file` configurados, las reglas `topic …` al margen de cualquier
bloque `user` aplican **solo** a clientes anónimos. Los bloques
`user <nombre>` aplican **exclusivamente** a ese usuario (no heredan
las reglas del nivel superior). Por eso radar_iot_001 SOLO publica en
los topics de su bloque, mientras anónimos mantienen `readwrite #`.

**Patrón canónico — site + sensor anclados, un solo `#`** (vigente desde
2026-05-28). El ACL del publisher se ata explícitamente a su pareja
`<site_slug>/<sensor_id>` y el `#` final cubre los 4 suffixes (`track`,
`event`, `state`, `$health` incluido LWT y retained). Razón del anclaje:
**evita spoofing del segmento `<site>`** — un sensor comprometido no puede
falsificar mensajes como si viniera de otro emplazamiento. Si se reubica
físicamente un sensor, hay que reemitir credencial + actualizar su bloque
ACL (es deliberado: el site es dato de inventario, no de config dinámica).

**Cómo añadir un publisher nuevo** (p.ej. `radar_iot_002` en otro concejo):
```bash
# 1. Hash del password (32+ chars random)
NEW_PWD=$(openssl rand -base64 36 | tr -d '\n/+=' | cut -c1-40)
docker exec aglabs_mosquitto mosquitto_passwd -b /mosquitto/config/passwordfile radar_iot_002 "$NEW_PWD"

# 2. Bloque ACL — añadir al final de mosquitto/config/aclfile usando el
#    site_slug del emplazamiento (mismo slug que va en RADAR_SITE del .env)
cat >> /root/aglabs_mqtt/mosquitto/config/aclfile <<'EOF'

user radar_iot_002
topic write aglabs/radar/concejo_gijon_paso_uria_12/radar_iot_002/#
EOF

# 3. Owner+perms (Edit/Write resetean a root:root — gotcha conocido)
chown 1883:1883 /root/aglabs_mqtt/mosquitto/config/{aclfile,passwordfile}
chmod 0600       /root/aglabs_mqtt/mosquitto/config/{aclfile,passwordfile}

# 4. Reiniciar bróker Y reload nginx (gotcha #6 de §Gotchas: tras restart de
#    aglabs_mosquitto, los publishers WSS se rechazan en silencio hasta que
#    nginx se entera de que el upstream se reinició).
docker restart aglabs_mosquitto
docker exec boreas_nginx nginx -s reload

# 5. Devolver al agente radar_iot el password ($NEW_PWD) para bridge/.env
```

**Cómo añadir un subscriber** (p.ej. Cortex anomaly detector lee radar):
crear bloque `user cortex_radar_consumer` con `topic read aglabs/radar/#`.

### Migración real de un Shelly Gen2 a aglabs — ingesta + RPC (2026-08-14)

Primer device productivo migrado end-to-end de Tactica a este bróker:
**`shellyproem50-08f9e0e7d058`** (Shelly Pro EM-50, `CONSH60134`, DEMO A.P.).
Cadena completa validada: device → `mqtt.aglabs.es:8883` → `nexus_telegraf`
(`read shellies2/#`) → Telegraf input aglabs (`/root/nexus/telegraf/telegraf.conf`)
→ Starlark `gen=2em50` → `nexus_electrical` → Boreas (cruce por `upper(uuid)`,
gadget ya existente). El `Mqtt.SetConfig` lo dispara Diana (`set_mqtt_broker`,
endpoint `set-broker`); ver `/root/diana/CLAUDE.md §"Migración de broker"`.

**Ingesta — 3 piezas en este repo + Nexus:**
1. Usuario por device: `readwrite shellies2/<cid>/#` (passwordfile + aclfile).
2. `nexus_telegraf` amplía ACL con `topic read shellies2/#` (sin esto, la
   suscripción se deniega **en silencio** — Telegraf loguea `Connected` igual).
3. Input nuevo en `telegraf.conf` apuntando a `tcp://aglabs_mosquitto:1883`
   (interno, sin TLS) clonando el bloque EM-50/3EM pero cambiando el transporte.
   El Starlark es agnóstico al bróker (filtra por tag `gen`) → no se toca.

**RPC desde aglabs — usuario `aglabs_shelly_rpc`:**
```
user aglabs_shelly_rpc
topic write shellies2/+/rpc      # enviar petición a cualquier Shelly
topic read  shellies2/+/+/rpc    # leer la respuesta (4 niveles)
topic read  shellies2/+/online
```
**CLAVE del diseño**: el peticionario DEBE poner `src=shellies2/<cid>/<suf>`,
de modo que la respuesta caiga en `shellies2/<cid>/<suf>/rpc`, **dentro** del
prefijo del device (que ya tiene `readwrite shellies2/<cid>/#` y por tanto puede
publicarla). Así los comodines de la ACL son de nivel completo. Ejemplo:
```bash
CID=shellyproem50-08f9e0e7d058; SRC="shellies2/$CID/aglabs-rpc"
# terminal A: escucha la respuesta
mosquitto_sub -h mqtt.aglabs.es -p 8883 --capath /etc/ssl/certs \
  -u aglabs_shelly_rpc -P '<pwd>' -t "$SRC/rpc" -v
# terminal B: envía la orden
mosquitto_pub -h mqtt.aglabs.es -p 8883 --capath /etc/ssl/certs \
  -u aglabs_shelly_rpc -P '<pwd>' -t "shellies2/$CID/rpc" \
  -m "{\"id\":1,\"src\":\"$SRC\",\"method\":\"Shelly.GetDeviceInfo\"}"
```
**GOTCHA que tumbó el bróker (~30s el 2026-08-14):** un `src` hermano tipo
`shellies2/diana-live-<token>` obliga a una ACL `topic write shellies2/diana-live-+/rpc`
con **comodín parcial** (`diana-live-+`), que mosquitto **rechaza** — no arranca
el `acl_file` y el bróker entra en bucle de reinicio, afectando a TODOS los
usuarios (radar, lorawan, dashboard). Los `+` deben ocupar un nivel completo.
Por eso el `src` va bajo el prefijo del device, no como hermano.

**GOTCHA — validar la ACL sin tumbar el bróker vivo**: antes de `docker restart
aglabs_mosquitto`, probar en efímero:
`docker run --rm --volumes-from aglabs_mosquitto --entrypoint sh eclipse-mosquitto
-c 'mosquitto -c /mosquitto/config/mosquitto.conf -v & sleep 2; kill %1'` y grepear
`invalid|error`.

### Routers Teltonika migrados (2026-09-01)

**Regla de alcance (usuario, 2026-09-01): un emplazamiento se migra COMPLETO.**
No está migrado mientras le quede una pieza publicando en Tactica — el router
incluido.

Credencial por router, ACL `write router/<serial>/#` (solo `write`: el RUT
publica y no se suscribe). Anclada al **serial**, que es lo que va en el topic;
ojo, **no** coincide con el `device_id` de Boreas (`COMT241158` vs
`6005009079`).

**El paquete `mqtt_pub` de Tactica no puede migrar: no soporta TLS.** Poner
`tls='1'` impide que el servicio arranque (`Failed to initialize service
configuration`). Aislado con A/B sobre hardware real: `tls=1` con host de
Tactica → no arranca; `tls=0` con host de aglabs → arranca. Como este broker
**solo expone el 8883**, no hay ruta. El binario está empaquetado con **UPX**,
sin LuCI ni documentación (5 ficheros en el paquete), así que tampoco se pueden
enumerar sus opciones sin desempaquetarlo.

**La salida es el `data_sender` NATIVO de Teltonika**, ya instalado en estos
equipos con todos sus módulos. Su salida MQTT soporta TLS completo
(`mqtt_tls`, `mqtt_tls_type`, `mqtt_cafile`, `mqtt_insecure`, credenciales) y
**valida nuestro certificado contra el almacén de CA del propio router** —
verificado en el RUT: `Verification: OK`, `Verify return code: 0`. Que el 8883
sirva ahora un cert **RSA por la cadena clásica** (cambio del 31-ago para los
Nanoenvi) ayuda también aquí. Ventaja de fondo: deja de depender del software
del proveedor del que nos estamos yendo.

| Router | Emplazamiento | Desde |
|---|---|---|
| `rut_6005009079` (COMT241158) | Demo A.P. Pablo / Fitoria | 01-sep-2026 |
| `rut_6007130185` (COMT241272) | Demo A.P. Pablo / P. San Francisco | 01-sep-2026 |

Receta completa (config `data_sender`, script Lua y gotchas) en
`/root/diana/CLAUDE.md §"Migrar la telemetría de un RUT a aglabs"`.

### Estado del parque migrado a aglabs (actualizado 2026-08-31)

| Device | Emplazamiento | Perfil | Estado |
|---|---|---|---|
| `shellyproem50-08f9e0e7d058` (CONSH60134) | Demo A.P. Pablo / P. San Francisco | `monophase_EM50` | migrado 14-ago |
| `shellypro3em-2cbcbba64fd4` (CONSH50050) | Demo A.P. Pablo / Fitoria | `triphase` | **migrado 31-ago** — primer trifásico |
| `shellypro3em-34987a673194` | DEMO | `triphase` | **NO migrado** — sigue en Tactica |

**Cuidado con `34987a673194`:** tiene usuario y bloque ACL creados aquí desde el
piloto, pero el dispositivo **nunca se repuntó**. Su presencia en el `aclfile` no
es prueba de migración — la fuente de verdad es
`LanDevice.current_mqtt_broker` en Diana. Comprobarlo **siempre** antes de usar
un equipo como referencia de que "el camino aglabs funciona".

### Provisión en bloque de identidades Shelly (migración masiva + fábrica)

Al migrar el parque (144 Gen2) o dar de alta Shellys nuevos, la parte de este
repo es **crear un usuario MQTT por device** (`shellies2/<cid>/#`). Regla de
lote: **crear TODAS las identidades primero y reiniciar el bróker UNA vez** — no
un restart por device (cada uno arrastra el `nginx -s reload` del gotcha #6).

```bash
# Por cada <cid> (p.ej. shellyproem50-08f9e0e7d058):
CID=shellyproem50-08f9e0e7d058
PW=$(python3 -c "import secrets,re;print(re.sub(r'[^A-Za-z0-9]','',secrets.token_urlsafe(36))[:32])")
HASH=$(docker exec aglabs_mosquitto sh -c "rm -f /tmp/p.$$ && mosquitto_passwd -c -b /tmp/p.$$ '$CID' '$PW' >/dev/null && cat /tmp/p.$$ && rm -f /tmp/p.$$")
printf '%s\n' "$HASH" >> mosquitto/config/passwordfile
printf '\nuser %s\ntopic readwrite shellies2/%s/#\n' "$CID" "$CID" >> mosquitto/config/aclfile
echo "GUARDA: $CID -> $PW"     # se le pasa al device en el Mqtt.SetConfig (lado Diana)

# Al terminar el bloque (una sola vez):
chown 1883:1883 mosquitto/config/{passwordfile,aclfile}
chmod 0600       mosquitto/config/{passwordfile,aclfile}
docker restart aglabs_mosquitto && sleep 3 && docker exec boreas_nginx nginx -s reload
```

- **Guardar cada `$CID:$PW`**: la contraseña se necesita en el `Mqtt.SetConfig`
  que dispara Diana (no vive aquí después). El firmware **no** la devuelve por
  RPC, así que perderla obliga a re-emitirla.
- **`nexus_telegraf` ya lee `shellies2/#`** y el input aglabs de Telegraf cubre
  cualquier `<cid>` nuevo → **la ingesta no se toca por device**.
- **`aglabs_shelly_rpc` ya existe** → el control RPC de Diana cubre cualquier
  device migrado sin ACL nueva.
- **Alta de fábrica**: idéntico aquí (crear el usuario con el `<cid>` de fábrica
  `shellyproem50-<mac>`); lo que cambia es que el destino MQTT se fija en el
  device por su WebUI/app en vez de por `set-broker` desde Tactica.

Runbook operativo completo (canal de rescate, reboot obligatorio, ventana de
reloj, verificación) en
[`/root/diana/docs/runbook_migracion_masiva_aglabs.md`](/root/diana/docs/runbook_migracion_masiva_aglabs.md).

**Cuándo extender el bloque `nexus_telegraf`**: cuando Diana reconfigure
Shellys/Nanoenvi/Engelmann para publicar a `aglabs_mosquitto` directamente
(en vez de a Tactica), añadir `topic read shellies/#`, `topic read
shellies2/#`, `topic read gadget/#`, `topic read router/#` al bloque y
reiniciar. Hasta entonces Telegraf seguirá consumiendo esos topics desde
Tactica directamente (ver `/root/nexus/telegraf/telegraf.conf`).

**Permisos de los ficheros**: tanto `passwordfile` como `aclfile` están
`chown 1883:1883` (uid del usuario `mosquitto` dentro del contenedor
`eclipse-mosquitto:2`) y modo `0600`. Mosquitto 2.1+ emite warnings y
en versiones futuras rechazará ficheros con permisos más laxos.

**Verificación rápida tras editar la ACL**:
```bash
# Confirma que el usuario PUEDE publicar en topic permitido
docker exec aglabs_mosquitto mosquitto_pub -h 127.0.0.1 -u <user> -P '<pwd>' \
  -t '<allowed-topic>' -m 'ok'

# Confirma que NO puede publicar en topic prohibido (QoS 0 dropea silente;
# subscríbete primero como anónimo en otra terminal y mira si llega)
( docker exec aglabs_mosquitto mosquitto_sub -h 127.0.0.1 -t '<forbidden>' -W 3 -v ) &
docker exec aglabs_mosquitto mosquitto_pub -h 127.0.0.1 -u <user> -P '<pwd>' \
  -t '<forbidden>' -m 'denied'
# (si no aparece nada → ACL denegó como debía)
```

## Cert RSA para dispositivos con almacén de CA antiguo (paso 0 Nanoenvi, 2026-08-18)

Preparativo para migrar los sensores **Nanoenvi (Envira)** desde Tactica a este
broker. Pedido por Diana; el análisis completo está en
`/root/diana/CLAUDE.md §"Migrar los Nanoenvi a aglabs"`.

### El problema, medido

Los Nanoenvi validan la cadena TLS contra un almacén de CA congelado en firmware.
Comparando lo que sirve cada broker:

| | Cadena | Clave | Certs |
|---|---|---|---|
| Tactica — **lo aceptan** (incluso caducado) | `leaf -> R13 -> ISRG Root X1` | RSA 2048 | 2 |
| Este broker (multi-SAN, listener 8883 hoy) | `leaf -> YE1 -> Root YE -> **ISRG Root X2** -> ISRG Root X1` | ECDSA P-256 | 4 |
| **Nuevo `mqtt-iaq-rsa`** | `leaf -> YR1 -> Root YR -> ISRG Root X1` | **RSA 2048** | 3 |

Que acepten un certificado **caducado** demuestra que validan la cadena pero
**no la fecha** (equipo sin RTC fiable). Lo que los rompe es la cadena, y el
sospechoso principal es **ISRG Root X2**, que su almacén casi seguro no tiene.
El 12-ago-2026 Tactica renovó a la cadena nueva y se le cayeron los 58 sensores
hasta revertir.

**El certificado emitido es lo mejor obtenible de Let's Encrypt hoy, pero NO es
idéntico al bueno conocido.** LE ya no emite la cadena clásica de 2 niveles ni
en RSA: se comprobó la cadena alternativa que ofrece el ACME y es la misma.
Queda RSA + terminar en ISRG Root X1 (sin pasar por X2), que es el máximo
acercamiento posible. **Si el sensor piloto lo rechaza igualmente**, las opciones
restantes son: (a) una CA comercial cuya raíz esté en el firmware, (b) `ssl:false`
en 1883 (credenciales en claro, desaconsejado), o (c) OTA de firmware con Envira.
**No** se puede instalar una CA propia: la config MQTT del Nanoenvi (`host`,
`port`, `ssl`, `user`, `pass`, `topic`, `comm_period`) **no tiene campo `ssl_ca`**.

### Emisión

```bash
certbot certonly --webroot -w /var/www/certbot -d mqtt.aglabs.es \
  --cert-name mqtt-iaq-rsa --key-type rsa --rsa-key-size 2048 \
  --preferred-chain "ISRG Root X1"
```

Lineage **separado** a propósito: el multi-SAN ECDSA de `boreas.aglabs.es` lo
usan los 15 vhosts web de nginx y **no se toca**. Solo el listener 8883 pasa a
RSA. Para el resto de clientes del 8883 el cambio es indiferente (RSA + ISRG
Root X1 lo acepta todo, y el Shelly migrado va con `ssl_ca:"*"` — no valida).

`scripts/sync_certs.sh` copia también este cert (`rsa_fullchain.pem` /
`rsa_privkey.pem`, owner 1883) y **no falla** si el lineage no existe.

### Estado: ✅ ACTIVADO el 2026-08-31

El listener 8883 sirve ya el cert RSA. Verificado en caliente:

```
0 s:CN = mqtt.aglabs.es              i:CN = YR1        ← rsaEncryption
1 s:CN = YR1                          i:CN = Root YR
2 s:CN = Root YR                      i:CN = ISRG Root X1
```

Profundidad 3, RSA, **sin pasar por ISRG Root X2** — exactamente el objetivo.
Caduca el 16-nov-2026; la renovación la cubre el deploy-hook de certbot, que ya
invoca `sync_certs.sh` (el cual copia ambos lineages).

**Sin impacto en los Shelly:** `shellypro3em-2cbcbba64fd4` reconectó al instante
negociando `ECDHE-RSA-AES256-GCM-SHA384` (antes ECDSA) — van con `ssl_ca:"*"` y
no validan. Ningún otro cliente usa 8883: Telegraf y el RPC de Diana entran por
1883 interno, el dashboard por WSS 443.

**Vuelta atrás** (30 s): cambiar las dos líneas del listener a
`fullchain.pem`/`privkey.pem` y `docker restart aglabs_mosquitto`.

**Lo que este cert NO resuelve:** que la cadena sea ahora la "correcta" es una
hipótesis razonada a partir de lo que sí acepta Tactica, **no una certeza**. El
firmware Nanoenvi podría rechazarla igual, y **sigue sin haber canal de rescate**.
Por eso el primer sensor debe ser `CAINAN0010` (DEMO, sede propia).

<details>
<summary>Procedimiento original de activación (histórico)</summary>

El certificado está emitido y el ACL preparado, pero el listener **sigue en el
multi-SAN** a propósito. Falta:

```bash
# 1) copiar el cert RSA con owner 1883
/root/aglabs_mqtt/scripts/sync_certs.sh

# 2) en mosquitto.conf, listener 8883: cambiar las dos lineas a
#      certfile /mosquitto/certs/rsa_fullchain.pem
#      keyfile  /mosquitto/certs/rsa_privkey.pem
#    (estan marcadas con "PENDIENTE DE ACTIVAR" en el fichero)

# 3) anadir los 2 usuarios al passwordfile y
docker restart aglabs_mosquitto
```

**Por qué se dejó a medias en su día:** apuntar el listener a un fichero que no
existe **impide arrancar mosquitto entero** — se llevaría por delante el 8883
(Shelly migrado), el 9001 (dashboard/radar) y el bridge. De ahí el orden
copiar-primero, apuntar-después. Se validó en un contenedor efímero
(`docker run --rm --volumes-from aglabs_mosquitto ...`) antes de tocar el vivo.

</details>

Verificación después de activar:
```bash
echo | openssl s_client -connect mqtt.aglabs.es:8883 -servername mqtt.aglabs.es \
  -showcerts 2>/dev/null | grep -E "^ *[0-9]+ s:|^ *i:"   # debe acabar en ISRG Root X1, sin X2
docker logs aglabs_mosquitto --tail 20 | grep -i shellyproem50   # el Shelly debe reconectar
```

### Usuarios y ACL para Nanoenvi

> **MIGRADO 2026-09-04.** `CAINAN0010` publica ya en este bróker: conectó a las
> 10:44:23 UTC negociando **TLSv1.2 / AES256-SHA256** — es decir, **el firmware
> Nanoenvi SÍ acepta la cadena RSA** que servimos desde el 31-ago. La hipótesis
> del cert era correcta. Traspaso **sin perder una sola lectura**: 75-80 filas/hora
> antes y después. No hizo falta `reset`: resuscribió sus topics de control solo.
>
> **Estado 2026-08-31:** `nanoenvi_cainan0010` y `diana_iaq` **existen ya en el
> `passwordfile`**. Hasta esa fecha sus bloques ACL estaban escritos (18-ago)
> pero **ningún usuario había sido creado** — un intento de conexión habría
> fallado en la autenticación, antes siquiera de llegar a la ACL. Trampa fácil de
> pisar: **un bloque en el `aclfile` no implica que el usuario exista.** Los dos
> ficheros se editan por separado y nada los mantiene sincronizados; al dar de
> alta un dispositivo hay que tocar **ambos**.
>
> Probado en caliente: `diana_iaq` publica en `IAQ_RPC/<uuid>` y recibe por
> `IAQ_RESP/#`, y `nanoenvi_cainan0010` publica en `IAQ_Measures`. Nota útil:
> **mosquitto evalúa la ACL por mensaje entregado, no por patrón de
> suscripción**, así que un cliente suscrito a `IAQ_RESP/#` con ACL
> `read IAQ_RESP/+` sí recibe — no hace falta ampliar la ACL a `#`.

Topología de topics del firmware (distinta a la de Shelly):

```
publica medida  ->  IAQ_Measures      <- topic UNICO Y COMPARTIDO por todos
escucha         ->  IAQ_RPC/<uuid>
responde        ->  IAQ_RESP/<uuid>
```

**Consecuencia de seguridad**: `IAQ_Measures` es compartido, así que la escritura
de medida **no se puede acotar por dispositivo** — cualquier sensor con credencial
puede publicar medidas de otro. Es el modelo que traen de Tactica (donde además
comparten el usuario `nodered`/`nodered`, visible en claro en `config.get.mqtt`).
Aquí se mejora en lo posible: **credencial por sensor**, con RPC y respuesta
anclados a SU uuid, para que un sensor no lea los comandos de otro ni suplante
sus respuestas.

Bloques ya añadidos al `aclfile` (con plantilla para dar de alta los siguientes):

- `nanoenvi_cainan0010` — piloto (DEMO / Sede Asturias, uuid `2907f342-...`).
- `diana_iaq` — plano de control (Diana + Node-RED): lee `IAQ_Measures`, escribe
  `IAQ_RPC/+`, lee `IAQ_RESP/+`.

**Las contraseñas están generadas pero NO añadidas al `passwordfile`** (ver
§Estado). Sin ellas, esos dos usuarios no existen todavía para el broker.

**Gotcha #7 recordado**: tras editar `aclfile`/`passwordfile` con un editor, el
owner vuelve a `root` y mosquitto (uid 1883) deja de leerlos → el siguiente
restart falla. Siempre `chown 1883:1883` + `chmod 0600` después. (Al `aclfile` ya
se le hizo.)

### Hallazgo colateral: el bridge a Tactica lleva caído desde el 12-ago

Mirando los logs del broker para esta tarea: **`tactica_bridge` falla de forma
continua desde 2026-08-12 08:59 UTC** — 17.343 errores, reintentando cada 30 s:

```
OpenSSL Error ... error:0A000086:SSL routines::certificate verify failed
Client local.aglabs-mosquitto-bridge disconnected: bad socket read/write: A TLS error occurred
```

Causa: el certificado de Tactica **caducó el 11-ago 22:36 UTC** y siguen sirviendo
el viejo (a propósito, para no tumbar los Nanoenvi). Mosquitto **sí** valida la
caducidad en el bridge, así que no conecta. Es el mismo hecho que obligó a Diana a
poner `MQTT_TLS_INSECURE=1` el 13-ago; Diana se parcheó, **el bridge no**.

**Impacto real: NINGUNO sobre las lecturas de Boreas** (verificado 2026-08-18).
`nexus_telegraf` **no lee de este espejo**: conecta **directamente** a Tactica
(`ssl://mqtt.solutions-iot.es:8883`) con **`insecure_skip_verify = true`**, así que
el cert caducado no le afecta. Comprobado: el volumen diario de `nexus_iaq` no se
inmuta al morir el bridge (12-ago 96.775 filas, 13-ago 96.269, 17-ago 93.918) y
los datos siguen entrando al minuto. Las entradas de telegraf que SÍ apuntan a
este broker (`tcp://aglabs_mosquitto:1883`) son para los **Shelly ya migrados**
(`shellies2/+/status/em1:*`, `em:0`) y para `aglabs/radar/#` — todos publican aquí
**directamente**, no por el espejo.

Lo que se pierde, por tanto, es **la copia local** de `shellies/#`, `shellies2/#`,
`gadget/#` y `router/#`, no la ingesta. Y conviene matizar la mitigación que le
atribuye `/root/diana/docs/contingencia_tactica.md`: un espejo Tactica→aglabs solo
tiene valor **mientras Tactica esté viva**; si Tactica cae, los dispositivos siguen
apuntando allí y el espejo no tiene nada que copiar. Como red de seguridad ante la
caída de Tactica vale poco — la mitigación de verdad es migrar los dispositivos.

**Arreglarlo antes de que Tactica renueve no es trivial**: `bridge_insecure true`
solo desactiva la verificación de *hostname*, no la de caducidad, y mosquitto no
tiene opción para ignorar fechas. Haría falta sustituir el bridge nativo por un
relay propio (paho con `CERT_NONE`, igual que `MQTT_TLS_INSECURE` en Diana) que
suscriba en Tactica y republique aquí.

**No tiene arreglo limpio desde aquí**: `bridge_insecure true` solo desactiva la
verificación de *hostname*, no la de caducidad. Hasta que Tactica sirva un
certificado válido, el bridge no levanta — lo que refuerza el argumento para
migrar los dispositivos a este broker en vez de depender del espejo.

## Operación

```bash
cd /root/aglabs_mqtt && docker compose up -d        # arrancar
cd /root/aglabs_mqtt && docker compose restart      # reiniciar todo
docker restart aglabs_mosquitto                     # solo bróker
docker logs aglabs_mosquitto --tail=30              # estado bridge

# verificar que llegan mensajes desde Tactica vía bridge
docker exec aglabs_mosquitto mosquitto_sub -h 127.0.0.1 -t '#' -W 5 -v | head -20

# verificar handshake WS público
curl -i --http1.1 -u pablo:<pwd> \
  -H "Connection: Upgrade" -H "Upgrade: websocket" \
  -H "Sec-WebSocket-Version: 13" -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" \
  -H "Sec-WebSocket-Protocol: mqtt" --max-time 3 \
  https://mqtt.aglabs.es/mqtt   # → 101 Switching Protocols
```

Tras editar `mosquitto.conf` o `bridge.conf` hace falta **restart** del
contenedor (no hay reload-on-edit). Si solo cambias compose o el bind-mount,
`docker compose up -d` detecta y recrea lo necesario.

Tras editar `/root/boreas_rgiot/nginx.conf` (para tocar el vhost
mqtt.aglabs.es) → `docker restart boreas_nginx` (caveat del bind-mount; un
reload no basta).

## Gotchas / lecciones aprendidas

1. **MQTTX Web sirve `app.js` de 36 MB sin minificar.** Sin `gzip` en nginx
   la primera carga es 30 s+ de pantalla blanca y el usuario cree que no
   funciona. El vhost lleva `gzip on` con tipos relevantes — no quitar.
2. **Permisos del htpasswd**: nginx dentro del contenedor corre como `nginx`
   (uid distinto al de root). Si el fichero está en 640 → `permission denied`
   y nginx devuelve 500 incluso con credenciales válidas. Debe ser 644.
3. **Bridge no muestra "Connected"** explícitamente en log INFO; lo único
   que se ve es "Connecting bridge tactica_bridge (host:port)". Para
   confirmar que el bridge está enganchado, lo correcto es suscribir a `#`
   en local y ver mensajes inmediatos (los retained de `*/online` llegan al
   instante; los de telemetría en 1-3 s).
4. **MQTTX Web no soporta pre-poblar conexión por URL ni por config.** Las
   guarda en IndexedDB del navegador, schema interno del bundle minificado.
   No intentes hackear `localStorage` ni inyectar `<script>` — se rompe en
   cada update. Para "auto-connect" usa el dashboard propio.
5. **Bind-mount caveat en nginx.conf**: `/root/boreas_rgiot/nginx.conf` se
   monta como fichero único; al editar con escritura atómica el inode cambia
   y el contenedor se queda con la versión vieja. Siempre `docker restart
   boreas_nginx`. Mismo patrón que `rules.yml` de prometheus-stack.
6. **`docker restart aglabs_mosquitto` exige `nginx -s reload` después**.
   Tras reiniciar el bróker, los publishers WSS externos (Pi de radar_iot,
   etc.) recibían "MQTT_ERR_PROTOCOL" / nginx loggeaba "upstream prematurely
   closed connection while reading response header". El resolver dinámico
   (`valid=5s`) re-resuelve el DNS pero las conexiones WebSocket ya
   inicializadas hacia el upstream viejo del bróker quedan en estado raro y
   nginx empuja el primer upgrade al socket muerto antes de notarlo. Fix:
   `docker exec boreas_nginx nginx -s reload` tras CUALQUIER restart de
   `aglabs_mosquitto`. Síntoma típico (broker logs): "Client X
   [...] disconnected: not authorised" sin "New client connected" previo,
   incluso con credenciales válidas — confunde porque parece un problema de
   ACL, pero el CONNECT nunca llega al bróker (se queda en nginx). Verificado
   2026-05-28 al provisionar radar_iot_001 y aplicar el ACL más estricto.
7. **Edit tool resetea owner del aclfile/passwordfile**. El editor reescribe
   con uid del proceso (root). Tras editar `/root/aglabs_mqtt/mosquitto/
   config/{aclfile,passwordfile}` con Edit/Write, mosquitto rechaza el
   arranque ("Unable to open"). Re-aplicar siempre:
   `chown 1883:1883 <fichero> && chmod 0600 <fichero>` antes del restart.

## Dependencias cruzadas

- **`/root/boreas_rgiot/nginx.conf`** — contiene el vhost de `mqtt.aglabs.es`,
  el bind-mount al estático y el location `^~ /dashboard/` con alias
  `/app/aglabs_mqtt_web/dashboard/`. Cambiar el path o el dominio aquí
  requiere editar ahí también. **`auth_basic` está location-scoped**, no
  server-scoped: protege `/` (MQTTX Web) y `/dashboard/` (UI propia) pero
  NO `/mqtt` (WS de publishers, que se autentica con MQTT-nativo). Si
  refactorizas el vhost, no muevas el `auth_basic` al server block — los
  publishers no-browser (Paho via WSS) no inyectan basic auth headers en
  el upgrade y romperías la conexión.
- **`/root/boreas_rgiot/docker-compose.yml`** — bind-mount
  `/root/aglabs_mqtt/web:/app/aglabs_mqtt_web:ro` en el servicio `nginx`.
  Si se renombra el directorio o el path interno, recordar actualizar el
  alias del location en `nginx.conf`.
- **`/root/nexus/.env`** — fuente de las credenciales del bróker Tactica
  usadas por el bridge. Si Tactica rota credenciales, actualizar ahí y
  regenerar `bridge.conf`.
- **`/root/ag`** — registra `App(slug='mqtt')` + `AppItem(slug='home',
  kind='external', path='/dashboard/', restricted_to_users=[pablo])`.
  Cambiar el path destino (p.ej. mover el dashboard a otra URL) requiere
  actualizar `AppItem.path` vía shell o admin.
- **Certificado TLS** — `mqtt.aglabs.es` está incluido en el multi-SAN de
  `/etc/letsencrypt/live/boreas.aglabs.es/`. Renovación gestionada por
  certbot del host. Para añadir/quitar SAN: `certbot certonly --webroot
  -w /var/www/certbot --cert-name boreas.aglabs.es --expand -d ...`

## Plan futuro (no implementado todavía)

- Listener TLS 8883 expuesto al exterior + passwordfile/ACL → para que los
  Shellys reconfigurados por Diana publiquen aquí en vez de a Tactica.
- Eventualmente: apagar el bridge a Tactica y cortar la dependencia.
- Cuando esté maduro: si el dashboard es la cara visible, MQTTX Web pasa a
  estar bajo `/mqttx/` o se elimina. El card de AG ya apunta a `/dashboard/`.

Contexto histórico completo en la conversación del agente Claude del
2026-05-16 (despliegue inicial Mosquitto + bridge + MQTTX + dashboard).
