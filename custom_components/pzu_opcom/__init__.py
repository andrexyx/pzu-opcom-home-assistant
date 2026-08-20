"""PZU OPCOM prices exposed as stable Home Assistant states."""
from __future__ import annotations

import asyncio
import csv
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from io import StringIO
import logging
from statistics import fmean
from typing import Any

from aiohttp import ClientError, ClientResponseError, CookieJar
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

DOMAIN = "pzu_opcom"

TIME_ZONE = "Europe/Bucharest"
SCAN_INTERVAL = timedelta(minutes=30)

SOURCE_URL = (
    "https://www.opcom.ro/"
    "grafice-ip-raportPIP-si-volumTranzactionat/ro"
)

CSV_URL = (
    "https://www.opcom.ro/"
    "rapoarte-pzu-raportPIP-export-csv/"
    "{day:02d}/{month:02d}/{year:04d}/ro"
    "?resolution=60"
)

REQUEST_TIMEOUT = 30
MAX_FETCH_ATTEMPTS = 3

RETRYABLE_STATUSES = {
    403,
    429,
    500,
    502,
    503,
    504,
}

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/127.0.0.0 Safari/537.36"
    ),
    "Accept": "text/csv,text/plain;q=0.9,*/*;q=0.8",
    "Accept-Language": "ro-RO,ro;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": SOURCE_URL,
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

WARMUP_HEADERS = {
    **BROWSER_HEADERS,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
}

ENTITY_NAMES = {
    "sensor.pzu_pret_curent": "PZU Pret Curent",
    "sensor.pzu_pret_ora_urmatoare": "PZU Pret Ora Urmatoare",
    "sensor.pzu_pret_minim_azi": "PZU Pret Minim Azi",
    "sensor.pzu_pret_maxim_azi": "PZU Pret Maxim Azi",
    "sensor.pzu_pret_mediu_azi": "PZU Pret Mediu Azi",
    "sensor.pzu_strategie_baterie": "PZU Strategie Baterie",
}

ENTITY_ICONS = {
    "sensor.pzu_pret_curent": "mdi:cash-clock",
    "sensor.pzu_pret_ora_urmatoare": "mdi:clock-outline",
    "sensor.pzu_pret_minim_azi": "mdi:arrow-down-bold-circle-outline",
    "sensor.pzu_pret_maxim_azi": "mdi:arrow-up-bold-circle-outline",
    "sensor.pzu_pret_mediu_azi": "mdi:calculator",
    "sensor.pzu_strategie_baterie": "mdi:battery-sync",
}

_LOGGER = logging.getLogger(__name__)


def _market_now() -> datetime:
    """Return current time in the Romanian electricity market timezone."""
    return dt_util.now(dt_util.get_time_zone(TIME_ZONE))


def _decimal(raw: str) -> float:
    """Convert OPCOM decimal format to float."""
    value = (
        raw.strip()
        .replace("\ufeff", "")
        .replace("\u00a0", "")
        .replace(" ", "")
    )

    if not value:
        raise ValueError("empty number")

    if "," in value:
        value = value.replace(".", "").replace(",", ".")

    return float(value)


def _delimiter(payload: str) -> str:
    """Detect CSV delimiter."""
    first_line = next(
        (
            line
            for line in payload.splitlines()
            if line.strip()
        ),
        "",
    )

    return (
        ";"
        if first_line.count(";") >= first_line.count(",")
        else ","
    )


def _price_column(header: list[str]) -> int | None:
    """Find price column in OPCOM CSV."""
    for index, cell in enumerate(header):
        normalized = (
            cell.casefold()
            .replace("ț", "t")
            .replace("ţ", "t")
        )

        if (
            "lei/mwh" in normalized
            or "pret" in normalized
            or "price" in normalized
        ):
            return index

    return None


def _parse_csv(payload: str) -> list[float]:
    """Parse OPCOM CSV and return hourly prices in RON/kWh."""
    rows = list(
        csv.reader(
            StringIO(payload),
            delimiter=_delimiter(payload),
        )
    )

    price_index: int | None = None
    prices_mwh: list[float] = []

    for row in rows:
        if not row:
            continue

        if price_index is None:
            price_index = _price_column(row)

            if price_index is not None:
                continue

        candidate_index = (
            price_index
            if price_index is not None
            else 1
        )

        if candidate_index >= len(row):
            continue

        try:
            prices_mwh.append(
                _decimal(row[candidate_index])
            )
        except ValueError:
            continue

    if len(prices_mwh) in (92, 96, 100):
        prices_mwh = [
            fmean(
                prices_mwh[index : index + 4]
            )
            for index in range(
                0,
                len(prices_mwh),
                4,
            )
        ]

    if len(prices_mwh) not in (23, 24, 25):
        raise ValueError(
            f"OPCOM returned "
            f"{len(prices_mwh)} price intervals"
        )

    return [
        price / 1000.0
        for price in prices_mwh
    ]


class _OpcomTableParser(HTMLParser):
    """Collect rows and cells from OPCOM HTML tables."""

    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._cell is not None:
            assert self._row is not None
            self._row.append(" ".join(self._cell).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None:
            assert self._table is not None
            if any(self._row):
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            if self._table:
                self.tables.append(self._table)
            self._table = None


def _parse_html_60min(payload: str) -> list[float]:
    """Extract the official 60-minute price table from the OPCOM page."""
    parser = _OpcomTableParser()
    parser.feed(payload)

    for table in parser.tables:
        header = " ".join(cell for row in table[:2] for cell in row).casefold()
        if "pret mediu 60 min" not in header and "preț mediu 60 min" not in header:
            continue

        prices: list[float] = []
        for row in table:
            if len(row) < 2:
                continue
            try:
                interval = int(row[0].strip())
                price = _decimal(row[1])
            except ValueError:
                continue
            if interval == len(prices) + 1:
                prices.append(price / 1000.0)

        if len(prices) in (23, 24, 25):
            return prices

    raise ValueError("OPCOM 60-minute price table was not found")


class PzuRuntime:
    """Fetch OPCOM prices and publish stable HA states."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.cache: dict[
            str,
            list[float],
        ] = {}
        self.values: dict[str, Any] = {}
        self.attributes: dict[str, dict[str, Any]] = {}
        self.entities: list[Any] = []

        self.session = async_create_clientsession(
            hass,
            cookie_jar=CookieJar(
                unsafe=True
            ),
        )

    async def _warmup_session(self) -> None:
        """
        Visit OPCOM page before CSV request.

        This allows OPCOM to create cookies/session
        similar to a normal browser visit.
        """

        try:
            async with self.session.get(
                SOURCE_URL,
                headers=WARMUP_HEADERS,
                timeout=REQUEST_TIMEOUT,
                # OPCOM currently serves an incomplete TLS certificate chain.
                # This endpoint is public and carries no credentials; limit the
                # workaround to OPCOM requests instead of changing HA globally.
                ssl=False,
            ) as response:
                await response.read()

                if response.status >= 400:
                    _LOGGER.debug(
                        "OPCOM warm-up returned HTTP %s",
                        response.status,
                    )

        except (
            ClientError,
            TimeoutError,
        ) as err:
            _LOGGER.debug(
                "OPCOM warm-up failed: %s",
                err,
            )

    async def _fetch_day(
        self,
        target: date,
    ) -> list[float]:
        """Fetch one OPCOM market day."""

        url = CSV_URL.format(
            day=target.day,
            month=target.month,
            year=target.year,
        )

        last_error: Exception | None = None

        for attempt in range(
            1,
            MAX_FETCH_ATTEMPTS + 1,
        ):
            await self._warmup_session()

            try:
                async with self.session.get(
                    url,
                    headers=BROWSER_HEADERS,
                    timeout=REQUEST_TIMEOUT,
                    ssl=False,
                ) as response:

                    if (
                        response.status
                        in RETRYABLE_STATUSES
                    ):
                        body = await response.text(
                            errors="replace"
                        )

                        raise ClientResponseError(
                            response.request_info,
                            response.history,
                            status=response.status,
                            message=(
                                body[:160]
                                or response.reason
                                or "OPCOM request blocked"
                            ),
                            headers=response.headers,
                        )

                    response.raise_for_status()

                    payload = await response.text(
                        encoding="utf-8",
                        errors="replace",
                    )

                return _parse_csv(payload)

            except (
                ClientError,
                TimeoutError,
                ValueError,
            ) as err:
                last_error = err

                if (
                    attempt
                    >= MAX_FETCH_ATTEMPTS
                ):
                    raise

                delay = float(attempt)

                _LOGGER.debug(
                    "OPCOM fetch attempt "
                    "%s/%s failed for %s: %s; "
                    "retrying in %.0fs",
                    attempt,
                    MAX_FETCH_ATTEMPTS,
                    target.isoformat(),
                    err,
                    delay,
                )

                await asyncio.sleep(delay)

        # OPCOM occasionally blocks or changes the CSV export while the public
        # results page remains available. For the current market day, use its
        # official 60-minute table as a resilient fallback.
        if target == _market_now().date():
            try:
                async with self.session.get(
                    SOURCE_URL,
                    headers=WARMUP_HEADERS,
                    timeout=REQUEST_TIMEOUT,
                    ssl=False,
                ) as response:
                    response.raise_for_status()
                    page = await response.text(errors="replace")
                prices = _parse_html_60min(page)
                _LOGGER.info("Using OPCOM HTML 60-minute table fallback")
                return prices
            except (ClientError, TimeoutError, ValueError) as fallback_error:
                if last_error is not None:
                    raise ValueError(
                        f"CSV failed: {last_error}; HTML fallback failed: "
                        f"{fallback_error}"
                    ) from fallback_error
                raise

        if last_error is not None:
            raise last_error

        raise RuntimeError(
            "OPCOM fetch failed without an error"
        )

    def _base_attributes(
        self,
        now: datetime,
        stale: bool,
        errors: list[str],
    ) -> dict[str, Any]:
        """Create common entity attributes."""

        attrs: dict[str, Any] = {
            "source": "OPCOM",
            "source_url": SOURCE_URL,
            "timezone": TIME_ZONE,
            "last_update": now.isoformat(),
            "stale": stale,
        }

        if errors:
            attrs["update_errors"] = errors

        return attrs

    def _set_unavailable(
        self,
        now: datetime,
        errors: list[str],
    ) -> None:
        """Set all PZU entities unavailable."""

        attrs = self._base_attributes(
            now,
            True,
            errors,
        )

        for entity_id, name in ENTITY_NAMES.items():
            self.values[entity_id] = None
            self.attributes[entity_id] = {
                **attrs,
                "friendly_name": name,
                "icon": ENTITY_ICONS[entity_id],
            }

        self.async_notify_entities()

    def async_notify_entities(self) -> None:
        """Notify native sensor entities that new values are available."""
        for entity in self.entities:
            entity.schedule_update_ha_state()

    async def async_update(
        self,
        _now: datetime | None = None,
    ) -> None:
        """Update today and tomorrow OPCOM prices."""

        now = _market_now()

        today = now.date()

        tomorrow = (
            today
            + timedelta(days=1)
        )

        errors: list[str] = []

        today_fresh = False

        for target in (
            today,
            tomorrow,
        ):
            try:
                self.cache[
                    target.isoformat()
                ] = await self._fetch_day(
                    target
                )

                if target == today:
                    today_fresh = True

            except (
                ClientError,
                TimeoutError,
                ValueError,
            ) as err:
                errors.append(
                    f"{target.isoformat()}: {err}"
                )

        today_prices = self.cache.get(
            today.isoformat()
        )

        if not today_prices:
            _LOGGER.warning(
                "No valid OPCOM prices for today: %s",
                "; ".join(errors),
            )

            self._set_unavailable(
                now,
                errors,
            )

            return

        self.cache = {
            key: value
            for key, value
            in self.cache.items()
            if key
            in {
                today.isoformat(),
                tomorrow.isoformat(),
            }
        }

        index = min(
            now.hour,
            len(today_prices) - 1,
        )

        current = today_prices[index]

        if (
            index + 1
            < len(today_prices)
        ):
            following: float | None = (
                today_prices[
                    index + 1
                ]
            )
        else:
            tomorrow_prices = (
                self.cache.get(
                    tomorrow.isoformat()
                )
            )

            following = (
                tomorrow_prices[0]
                if tomorrow_prices
                else None
            )

        minimum = min(today_prices)
        maximum = max(today_prices)
        average = fmean(today_prices)

        charge = (
            minimum
            + (
                maximum
                - minimum
            )
            * 0.25
        )

        sell = (
            minimum
            + (
                maximum
                - minimum
            )
            * 0.75
        )

        if current <= charge:
            strategy = (
                "Incarca Baterie & Consuma"
            )

        elif current >= sell:
            strategy = (
                "Vinde din Baterie / Discharge"
            )

        else:
            strategy = (
                "Standby / Pasiv"
            )

        base = self._base_attributes(
            now,
            not today_fresh,
            errors,
        )

        numeric_attrs = {
            **base,
            "unit_of_measurement": (
                "RON/kWh"
            ),
            "state_class": (
                "measurement"
            ),
            "suggested_display_precision": 4,
        }

        values = {
            "sensor.pzu_pret_curent": round(current, 4),
            "sensor.pzu_pret_ora_urmatoare": round(following, 4),
            "sensor.pzu_pret_minim_azi": round(minimum, 4),
            "sensor.pzu_pret_maxim_azi": round(maximum, 4),
            "sensor.pzu_pret_mediu_azi": round(average, 4),
        }

        for (
            entity_id,
            value,
        ) in values.items():

            attributes = {
                **numeric_attrs,
                "friendly_name": (
                    ENTITY_NAMES[
                        entity_id
                    ]
                ),
                "icon": (
                    ENTITY_ICONS[
                        entity_id
                    ]
                ),
            }

            self.values[entity_id] = value
            self.attributes[entity_id] = attributes

        strategy_entity_id = "sensor.pzu_strategie_baterie"
        self.values[strategy_entity_id] = strategy
        self.attributes[strategy_entity_id] = {
                **base,
                "friendly_name": (
                    ENTITY_NAMES[
                        "sensor.pzu_strategie_baterie"
                    ]
                ),
                "icon": (
                    ENTITY_ICONS[
                        "sensor.pzu_strategie_baterie"
                    ]
                ),
                "prag_incarcare": round(
                    charge,
                    4,
                ),
                "prag_vanzare": round(
                    sell,
                    4,
                ),
            }

        self.async_notify_entities()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Any,
) -> bool:
    """Set up PZU OPCOM from a config entry."""

    await _async_setup_runtime(hass)

    await hass.config_entries.async_forward_entry_setups(
        entry,
        ["sensor"],
    )
    return True


async def _async_setup_runtime(
    hass: HomeAssistant,
) -> bool:
    """Create the shared runtime and start its update interval."""

    if DOMAIN in hass.data:
        return True

    runtime = PzuRuntime(hass)

    hass.data[DOMAIN] = runtime

    await runtime.async_update()

    async_track_time_interval(
        hass,
        runtime.async_update,
        SCAN_INTERVAL,
    )

    return True
