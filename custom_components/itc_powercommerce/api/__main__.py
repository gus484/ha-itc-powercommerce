"""CLI: print monthly meter readings as a table.

    python -m custom_components.itc_powercommerce.api --user X --password Y
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import logging
import sys
from datetime import date, datetime

import aiohttp

from .client import (
    DEFAULT_HOST,
    DEFAULT_TENANT,
    USER_AGENT,
    ITCPowerCommerceClient,
)
from .exceptions import PortalError
from .models import Reading


def _parse_date(text: str) -> date:
    return datetime.strptime(text, "%Y-%m-%d").date()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="itc_powercommerce",
        description="Read monthly electricity meter readings from an "
        "ITC PowerCommerce portal.",
    )
    parser.add_argument("--user", required=True, help="portal login name")
    parser.add_argument(
        "--password",
        help="portal password (prompted for if omitted)",
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="portal base URL")
    parser.add_argument("--tenant", default=DEFAULT_TENANT, help="portal tenant/mandant")
    parser.add_argument("--start", type=_parse_date, help="earliest date (YYYY-MM-DD)")
    parser.add_argument("--end", type=_parse_date, help="latest date (YYYY-MM-DD)")
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="enable debug logging"
    )
    return parser


def _print_table(readings: list[Reading]) -> None:
    if not readings:
        print("no readings found")
        return
    date_w = max(len("Datum"), 10)
    val_w = max(len("Zählerstand"), *(len(f"{r.value}") for r in readings))
    print(f"{'Datum':<{date_w}}  {'Zählerstand':>{val_w}}  Einheit")
    print(f"{'-' * date_w}  {'-' * val_w}  -------")
    for r in sorted(readings, key=lambda x: x.reading_date, reverse=True):
        print(
            f"{r.reading_date.isoformat():<{date_w}}  "
            f"{str(r.value):>{val_w}}  {r.unit}"
        )


async def _run(args: argparse.Namespace) -> int:
    password = args.password or getpass.getpass("Portal password: ")
    # The client never owns a session; in Home Assistant it is handed the
    # shared one, here the CLI opens and closes its own.
    async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}) as session:
        client = ITCPowerCommerceClient(
            args.user,
            password,
            session,
            host=args.host,
            tenant=args.tenant,
        )
        try:
            await client.login()
            meters = await client.get_meters()
            for meter in meters:
                print(
                    f"\nZähler {meter.meter_no} "
                    f"({meter.media_type_label}, {meter.unit})"
                )
                readings = await client.get_readings(
                    meter.meter_no, start=args.start, end=args.end
                )
                _print_table(readings)
        except PortalError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
