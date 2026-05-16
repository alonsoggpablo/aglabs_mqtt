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
- **URL**: pide HTTP basic auth (usuario `pablo`). El navegador la recuerda
  tras la primera vez.

## Puertos / superficie de red

| Listener | Puerto | Expuesto al host | Para qué |
|----------|--------|------------------|----------|
| Mosquitto TCP | 1883 | NO (solo `boreas_net`) | Clientes internos (Telegraf, Cortex, …) |
| Mosquitto WebSockets | 9001 | NO (solo `boreas_net`) | Browser vía proxy nginx `/mqtt` |
| nginx HTTPS | 443 | sí (vhost `mqtt.aglabs.es`) | UI + WS proxy |
| Mosquitto TLS para dispositivos | 8883 | **no abierto todavía** | Futura entrada de Shellys reconfigurados |

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
│       ├── mosquitto.conf         # listeners + persistencia
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
