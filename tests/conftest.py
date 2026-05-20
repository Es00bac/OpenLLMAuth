from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def clear_fastapi_dependency_overrides():
    """Prevent auth dependency overrides from leaking between tests."""

    from open_llm_auth.main import app

    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()
