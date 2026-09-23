# NissanConnect [JP] for Home Assistant

An unofficial Home Assistant integration for Nissan vehicles in Japan, using the
MyNISSAN app's backend.

A fork of [dan-r/HomeAssistant-NissanConnect](https://github.com/dan-r/HomeAssistant-NissanConnect),
rewritten for the Japanese backend. The JP API is a different system to the
European one, so the login flow, remote actions and entity set have been
replaced rather than extended. Original work by
[mitchellrj](https://github.com/mitchellrj/kamereon-python) and
[tobiaswk](https://github.com/Tobiaswk/dartnissanconnect).

No affiliation with Nissan.

## Scope

- **Japan only.** The upstream European code is still present and the region
  selector still lists EU, but only the JP path is developed or tested.
- Petrol and hybrid cars. EV entities (charging, battery) are inherited from
  upstream and untested against the JP backend.

## Tested Vehicles

- Nissan Note (E13, 2021)

## Requirements

- Home Assistant 2023.11.0 or newer
- A MyNISSAN account with an active NissanConnect subscription

## Installation

### HACS

Add `hgn32/HomeAssistant-NissanConnect-JP` as a custom repository in HACS,
install **NissanConnect [JP]**, then restart Home Assistant.

### Manual

Copy `custom_components/nissan_connect` into your Home Assistant
`config/custom_components/` directory and restart.

## Setup

Add the integration from Settings → Devices & Services, and sign in with the ID
and password you use in the MyNISSAN app.

## Entities

Entities appear only if the car reports the matching capability.

**Buttons**

- Start Engine
- Stop Engine
- Lock Doors
- Update Data

**Sensors**

- Odometer
- Fuel Autonomy, Fuel Quantity
- Remote Engine State (raw value exposed as an attribute)
- Location Last Updated, Lock Status Last Updated
- Subscription Plan, Subscription End Date
- Daily / Monthly Distance and Trips

**Binary sensors**

- Doors Locked, Doors Open
- Warning lights: ABS, Airbag, Brake, Engine, Oil Pressure

**Device tracker**

- Location

## Polling

Two intervals are configurable: a polling interval that wakes the car and
fetches fresh status, and an update interval that reads the data Nissan already
holds without waking the car.

## API

The endpoints this integration calls are listed in [docs/jp_api.md](docs/jp_api.md).
Remote actions use the app's GraphQL `ApplyProcedure` mutation.

## Translations

English and Japanese are maintained. The other languages are inherited from
upstream and may be missing JP-specific entity names.

## Issues

Please open an issue on this repository, not upstream.
