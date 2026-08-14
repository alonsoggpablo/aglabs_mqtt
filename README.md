# aglabs_mqtt

Infraestructura MQTT propia de **AGlabs**: bróker Mosquitto, bridge unidireccional
al bróker de Tactica TIC, cliente MQTTX Web y dashboard estático con
auto-conexión.

URL pública: **https://mqtt.aglabs.es** (acceso restringido).

## Por qué

Toda la telemetría que ingesta AGlabs (Shellys, Nanoenvi, Engelmann calor,
Teltonika) viaja hoy por el bróker MQTT de Tactica TIC. Para reducir esa
dependencia sin desmontar nada de Tactica, este proyecto:

1. Levanta un Mosquitto propio en infraestructura AGlabs (`aglabs_mosquitto`,
   contenedor en `boreas_net`).
2. Configura un **bridge unidireccional** que copia en local todo el tráfico
   del bróker de Tactica (`shellies/#`, `shellies2/#`, `gadget/#`, `router/#`).
3. Expone una UI web para visualizar/depurar el bróker propio:
   - **`/`** — MQTTX Web (cliente MQTT genérico, publish/subscribe ad-hoc).
   - **`/dashboard/`** — dashboard estático auto-conectado (árbol de topics
     + último payload + firehose en vivo). Cero clicks.

El día que los dispositivos del parque se reconfiguren para publicar
directamente a `mqtt.aglabs.es`, esta pieza ya está lista para recibirlos.
Mientras tanto coexiste con la infraestructura de Tactica sin afectarla.

## Stack

- **Bróker**: `eclipse-mosquitto:2`
- **Cliente web**: `emqx/mqttx-web:latest`
- **Dashboard propio**: HTML + [MQTT.js](https://github.com/mqttjs/MQTT.js) vía CDN
- **Red**: `boreas_net` (compartida con el resto del portfolio)
- **TLS**: termina en `boreas_nginx` (cert Let's Encrypt multi-SAN)

## Acceso

- **Card en el portal AGlabs**: visible solo para el usuario `pablo`
  (`AppItem.restricted_to_users`).
- **UIs** (`/` MQTTX Web, `/dashboard/` dashboard propio): HTTP basic auth
  (usuario `pablo`). El navegador la recuerda tras la primera vez.
- **WebSocket MQTT** (`/mqtt`): **sin basic auth**. La autenticación es
  MQTT-nativa (CONNECT user/password) contra el `password_file` del bróker.
  Esto permite que publishers no-browser (p.ej. el wrapper radar_iot
  corriendo en una Raspberry Pi) conecten con solo sus credenciales MQTT.

## Auth y ACL (desde 2026-05-28)

El bróker autentica clientes MQTT-nativos con `password_file` + `acl_file`
en ambos listeners (TCP 1883, WS 9001), conviviendo con tráfico anónimo:

- **Anónimos**: `readwrite #` — preserva el bridge a Tactica, el dashboard
  con auto-connect y los clientes ad-hoc desde MQTTX Web.
- **Publishers autenticados** (`radar_iot_NNN`, futuros Shellys nativos):
  ACL estricta anclada a `aglabs/radar/<site_slug>/<sensor_id>/#`.
  El `#` final cubre los 4 suffixes del wrapper (`track`, `event`, `state`,
  `$health`). El anclaje al `<site>` evita spoofing entre emplazamientos.
- **Subscriber `nexus_telegraf`**: lee `aglabs/radar/#` para volcar a
  `nexus_radar`, y `shellies2/#` para los Shelly Gen2 migrados desde Tactica
  (→ `nexus_electrical`).
- **Shellys Gen2 migrados**: un usuario por device (`shellies2/<cid>/#`). El
  primero productivo, el Pro EM-50 `shellyproem50-08f9e0e7d058` (DEMO A.P.),
  migró el 2026-08-14 con ingesta y control RPC funcionando.
- **Operador RPC `aglabs_shelly_rpc`**: envía comandos a los Shellys migrados
  y lee sus respuestas. El peticionario usa `src=shellies2/<cid>/<suf>` para
  que la respuesta caiga dentro del prefijo del device.

Detalle de cómo añadir un publisher nuevo, la migración Shelly→aglabs, el
usuario RPC y las trampas (comodines de ACL, validación sin tumbar el bróker)
en [CLAUDE.md §"Usuarios MQTT"](CLAUDE.md).

**Migración masiva / alta de fábrica**: la parte de este repo es crear un usuario
MQTT por Shelly (`shellies2/<cid>/#`) — en bloque, con **un solo restart** del
bróker por tanda. El paso a paso (identidades en bloque, guardar credenciales,
ingesta ya global) está en [CLAUDE.md §"Provisión en bloque de identidades
Shelly"](CLAUDE.md); el runbook operativo completo (canal de rescate, reboot,
ventana de reloj, verificación) vive en el lado Diana:
[`/root/diana/docs/runbook_migracion_masiva_aglabs.md`](../diana/docs/runbook_migracion_masiva_aglabs.md).

## Puertos / superficie de red

| Listener | Puerto | Expuesto al host | Para qué |
|----------|--------|------------------|----------|
| Mosquitto TCP | 1883 | NO (solo `boreas_net`) | Clientes internos (Telegraf, Cortex, …) |
| Mosquitto WebSockets | 9001 | NO (solo `boreas_net`) | Browser y publishers WSS vía proxy nginx `/mqtt` |
| nginx HTTPS | 443 | sí (vhost `mqtt.aglabs.es`) | UI (basic auth) + `/mqtt` WS proxy (auth MQTT-nativa) |
| Mosquitto TLS para dispositivos | 8883 | **sí (firewall abierto, en producción)** | Entrada de Shelly Gen2 reconfigurados (MQTT-TLS nativo, sin nginx — los Shelly no hablan WSS). Cert LE copiado con `scripts/sync_certs.sh`; un usuario MQTT por Shelly con ACL `readwrite shellies2/<cid>/#`. Primer device productivo migrado 2026-08-14. Detalle en CLAUDE.md §"Listener 8883 TLS nativo (Shellys)". |

## Operación rápida

```bash
# arrancar / reiniciar
docker compose up -d
docker compose restart

# logs
docker logs aglabs_mosquitto --tail=50
docker logs aglabs_mqttx --tail=20

# verificar bridge desde Tactica (debe ver mensajes en pocos segundos)
docker exec aglabs_mosquitto mosquitto_sub -h 127.0.0.1 -t '#' -W 5 -v | head -20
```

## Ficheros

```
aglabs_mqtt/
├── docker-compose.yml             # aglabs_mosquitto + aglabs_mqttx
├── mosquitto/
│   └── config/
│       ├── mosquitto.conf         # listeners + per_listener_settings + auth
│       ├── aclfile                # ACL: anónimo broad + usuarios estrictos
│       ├── passwordfile           # hashes mosquitto $7$ (GITIGNORED)
│       └── conf.d/
│           └── bridge.conf        # bridge a Tactica (GITIGNORED)
├── web/
│   └── dashboard/
│       └── index.html             # dashboard auto-conectado
├── CLAUDE.md                      # detalles operativos completos
└── README.md                      # este fichero
```

El **vhost nginx** y el **bind-mount** del directorio `web/` viven en el
repo de Boreas (`/root/boreas_rgiot/nginx.conf` y `docker-compose.yml`),
no aquí — necesitan estar ahí porque `boreas_nginx` es quien termina el
TLS y sirve el frontend.

## Más detalles

- Para detalles operativos (auth, gotchas, lecciones, dependencias cruzadas,
  plan futuro) ver [CLAUDE.md](CLAUDE.md).
- Para el contexto estratégico (por qué reemplazar a Tactica) ver
  `/root/CLAUDE.md` global.
