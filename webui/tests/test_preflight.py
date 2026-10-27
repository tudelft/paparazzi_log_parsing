import unittest

import db
from . import test_historical
from .test_simulation import parse_simulation_fixture


class ParserPreflightTests(unittest.TestCase):
    def test_full_labels_crew_and_original_events_are_retained(self):
        airframe = """<airframe>
<checklist>
  <item name="pic" type="text">Enter the PIC</item>
  <item name="pac" type="text">Enter the PAC</item>
  <item name="gcs" type="text">Enter the GCS operator</item>
  <item name="goal" type="text">Goal of the flight</item>
  <item name="basic law">Location, airspace &amp; weather</item>
  <item name="heading">Check heading</item>
</checklist>
</airframe>"""
        bodies = [
            "pic: Pilot A", "pac: Pilot B", "gcs: Operator C", "goal: Test flight",
            "basic law: done", "heading: done", "heading: rechecked", "unknown: retained",
        ]
        doc = parse_simulation_fixture(
            {5: ("Aircraft", airframe)},
            [f'{i + 1} 5 INFO_MSG "[PFC] {body}"\n' for i, body in enumerate(bodies)]
            + ['9 5 INFO_MSG "Ordinary message"\n'],
        )
        ac = doc["aircraft"][0]
        self.assertEqual(ac["mission"], {
            "pic": "Pilot A", "pac": "Pilot B", "gcs": "Operator C", "goal": "Test flight",
        })
        checks = ac["preflight_checks"]
        self.assertEqual([m["text"] for m in checks], bodies)
        self.assertEqual([m["time_s"] for m in checks], list(range(1, 9)))
        self.assertTrue(all(m["utc"] for m in checks))
        self.assertEqual(checks[0]["checklist_label"], "Enter the PIC")
        self.assertEqual(checks[0]["checklist_value"], "Pilot A")
        self.assertEqual(checks[4]["checklist_label"], "Location, airspace & weather")
        self.assertEqual(checks[6]["checklist_value"], "rechecked")
        self.assertNotIn("checklist_label", checks[7])
        self.assertEqual([m["text"] for m in ac["info_messages"]], ["Ordinary message"])

    def test_labels_are_aircraft_scoped_and_missing_definitions_remain_raw(self):
        doc = parse_simulation_fixture(
            {5: ("First", '<airframe>\n<checklist><item name="check">First label</item></checklist>\n</airframe>'),
             6: ("Second", '<airframe>\n<checklist><item name="check">Second label</item></checklist>\n</airframe>'),
             7: ("No airframe", "")},
            [f'1 {ac} INFO_MSG "[PFC] check: done"\n' for ac in (5, 6, 7)],
        )
        aircraft = {a["ac_id"]: a for a in doc["aircraft"]}
        self.assertEqual(aircraft[5]["preflight_checks"][0]["checklist_label"], "First label")
        self.assertEqual(aircraft[6]["preflight_checks"][0]["checklist_label"], "Second label")
        self.assertEqual(aircraft[7]["preflight_checks"][0]["text"], "check: done")
        self.assertNotIn("checklist_label", aircraft[7]["preflight_checks"][0])

    def test_xml_text_comments_cdata_and_exact_keys(self):
        airframe = """<airframe>
<item name="outside">Not a checklist definition</item>
<checklist>
<!-- <item name="commented">Not active</item> -->
<item name="empty"/>
<item name='check'>Check
  <!-- not part of label --> heading &#38; <![CDATA[<airspeed>]]></item>
<item name="check:long">Longer key</item>
</checklist>
</airframe>"""
        bodies = ["check: done", "check:long: yes", "check", "checker: done",
                  "commented: done", "outside: done", "empty: done"]
        ac = parse_simulation_fixture(
            {5: ("Aircraft", airframe)},
            [f'{i + 1} 5 INFO_MSG "[PFC] {body}"\n' for i, body in enumerate(bodies)],
        )["aircraft"][0]
        checks = ac["preflight_checks"]
        self.assertEqual(checks[0]["checklist_label"], "Check heading & <airspeed>")
        self.assertEqual(checks[1]["checklist_label"], "Longer key")
        self.assertEqual(checks[1]["checklist_value"], "yes")
        self.assertEqual(checks[2]["checklist_value"], "")
        for check in checks[3:]:
            self.assertNotIn("checklist_label", check)


class StoredPreflightTests(test_historical.AppTests):
    def test_checklist_fields_survive_storage_and_both_apis(self):
        doc = parse_simulation_fixture(
            {5: ("Aircraft", '<airframe>\n<checklist><item name="pic">Enter the PIC</item></checklist>\n</airframe>')},
            ['1 5 INFO_MSG "[PFC] pic: Pilot A"\n'],
        )
        log_id = self.con.execute(
            "INSERT INTO logs(name,sha256,data_path,status,uploaded_at,uploaded_by,project_id)"
            " VALUES ('fixture','fixture','fixture','done','now',2,1)"
        ).lastrowid
        db.store_parsed(self.con, log_id, doc, None)
        self.con.commit()
        flight_id = self.con.execute(
            "SELECT f.id FROM flights f JOIN aircraft a ON a.id=f.aircraft_id WHERE a.log_id=?",
            (log_id,),
        ).fetchone()[0]
        expected = doc["aircraft"][0]["preflight_checks"]
        log = self.client.get(f"/api/logs/{log_id}")
        flight = self.client.get(f"/api/flights/{flight_id}/detail")
        self.assertEqual(log.status_code, 200)
        self.assertEqual(flight.status_code, 200)
        self.assertEqual(log.json["aircraft"][0]["preflight_checks"], expected)
        self.assertEqual(flight.json["preflight_checks"], expected)
        self.sign_in(4)
        self.assertEqual(self.client.get(f"/api/logs/{log_id}").status_code, 404)
        self.assertEqual(self.client.get(f"/api/flights/{flight_id}/detail").status_code, 404)
