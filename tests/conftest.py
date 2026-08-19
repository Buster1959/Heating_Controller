"""Shared pytest fixtures for the ZEAL test suite."""
import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Required by pytest-homeassistant-custom-component so hass will
    actually load a custom_components/ integration instead of only the
    ones bundled with HA core."""
    yield
