"""Analysis package — exposes the TOOLS registry with all diagnosis tools."""

import routebench.analysis.diagnosis  # noqa: F401 — triggers registration
from routebench.analysis.tools import TOOLS

__all__ = ["TOOLS"]
