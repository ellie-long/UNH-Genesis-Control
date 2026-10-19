"""Static regression for Genesis subsystem archive intervals."""

from pathlib import Path

import pytest
import yaml


CONFIGS = [
    "configs/ssrf_physical/archiver/settings.yaml",
    "configs/target_physical/archiver/settings.yaml",
    "configs/commissioning/microwave_physical/archiver/settings.yaml",
    "configs/commissioning/microwave_sim/archiver/settings.yaml",
]


@pytest.mark.parametrize("filename", CONFIGS)
def test_subsystem_archive_interval(filename):
    settings = yaml.safe_load(Path(filename).read_text())
    assert settings["archiver"]["time_increment"] == 1
