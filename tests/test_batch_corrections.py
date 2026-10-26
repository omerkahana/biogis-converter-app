"""Regression checks for deferred edits, matching cost, and plant metadata."""
from contextlib import contextmanager
from io import BytesIO
import unittest
from unittest.mock import call, patch

import pandas as pd

from test_butterflies import app, ui


class BatchCorrectionTests(unittest.TestCase):
    def setUp(self):
        self.plants = pd.DataFrame([{"שם המין": "כלנית מצויה", "סיווג": "מוגן"}])
        self.animals = pd.DataFrame([{"שם המין": "חסידה לבנה"}])
        self.occurrence = pd.DataFrame([{
            "species_heb": "קומלינה זקופה", "kingdom": "Plantae",
            "species": "Commelina erecta", "family": "Commelinaceae",
        }])

    def enrich(self, occurrence=None, plants=None, mapping=None):
        return app.enrich_occurrence(
            self.occurrence if occurrence is None else occurrence,
            self.plants if plants is None else plants,
            self.animals,
            pd.DataFrame() if mapping is None else mapping,
        )

    def editor(self):
        enriched = self.enrich()
        return app.prepare_batch_editor_df(app.make_review_table(enriched), enriched, "plants", 90)

    def test_repeated_observations_match_once_per_species_and_group(self):
        records = pd.DataFrame([
            {"species_heb": "שם חסר", "kingdom": "Plantae", "recordId": index}
            for index in range(30)
        ] + [{"species_heb": "שם חסר", "kingdom": "Animalia", "clazz": "Aves", "recordId": 30}])
        with patch.object(app, "find_best_match", wraps=app.find_best_match) as match:
            result = self.enrich(occurrence=records)
        self.assertEqual(match.call_count, 2)
        self.assertEqual(result.recordId.tolist(), list(range(31)))
        self.assertEqual(result.biogroup.tolist(), ["plants"] * 30 + ["vertebrates"])
        review = app.make_review_table(result)
        self.assertEqual(sorted(review.Records.tolist()), [1, 30])

    def test_editor_exposes_every_current_plant_index_field(self):
        editor = self.editor()
        self.assertTrue(set(app.PLANT_EDITOR_FIELDS).issubset(editor.columns))
        self.assertEqual(editor.iloc[0]["Family"], "Commelinaceae")
        self.assertEqual(editor.iloc[0]["Habitat"], "")
        self.assertEqual(editor.iloc[0]["Alien / invasive status"], "")

    def test_new_plant_metadata_survives_header_order_and_exports(self):
        editor = self.editor()
        editor.loc[0, "Save"] = True
        editor.loc[0, "Action"] = app.ACTION_ADD_NEW
        # The suggested correction is deliberately left selected: Add New must
        # use the manual name and metadata instead of the fuzzy suggestion.
        for field in app.PLANT_EDITOR_FIELDS:
            editor.loc[0, field] = f"value for {field}"
        editor.loc[0, "Classification"] = "פולש"
        editor.loc[0, "Alien / invasive status"] = "פולש אמריקאי"
        editor.loc[0, "Habitat"] = "בתי גידול מופרים"
        editor.loc[0, "Endemism"] = "לא אנדמי"

        class Worksheet:
            def __init__(self, headers):
                self.headers = headers
                self.rows = []

            def row_values(self, number):
                return self.headers

            def append_row(self, values, **kwargs):
                self.rows.append(dict(zip(self.headers, values)))

        plant_headers = ["שם המין", "שם מדעי", *app.PLANT_EDITOR_FIELDS.values()]
        plants = Worksheet(list(reversed(plant_headers)))
        mappings = Worksheet(["שם מקורי מ-BioGIS", "שם מתוקן באינדקס", "קבוצה", "מקור תיקון", "פעיל"])
        worksheets = {app.PLANTS_SHEET: plants, app.MAPPING_SHEET: mappings}
        with patch.object(app, "get_worksheet", side_effect=worksheets.__getitem__):
            count, errors = app.save_batch_corrections(editor, "plants", "test.csv")
        self.assertEqual((count, errors), (1, []))
        self.assertEqual(plants.rows[0]["שם המין"], "קומלינה זקופה")
        self.assertEqual(plants.rows[0]["שם מדעי"], "Commelina erecta")
        for field, header in app.PLANT_EDITOR_FIELDS.items():
            self.assertEqual(plants.rows[0][header], editor.loc[0, field])
        self.assertEqual(mappings.rows[0]["שם מתוקן באינדקס"], "קומלינה זקופה")

        new_index = pd.DataFrame(plants.rows)
        enriched = self.enrich(plants=new_index, mapping=pd.DataFrame(mappings.rows))
        gis = app.make_gis_occurrence_export(enriched, list(self.occurrence.columns))
        report = app.make_index_report(enriched, new_index, "plants", app.PLANT_REPORT_COLUMNS)
        self.assertEqual(gis.iloc[0].classification, "פולש")
        self.assertEqual(gis.iloc[0].endemism, "לא אנדמי")
        self.assertEqual(report.iloc[0]['בית גידול (עפ"י צמחיית ישראל ברשת)'], "בתי גידול מופרים")
        self.assertEqual(report.iloc[0]["צורת חיים"], editor.loc[0, "Life form"])

    def test_cleared_optional_fields_are_saved_as_blanks(self):
        for empty in [None, float("nan"), pd.NA]:
            with self.subTest(value=empty):
                editor = self.editor()
                editor.loc[0, "Save"] = True
                editor.loc[0, "Action"] = app.ACTION_ADD_NEW
                editor.loc[0, "Habitat"] = empty
                with patch.object(app, "append_row_by_headers") as write:
                    count, errors = app.save_batch_corrections(editor, "plants", "test.csv")
                self.assertEqual((count, errors), (1, []))
                self.assertEqual(write.call_args_list[0].args[1]['בית גידול (עפ"י צמחיית ישראל ברשת)'], "")

    def test_edits_are_in_form_and_write_only_when_submitted(self):
        enriched = self.enrich()
        review = app.make_review_table(enriched)
        events = []

        @contextmanager
        def form(**kwargs):
            events.append("enter")
            yield
            events.append("exit")

        def data_editor(frame, **kwargs):
            events.append("editor")
            frame.loc[0, "Save"] = True
            frame.loc[0, "Action"] = app.ACTION_ADD_NEW
            frame.loc[0, "Habitat"] = "בתי גידול מופרים"
            return frame

        for submitted in [False, True]:
            with self.subTest(submitted=submitted):
                events.clear()
                def submit(*args, **kwargs):
                    events.append("submit")
                    return submitted

                with patch.object(ui, "form", side_effect=form), \
                     patch.object(ui, "data_editor", side_effect=data_editor), \
                     patch.object(ui, "form_submit_button", side_effect=submit), \
                     patch.object(app, "append_row_by_headers") as write, \
                     patch.object(app, "clear_cache_and_rerun") as refresh:
                    app.render_batch_editor("Plants", "plants", review, enriched, self.plants, "test.csv", 90)
                self.assertEqual(events, ["enter", "editor", "submit", "exit"])
                self.assertEqual(write.call_count, 2 if submitted else 0)
                self.assertEqual(refresh.call_count, 1 if submitted else 0)

    def test_saving_refreshes_only_affected_google_sheet_reads(self):
        with patch.object(app.load_sheet_as_dataframe, "clear", create=True) as clear:
            app.clear_correction_cache("plants")
        self.assertEqual(clear.call_args_list, [call(app.MAPPING_SHEET), call(app.PLANTS_SHEET)])

    def test_csv_read_is_independent_of_upload_cursor(self):
        upload = BytesIO("שם מדעי (עברית),ממלכה\nקומלינה זקופה,Plantae\n".encode("utf-8-sig"))
        upload.seek(8)
        first = app.read_occurrence_csv(upload)
        upload.seek(0, 2)
        pd.testing.assert_frame_equal(first, app.read_occurrence_csv(upload))


if __name__ == "__main__":
    unittest.main()
