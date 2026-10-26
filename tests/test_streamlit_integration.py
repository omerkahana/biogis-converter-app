"""Exercise real Streamlit forms and caches without Google credentials."""
from importlib.util import find_spec
from io import BytesIO
import unittest
from unittest.mock import patch

import pandas as pd


@unittest.skipUnless(find_spec("streamlit"), "Install requirements.txt for Streamlit integration checks")
class StreamlitIntegrationTests(unittest.TestCase):
    def setUp(self):
        import app
        self.app = app
        self.app.enrich_occurrence.clear()
        self.app.read_occurrence_bytes.clear()
        self.app.to_excel_bytes.clear()
        self.app.to_csv_bytes.clear()

    def test_real_cache_reuses_processing_and_refreshes_when_inputs_change(self):
        app = self.app
        occurrence = pd.DataFrame([{"species_heb": "קומלינה זקופה", "kingdom": "Plantae"}])
        plants = pd.DataFrame([{"שם המין": "כלנית מצויה", "סיווג": "מוגן"}])
        animals = pd.DataFrame([{"שם המין": "חסידה לבנה"}])
        with patch.object(app, "find_best_match", wraps=app.find_best_match) as match:
            first = app.enrich_occurrence(occurrence, plants, animals, pd.DataFrame())
            second = app.enrich_occurrence(occurrence.copy(), plants.copy(), animals.copy(), pd.DataFrame())
        self.assertEqual(match.call_count, 1)
        pd.testing.assert_frame_equal(first, second)
        plants = pd.concat([plants, pd.DataFrame([{"שם המין": "קומלינה זקופה", "סיווג": "פולש"}])], ignore_index=True)
        refreshed = app.enrich_occurrence(occurrence, plants, animals, pd.DataFrame())
        self.assertEqual(refreshed.iloc[0].match_status, "exact_index")
        self.assertEqual(refreshed.iloc[0].classification, "פולש")
        plants.loc[1, "סיווג"] = "סיווג מעודכן"
        refreshed = app.enrich_occurrence(occurrence, plants, animals, pd.DataFrame())
        self.assertEqual(refreshed.iloc[0].classification, "סיווג מעודכן")

    def test_real_csv_and_excel_caches_skip_repeated_conversions(self):
        app = self.app
        upload = BytesIO("species_heb\nקומלינה זקופה\n".encode("utf-8-sig"))
        with patch.object(app.pd, "read_csv", wraps=pd.read_csv) as read:
            frame = app.read_occurrence_csv(upload)
            upload.seek(0, 2)
            pd.testing.assert_frame_equal(frame, app.read_occurrence_csv(upload))
        self.assertEqual(read.call_count, 1)
        with patch.object(app.pd, "ExcelWriter", wraps=pd.ExcelWriter) as writer:
            first = app.to_excel_bytes({"test": frame})
            self.assertEqual(first, app.to_excel_bytes({"test": frame.copy()}))
        self.assertEqual(writer.call_count, 1)

    def test_large_table_cache_detects_changes_outside_default_sample(self):
        frame = pd.DataFrame({"value": ["original"] * 50001})
        sampled = set(frame.sample(n=10000, random_state=0).index)
        changed_row = next(index for index in frame.index if index not in sampled)
        original = self.app.to_csv_bytes(frame)
        frame.loc[changed_row, "value"] = "changed"
        updated = self.app.to_csv_bytes(frame)
        self.assertNotEqual(original, updated)
        self.assertEqual(pd.read_csv(BytesIO(updated)).loc[changed_row, "value"], "changed")

    def test_main_renders_real_form_and_all_outputs_without_google_writes(self):
        from streamlit.testing.v1 import AppTest

        script = '''
from io import BytesIO
from unittest.mock import patch
import pandas as pd
import streamlit as st
import app

plants = pd.DataFrame([{"שם המין": "כלנית מצויה", "סיווג": "מוגן"}])
animals = pd.DataFrame([{"שם המין": "חסידה לבנה"}])
butterflies = pd.DataFrame([{"שם המין": "שלגן חרמוני"}])
frames = {app.PLANTS_SHEET: plants, app.ANIMALS_SHEET: animals, app.BUTTERFLIES_SHEET: butterflies}
upload = BytesIO("species_heb,kingdom\\nקומלינה זקופה,Plantae\\n".encode("utf-8-sig"))
upload.name = "test.csv"
with patch.object(app.st, "file_uploader", return_value=upload), \\
     patch.object(app, "load_sheet_as_dataframe", side_effect=frames.__getitem__), \\
     patch.object(app, "load_name_mapping", return_value=pd.DataFrame()), \\
     patch.object(app, "append_row_by_headers") as write:
    app.main()
    st.session_state["test_write_count"] = write.call_count
'''
        # AppTest owns the normal Streamlit runtime; only data sources and
        # writes are isolated. A rerun must preserve a valid editable form.
        view = AppTest.from_string(script, default_timeout=15).run()
        self.assertEqual(len(view.exception), 0)
        self.assertEqual(view.session_state["test_write_count"], 0)
        self.assertEqual(len(view.get("form")), 1)
        self.assertEqual(view.button[0].label, "Save checked plants corrections")
        self.assertEqual(len(view.get("download_button")), 4)
        editors = [element for element in view.dataframe if element.proto.form_id]
        self.assertEqual(len(editors), 1)
        self.assertEqual(editors[0].proto.form_id, "batch_form_plants")
        self.assertIn("Habitat", editors[0].value.columns)
        self.assertIn("Alien / invasive status", editors[0].value.columns)
        view.button[0].click().run()
        self.assertEqual(len(view.exception), 0)
        self.assertEqual(view.session_state["test_write_count"], 0)


if __name__ == "__main__":
    unittest.main()
