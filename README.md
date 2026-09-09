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
  one bar per month, not as a smooth curve. That is expected, not a bug.
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
