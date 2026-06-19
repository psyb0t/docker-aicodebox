"""aicodebox — multi-harness wrapper for terminal coding agents."""

from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    __version__ = _pkg_version("aicodebox")
except PackageNotFoundError:
    # Not installed (running from a source checkout without `pip install`
    # / `uv sync` having registered the dist-info). Fall back to a clear
    # sentinel rather than a stale hardcoded number.
    __version__ = "0.0.0+source"
