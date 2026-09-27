#!/usr/bin/env bash
set -u

ROOT="/Users/shrestha/project/unh/genesis/Development/genesis-phase1"
cd "$ROOT" || exit 1

echo "============================================================"
echo " GENESIS SSRF MAIN-GUI WORKFLOW CONSOLIDATION"
echo "============================================================"

python - <<'PY'
from pathlib import Path
import re

ROOT = Path.cwd()
backend = ROOT / "tools/ssrf_physical_no_rf_demo_ioc.py"
gui = ROOT / "gui/genesis_ssrf.bob"
start = ROOT / "scripts/start_ssrf_physical_no_rf_demo.sh"
stop = ROOT / "scripts/stop_ssrf_physical_no_rf_demo.sh"
docs = ROOT / "docs/SSRF_PHYSICAL_NO_RF_DEMO.md"
physdoc = ROOT / "docs/SSRF_PHYSICAL_COMMISSIONING.md"
testfile = ROOT / "tests/test_ssrf_main_workflow_gui.py"

for path in (backend, gui, start, docs, physdoc):
    if not path.exists():
        raise RuntimeError(f"required file missing: {path}")

def rep(text, old, new, label):
    if old not in text:
        raise RuntimeError(f"patch anchor missing: {label}")
    return text.replace(old, new, 1)

text = backend.read_text()

# Expose START_CMD as the single BLAST/BURN execution command.
if 'p["START_CMD"] = builder.boolOut(' not in text:
    text = rep(
        text,
        '''        p["DEMO_RUN_CMD"] = builder.boolOut(
            "DEMO_RUN_CMD", ZNAM="IDLE", ONAM="RUN DEMO",
            always_update=True, on_update_name=self._cmd_run_demo)''',
        '''        p["START_CMD"] = builder.boolOut(
            "START_CMD", ZNAM="IDLE", ONAM="BLAST / BURN",
            always_update=True, on_update_name=self._cmd_blast)
        p["DEMO_RUN_CMD"] = builder.boolOut(
            "DEMO_RUN_CMD", ZNAM="IDLE", ONAM="RUN DEMO",
            always_update=True, on_update_name=self._cmd_run_demo)''',
        "START_CMD",
    )

# Production-style state mirrors used by the main GUI.
if 'p["EXECUTION_STATE"] = builder.stringIn(' not in text:
    text = rep(
        text,
        '''        p["EXECUTION_ARMED"] = builder.boolIn(
            "EXECUTION_ARMED", ZNAM="DISARMED", ONAM="ARMED")

        p["FITTER_UP"]''',
        '''        p["EXECUTION_ARMED"] = builder.boolIn(
            "EXECUTION_ARMED", ZNAM="DISARMED", ONAM="ARMED")
        p["EXECUTION_STATE"] = builder.stringIn("EXECUTION_STATE")
        p["EXECUTION_REASON"] = builder.longStringIn(
            "EXECUTION_REASON", length=256)
        p["EXECUTION_MODE"] = builder.stringIn("EXECUTION_MODE")

        p["FITTER_UP"]''',
        "execution state PVs",
    )

if 'p["EXECUTION_STATE"].set("IDLE")' not in text:
    text = rep(
        text,
        '''        p["EXECUTION_ARMED"].set(0)
        p["FITTER_UP"].set(0)''',
        '''        p["EXECUTION_ARMED"].set(0)
        p["EXECUTION_STATE"].set("IDLE")
        p["EXECUTION_REASON"].set("Physical no-RF commissioning idle")
        p["EXECUTION_MODE"].set("PHYSICAL_NO_RF")
        p["FITTER_UP"].set(0)''',
        "execution initial values",
    )

old = '''    def _set_state(self, state, reason):
        self.pvs["DEMO_STATE"].set(str(state)[:39])
        self.pvs["DEMO_REASON"].set(str(reason)[:255])'''
if old in text:
    text = text.replace(
        old,
        '''    def _set_state(self, state, reason):
        state = str(state)
        reason = str(reason)
        self.pvs["DEMO_STATE"].set(state[:39])
        self.pvs["DEMO_REASON"].set(reason[:255])
        if "EXECUTION_STATE" in self.pvs:
            self.pvs["EXECUTION_STATE"].set(state[:39])
        if "EXECUTION_REASON" in self.pvs:
            self.pvs["EXECUTION_REASON"].set(reason[:255])''',
        1,
    )

if 'def _cmd_blast(' not in text:
    text = rep(
        text,
        '''    def _cmd_run_demo(self, value, pv):
        self._reset_cmd(pv)
        if value:
            self._launch("run-demo", self.run_switch_demo)''',
        '''    def _cmd_blast(self, value, pv):
        self._reset_cmd(pv)
        if value:
            self._launch("blast", self.blast_workflow)

    def _cmd_run_demo(self, value, pv):
        self._reset_cmd(pv)
        if value:
            self._launch("run-demo", self.run_switch_demo)''',
        "blast callback",
    )

# Replace arm_demo with workflow-level arm.
a = text.find("    def arm_demo(self):")
b = text.find("\n    def run_switch_demo(self):", a)
if a < 0 or b < 0:
    raise RuntimeError("arm_demo/run_switch_demo block not found")

arm = '''    def arm_demo(self):
        # One-shot workflow arm. No switch motion and no RF occurs here.
        if not _port_open(8777) or not _port_open(8778):
            raise RuntimeError("fitter is not running")

        calibrated = int(float(_ca_get(f"{FIT}:CALIBRATED", 5.0)))
        if calibrated != 1:
            raise RuntimeError("fitter is not calibrated")

        self._assert_no_rf()
        output, points, ready = self._read_wgen()
        nmr_mask, ssrf_mask = self._read_switches()

        if output != 0:
            raise RuntimeError(f"physical WaveGen output is ON: {output}")

        if nmr_mask != 0 or ssrf_mask != 3:
            raise RuntimeError(
                f"routing is not NMR_LIVE: NMR={nmr_mask}, ssRF={ssrf_mask}"
            )

        self.execution_armed = True
        self.pvs["EXECUTION_ARMED"].set(1)
        self._set_state(
            "ARMED",
            "One-shot workflow armed; BLAST will fit, plan, load, route, and restore",
        )

    def blast_workflow(self):
        # One-button transaction after ARM.
        if not self.execution_armed:
            raise RuntimeError("BLAST blocked: execution is not armed")

        try:
            self._set_state(
                "1/7 FIT + PLAN",
                "Fresh ssRF fit -> adaptive WaveformPlan",
            )
            self.prepare_waveform(preserve_arm=True)

            if not self.execution_armed:
                raise RuntimeError("workflow arm was revoked during preparation")

            self._set_state(
                "4/7 KEYSIGHT READY",
                "8192-point adaptive waveform loaded; output remains OFF",
            )

            # Existing switch routine consumes the one-shot arm.
            self.run_switch_demo()

        except Exception:
            self.execution_armed = False
            self.pvs["EXECUTION_ARMED"].set(0)
            raise
'''
text = text[:a] + arm + text[b:]

text = text.replace(
    "    def prepare_waveform(self):\n",
    "    def prepare_waveform(self, preserve_arm=False):\n",
    1,
)

disarm = '''        self.execution_armed = False
        self.pvs["EXECUTION_ARMED"].set(0)

'''
if disarm in text:
    text = text.replace(
        disarm,
        '''        if not preserve_arm:
            self.execution_armed = False
            self.pvs["EXECUTION_ARMED"].set(0)

''',
        1,
    )

text = text.replace(
    'self._set_stage("WAITING FIT", f"case={self.case}")',
    'self._set_stage("WAITING FIT", f"case={self.case}")\n'
    '        self._set_state("2/7 WAITING FIT", '
    '"Waiting for fresh calibrated ssRF fitter result")',
    1,
)
text = text.replace(
    '"offline NMR -> fit -> WaveformPlan -> physical Keysight complete",',
    '"3/7 WAVEFORM LOADED: fit -> WaveformPlan -> physical Keysight complete",',
    1,
)
text = text.replace(
    'self._set_state("1/4 NMR DISCONNECT", "NMR switch 0 -> 3")',
    'self._set_state("5/7 NMR DISCONNECT", "Physical NMR switch 0 -> 3")',
)
text = text.replace(
    'self._set_state("2/4 ssRF CONNECT", "ssRF switch 3 -> 0")',
    'self._set_state("6/7 ssRF CONNECT", "Physical ssRF switch 3 -> 0")',
)
text = text.replace(
    '"DEMO ACTIVE",\n                "ssRF route connected for 2.5 s; SMC RF/FM OFF; WaveGen output OFF",',
    '"6/7 NO-RF BURN PATH",\n                "ssRF route connected; SMC RF/FM OFF; WaveGen output OFF",',
)

backend.write_text(text)
print("[PASS] backend exposes ARM + START(BLAST) + detailed state")

# Append integrated workflow panel to real main GUI.
text = gui.read_text()
if "<name>Workflow_BurnPanel</name>" not in text:
    m = re.search(
        r'<display version="2\.0\.0">.*?<height>(\d+)</height>',
        text,
        flags=re.S,
    )
    if not m:
        raise RuntimeError("main GUI display height not found")

    old_h = int(m.group(1))
    y = old_h + 20
    new_h = old_h + 510
    text = text[:m.start(1)] + str(new_h) + text[m.end(1):]

    frag = f'''
  <widget type="rectangle" version="2.0.0">
    <name>Workflow_BurnPanel</name>
    <x>20</x><y>{y}</y><width>1140</width><height>460</height>
    <line_width>2</line_width>
    <line_color><color red="0" green="53" blue="145" /></line_color>
    <background_color><color red="240" green="241" blue="244" /></background_color>
  </widget>
  <widget type="rectangle" version="2.0.0">
    <name>Workflow_Header</name>
    <x>20</x><y>{y}</y><width>1140</width><height>42</height>
    <line_width>0</line_width>
    <background_color><color red="0" green="53" blue="145" /></background_color>
  </widget>
  <widget type="label" version="2.0.0">
    <name>Workflow_Title</name>
    <x>35</x><y>{y+8}</y><width>660</width><height>28</height>
    <text>SSRF FIT → WAVEFORM → BURN WORKFLOW</text>
    <font><font family="Liberation Sans" style="BOLD" size="16.0" /></font>
    <foreground_color><color red="255" green="255" blue="255" /></foreground_color>
    <transparent>true</transparent>
  </widget>
  <widget type="textupdate" version="2.0.0">
    <name>Workflow_Mode</name>
    <x>830</x><y>{y+8}</y><width>300</width><height>28</height>
    <pv_name>pva://UNH:GEN:SSRF:SEQ:EXECUTION_MODE</pv_name>
  </widget>

  <widget type="label" version="2.0.0"><name>WF_Fitter_L</name><x>40</x><y>{y+65}</y><width>110</width><height>24</height><text>Fitter running</text></widget>
  <widget type="led" version="2.0.0"><name>WF_Fitter</name><x>155</x><y>{y+62}</y><width>28</width><height>28</height><pv_name>pva://UNH:GEN:SSRF:FIT:FITTER_UP</pv_name><bit>0</bit><off_color><color red="200" green="50" blue="50" /></off_color><on_color><color red="30" green="160" blue="70" /></on_color></widget>
  <widget type="label" version="2.0.0"><name>WF_Cal_L</name><x>215</x><y>{y+65}</y><width>95</width><height>24</height><text>Calibrated</text></widget>
  <widget type="led" version="2.0.0"><name>WF_Cal</name><x>315</x><y>{y+62}</y><width>28</width><height>28</height><pv_name>pva://UNH:GEN:SSRF:FIT:CALIBRATED</pv_name><bit>0</bit><off_color><color red="210" green="130" blue="20" /></off_color><on_color><color red="30" green="160" blue="70" /></on_color></widget>
  <widget type="label" version="2.0.0"><name>WF_Mode_L</name><x>375</x><y>{y+65}</y><width>70</width><height>24</height><text>Fit mode</text></widget>
  <widget type="textupdate" version="2.0.0"><name>WF_Mode</name><x>445</x><y>{y+62}</y><width>120</width><height>28</height><pv_name>pva://UNH:GEN:SSRF:FIT:MODE_RBV</pv_name></widget>
  <widget type="action_button" version="3.0.0">
    <name>WF_OpenFitter</name><x>600</x><y>{y+58}</y><width>190</width><height>34</height>
    <actions><action type="open_display"><file>genesis_ssrf_fitter.bob</file><target>window</target><description>Open detailed NMR fitter</description></action></actions>
    <text>Open NMR Fitter</text>
  </widget>

  <widget type="label" version="2.0.0"><name>WF_WaveState_L</name><x>40</x><y>{y+110}</y><width>120</width><height>24</height><text>Waveform stage</text></widget>
  <widget type="textupdate" version="2.0.0"><name>WF_WaveState</name><x>165</x><y>{y+107}</y><width>180</width><height>28</height><pv_name>pva://UNH:GEN:SSRF:SEQ:WAVEFORM_STAGE_STATE</pv_name></widget>
  <widget type="label" version="2.0.0"><name>WF_Points_L</name><x>375</x><y>{y+110}</y><width>90</width><height>24</height><text>ARB points</text></widget>
  <widget type="textupdate" version="2.0.0"><name>WF_Points</name><x>470</x><y>{y+107}</y><width>90</width><height>28</height><pv_name>pva://UNH:GEN:SSRF:WGEN:ARB_POINTS_RBV</pv_name></widget>
  <widget type="label" version="2.0.0"><name>WF_WReady_L</name><x>600</x><y>{y+110}</y><width>100</width><height>24</height><text>WGen ready</text></widget>
  <widget type="led" version="2.0.0"><name>WF_WReady</name><x>705</x><y>{y+107}</y><width>28</width><height>28</height><pv_name>pva://UNH:GEN:SSRF:WGEN:WAVEFORM_READY</pv_name><bit>0</bit><off_color><color red="210" green="130" blue="20" /></off_color><on_color><color red="30" green="160" blue="70" /></on_color></widget>
  <widget type="label" version="2.0.0"><name>WF_WOut_L</name><x>780</x><y>{y+110}</y><width>100</width><height>24</height><text>WGen output</text></widget>
  <widget type="textupdate" version="2.0.0"><name>WF_WOut</name><x>885</x><y>{y+107}</y><width>80</width><height>28</height><pv_name>pva://UNH:GEN:SSRF:WGEN:OUTPUT_RBV</pv_name></widget>

  <widget type="label" version="2.0.0"><name>WF_Safety_L</name><x>40</x><y>{y+155}</y><width>100</width><height>24</height><text>No-RF lock</text></widget>
  <widget type="led" version="2.0.0"><name>WF_Safety</name><x>155</x><y>{y+152}</y><width>28</width><height>28</height><pv_name>pva://UNH:GEN:SSRF:SEQ:NO_RF_LOCK</pv_name><bit>0</bit><off_color><color red="200" green="50" blue="50" /></off_color><on_color><color red="30" green="160" blue="70" /></on_color></widget>
  <widget type="label" version="2.0.0"><name>WF_NMR_L</name><x>215</x><y>{y+155}</y><width>90</width><height>24</height><text>NMR mask</text></widget>
  <widget type="textupdate" version="2.0.0"><name>WF_NMR</name><x>310</x><y>{y+152}</y><width>65</width><height>28</height><pv_name>pva://UNH:GEN:SSRF:SEQ:NMR_MASK</pv_name></widget>
  <widget type="label" version="2.0.0"><name>WF_SSRF_L</name><x>405</x><y>{y+155}</y><width>90</width><height>24</height><text>ssRF mask</text></widget>
  <widget type="textupdate" version="2.0.0"><name>WF_SSRF</name><x>500</x><y>{y+152}</y><width>65</width><height>28</height><pv_name>pva://UNH:GEN:SSRF:SEQ:SSRF_MASK</pv_name></widget>
  <widget type="label" version="2.0.0"><name>WF_RF_L</name><x>600</x><y>{y+155}</y><width>85</width><height>24</height><text>SMC RF/FM</text></widget>
  <widget type="textupdate" version="2.0.0"><name>WF_RF</name><x>690</x><y>{y+152}</y><width>60</width><height>28</height><pv_name>pva://UNH:GEN:SSRF:SEQ:SMC_RF</pv_name></widget>
  <widget type="textupdate" version="2.0.0"><name>WF_FM</name><x>755</x><y>{y+152}</y><width>60</width><height>28</height><pv_name>pva://UNH:GEN:SSRF:SEQ:SMC_FM</pv_name></widget>
  <widget type="label" version="2.0.0"><name>WF_Armed_L</name><x>850</x><y>{y+155}</y><width>65</width><height>24</height><text>Armed</text></widget>
  <widget type="led" version="2.0.0"><name>WF_Armed</name><x>920</x><y>{y+152}</y><width>28</width><height>28</height><pv_name>pva://UNH:GEN:SSRF:SEQ:EXECUTION_ARMED</pv_name><bit>0</bit><off_color><color red="210" green="130" blue="20" /></off_color><on_color><color red="30" green="160" blue="70" /></on_color></widget>

  <widget type="label" version="2.0.0">
    <name>WF_Instruction</name><x>40</x><y>{y+205}</y><width>1060</width><height>38</height>
    <text>ARM authorizes one transaction. BLAST/BURN then performs: fresh ssRF fit → WaveformPlan → 8192-point Keysight load → route ssRF → execute active profile → restore NMR.</text>
    <wrap_words>true</wrap_words>
  </widget>

  <widget type="bool_button" version="2.0.0"><name>WF_ARM</name><x>60</x><y>{y+255}</y><width>170</width><height>45</height><pv_name>pva://UNH:GEN:SSRF:SEQ:ARM_CMD</pv_name></widget>
  <widget type="bool_button" version="2.0.0"><name>WF_DISARM</name><x>250</x><y>{y+255}</y><width>150</width><height>45</height><pv_name>pva://UNH:GEN:SSRF:SEQ:DISARM_CMD</pv_name></widget>
  <widget type="bool_button" version="2.0.0"><name>WF_BLAST</name><x>430</x><y>{y+240}</y><width>280</width><height>75</height><pv_name>pva://UNH:GEN:SSRF:SEQ:START_CMD</pv_name></widget>
  <widget type="bool_button" version="2.0.0"><name>WF_ABORT</name><x>740</x><y>{y+255}</y><width>170</width><height>45</height><pv_name>pva://UNH:GEN:SSRF:SEQ:ABORT_CMD</pv_name></widget>

  <widget type="label" version="2.0.0"><name>WF_State_L</name><x>40</x><y>{y+340}</y><width>110</width><height>24</height><text>Current step</text></widget>
  <widget type="textupdate" version="2.0.0"><name>WF_State</name><x>155</x><y>{y+337}</y><width>320</width><height>30</height><pv_name>pva://UNH:GEN:SSRF:SEQ:EXECUTION_STATE</pv_name></widget>
  <widget type="label" version="2.0.0"><name>WF_Reason_L</name><x>40</x><y>{y+385}</y><width>110</width><height>24</height><text>What is happening</text></widget>
  <widget type="textupdate" version="2.0.0"><name>WF_Reason</name><x>155</x><y>{y+378}</y><width>940</width><height>55</height><pv_name>pva://UNH:GEN:SSRF:SEQ:EXECUTION_REASON</pv_name></widget>
'''

    text = text.replace("</display>", frag + "\n</display>", 1)
    gui.write_text(text)
    print(f"[PASS] workflow panel added to main GUI at y={y}")
else:
    print("[PASS] workflow panel already present")

# Commissioning launcher opens normal main GUI.
text = start.read_text()
text = text.replace('GUI="$ROOT/gui/genesis_ssrf_demo.bob"', 'GUI="$ROOT/gui/genesis_ssrf.bob"')
text = text.replace("gui/genesis_ssrf_demo.bob", "gui/genesis_ssrf.bob")
start.write_text(text)
print("[PASS] commissioning launcher opens main ssRF GUI")

# Robust stop helper.
stop.write_text(r'''#!/usr/bin/env bash
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

echo "============================================================"
echo " GENESIS SSRF PHYSICAL NO-RF WORKFLOW STOP"
echo "============================================================"

if [ -f /tmp/genesis_ssrf_demo_ioc.pid ]; then
    PID="$(cat /tmp/genesis_ssrf_demo_ioc.pid 2>/dev/null || true)"
    if [ -n "${PID:-}" ] && kill -0 "$PID" 2>/dev/null; then
        kill "$PID" 2>/dev/null || true
        echo "[PASS] workflow IOC stop requested: PID $PID"
    fi
    rm -f /tmp/genesis_ssrf_demo_ioc.pid
fi
pkill -f 'python .*ssrf_physical_no_rf_demo_ioc.py' 2>/dev/null || true
pkill -f 'python .*online_fitter\.epics_run' 2>/dev/null || true

ssh genesis-keysight   'screen -S genesis_ssrf_demo_wgen -X quit >/dev/null 2>&1 || true'   || true

sleep 3

echo
echo "===== VERIFY RUNTIMES ====="
ps aux | grep -E   'ssrf_physical_no_rf_demo_ioc|online_fitter\.epics_run|fitter_stage_runtime'   | grep -v grep   || echo "[PASS] no workflow/fitter runtime processes"

for port in 5074 8777 8778
do
    if lsof -nP -iTCP:${port} -sTCP:LISTEN >/dev/null 2>&1; then
        echo "[BUSY] ${port}"
        lsof -nP -iTCP:${port} -sTCP:LISTEN || true
    else
        echo "[PASS] port ${port} free"
    fi
done

ssh genesis-keysight   'pgrep -af "[p]ython .*master_ioc.py.*ssrf_physical_upload.*keysight_wavegen" || echo "[PASS] no physical upload IOC"'   || true

echo
echo "===== FINAL PHYSICAL STATE ====="
./scripts/genesisctl.sh physical-status || true
''')
stop.chmod(0o755)
print("[PASS] stop helper replaced with robust cleanup")

# Documentation.
d = docs.read_text()
d = d.replace("gui/genesis_ssrf_demo.bob", "gui/genesis_ssrf.bob")
if "BLAST / BURN" not in d:
    d += '''

## Consolidated operator workflow

The normal operator screen is `gui/genesis_ssrf.bob`.

After fitter setup/calibration, the deliberate execution pattern is:

```text
ARM
  -> BLAST / BURN
```

In the physical no-RF commissioning profile, BLAST/BURN performs a fresh
ssRF fit, adaptive WaveformPlan generation, 8192-point physical Keysight
ARB load, physical ssRF routing, no-RF dwell, and automatic NMR restore.

The main GUI shows every major execution state/reason and the relevant
fitter, waveform, Keysight, SMC, and switch readbacks.
'''
docs.write_text(d)

marker = "<!-- BEGIN 2026-09-27 FULL NO-RF E2E -->"
if marker not in physdoc.read_text():
    with physdoc.open("a") as f:
        f.write(r'''

<!-- BEGIN 2026-09-27 FULL NO-RF E2E -->

### 2026-09-27 full physical no-RF workflow — PASS

Verified end-to-end:

```text
offline NMR
-> equilibrium fitter
-> Update Constants
-> ssRF fit
-> adaptive WaveformPlan
-> 8192-point physical Keysight ARB load
-> one-shot ARM
-> NMR 0 -> 3
-> ssRF 3 -> 0
-> no-RF dwell
-> ssRF 0 -> 3
-> NMR 3 -> 0
-> COMPLETE / NMR_LIVE
```

Final state was Keysight output OFF, SMC RF/FM OFF, NMR mask 0,
ssRF mask 3, and the 8192-point adaptive waveform retained in ARB memory.

Both physical switch boxes visibly actuated and returned. No amplified-RF
or target burn was performed.

<!-- END 2026-09-27 FULL NO-RF E2E -->
''')
    print("[PASS] physical commissioning doc updated")

# Static architecture test.
testfile.write_text(r'''import unittest
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
GUI = ROOT / "gui" / "genesis_ssrf.bob"
BACKEND = ROOT / "tools" / "ssrf_physical_no_rf_demo_ioc.py"

class TestSsrfMainWorkflowGui(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gui = GUI.read_text()
        cls.backend = BACKEND.read_text()

    def test_xml(self):
        ET.parse(GUI)

    def test_integrated_panel(self):
        self.assertIn("<name>Workflow_BurnPanel</name>", self.gui)

    def test_operator_commands_use_sequencer_boundary(self):
        for pv in (
            "UNH:GEN:SSRF:SEQ:ARM_CMD",
            "UNH:GEN:SSRF:SEQ:DISARM_CMD",
            "UNH:GEN:SSRF:SEQ:START_CMD",
            "UNH:GEN:SSRF:SEQ:ABORT_CMD",
        ):
            self.assertIn(pv, self.gui)

    def test_commissioning_backend_exposes_start(self):
        self.assertIn('p["START_CMD"] = builder.boolOut(', self.backend)
        self.assertIn("def blast_workflow(self):", self.backend)

    def test_no_rf_authority_added(self):
        for forbidden in (".set_rf(", ".set_fm(", ".set_level_dbm("):
            self.assertNotIn(forbidden, self.backend)

if __name__ == "__main__":
    unittest.main()
''')
print("[PASS] main workflow static test created")
PY

echo
echo "============================================================"
echo " VALIDATION"
echo "============================================================"

python -m py_compile tools/ssrf_physical_no_rf_demo_ioc.py

python - <<'PY'
import xml.etree.ElementTree as ET
ET.parse("gui/genesis_ssrf.bob")
print("[PASS] main ssRF GUI XML")
PY

bash -n scripts/start_ssrf_physical_no_rf_demo.sh
bash -n scripts/stop_ssrf_physical_no_rf_demo.sh

python -m unittest -v   tests.test_ssrf_physical_no_rf_demo_static   tests.test_ssrf_main_workflow_gui

git diff --check

echo
echo "===== HARD NO-RF AUDIT ====="
if grep -nE   '\.set_rf\(|\.set_fm\(|\.set_level_dbm\(|_ca_put\(f"\{WGEN\}:OUTPUT_CMD"'   tools/ssrf_physical_no_rf_demo_ioc.py
then
    echo "[FAIL] prohibited no-RF authority found"
    exit 1
else
    echo "[PASS] no SMC RF/FM/level writes and no WaveGen output-on write"
fi

echo
echo "===== WORKTREE ====="
git status --short

echo
echo "============================================================"
echo " CONSOLIDATION COMPLETE"
echo "============================================================"
echo "Next:"
echo "  ./scripts/start_ssrf_physical_no_rf_demo.sh"
echo
echo "It should now open gui/genesis_ssrf.bob."
echo "After fitter setup/calibration: ARM -> BLAST / BURN."
