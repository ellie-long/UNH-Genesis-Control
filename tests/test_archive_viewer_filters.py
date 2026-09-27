import unittest

from archive.viewer_filters import (
    MICROWAVE_PRESETS,
    filter_pvs,
    microwave_group_for_pv,
    normalized_archive_key,
    resolve_microwave_preset,
    subsystem_for_pv,
)


class GenesisArchiveViewerFilters(unittest.TestCase):

    def setUp(self):
        # These deliberately use the viewer's current display-name
        # convention. The helper normalizes them back to archive keys.
        self.pvs = [
            "UNH:GEN_MW_PS_VMEAS",
            "UNH:GEN_MW_PS_IMEAS",
            "UNH:GEN_MW_PS_PMEAS",
            "UNH:GEN_MW_PS_OUTPUT_RBV",

            "UNH:GEN_MW_SRC_MW_OutFI",
            "UNH:GEN_MW_SRC_MW_PWRI",
            "UNH:GEN_MW_SRC_MW_RF",
            "UNH:GEN_MW_SRC_MW_Lock",

            "UNH:GEN_MW_SUP_OutputFrequencyRBV",
            "UNH:GEN_MW_SUP_RFEnable",
            "UNH:GEN_MW_SUP_RFPermissive",
            "UNH:GEN_MW_SUP_SystemConnected",

            "UNH:GEN_MW_ROBOT_X_CM",
            "UNH:GEN_MW_ROBOT_Y_CM",
            "UNH:GEN_MW_ROBOT_X_BUSY",
            "UNH:GEN_MW_ROBOT_Y_BUSY",

            # Representative ssRF records.
            "UNH:GEN_SSRF_GEN_FREQ_RBV",
            "UNH:GEN_SSRF_GEN_FM_RBV",
            "UNH:GEN_SSRF_WGEN_LAST_OK",

            "TGT:MEOP:Voltage1_VC",
        ]

    def test_normalization_matches_archive_filename_style(self):
        self.assertEqual(
            normalized_archive_key(
                "UNH:GEN_MW_SRC_MW_FI"
            ),
            "UNH_GEN_MW_SRC_MW_FI",
        )

    def test_subsystem_classification(self):
        self.assertEqual(
            subsystem_for_pv(
                "UNH:GEN_MW_SRC_MW_FI"
            ),
            "Microwave",
        )

        self.assertEqual(
            subsystem_for_pv(
                "UNH:GEN_SSRF_GEN_FREQ_RBV"
            ),
            "ssRF",
        )

        self.assertEqual(
            subsystem_for_pv(
                "TGT:MEOP:Voltage1_VC"
            ),
            "Other",
        )

    def test_ssrf_filter_preserves_existing_ssrf_pvs(self):
        expected = [
            pv
            for pv in self.pvs
            if "UNH:GEN_SSRF_" in pv
        ]

        actual = filter_pvs(
            self.pvs,
            subsystem="ssRF",
        )

        self.assertEqual(
            actual,
            expected,
        )

    def test_all_genesis_preserves_complete_list(self):
        self.assertEqual(
            filter_pvs(
                self.pvs,
                subsystem="All Genesis",
            ),
            self.pvs,
        )

    def test_microwave_group_classification(self):
        self.assertEqual(
            microwave_group_for_pv(
                "UNH:GEN_MW_PS_VMEAS"
            ),
            "UCA / PS",
        )

        self.assertEqual(
            microwave_group_for_pv(
                "UNH:GEN_MW_SRC_MW_FI"
            ),
            "Vaunix / SRC",
        )

        self.assertEqual(
            microwave_group_for_pv(
                "UNH:GEN_MW_SUP_RFEnable"
            ),
            "Supervisor / SUP",
        )

        self.assertEqual(
            microwave_group_for_pv(
                "UNH:GEN_MW_ROBOT_X_CM"
            ),
            "Robot",
        )

    def test_microwave_group_filter(self):
        actual = filter_pvs(
            self.pvs,
            subsystem="Microwave",
            microwave_group="Robot",
        )

        self.assertTrue(actual)

        for pv in actual:
            self.assertEqual(
                microwave_group_for_pv(pv),
                "Robot",
            )

    def test_frequency_rf_preset_only_resolves_existing_pvs(self):
        result = resolve_microwave_preset(
            self.pvs,
            "Microwave - Frequency",
        )

        self.assertEqual(
            len(result),
            2,
        )

        self.assertLessEqual(
            len(result),
            4,
        )

        for pv in result:
            self.assertIn(
                pv,
                self.pvs,
            )

            self.assertEqual(
                subsystem_for_pv(pv),
                "Microwave",
            )

    def test_manual_selection_has_no_forced_preset(self):
        self.assertEqual(
            resolve_microwave_preset(
                self.pvs,
                "Manual selection",
            ),
            [],
        )

    def test_all_presets_limited_to_four_traces(self):
        for name in MICROWAVE_PRESETS:
            result = resolve_microwave_preset(
                self.pvs,
                name,
            )

            self.assertLessEqual(
                len(result),
                4,
            )


if __name__ == "__main__":
    unittest.main()
