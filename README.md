# ITC PowerCommerce for Home Assistant

Home Assistant integration for German utility customer portals built on the
**ITC PowerCommerce** platform (ITC AG). It reads electricity meter readings from the
portal and imports them into the Energy Dashboard.

The platform is used by a number of municipal utilities. Portal URLs follow the pattern
`<host>/powercommerce/<tenant>/fo/portal/<view>`. The first supported tenant is
Stadtwerke Elbtal (`swet`); other utilities on the same platform can be set up by
entering host and tenant by hand.

> **Status: no release yet.** The integration works and has been verified on a test
> instance, but there is no tagged release. Until there is, HACS installs the latest
> commit instead of a version.

## What it does

- **Meter reading sensor** — the current cumulative reading in kWh
  (`device_class: energy`, `state_class: total_increasing`), grouped under a device
  named after the meter. Attributes:
  - `meter_number`
  - `reading_date` — the date of the reading, not of the poll
  - `reading_source` — `read`, `estimated` or `self_reported`, where the portal
    discloses it
- **History backfill** — the roughly 14 months of readings the portal keeps are
  imported as an external statistic named **"Strom &lt;meter number&gt;"**
  (`itc_powercommerce:<tenant>_<meter number>_energy`), so the Energy Dashboard
  shows consumption from before the day you set the integration up. Readings that
  later drop out of the portal's window stay in Home Assistant.
- **Two polling rhythms** — the current reading comes from a small JSON endpoint and
  is fetched every 12 hours; the history is a full page and is fetched once a day.
- Electricity only. Read-only — the integration never submits readings to the portal.

## Requirements

- Home Assistant 2025.2 or newer, with the recorder enabled (it is by default)
- A login for your utility's customer portal **without two-factor authentication**

## Installation

### HACS (recommended)

1. In HACS, open the menu (⋮) at the top right and choose **Custom repositories**.
2. Enter `https://github.com/gus484/ha-itc-powercommerce`, pick the type
   **Integration**, and add it.
3. Search for **ITC PowerCommerce** in HACS and download it.
4. Restart Home Assistant.

### Manual

Copy `custom_components/itc_powercommerce/` from this repository into the
`custom_components/` folder of your Home Assistant configuration directory, then
restart Home Assistant.

## Configuration

**Settings → Devices & services → Add integration → ITC PowerCommerce**

1. **Utility** — pick yours from the list. If it is not listed, choose
   **Andere / Other** and enter host and tenant from your portal's URL. For
   Stadtwerke Elbtal the URL is
   `https://onlineservice.stadtwerke-elbtal.de/powercommerce/swet/fo/portal/`: the
   part before `/powercommerce` is the host, the part after it (`swet`) the tenant.
2. **Login** — the email address and password you use on the portal. They are checked
   by an actual login before anything is saved. A meter can only be added once.

### Energy Dashboard

**Settings → Dashboards → Energy → Electricity grid → Add consumption**, then pick the
statistic **"Strom &lt;meter number&gt;"**. Do **not** add the meter-reading sensor as
well: it only knows readings from the day it was set up, and adding both counts the
consumption twice.

### Options

**Settings → Devices & services → ITC PowerCommerce → Configure**

| Option | Default | Range |
|---|---|---|
| Current reading, every … hours | 12 | 1–168 |
| Reading history, every … hours | 24 | 24–168 |

Saving reloads the integration. The portal updates readings monthly at best, so
shorter intervals do not bring newer data. They do bring more logins, since the portal
session usually expires between polls — and repeated failed logins can lock your
portal account.

### Re-authentication

If the portal rejects the stored credentials, for example after a password change,
Home Assistant shows a re-authentication prompt. Check that you can still log in on
the portal's website before retrying, so failed attempts do not add up to a lock.

## Known limitations

- The portal only provides **monthly** values. In the Energy Dashboard these render as
  one bar per month, not as a smooth curve. That is expected, not a bug. Day and week
  views show nothing useful.
- The **first imported month shows 0 kWh**. There is no earlier reading to take a
  difference against.
- **Consumption lands in the month of the reading that closes it.** Home Assistant
  only sees the difference between two readings and books it on the date of the
  later one; it cannot know how that consumption was spread over the time in between.
  Most portal readings fall on month ends, which gives one correct bar per month.
  Where they do not, bars shift:
  - Missing months merge into the next reading. With no reading for October and
    November, the December bar holds all three months.
  - A reading in the middle of a month splits consumption at that date. With a
    reading on 14 May and the next on 20 June, May only covers the time up to the
    14th, and June carries everything from 14 May to its own month end, so June
    looks too high and May too low.
  - Two readings in one month (for example a self-reported one plus the regular one)
    simply add up in that month.
- **Two-factor authentication is not supported.** With 2FA enabled on the account,
  setup fails with "Invalid credentials, or the portal requires a two-factor code."
- Electricity only. Gas, water and district heating meters on the same account are
  ignored.

## Privacy

- Home Assistant stores config entry credentials in `.storage` in **plain text**. Anyone
  with access to your configuration directory or an unencrypted backup can read your
  portal password.
- **Diagnostics** (integration menu → *Download diagnostics*) are meant for bug
  reports. Username, password, meter number, customer number, name, address and email
  are redacted, and the statistic id is left out because it contains the meter
  number. Look through the file before you post it anyway.

## Troubleshooting

Enable debug logging in `configuration.yaml` and restart:

```yaml
logger:
  default: warning
  logs:
    custom_components.itc_powercommerce: debug
```

If the portal changes its page layout, the history import fails with a parse error in
the log while the current reading keeps working. Please open an issue with the log
lines and a diagnostics file.

## Development

```sh
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt
.venv/Scripts/python -m pytest
```

The parser tests run against anonymized fixtures in `tests/fixtures/` and are the
early-warning system for portal markup changes. CI runs `hassfest`, the HACS
validation and `pytest` on every push and pull request, and weekly.

## License

MIT — see [LICENSE](LICENSE).
