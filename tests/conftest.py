from pathlib import Path


def pytest_configure(config):
    """Create the ignored parent required by the existing basetemp contract."""
    (Path(config.rootpath) / "tmp").mkdir(parents=True, exist_ok=True)
