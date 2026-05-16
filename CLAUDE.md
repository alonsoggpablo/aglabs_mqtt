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
| `/root/boreas_rgiot/ssl/htpasswd_aglabs_mqtt` | hash apr1 del basic auth `pablo` | generado con `openssl passwd -apr1`; password en memoria de Claude (`user_credentials.md`) |

**Ninguno se commitea**. El bridge.conf está en `.gitignore` local; el
htpasswd está en `.gitignore` de boreas_rgiot (patrón `ssl/htpasswd_*`).

Si se redespliega en otra máquina:
1. Copiar el .env de Nexus para tener las credenciales del bróker Tactica.
2. `set -a; . /root/nexus/.env; set +a` y regenerar `bridge.conf` con el
   patrón del template (heredoc en la conversación de despliegue).
3. `openssl passwd -apr1 '<password>'` para el htpasswd.

## Auth y exposición pública

- **Mosquitto**: `allow_anonymous true` en ambos listeners. **Seguro hoy**
  porque ninguno se expone al host — solo accesibles desde `boreas_net`.
  El día que se abra el puerto 8883 al exterior (para Shellys reconfigurados
  por Diana), hay que: (a) `listener 8883` con `cafile/certfile/keyfile` del
  cert de mqtt.aglabs.es, (b) `passwordfile` + ACL, (c) `allow_anonymous
  false` SOLO para ese listener (mantenerlo en true para 1883/9001 internos),
  (d) abrir el puerto en el firewall.
- **UI** (`/` y `/dashboard/` en `mqtt.aglabs.es`): protegidas con HTTP
  basic auth en nginx. Usuario único `pablo`. Defensa en profundidad
  combinada con `AppItem.restricted_to_users=[pablo]` en AG (el card del
  portal solo lo ve pablo).
- **No SSO**: AG generaría `/sso/?token=<jwt>` por defecto para apps con
  backend Django. Como mqtt.aglabs.es no tiene backend que valide JWT,
  el `AppItem` se registra con `kind='external'` para que AG redirija
  directamente al `full_url` sin pasar por `/sso/`. Si en el futuro se
  quiere single-click sin diálogo basic auth, construir un shim Python
  (~50 líneas) que valide el JWT y setee cookie; nginx haría `auth_request`
  contra el shim.

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

## Dependencias cruzadas

- **`/root/boreas_rgiot/nginx.conf`** — contiene el vhost de `mqtt.aglabs.es`,
  el bind-mount al estático y el location `^~ /dashboard/` con alias
  `/app/aglabs_mqtt_web/dashboard/`. Cambiar el path o el dominio aquí
  requiere editar ahí también.
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
