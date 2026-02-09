import pytest


def pytest_configure(config):
    """Configure pytest with custom markers."""
    config.addinivalue_line(
        "markers",
        "integration: mark test as an integration test (makes real API calls)",
    )
    config.addinivalue_line(
        "markers",
        "performance: mark test as a performance test (full-season downloads)",
    )
