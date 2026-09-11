# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project state

Phase 0 (recon) and Phase 1 (standalone Python prototype) are complete. Phase 2 — the
Home Assistant HACS integration — is the current work.

- `custom_components/itc_powercommerce/api/` — the working async client and CLI from
  Phase 1, moved here from `swet_meter/` for Phase 2. Tests resolve it through
  `pythonpath = ["."]` in `pyproject.toml`; there is no installable package any more.
- `logs/` — Phase 0 artifacts: HAR capture plus saved HTML of the login page and
  `meterDetails`. Source material for the anonymized fixtures in `tests/fixtures/`.
- `PLAN.md` — the original German work plan. **Local only, gitignored, not part of
  the repo.** It was written before the HAR was analyzed and describes a portal that
  does not exist (see below), so publishing it would mislead anyone who read it as a
  spec. Where it and this file disagree, this file wins.

Goal: scrape monthly electricity meter readings from the Stadtwerke Elbtal customer
portal (ITC PowerCommerce platform) into Home Assistant.

## What the portal actually looks like

The URL pattern is `<host>/powercommerce/<mandant>/fo/portal/<view>`; `swet` is the
Stadtwerke Elbtal tenant. v1 may hardcode `swet` but keeps host/tenant swappable for
other utilities on the same platform.

- **Login is a plain POST** to `/powercommerce/swet/fo/portal/loginProcess` with
  `login`, `password`, and `twoFactorAuthenticationCode` (empty when 2FA is off).
  Returns 302, then `/home`.
  **There is no Spring Web Flow, no `execution` token, and no CSRF field.** The
  original plan assumed otherwise; the captures do not support it. Do not build
  token-rotation handling.
- **Latest reading as JSON:** `/powercommerce/swet/fo/portal/meterWidget.json` —
  `meterNo`, `mediaTypeStr` ("Strom"), `lastMeterReadings.DEFAULT_TARIFF` with `unit`,
  `meterValue` (zero-padded string, e.g. `000013364,00`), `readingDate`
  (`DD.MM.YYYY`). Cheap. Preferred source for the current value.
  Sibling endpoints exist: `invoiceWidget.json`, `budgetBillingPlanWidget.json`.
- **Reading history as HTML** on `/powercommerce/swet/fo/portal/meterDetails`, with
  `<td data-title="Datum">` and `<td data-title="Zählerstand">` cells. German number
  formatting: `.` thousands separator, `,` decimal (`13.114,7 kWh`). The capture spans
  roughly 14 months.

## Conventions

- **Use `Decimal`, never `float`, for meter readings** throughout the client and
  models. Rounding errors propagate through cumulative values. Convert to `float`
  only at the boundary where an external API demands it (see Statistics below).
- **Parse the known field names directly.** The earlier instruction to collect all
  `<input type="hidden">` generically was written for the assumed Web Flow and is now
  obsolete — there are no hidden fields to echo. Ignore it wherever it still appears.
- **Detect session expiry by checking whether a response is the login page**, not by
  parsing the wrong document. On expiry, re-login exactly once, then fail with
  `AuthError`. Whether `SessionExpired` still needs to be a distinct exception depends
  on whether Phase 1 ever raises it — if it does not, collapse it into `AuthError`
  rather than carrying a dead class into the integration.
- **Do not hammer the portal.** Repeated failed logins can lock the account. During
  development, work against cached responses.
- **The parser test is the priority test.** It is the early-warning system for portal
  markup changes.
- Data scope: electricity only. Don't code against gas/water/heat, but don't hardcode
  against them either.

## Fixtures / privacy

Before anything from `logs/` is committed as a fixture, redact: password, cookies,
`Authorization` headers, customer number, meter number, metering-point ID, name,
address. Use consistent placeholders so fixtures stay parseable.

Diagnostics in the integration must use `async_redact_data` over the same field set.

---

# Phase 2 — Home Assistant integration

## Repo layout

HACS expects the integration at `custom_components/itc_powercommerce/` in the repo
root. Domain is `itc_powercommerce`, not `stadtwerke_elbtal`, so other utilities on
the same platform benefit.

Vendor the Phase 1 client into the integration directory rather than publishing it to
PyPI — custom integrations may ship their own code, and this avoids a release
dependency. Keep the module boundary clean (client knows nothing about Home
Assistant) so extraction to PyPI stays possible later.

```
custom_components/itc_powercommerce/
  __init__.py        async_setup_entry / async_unload_entry
  manifest.json
  const.py
  api/               vendored Phase 1 client — no HA imports below this line
  coordinator.py
  config_flow.py
  sensor.py
  statistics.py
  diagnostics.py
  strings.json
  translations/de.json, en.json
hacs.json
```

### manifest.json

- `"config_flow": true`, `"iot_class": "cloud_polling"`
- `"version"` must match the Git tag exactly or HACS rejects the release
- `"requirements": ["beautifulsoup4"]` only
- **Do not pin aiohttp.** The `aiohttp<3.12` pin in `requirements.txt` is a dev-only
  workaround for `aioresponses`. Carrying it into `manifest.json` would fight Home
  Assistant's own aiohttp and break the install.

## Porting the client

- Replace the client's own `ClientSession` with `async_get_clientsession(hass)`.
  This is the single largest change from script to integration.
- Drop the `--debug` response dumper and the local response cache.
- Keep all blocking work out of the event loop; the client is already async.

## Coordinators — two rhythms, not one

The recon suggests a split the original plan did not have:

- **`meterWidget.json`** is cheap and gives the current reading → regular coordinator
  poll, roughly every 12 hours.
- **`meterDetails` HTML** is only needed for the history backfill → once at setup,
  then at most daily.

Two coordinators (or one coordinator with a separate, slower backfill task), not a
single interval doing both.

Error mapping: `AuthError` → `ConfigEntryAuthFailed` (triggers the reauth dialog),
everything transient → `UpdateFailed`.

## Statistics — the part most likely to go wrong

Historical monthly values must be written via
`homeassistant.components.recorder.statistics.async_add_external_statistics`. A normal
sensor cannot backfill the past.

- `statistic_id` needs the domain prefix with a colon:
  `itc_powercommerce:swet_<meterno>_energy`
- `StatisticMetaData` with `has_sum=True`, `unit_of_measurement="kWh"`,
  `source="itc_powercommerce"`
- `meterValue` parses to `Decimal`; the statistics API takes `float`. Convert
  immediately before writing, nowhere earlier.
- `readingDate` is `DD.MM.YYYY` with no time. Build midnight in `Europe/Berlin`, then
  `dt_util.as_utc`. **`start` must be hour-aligned and timezone-aware** or the recorder
  rejects the row.
- `state` is the meter reading; `sum` is the meter reading **minus a baseline**, so
  the oldest row has `sum = 0`. HA derives consumption from the delta between
  consecutive `sum` values, and for the oldest row it computes `sum − 0` — a raw
  meter reading as `sum` puts the entire reading (~11 MWh) into the first month.
  `sum` must also never decrease. There is no `last_reset` for external statistics.
- The baseline is read back from the recorder (`state − sum` of the oldest stored
  row), falling back to the earliest reading when nothing is stored. It is not kept
  in the config entry. That keeps re-imports consistent after the portal's window
  has moved past the first import.
- Imports are idempotent on `(statistic_id, start)`, so the full ~14 months can be
  rewritten on every backfill without diffing.
- The row logic lives in `series.py` with no HA imports, so `tests/test_series.py`
  covers it without installing Home Assistant.
- A `DataUpdateCoordinator` only reschedules while it has listeners. The history
  coordinator has no entities, so `__init__.py` gives it a no-op listener — without
  it the backfill runs once at setup and never again.

**Known limitation, document it in the README:** monthly values render in the Energy
Dashboard as one bar per month, not as a smooth curve. Linear distribution across the
month's hours looks better but invents data — if implemented at all, make it an
option that is off by default.

**Test the statistics import against a throwaway HA instance, not a production one.**
Sparse monthly points in a table designed for hourly data is where breakage is
expected.

## Config flow

- Step 1: utility selection — dropdown of known tenants (name → host + mandant) plus
  "other" with manual entry. v1 may ship only Stadtwerke Elbtal.
- Step 2: username / password, validated by a real login attempt.
- `async_set_unique_id(meterNo)` followed by `abort_if_unique_id_configured()`, or the
  same installation can be added twice.
- Reauth flow handling `ConfigEntryAuthFailed`.
- Options flow for the two poll intervals.
- `strings.json` plus `translations/de.json` and `translations/en.json`. Error keys for
  `invalid_auth`, `cannot_connect`, `unknown`.

## Entities

- `_attr_has_entity_name = True`
- `DeviceInfo` identified by meter number, so sensors group under one device
- `unique_id` derived from `meterNo`, not from the config entry ID
- Current-reading sensor: `device_class: energy`, `state_class: total_increasing`,
  `native_unit_of_measurement: kWh`. Attributes: reading date, meter number, reading
  source (abgelesen / geschätzt / gemeldet) where available.

## CI

GitHub Actions running `hassfest` and the HACS validation action. These catch manifest
and structure errors that otherwise surface only at the user's install.

## Out of scope for Phase 2

- Writing meter readings back to the portal. Read-only.
- Gas, water, district heating.
- Submission to the HACS default store (needs a `home-assistant/brands` PR for icon
  and logo). Custom-repository install is the target for now.
