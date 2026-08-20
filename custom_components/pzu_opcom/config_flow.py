"""Config flow for PZU OPCOM."""
from __future__ import annotations

from typing import Any

from homeassistant import config_entries

from . import DOMAIN


class PzuOpcomConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle PZU OPCOM configuration through the Home Assistant UI."""

    VERSION = 1

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Create the integration entry; no credentials are required."""
        if self._async_current_entries():
            return self.async_abort(reason="already_configured")

        if user_input is not None:
            return self.async_create_entry(
                title="PZU OPCOM",
                data={},
            )

        return self.async_show_form(step_id="user")