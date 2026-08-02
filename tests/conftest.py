"""Session-wide fixtures for otaman-core tests."""

import inspect
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
    # On Python <=3.12 `home` is defined on Path itself; on 3.13+ pathlib was
    # restructured and Path inherits it, so Path.__dict__["home"] would raise
    # KeyError. Grab the descriptor via MRO walk and remember where it lived.
    had_own_home = "home" in pathlib.Path.__dict__
    original = inspect.getattr_static(pathlib.Path, "home")
    pathlib.Path.home = classmethod(lambda cls: fake_home)
    yield fake_home
    if had_own_home:
        pathlib.Path.home = original  # put Path's own descriptor back
    else:
        del pathlib.Path.home  # drop the override; inherited original resurfaces
