"""Regression tests for physical Genesis master CA address discovery."""

from archive.master_service import ca_addresses


def test_physical_ca_addresses(monkeypatch):
    monkeypatch.delenv("GENESIS_MASTER_CA_ADDR_LIST", raising=False)
    monkeypatch.delenv("EPICS_CA_ADDR_LIST", raising=False)

    addresses = ca_addresses().split()

    required = {
        "127.0.0.1:5111",       # Cryo
        "127.0.0.1:5085",       # Microwave supervisor
        "127.0.0.1:5100",       # Physical ssRF fitter
        "127.0.0.1:5092",       # Physical ssRF archiver
        "192.168.1.24:5064",   # Keysight Pi
    }

    assert required.issubset(addresses)
    assert len(addresses) == len(set(addresses))
    assert addresses == sorted(addresses)
    assert "127.0.0.1:5070" not in addresses  # Simulator only


def test_master_explicit_override(monkeypatch):
    monkeypatch.setenv(
        "GENESIS_MASTER_CA_ADDR_LIST",
        "127.0.0.1:5991 127.0.0.1:5992",
    )
    monkeypatch.setenv(
        "EPICS_CA_ADDR_LIST",
        "127.0.0.1:5993",
    )

    assert ca_addresses() == (
        "127.0.0.1:5991 127.0.0.1:5992"
    )


def test_inherited_epics_override(monkeypatch):
    monkeypatch.delenv("GENESIS_MASTER_CA_ADDR_LIST", raising=False)
    monkeypatch.setenv(
        "EPICS_CA_ADDR_LIST",
        "127.0.0.1:5994",
    )

    assert ca_addresses() == "127.0.0.1:5994"


def test_empty_overrides_use_discovery(monkeypatch):
    monkeypatch.setenv("GENESIS_MASTER_CA_ADDR_LIST", " ")
    monkeypatch.setenv("EPICS_CA_ADDR_LIST", " ")

    addresses = ca_addresses().split()

    assert "192.168.1.24:5064" in addresses
    assert "127.0.0.1:5111" in addresses
