"""Extension Claude Desktop (mcpb/) : même version que le paquet, serveur PyPI épinglé, manifeste complet."""

import json
import tomllib
from pathlib import Path

from nexus_android import __version__

MCPB = Path(__file__).resolve().parent.parent / "mcpb"


def test_extension_follows_the_package_version():
    manifest = json.loads((MCPB / "manifest.json").read_text(encoding="utf-8"))
    project = tomllib.loads((MCPB / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert manifest["version"] == project["version"] == __version__
    assert project["dependencies"] == [f"nexus-android=={__version__}"]
    assert manifest["server"]["type"] == "uv" and (MCPB / manifest["server"]["entry_point"]).is_file()
    assert (MCPB / manifest["icon"]).is_file()
