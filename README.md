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
background, so you never have to sign in again.

> **Unofficial integration.** This project is not affiliated with or endorsed by
> Rebrama. It talks to the same private REST API the official mobile app uses
> (see [`REBRAMA_API.md`](REBRAMA_API.md) for the reverse-engineered reference).

---

## Features

- 🔑 **UI configuration**: set up entirely from *Settings → Devices & Services*; no YAML.
- 🔄 **Hands-off authentication**: access tokens are refreshed proactively and
  on demand; if the refresh token ever expires the integration silently logs in
  again with your stored credentials. Re-authentication is only ever requested
  if your password actually changes. A flaky internet connection never triggers it.
- 🧭 **Automatic discovery**: every place and access point on your account is
  added as a Home Assistant device, and the list is kept in sync (new doors
  appear, removed ones are cleaned up, re-granted ones come back).
- 🚪 **Open buttons**: one button per access point to buzz it open.
- 📶 **Connectivity sensors**: know whether each access point is online.
- 🕓 **Last-opened sensors**: see who last opened each place and when.
- 📇 **Account sensors**: subscription expiry, how many access points are
  online, and how many share links are active, all on the hub device.
- 📅 **Temporary-access calendar**: see, create and revoke time-limited share
  links visually from the Home Assistant calendar panel.
- ⏱️ **Temporary access actions**: create and delete time-limited share links
  from automations (e.g. let a delivery in for one hour).
- 🩺 **Diagnostics**: downloadable, secret-redacted diagnostics for support.

## Supported devices

| Rebrama concept | Home Assistant representation |
|---|---|
| Account | A service device (the hub) named `Rebrama (<phone>)`, carrying the account sensors and the temporary-access calendar |
| Place (building / complex) | A device, with a *Last opened* sensor when you manage the place |
| Access point (door / gate) | A device with an **Open** button and a **Connectivity** sensor |

Access-point devices are linked to their place, and places to the account, so
the relationships are visible in the device hierarchy.

## Requirements

- Home Assistant **2026.8.0** or newer.
- A registered Rebrama account. **Create the account in the official Rebrama
  mobile app first**: this integration can sign in but cannot register a new
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
| **Update interval** | How often (in seconds, 60–3600) to refresh access-point status, share links and the latest opening. Leave empty for the default of 5 minutes. |

Changing options reloads the integration automatically.

### Re-authentication & reconfiguration

- If your stored credentials stop working, Home Assistant raises a
  **re-authentication** prompt asking for your password again.
- Use the entry's **Reconfigure** option to change the phone number or
  password proactively (it must remain the same Rebrama account).

## Entities

| Platform | Entity | Notes |
|---|---|---|
| `button` | *Open* (one per access point) | Press to open the door/gate. Unavailable when the access point is offline. |
| `binary_sensor` | *Connectivity* (one per access point) | `on` = online. Diagnostic category. |
| `sensor` | *Last opened* (one per managed place) | Timestamp of the most recent opening, with attributes `opened_by`, `opened_by_phone`, `access_point`, `temporary_access`. |
| `sensor` | *Subscription expires* (account) | Timestamp of when the Rebrama subscription lapses. Diagnostic category. |
| `sensor` | *Access points online* (account) | How many access points are online, with `total` and `offline` attributes. Diagnostic category. |
| `sensor` | *Temporary accesses* (account) | How many share links have not expired yet, with an `accesses` attribute listing each one (description, URL, validity, max uses). The count drops the moment a link expires. |
| `calendar` | *Temporary access* (account) | Each share link as a calendar event (the share URL is in the event description). Create an event to make a new link; delete one to revoke it. |

## Temporary access

Temporary accesses are time-limited share links that let a guest (a cleaner, a
delivery, a visitor) open your doors for a bounded window. There are two ways to
work with them:

- **From the UI: the calendar.** The account's *Temporary access* calendar
  shows every link as an event, with the share URL in the event description.
  **Add an event** to create a new link, and **delete an event** to revoke it.
  The event summary becomes the link's description, and its start/end become
  the validity window (all-day events run from midnight to midnight, local
  time). Creating from the calendar grants *every access point the account is
  allowed to share* and does not set a usage limit; for a specific door or a
  maximum number of uses, use the action below. Recurring events are rejected,
  because a share link is a one-off. Links are also reflected in the
  *Temporary accesses* sensor (with the share URL in its attributes).
- **From automations: the actions.** Use `rebrama.create_temporary_access` /
  `rebrama.delete_temporary_access` for full control (specific access points,
  usage limits, and the returned share URL).

Either way, a start time in the past means *right now* (the Rebrama API refuses
links that start in the past, and calendar dialogs and `now()` templates
routinely produce a start a few seconds ago). The end time must be in the
future. Access points that Rebrama does not let you share are refused with a
clear message before anything is sent.

Links created or revoked in the **Rebrama mobile app** show up in Home
Assistant on the next update (5 minutes by default).

## Services / Actions

### `rebrama.create_temporary_access`

Create a time-limited share link for one or more access points. Returns the
share `url` and its `link` (slug).

```yaml
action: rebrama.create_temporary_access
data:
  access_points:
    - button.front_gate_open
  start: "{{ now() }}"
  end: "{{ now() + timedelta(hours=1) }}"
  description: "Cleaner"
  uses: 1          # optional
response_variable: share
```

All selected access points must belong to the same Rebrama account. Invalid
input (bad time range, an access point that cannot be shared, or a request the
Rebrama server rejects) fails with a validation error; network problems fail
with a regular error, so automations can tell the two apart.

### `rebrama.delete_temporary_access`

Delete a temporary access by its share URL or slug. Deleting a link that is
already gone counts as success.

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

Send a guest a one-hour link:

```yaml
automation:
  - alias: "Guest link"
    triggers:
      - trigger: state
        entity_id: input_button.guest_link
    actions:
      - action: rebrama.create_temporary_access
        data:
          access_points:
            - button.front_gate_open
          start: "{{ now() }}"
          end: "{{ now() + timedelta(hours=1) }}"
          description: "Guest"
          uses: 1
        response_variable: share
      - action: notify.mobile_app
        data:
          message: "Your door link: {{ share.url }}"
```

## How data is updated

The integration polls the Rebrama cloud on an interval (default **5 minutes**,
configurable to 60–3600 s in the integration's options). Every poll refreshes
the list of places and access points (including online status), the
subscription expiry, the temporary-access links, and the latest opening for
each place you manage (other places never generate log calls). It is a
`cloud_polling` integration, so changes may be reflected with a short delay; if
you need quicker updates, lower the interval.

Pressing an *Open* button and creating or deleting a share link from Home
Assistant refresh the affected data immediately. If one of the secondary
requests fails (for example the opening log for one place), the last known
value is kept and everything else still updates.

## Troubleshooting

- **"No Rebrama account exists for this phone number."**: register the account
  in the Rebrama mobile app first, then add the integration.
- **"Invalid phone number or password."**: double-check the credentials you use
  in the app. Include the country code in the phone number.
- **Re-authentication keeps appearing**: your password likely changed; enter
  the new one when prompted.
- **A door's *Open* button is unavailable**: its access point is reported
  offline (`Connectivity` sensor is `off`).
- **"Rebrama rejected the request: ..."** when creating a share link: the
  server's own validation failed (for example a usage limit above the allowed
  maximum). The message is passed through unchanged.
- **Need more detail?** Enable debug logging and download diagnostics:

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
- The share-link list does not include how many uses a link has left; only
  the configured maximum is shown.

## Removing the integration

Delete the integration from **Settings → Devices & Services** (this removes its
devices and entities). If you installed via HACS and no longer want the files,
remove it from HACS and restart.

## Security note

To keep you signed in indefinitely, your password is stored in Home Assistant's
config-entry storage (the same place all integration credentials live) and used
only to obtain fresh tokens if the refresh token ever expires. Protect your
Home Assistant configuration directory accordingly.

Share links open doors for anyone who has them. They are exposed in the
*Temporary accesses* sensor's attributes and in the calendar event descriptions
(that is how you get them to your guest), but they are kept out of the recorder
database and out of diagnostics downloads.

## Development

Home Assistant 2026.3 and newer require **Python 3.14**.

```bash
python -m pip install -r requirements_test.txt
pytest
ruff check . && ruff format --check .
```

Brand images under `custom_components/rebrama/brand/` are generated by
`scripts/generate_brand_assets.py`.

### Releasing

HACS offers users whatever GitHub releases exist, so a release is what ships a
fix:

1. Bump `version` in `custom_components/rebrama/manifest.json`.
2. Merge to `main` with the checks green.
3. Publish a release whose tag matches the version:
   `gh release create v1.2.0 --generate-notes --title v1.2.0`.

Only the *Scheduled validation* workflow has a schedule; GitHub disables it
after 60 days without commits (a repository rule). Re-enable it from the Actions
tab or with `gh workflow enable "Scheduled validation"`. The pull-request
checks are unaffected.

## Credits

- Built for the [Rebrama](https://rebrama.com) access-control system.
- API reference reverse-engineered in [`REBRAMA_API.md`](REBRAMA_API.md).

## License

[MIT](LICENSE) © maksii
