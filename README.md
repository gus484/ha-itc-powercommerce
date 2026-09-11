# ITC PowerCommerce for Home Assistant

Home Assistant integration for German utility customer portals built on the
**ITC PowerCommerce** platform (ITC AG). It reads electricity meter readings from the
portal and imports them into the Energy Dashboard.

The platform is used by a number of municipal utilities. Portal URLs follow the pattern
`<host>/powercommerce/<mandant>/fo/portal/<view>`. The first supported tenant is
Stadtwerke Elbtal (`swet`); other utilities on the same platform can be added.

> **Status: work in progress.** Phase 1 (standalone client) is done and covered by
> tests. The Home Assistant integration itself is under construction — this repository
> is not installable via HACS yet.

## Planned scope

- Current meter reading as a sensor (`device_class: energy`,
  `state_class: total_increasing`)
- Roughly 14 months of history backfilled as external statistics, so the readings show
  up in the Energy Dashboard
- Config flow with utility selection, credential validation against a real login, and
  reauth
- Electricity only. Read-only — the integration never submits readings to the portal.

## Installation

Not available yet. The target is installation via HACS as a custom repository.

## Known limitations

- The portal only provides **monthly** values. In the Energy Dashboard these render as
  one bar per month, not as a smooth curve. That is expected, not a bug. Day and week
  views show nothing useful.
- The **first imported month shows 0 kWh**. There is no earlier reading to take a
  difference against.
- **Missing months are merged into the next reading.** If the portal has no reading
  for October and November, the December bar holds all three months. Two readings in
  one month (a self-reported one plus the regular one) simply add up in that month.
- In the Energy Dashboard, pick the statistic named **"Strom &lt;meter number&gt;"**
  (`itc_powercommerce:…`) as grid consumption, **not** the meter-reading sensor.
  The sensor only knows readings from the day it was set up; the statistic carries
  the backfilled history. Adding both counts the consumption twice.
- Home Assistant stores config entry credentials in `.storage` in plain text.

## Development

```sh
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt
.venv/Scripts/python -m pytest
```

The parser tests run against anonymized fixtures in `tests/fixtures/` and are the
early-warning system for portal markup changes.

## License

MIT — see [LICENSE](LICENSE).
