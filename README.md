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
- **TLS 8883**: cert Let's Encrypt RSA `mqtt-iaq-rsa`, renovado por certbot; un deploy-hook (solo para los certificados del broker) lo copia y reinicia Mosquitto. Dharma vigila que el servido coincida con el renovado. Detalle en CLAUDE.md.

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
en los tres listeners (TCP 1883, WS 9001, TLS 8883):

- **Anónimos**: solo por el 1883 interno (`boreas_net`). **Desde el 2026-10-09 el
  WebSocket público (`wss://mqtt.aglabs.es/mqtt`) exige usuario**: antes admitía
  anónimos con permiso de escritura, y cualquiera podía mandar órdenes a los
  relés. El dashboard anónimo deja de conectar (no se usaba).
- **Shellys nuevos de RG (`rg_shelly`)**: una contraseña compartida, como pidió
  RG, pero limitada a la lista de Client ID de su pedido (una línea por equipo).
  No es `rgiot`.
  Para no configurarlo a mano, Diana (Scripts Shelly → Aprovisionamiento, solo
  staff) o `scripts/make_provision_script.py` generan un
  script de aprovisionamiento (uno para todo el pedido) que deja el MQTT del
  equipo listo y desaparece tras el reinicio. Lleva la contraseña: se entrega
  por canal privado. Detalle en [CLAUDE.md](CLAUDE.md).
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

Detalle del cierre de anónimos y de `rg_shelly` en [CLAUDE.md §"Anónimos cerrados en el 9001"](CLAUDE.md).
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
| Mosquitto WebSockets | 9001 | NO (solo `boreas_net`) | Publishers WSS vía proxy nginx `/mqtt` — **exige usuario** desde 2026-10-09 |
| nginx HTTPS | 443 | sí (vhost `mqtt.aglabs.es`) | UI (basic auth) + `/mqtt` WS proxy (auth MQTT-nativa) |
| Mosquitto TLS para dispositivos | 8883 | **sí (firewall abierto, en producción)** | Entrada de Shelly Gen2 reconfigurados (MQTT-TLS nativo, sin nginx — los Shelly no hablan WSS). Cert LE copiado con `scripts/sync_certs.sh`; un usuario MQTT por Shelly con ACL `readwrite shellies2/<cid>/#`. Primer device productivo migrado 2026-08-14. Detalle en CLAUDE.md §"Listener 8883 TLS nativo (Shellys)". |

## Operación rápida

```bash
# aplicar cambios de config (ÚNICA vía: valida, aplica y revierte si falla)
/root/.aglabs/mosquitto_apply.sh reload    # aclfile / passwordfile
/root/.aglabs/mosquitto_apply.sh restart   # mosquitto.conf / conf.d / certs

# levantar si está parado
docker start aglabs_mosquitto

# logs
docker logs aglabs_mosquitto --tail=50
docker logs aglabs_mqttx --tail=20

# verificar bridge desde Tactica (debe ver mensajes en pocos segundos)
docker exec aglabs_mosquitto mosquitto_sub -h 127.0.0.1 -t '#' -W 5 -v | head -20
```

> **No reiniciar ni recargar el bróker a mano.** El 2026-10-08 una recarga con el
> `aclfile` ilegible lo tuvo 2 h 45 min caído. Detalle en `CLAUDE.md`
> §"Incidente 2026-10-08".

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

## Migración de Shelly desde Tactica

Un Shelly Gen2 migrado conserva su prefijo nativo (`shellies2/<cid>/…`) y
necesita aquí **una credencial propia** con ACL `readwrite` anclada a ese
prefijo (`readwrite`, no solo `write`: el firmware se suscribe a `/rpc` y
`/command/#` para el control).

Equipos en este broker a 2026-08-31:

| Device | Emplazamiento | Perfil | Desde |
|---|---|---|---|
| `shellyproem50-08f9e0e7d058` | P. San Francisco | monofásico EM-50 | 14-ago |
| `shellypro3em-2cbcbba64fd4` | Fitoria | trifásico + relé | 31-ago |

`shellypro3em-34987a673194` tiene credencial creada pero **no está migrado**:
sigue publicando en Tactica. El estado real de cada equipo es
`LanDevice.current_mqtt_broker` en Diana, no la presencia de un bloque ACL aquí.

Pasos: crear usuario+ACL → reiniciar broker → `nginx -s reload` →
`Mqtt.SetConfig` desde Diana → **`Shelly.Reboot` obligatorio** (sin él el
equipo publica pero no resuscribe los topics de control y el RPC queda mudo).
Detalle completo y gotchas en [`CLAUDE.md`](CLAUDE.md).

### Routers Teltonika (desde 2026-09-01)

Un emplazamiento se migra **completo**: sensores y también su router. Los RUT
publican su telemetría (`signal`, `uptime`) y necesitan aquí credencial propia
con ACL `write router/<serial>/#`.

El paquete que usaban en Tactica (`mqtt_pub`) **no habla TLS**, y este broker
solo expone el 8883, así que los migrados usan el `data_sender` **nativo** de
Teltonika, que sí valida nuestro certificado LE contra el almacén de CA del
propio router. Publican un JSON a `router/<serial>/data` cada 5 minutos.

En Tactica iban en **claro por el 1883 con `teltonika/teltonika`**, credencial
compartida por toda la flota; aquí van con TLS 1.3 y credencial por equipo.

| Router | Emplazamiento | Desde |
|---|---|---|
| `rut_6005009079` (COMT241158) | Fitoria | 01-sep |
| `rut_6007130185` (COMT241272) | Parque San Francisco | 01-sep |
| `rut_6007130186` (COMT241273) | Mástil Ctra. L'Arena, Soto del Barco (aún en la oficina) | 14-oct |

La contraseña de cada router migrado se guarda en `/root/.aglabs/secrets/mqtt_rut_<serial>` (0600). La de los dos primeros solo existe en el propio RUT.

### El bridge a Tactica ya no espeja `shellies`

Desde el 2026-08-31 el bridge solo copia `gadget/#` y `router/#`. Espejar
`shellies/#` y `shellies2/#` provocaba **doble ingesta** en Nexus, que consume
esos topics en los dos brokers a la vez. Además el bridge lleva caído desde
~11-ago porque el certificado de Tactica caducó y aquí sí se valida; la ingesta
no depende de él.

## Certificado RSA para sensores IoT antiguos (Nanoenvi) — 2026-08-18

> **Validado con hardware el 2026-09-04.** El primer Nanoenvi (`CAINAN0010`)
> conectó aceptando este certificado (TLS 1.2, AES256-SHA256). La hipótesis de
> que el firmware rechazaba la cadena anterior y aceptaría la RSA era correcta.
>
> **Activo desde el 2026-08-31.** El listener 8883 sirve ya el certificado RSA
> (`mqtt.aglabs.es` → YR1 → Root YR → ISRG Root X1). Los Shelly no se enteraron
> (van con `ssl_ca:"*"`). Falta aún crear las credenciales de los sensores y el
> enrutado multi-broker en Diana antes de repuntar ningún Nanoenvi.

Los sensores Nanoenvi validan TLS contra un almacén de CA congelado en firmware y
**rechazan** el certificado multi-SAN de este broker, porque su cadena pasa por
`ISRG Root X2`. Aceptan el de Tactica (RSA, terminando en `ISRG Root X1`)
**incluso caducado** — validan la cadena, no la fecha.

Se ha emitido un certificado **RSA aparte** (`mqtt-iaq-rsa`) solo para el listener
8883, sin tocar el multi-SAN que usan los 15 vhosts web de nginx.

**Estado: emitido y preparado, pendiente de activar** (copiar el cert, cambiar dos
líneas en `mosquitto.conf` y reiniciar). Los pasos exactos, y por qué se dejó a
medias, en [CLAUDE.md](CLAUDE.md) §"Cert RSA para dispositivos con almacén de CA
antiguo".

⚠ **El bridge a Tactica lleva caído desde el 12-ago-2026**: el certificado de
Tactica caducó y mosquitto valida la fecha, así que no conecta. Este broker no
recibe `shellies/#`, `shellies2/#`, `gadget/#` ni `router/#` desde entonces.
Detalle en CLAUDE.md.

## Más detalles

- Para detalles operativos (auth, gotchas, lecciones, dependencias cruzadas,
  plan futuro) ver [CLAUDE.md](CLAUDE.md).
- Para el contexto estratégico (por qué reemplazar a Tactica) ver
  `/root/CLAUDE.md` global.
