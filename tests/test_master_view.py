from datetime import datetime, timezone
import json
from archive.master_recorder import MasterRecorder
from archive.master_view import list_master_sessions, master_pvs, load_master_pv_data


def test_master_view_quality_and_session(tmp_path):
    from archive.master_recorder import UTC
    root = tmp_path / "master"
    session = root / "20261018T120000Z_abc123"
    session.mkdir(parents=True)
    (session / "session.json").write_text(json.dumps({"git_sha": "abc123"}))
    pv = "UNH:GEN:CRYO:MAG:M1_TI"
    recorder = MasterRecorder(session, {pv: {}})
    t = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)
    recorder.record(pv, 4.2, at=t)
    from datetime import timedelta
    recorder.snapshot(t)
    recorder.record(pv, 5.0, at=t+timedelta(seconds=2), connected=False)
    recorder.snapshot(t+timedelta(seconds=2))
    recorder.close()
    assert len(list_master_sessions(root)) == 1
    assert master_pvs(session) == [pv]
    frame = load_master_pv_data(session, pv, datetime(2026,10,18,7), datetime(2026,10,18,10))
    assert len(frame) == 2
    assert frame.iloc[0]["Value"] == 4.2
    assert frame.iloc[1]["Quality"] == "disconnected"
    assert str(frame.iloc[1]["Value"]) == "nan"
