import json
import subprocess
import tempfile
import unittest
from pathlib import Path


PARSER = Path(__file__).resolve().parents[2] / "cpp/build/paparazzi_log_parsing"
NPS_MESSAGES = (
    "NPS_RATE_ATTITUDE", "NPS_POS_LLH", "NPS_SPEED_POS",
    "NPS_GYRO_BIAS", "NPS_SENSORS_SCALED", "NPS_WIND",
)


def parse_simulation_fixture(configs, lines=(), protocol=""):
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "fixture.data"
        path.write_text("".join(lines) + "".join(
            f"10 {ac} ROTORCRAFT_STATUS 0 1\n20 {ac} ROTORCRAFT_STATUS 0 0\n"
            for ac in configs
        ))
        header = '<configuration time_of_day="1751457600">\n'
        for ac, (name, airframe) in configs.items():
            header += f'<aircraft ac_id="{ac}" name="{name}">\n{airframe}\n</aircraft>\n'
        header += (
            '<protocol>\n<msg_class name="telemetry">\n'
            '<message name="ROTORCRAFT_STATUS">\n'
            '<field name="ap_mode"/>\n<field name="ap_in_flight"/>\n</message>\n'
            + protocol + '</msg_class>\n</protocol>\n</configuration>\n'
        )
        path.with_suffix(".log").write_text(header)
        result = subprocess.run([str(PARSER), str(path)], capture_output=True, text=True, check=True)
        return json.loads(result.stdout)


class ParserSimulationTests(unittest.TestCase):
    def test_each_known_simulator_message_is_evidence_without_configuration(self):
        for name in NPS_MESSAGES:
            with self.subTest(message=name):
                doc = parse_simulation_fixture({5: ("Aircraft", "")}, [f"1 5 {name} 0 0 0\n"])
                self.assertEqual(doc["parser"]["version"], "1.7.8")
                self.assertEqual(doc["aircraft"][0]["simulation"], {
                    "detected": True, "evidence": [f"telemetry:{name}"],
                })

    def test_evidence_is_unique_and_scoped_to_recorded_aircraft(self):
        doc = parse_simulation_fixture(
            {5: ("Simulator", ""), 6: ("Physical aircraft", "")},
            ["1 5 NPS_WIND 0 0 0\n", "2 5 NPS_WIND 0 0 0\n", "3 5 NPS_POS_LLH 0 0 0\n"],
        )
        aircraft = {a["ac_id"]: a for a in doc["aircraft"]}
        self.assertEqual(aircraft[5]["simulation"], {
            "detected": True, "evidence": ["telemetry:NPS_POS_LLH", "telemetry:NPS_WIND"],
        })
        self.assertEqual(aircraft[6]["simulation"], {"detected": False, "evidence": []})
        for ac in aircraft.values():
            self.assertEqual(ac["summary"]["num_flights"], 1)
            self.assertEqual(ac["flights"][0]["duration_s"], 10)

    def test_simulation_only_configuration_is_sufficient(self):
        xml = '<airframe name="Test">\n<firmware name="rotorcraft">\n<target NAME="nps" BOARD="pc"/>\n</firmware>\n</airframe>'
        doc = parse_simulation_fixture({5: ("Aircraft", xml)})
        self.assertEqual(doc["aircraft"][0]["simulation"], {
            "detected": True, "evidence": ["configuration:nps-only-pc"],
        })

    def test_configuration_and_telemetry_reasons_are_both_retained(self):
        xml = '<airframe>\n<target name="nps" board="pc"/>\n</airframe>'
        doc = parse_simulation_fixture({5: ("Aircraft", xml)}, ["1 5 NPS_WIND 0 0 0\n"])
        self.assertEqual(doc["aircraft"][0]["simulation"]["evidence"], [
            "telemetry:NPS_WIND", "configuration:nps-only-pc",
        ])

    def test_configuration_is_scoped_to_its_aircraft(self):
        xml = '<airframe>\n<target name="nps" board="pc"/>\n</airframe>'
        doc = parse_simulation_fixture({5: ("Simulator", xml), 6: ("Physical aircraft", "")})
        self.assertEqual([a["simulation"]["detected"] for a in doc["aircraft"]], [True, False])

    def test_names_definitions_and_unknown_nps_messages_are_not_evidence(self):
        doc = parse_simulation_fixture(
            {5: ("RW3C_DePonti_Simulation", "")}, ["1 5 NPS_UNKNOWN 0\n"],
            '<message name="NPS_WIND">\n<field name="wind"/>\n</message>\n',
        )
        self.assertEqual(doc["aircraft"][0]["simulation"], {"detected": False, "evidence": []})

    def test_mixed_hardware_and_commented_targets_do_not_trigger_simulation(self):
        cases = [
            '<target name="nps" board="pc"/><target name="ap" board="cube"/>',
            '<target name="ap" board="cube"/><target name="nps" board="pc"/>',
            '<!-- <target name="nps" board="pc"/> -->',
            '<description>Simulation with &lt;target name="nps" board="pc"/&gt;</description>',
            '<target name="nps" board="cube"/>',
            '<target name="nps"/>',
        ]
        for targets in cases:
            with self.subTest(targets=targets):
                doc = parse_simulation_fixture({5: ("Simulation", f"<airframe>\n{targets}\n</airframe>")})
                self.assertEqual(doc["aircraft"][0]["simulation"], {"detected": False, "evidence": []})

    def test_commented_hardware_target_does_not_block_simulation_only_configuration(self):
        xml = '<airframe>\n<!-- <target name="ap" board="cube"/> -->\n<target name="nps" board="pc"/>\n</airframe>'
        doc = parse_simulation_fixture({5: ("Aircraft", xml)})
        self.assertTrue(doc["aircraft"][0]["simulation"]["detected"])
