"""PZU OPCOM prices exposed as stable Home Assistant states."""
from __future__ import annotations

import csv
from datetime import date, datetime, timedelta
from io import StringIO
import logging
from statistics import fmean
from typing import Any

from aiohttp import ClientError
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

DOMAIN = "pzu_opcom"
CONFIG_SCHEMA = cv.empty_config_schema(DOMAIN)
TIME_ZONE = "Europe/Bucharest"
SCAN_INTERVAL = timedelta(minutes=30)
SOURCE_URL = "https://www.opcom.ro/grafice-ip-raportPIP-si-volumTranzactionat/ro"
CSV_URL = (
    "https://www.opcom.ro/"
    "rapoarte-pzu-raportPIP-export-csv/{day:02d}/{month:02d}/{year:04d}/ro"
    "?resolution=60"
)

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
    return dt_util.now(dt_util.get_time_zone(TIME_ZONE))


def _decimal(raw: str) -> float:
    value = raw.strip().replace("\ufeff", "").replace("\u00a0", "").replace(" ", "")
    if not value:
        raise ValueError("empty number")
    if "," in value:
        value = value.replace(".", "").replace(",", ".")
    return float(value)


def _delimiter(payload: str) -> str:
    first_line = next((line for line in payload.splitlines() if line.strip()), "")
    return ";" if first_line.count(";") >= first_line.count(",") else ","


def _price_column(header: list[str]) -> int | None:
    for index, cell in enumerate(header):
        normalized = cell.casefold().replace("ț", "t").replace("ţ", "t")
        if "lei/mwh" in normalized or "pret" in normalized or "price" in normalized:
            return index
    return None


def _parse_csv(payload: str) -> list[float]:
    rows = list(csv.reader(StringIO(payload), delimiter=_delimiter(payload)))
    price_index: int | None = None
    prices_mwh: list[float] = []

    for row in rows:
        if not row:
            continue
        if price_index is None:
            price_index = _price_column(row)
            if price_index is not None:
                continue
        candidate_index = price_index if price_index is not None else 1
        if candidate_index >= len(row):
            continue
        try:
            prices_mwh.append(_decimal(row[candidate_index]))
        except ValueError:
            continue

    if len(prices_mwh) in (92, 96, 100):
        prices_mwh = [
            fmean(prices_mwh[index : index + 4])
            for index in range(0, len(prices_mwh), 4)
        ]
    if len(prices_mwh) not in (23, 24, 25):
        raise ValueError(f"OPCOM returned {len(prices_mwh)} price intervals")
    return [price / 1000.0 for price in prices_mwh]


class PzuRuntime:
    """Fetch prices, retain the last valid day and publish stable states."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.cache: dict[str, list[float]] = {}

    async def _fetch_day(self, target: date) -> list[float]:
        url = CSV_URL.format(day=target.day, month=target.month, year=target.year)
        async with async_get_clientsession(self.hass).get(
            url,
            headers={"User-Agent": "HomeAssistant-PZU-OPCOM/2.0"},
            timeout=30,
        ) as response:
            response.raise_for_status()
            payload = await response.text(encoding="utf-8", errors="replace")
        return _parse_csv(payload)

    def _base_attributes(
        self,
        now: datetime,
        stale: bool,
        errors: list[str],
    ) -> dict[str, Any]:
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

    def _set_unavailable(self, now: datetime, errors: list[str]) -> None:
        attrs = self._base_attributes(now, True, errors)
        for entity_id, name in ENTITY_NAMES.items():
            self.hass.states.async_set(
                entity_id,
                STATE_UNAVAILABLE,
                {**attrs, "friendly_name": name, "icon": ENTITY_ICONS[entity_id]},
            )

    async def async_update(self, _now: datetime | None = None) -> None:
        now = _market_now()
        today = now.date()
        tomorrow = today + timedelta(days=1)
        errors: list[str] = []
        today_fresh = False

        for target in (today, tomorrow):
            try:
                self.cache[target.isoformat()] = await self._fetch_day(target)
                if target == today:
                    today_fresh = True
            except (ClientError, TimeoutError, ValueError) as err:
                errors.append(f"{target.isoformat()}: {err}")

        today_prices = self.cache.get(today.isoformat())
        if not today_prices:
            _LOGGER.warning("No valid OPCOM prices for today: %s", "; ".join(errors))
            self._set_unavailable(now, errors)
            return

        self.cache = {
            key: value
            for key, value in self.cache.items()
            if key in {today.isoformat(), tomorrow.isoformat()}
        }
        index = min(now.hour, len(today_prices) - 1)
        current = today_prices[index]
        if index + 1 < len(today_prices):
            following: float | None = today_prices[index + 1]
        else:
            tomorrow_prices = self.cache.get(tomorrow.isoformat())
            following = tomorrow_prices[0] if tomorrow_prices else None

        minimum = min(today_prices)
        maximum = max(today_prices)
        average = fmean(today_prices)
        charge = minimum + (maximum - minimum) * 0.25
        sell = minimum + (maximum - minimum) * 0.75
        if current <= charge:
            strategy = "Incarca Baterie & Consuma"
        elif current >= sell:
            strategy = "Vinde din Baterie / Discharge"
        else:
            strategy = "Standby / Pasiv"

        base = self._base_attributes(now, not today_fresh, errors)
        numeric_attrs = {
            **base,
            "unit_of_measurement": "RON/kWh",
            "state_class": "measurement",
            "suggested_display_precision": 4,
        }
        values = {
            "sensor.pzu_pret_curent": current,
            "sensor.pzu_pret_ora_urmatoare": following,
            "sensor.pzu_pret_minim_azi": minimum,
            "sensor.pzu_pret_maxim_azi": maximum,
            "sensor.pzu_pret_mediu_azi": average,
        }
        for entity_id, value in values.items():
            attributes = {
                **numeric_attrs,
                "friendly_name": ENTITY_NAMES[entity_id],
                "icon": ENTITY_ICONS[entity_id],
            }
            self.hass.states.async_set(
                entity_id,
                round(value, 6) if value is not None else STATE_UNAVAILABLE,
                attributes,
            )

        self.hass.states.async_set(
            "sensor.pzu_strategie_baterie",
            strategy,
            {
                **base,
                "friendly_name": ENTITY_NAMES["sensor.pzu_strategie_baterie"],
                "icon": ENTITY_ICONS["sensor.pzu_strategie_baterie"],
                "prag_incarcare": round(charge, 6),
                "prag_vanzare": round(sell, 6),
            },
        )


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up PZU OPCOM from the top-level pzu_opcom YAML key."""
    runtime = PzuRuntime(hass)
    hass.data[DOMAIN] = runtime
    await runtime.async_update()
    async_track_time_interval(hass, runtime.async_update, SCAN_INTERVAL)
    return True
