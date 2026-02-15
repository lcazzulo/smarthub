# SmartHub – Mira Home Automation Stack

This repository contains the home automation runtime stack running on the host mira.
Its purpose is to act as a local smart hub, aggregating:

- energy events from EMDC (via MQTT)

- Zigbee devices (via Zigbee2MQTT)

- a single Mosquitto broker used by all components

- Home Assistant (external) connects to this broker

- All services run as Docker containers and are orchestrated via Docker Compose.

## Architecture Overview

```text
┌────────────┐
│   EMDC     │
│ (Raspberry)│
│            │
│ publishes  │
│ emdc/...   │
└─────┬──────┘
      │ MQTT
      ▼
┌──────────────────────────┐
│          mira            │
│                          │
│  ┌──────────────┐        │
│  │ Mosquitto    │◄────┐  │
│  │ (single      │     │  │
│  │  broker)     │     │  │
│  └─────┬────────┘     │  │
│        │              │  │
│        │              │  │
│  ┌─────▼──────────┐   │  │
│  │ emdc_mqtt      │   │  │
│  │ writer         │───┘  │
│  │ (MQTT→MQTT)    │      │
│  └─────┬──────────┘      │
│        │                 │
│  ┌─────▼──────────┐      │
│  │ zigbee2mqtt    │──────┘
│  └────────────────┘      │
│                          │
└──────────┬───────────────┘
           │ MQTT
           ▼
   Home Assistant
```

## Design principles

One MQTT broker only (no bridging, no duplication)

Stateless containers, state lives on host volumes

Secrets never committed

EMDC code stays in the EMDC repo

SmartHub repo only wires things together

## Services

1. Mosquitto (MQTT broker)

Central MQTT broker for the entire system

Used by:

EMDC (energy events)

emdc_mqtt_writer

Zigbee2MQTT

Home Assistant

Ports

1883/tcp

Persistent data

Configuration

Message persistence

Logs

Directory

```text
mosquitto/
├── config/
│   └── mosquitto.conf
├── data/
└── log/
```

2. emdc_mqtt_writer

Python service that:

Subscribes to:

emdc/events/energy/#

Maintains persistent cumulative energy state

Publishes Home Assistant–friendly topics:

home/energy/active_energy
home/energy/reactive_energy
home/energy/active_power
home/energy/reactive_power

Important

This service is built from code in the EMDC backend repository

The SmartHub repo does not duplicate the source code

Persistent data

Energy counters and timestamps

Directory

```text
emdc_mqtt_writer/
└── state/
    └── energy_state.json
```

3. Zigbee2MQTT

Handles Zigbee coordinator and devices

Publishes all Zigbee device data to MQTT

Uses the same Mosquitto broker

Web UI

http://mira:8080

### Coordinator

Adapter: zstack

Device: Sonoff Zigbee 3.0 USB Dongle Plus

Channel: 11

Persistent data

Zigbee database

Coordinator state

Network configuration

## Directory

```text
zigbee2mqtt/
└── data/
    ├── configuration.yaml        # NOT committed (contains secrets)
    ├── database.db
    └── state.json

Repository Layout
smarthub/
├── docker-compose.yml
├── .env.example
├── mosquitto/
│   ├── config/
│   │   └── mosquitto.conf
│   ├── data/
│   └── log/
├── emdc_mqtt_writer/
│   └── state/
└── zigbee2mqtt/
    └── data/
        ├── configuration.yaml.template
        ├── database.db
        └── state.json
```

## Environment Configuration
.env file

Docker Compose variables are loaded from .env.

Example (.env.example)

EMDC_BACKEND_PATH=/home/luca/EMDC/backend
TZ=Europe/Rome


## Rules

.env is not committed

.env.example is committed

Paths must be absolute
