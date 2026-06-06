# Rebrama for Home Assistant

[![HACS: Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![Validate with hassfest](https://github.com/maksii/ha-rebrama/actions/workflows/hassfest.yaml/badge.svg)](https://github.com/maksii/ha-rebrama/actions/workflows/hassfest.yaml)
[![HACS validation](https://github.com/maksii/ha-rebrama/actions/workflows/validate.yaml/badge.svg)](https://github.com/maksii/ha-rebrama/actions/workflows/validate.yaml)
[![Tests](https://github.com/maksii/ha-rebrama/actions/workflows/tests.yaml/badge.svg)](https://github.com/maksii/ha-rebrama/actions/workflows/tests.yaml)

Control your [**Rebrama**](https://rebrama.com) cloud access-control system (smart
intercoms, building entrances and gates for apartment complexes) directly from
Home Assistant.

After a one-time sign-in with your phone number and password, the integration
**discovers all of your places and access points automatically**, keeps them up
to date, and lets you open any door or gate from a dashboard, automation or
voice assistant. Authentication tokens are refreshed transparently in the
background — you never have to sign in again.

> **Unofficial integration.** This project is not affiliated with or endorsed by
> Rebrama. It talks to the same private REST API the official mobile app uses
> (see [`REBRAMA_API.md`](REBRAMA_API.md) for the reverse-engineered reference).

---

## Features

- 🔑 **UI configuration** — set up entirely from *Settings → Devices & Services*; no YAML.
- 🔄 **Hands-off authentication** — access tokens are refreshed proactively and
  on demand; if the refresh token ever expires the integration silently logs in
  again with your stored credentials. Re-authentication is only ever requested
  if your password actually changes.
- 🧭 **Automatic discovery** — every place and access point on your account is
  added as a Home Assistant device, and the list is kept in sync (new doors
  appear, removed ones are cleaned up).
- 🚪 **Open buttons** — one button per access point to buzz it open.
- 📶 **Connectivity sensors** — know whether each access point is online.
- 🕓 **Last-opened sensors** — see who last opened each place and when.
- 📇 **Account sensors** — subscription expiry, how many access points are
  online, and how many share links are active, all on the hub device.
- 📅 **Temporary-access calendar** — see, create and revoke time-limited share
  links visually from the Home Assistant calendar panel.
- ⏱️ **Temporary access services** — create and delete time-limited share links
  from automations (e.g. let a delivery in for one hour).
- 🩺 **Diagnostics** — downloadable, secret-redacted diagnostics for support.

## Supported devices

| Rebrama concept | Home Assistant representation |
|---|---|
| Account | A service device (the hub) named `Rebrama (<phone>)`, carrying the account sensors and the temporary-access calendar |
| Place (building / complex) | A device, with a *Last opened* sensor |
| Access point (door / gate) | A device with an **Open** button and a **Connectivity** sensor |

Access-point devices are linked to their place, and places to the account, so
the relationships are visible in the device hierarchy.

## Requirements

- Home Assistant **2025.1.0** or newer.
- A registered Rebrama account. **Create the account in the official Rebrama
  mobile app first** — this integration can sign in but cannot register a new
  account.
- Your Rebrama **phone number** and **password**.

## Installation

### HACS (recommended)

This repository is not (yet) in the default HACS store, so add it as a custom
repository:

1. In Home Assistant, open **HACS**.
2. Click the **⋮** menu (top-right) → **Custom repositories**.
3. Repository: `https://github.com/maksii/ha-rebrama`
4. Type: **Integration** → **Add**.
5. Search HACS for **Rebrama**, open it and click **Download**.
6. **Restart Home Assistant.**

### Manual

1. Copy `custom_components/rebrama` into your Home Assistant `config/custom_components` directory.
2. Restart Home Assistant.

## Configuration

1. Go to **Settings → Devices & Services → Add Integration**.
2. Search for **Rebrama**.
3. Enter your **phone number** (include the country code, e.g. `380XXXXXXXXX`;
   the leading `+` is optional) and your **password**.
4. Submit. Your places and access points are discovered automatically.

You can add more than one Rebrama account by repeating the steps with a
different phone number.

### Options

Open the integration's **Configure** dialog to set:

| Option | Description |
|---|---|
| **Update interval** | How often (in seconds, 60–3600) to refresh access-point status. Leave empty for the default of 5 minutes. |

Changing options reloads the integration automatically.

### Re-authentication & reconfiguration

- If your stored credentials stop working, Home Assistant raises a
  **re-authentication** prompt asking for your password again.
- Use the device/entry **Reconfigure** option to change the phone number or
  password proactively (it must remain the same Rebrama account).

## Entities

| Platform | Entity | Notes |
|---|---|---|
| `button` | *Open* (one per access point) | Press to open the door/gate. Unavailable when the access point is offline. |
| `binary_sensor` | *Connectivity* (one per access point) | `on` = online. Diagnostic category. |
| `sensor` | *Last opened* (one per place) | Timestamp of the most recent opening, with attributes `opened_by`, `opened_by_phone`, `access_point`, `temporary_access`. |
| `sensor` | *Subscription expires* (account) | Timestamp of when the Rebrama subscription lapses. Diagnostic category. |
| `sensor` | *Access points online* (account) | How many access points are online, with `total` and `offline` attributes. Diagnostic category. |
| `sensor` | *Temporary accesses* (account) | How many share links are active, with an `accesses` attribute listing each one (description, URL, validity, max uses). |
| `calendar` | *Temporary access* (account) | Each active/upcoming share link as a calendar event. Create an event to make a new link; delete one to revoke it. |

## Temporary access

Temporary accesses are time-limited share links that let a guest (a cleaner, a
delivery, a visitor) open your doors for a bounded window. There are two ways to
work with them:

- **From the UI — the calendar.** The account's *Temporary access* calendar
  shows every active and upcoming link as an event. **Add an event** to create a
  new link, and **delete an event** to revoke it. The event summary becomes the
  link's description, and its start/end become the validity window. Creating from
  the calendar grants *every access point the account is allowed to share* and
  does not set a usage limit — for a specific door or a maximum number of uses,
  use the action below. The active/active-soon link is also reflected in the
  *Temporary accesses* sensor (with the share URL in its attributes).
- **From automations — the actions.** Use `rebrama.create_temporary_access` /
  `rebrama.delete_temporary_access` for full control (specific access points,
  usage limits, and the returned share URL).

## Services / Actions

### `rebrama.create_temporary_access`

Create a time-limited share link for one or more access points. Returns the
share `url` and its `link` (slug).

```yaml
action: rebrama.create_temporary_access
data:
  access_points:
    - button.front_gate_open
  start: "2026-06-06 12:00:00"
  end: "2026-06-06 13:00:00"
  description: "Cleaner"
  uses: 1          # optional
response_variable: share
```

All selected access points must belong to the same Rebrama account.

### `rebrama.delete_temporary_access`

Delete a temporary access by its share URL or slug.

```yaml
action: rebrama.delete_temporary_access
data:
  config_entry_id: <your Rebrama account entry>
  link: "https://rebrama.com/access/abc123"   # or just "abc123"
```

## Example automations

Open the gate when you arrive home:

```yaml
automation:
  - alias: "Open gate on arrival"
    triggers:
      - trigger: zone
        entity_id: person.me
        zone: zone.home
        event: enter
    actions:
      - action: button.press
        target:
          entity_id: button.front_gate_open
```

Notify when someone opens the building door:

```yaml
automation:
  - alias: "Notify on door open"
    triggers:
      - trigger: state
        entity_id: sensor.home_last_opened
    actions:
      - action: notify.mobile_app
        data:
          message: >-
            {{ state_attr('sensor.home_last_opened', 'opened_by') }}
            opened {{ state_attr('sensor.home_last_opened', 'access_point') }}.
```

## How data is updated

The integration polls the Rebrama cloud on an interval (default **5 minutes**,
configurable to 60–3600 s in the integration's options) and refreshes
access-point online status, the list of places, and the latest opening for each
place. It is a `cloud_polling` integration, so changes may be reflected with a
short delay; if you need quicker updates, lower the interval. Opening logs are
only requested for places you can manage — other places never generate log
calls.

Two pieces of data are **not** re-fetched on that interval, because they don't
benefit from it:

- **Subscription expiry** is read once when the integration loads (it only
  changes when you renew). After renewing, reload the integration to refresh it.
- **Temporary-access links** are read once on load and then re-fetched only when
  you create or delete one from Home Assistant (via the calendar or the
  actions). Each link carries its own expiry, so Home Assistant drops it from
  the active list locally when it ends — no polling required. Links you create
  or delete in the **Rebrama mobile app** will appear after the next reload.

## Troubleshooting

- **"No Rebrama account exists for this phone number."** — Register the account
  in the Rebrama mobile app first, then add the integration.
- **"Invalid phone number or password."** — Double-check the credentials you use
  in the app. Include the country code in the phone number.
- **Re-authentication keeps appearing** — Your password likely changed; enter
  the new one when prompted.
- **A door's *Open* button is unavailable** — Its access point is reported
  offline (`Connectivity` sensor is `off`).
- **Need more detail?** — Enable debug logging and download diagnostics:

  ```yaml
  logger:
    default: warning
    logs:
      custom_components.rebrama: debug
  ```

## Known limitations

- There is no reliable open/closed state feedback from the API, so opening is a
  momentary, fire-and-forget action modeled as a **button** (not a lock).
- Push notifications and real-time events from the official app's Firebase
  channel are not used; status is obtained by polling.
- Rate limits are undocumented; the integration polls conservatively and
  serializes open commands.

## Removing the integration

Delete the integration from **Settings → Devices & Services** (this removes its
devices and entities). If you installed via HACS and no longer want the files,
remove it from HACS and restart.

## Security note

To keep you signed in indefinitely, your password is stored in Home Assistant's
config-entry storage (the same place all integration credentials live) and used
only to obtain fresh tokens if the refresh token ever expires. Protect your
Home Assistant configuration directory accordingly.

## Development

```bash
python -m pip install -r requirements_test.txt
pytest
```

Brand images under `custom_components/rebrama/brand/` are generated by
`scripts/generate_brand_assets.py`.

## Credits

- Built for the [Rebrama](https://rebrama.com) access-control system.
- API reference reverse-engineered in [`REBRAMA_API.md`](REBRAMA_API.md).

## License

[MIT](LICENSE) © maksii
