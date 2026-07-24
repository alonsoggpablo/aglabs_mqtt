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

- `shellies/#`   — Gen1 EM3/EM2
- `shellies2/#`  — Gen2 Pro3EM, ProEM50, switches, comandos, rpc
- `gadget/#`     — UUID-indexado: IAQ (nanoenvi), calor (engelmann), gas (sigfox)
- `router/#`     — Teltonika señal/uptime

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
| `nexus_telegraf` | Subscriber — Telegraf vuelca a `nexus_radar` hypertable | `read` en `aglabs/radar/#` y `$SYS/#` |
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
