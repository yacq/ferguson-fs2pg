"""Config flow for Ferguson FS2PG."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_PORT
from homeassistant.core import callback
import homeassistant.helpers.config_validation as cv

from .const import CONF_ALLOWED_SOURCES, CONF_DEVICES, DEFAULT_PORT, DOMAIN
from .server import parse_sources


class FergusonConfigFlow(ConfigFlow, domain=DOMAIN):
    """Single step: choose the port the local server listens on."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> FergusonOptionsFlow:
        return FergusonOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if user_input is not None:
            return self.async_create_entry(
                title="Ferguson FS2PG",
                data={CONF_PORT: user_input[CONF_PORT], CONF_DEVICES: []},
            )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required(CONF_PORT, default=DEFAULT_PORT): cv.port}
            ),
        )


class FergusonOptionsFlow(OptionsFlowWithReload):
    """Restrict which addresses may connect as a plug."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                sources = parse_sources(user_input.get(CONF_ALLOWED_SOURCES, ""))
            except ValueError:
                errors[CONF_ALLOWED_SOURCES] = "invalid_source"
            else:
                return self.async_create_entry(
                    data={CONF_ALLOWED_SOURCES: list(sources)}
                )
        current = ", ".join(self.config_entry.options.get(CONF_ALLOWED_SOURCES, []))
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_ALLOWED_SOURCES,
                        description={"suggested_value": current},
                    ): str
                }
            ),
            errors=errors,
        )
