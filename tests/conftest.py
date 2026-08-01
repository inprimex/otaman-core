"""Session-wide fixtures for otaman-core tests."""

import pathlib

import pytest


@pytest.fixture(autouse=True, scope="session")
def patch_path_home(tmp_path_factory):
    """Redirect Path.home() to the pytest base temp directory.

    The workspace-resolution path-traversal bound rejects markers that
    resolve outside $HOME. Test paths live under /tmp (not under the
    real $HOME), so without this patch all marker-based resolution tests
    would fail the outside-HOME security check.

    Setting fake_home = getbasetemp() puts every individual tmp_path
    directory "inside" HOME for the duration of the test session.

    Tests that specifically need a path *outside* fake_home should use
    ``monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: tight_home))``
    to temporarily narrow the boundary for that single test.
    """
    fake_home = tmp_path_factory.getbasetemp()
    original = pathlib.Path.__dict__["home"]  # raw classmethod descriptor
    pathlib.Path.home = classmethod(lambda cls: fake_home)
    yield fake_home
    pathlib.Path.home = original
