from pathlib import Path


def test_cryo_phoebus_uses_user_specific_log():
    script = Path("scripts/start_cryo_phoebus.sh").read_text()

    assert '"${HOME}/genesis_cryo_phoebus.log"' in script
    assert "/tmp/genesis_cryo_phoebus.log" not in script


def test_cryo_phoebus_has_independent_server_port():
    script = Path("scripts/start_cryo_phoebus.sh").read_text()

    assert '"4920"' in script
