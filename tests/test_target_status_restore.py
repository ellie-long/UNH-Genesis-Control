"""target_status remembers the selected state and material by NAME, so a
reordered menu (Ells added d-butanol / d-propanediol, 2026-10-16) never
restores a different material."""

import types

import pytest

target_status = pytest.importorskip("logic_devices.target_status")
Device = target_status.Device


class FakePV:
    def __init__(self, value=0):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


def fake(tmp_path, materials, status=0, material=0):
    dev = types.SimpleNamespace(
        last_file=str(tmp_path / "last.yaml"),
        status_names=["Warm", "Cooldown", "Cold"],
        material_names=list(materials),
        pvs={"status": FakePV(status), "material": FakePV(material)},
    )
    dev._remember = types.MethodType(Device._remember, dev)
    dev.connect = types.MethodType(Device.connect, dev)
    return dev


def test_restore_by_name_survives_a_reordered_menu(tmp_path):
    old = fake(tmp_path, ["Empty", "ND3", "NH3"], status=2, material=2)      # NH3
    old._remember()
    new = fake(tmp_path, ["Empty", "ND3", "d-butanol", "d-propanediol", "NH3"])
    new.connect()
    assert new.material_names[new.pvs["material"].get()] == "NH3"
    assert new.status_names[new.pvs["status"].get()] == "Cold"


def test_old_index_only_file_still_restores(tmp_path):
    (tmp_path / "last.yaml").write_text("status: 1\nmaterial: 1\n")
    dev = fake(tmp_path, ["Empty", "ND3", "d-butanol", "d-propanediol", "NH3"])
    dev.connect()
    assert dev.pvs["material"].get() == 1 and dev.pvs["status"].get() == 1


def test_dopant_saved_and_restored_by_name(tmp_path):
    dev = fake(tmp_path, ["Empty", "ND3", "d-butanol", "d-propanediol", "NH3"], material=2)
    dev.dopant_names = ["None", "Irradiated", "TEMPO", "Trityl"]
    dev.pvs["dopant"] = FakePV(1)
    dev._remember()
    new = fake(tmp_path, ["Empty", "ND3", "d-butanol", "d-propanediol", "NH3"])
    new.dopant_names = ["None", "TEMPO", "Irradiated", "Trityl"]        # reordered
    new.pvs["dopant"] = FakePV(0)
    new.connect()
    assert new.dopant_names[new.pvs["dopant"].get()] == "Irradiated"
    assert new.material_names[new.pvs["material"].get()] == "d-butanol"


def test_supervisor_matches_the_cryo_dopant_by_name():
    from logic_devices.microwave_supervisor.magic import dopant_index
    names = ["None", "Irradiated", "TEMPO", "Trityl"]
    assert dopant_index(names, "TEMPO") == 2
    assert dopant_index(names, " irradiated ") == 1
    assert dopant_index(names, "Unknown") is None
