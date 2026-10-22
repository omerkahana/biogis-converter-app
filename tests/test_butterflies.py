"""Exercise the actual app pipeline with UI and Google clients isolated.

Run with: python -m unittest discover -s tests -v
No credentials or network connection are needed.
"""
import importlib.util
from io import BytesIO
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch
from zipfile import ZipFile

import pandas as pd


ui = MagicMock()
ui.cache_data.side_effect = lambda **kwargs: lambda function: function
module_spec = importlib.util.spec_from_file_location("biogis_app_under_test", Path(__file__).resolve().parents[1] / "app.py")
app = importlib.util.module_from_spec(module_spec)
with patch.dict(sys.modules, {
    "streamlit": ui,
    "gspread": MagicMock(),
    "google": MagicMock(),
    "google.oauth2": MagicMock(),
    "google.oauth2.service_account": MagicMock(),
}):
    module_spec.loader.exec_module(app)


class ButterflyPipelineTests(unittest.TestCase):
    def setUp(self):
        self.plants = pd.DataFrame([{"שם המין": "כלנית מצויה", "סיווג": "מוגן", "אנדמיות": "לא"}])
        self.animals = pd.DataFrame([{"שם המין": "חסידה לבנה", "סטטוס שימור אזורי": "עתידו בסכנה"}])
        self.butterflies = pd.DataFrame([
            {"שם המין": "שלגן חרמוני", "שם מדעי": "Parnassius mnemosyne", "שם עברי קודם": "צבעוני החרמון", "סטטוס שימור ים-תיכוני": "לא בסיכון"},
            {"שם המין": "כתמית פנינים", "שם מדעי": "Issoria lathonia", "שם עברי קודם": "נִימְפִית הַפְּנִינִים", "סטטוס שימור ים-תיכוני": "לא בסיכון"},
        ]).fillna("")

    def enrich(self, records, mapping=None):
        return app.enrich_occurrence(pd.DataFrame(records), self.plants, self.animals,
                                     pd.DataFrame() if mapping is None else mapping,
                                     self.butterflies)

    def test_exact_name_without_taxonomy_and_aliases(self):
        result = self.enrich([{"species_heb": name} for name in ["שלגן חרמוני", "צבעוני החרמון", "נימפית הפנינים"]])
        self.assertEqual(result.biogroup.tolist(), ["butterflies"] * 3)
        self.assertEqual(result.species_heb_corrected.tolist(), ["שלגן חרמוני", "שלגן חרמוני", "כתמית פנינים"])
        self.assertEqual(result.match_status.tolist(), ["exact_index"] * 3)
        report = app.make_index_report(result, self.butterflies, "butterflies", app.BUTTERFLY_REPORT_COLUMNS)
        self.assertEqual(len(report), 2)
        self.assertEqual(set(report["שם מדעי"]), {"Parnassius mnemosyne", "Issoria lathonia"})

    def test_legacy_invertebrate_mapping_resolves_to_new_index(self):
        mapping = pd.DataFrame([{"שם מקורי מ-BioGIS": "שם מאושר ישן", "שם מתוקן באינדקס": "צבעוני החרמון", "קבוצה": "invertebrates", "פעיל": True}])
        result = self.enrich([{"species_heb": "שם מאושר ישן", "kingdom": "Animalia"}], mapping)
        self.assertEqual(result.iloc[0].biogroup, "butterflies")
        self.assertEqual(result.iloc[0].species_heb_corrected, "שלגן חרמוני")
        self.assertEqual(result.iloc[0].match_status, "mapped")

    def test_explicit_butterfly_mapping_group(self):
        mapping = pd.DataFrame([{"שם מקורי מ-BioGIS": "שם אחר", "שם מתוקן באינדקס": "שלגן חרמוני", "קבוצה": "פרפרים"}])
        result = self.enrich([{"species_heb": "שם אחר"}], mapping)
        self.assertEqual(result.iloc[0].biogroup, "butterflies")

    def test_approved_other_groups_keep_priority(self):
        mapping = pd.DataFrame([{"שם מקורי מ-BioGIS": "כלנית מצויה", "שם מתוקן באינדקס": "כלנית מצויה", "קבוצה": "invertebrates"}])
        result = self.enrich([{"species_heb": "כלנית מצויה"}], mapping)
        self.assertEqual(result.iloc[0].biogroup, "invertebrates")

    def test_unknown_butterfly_needs_review_not_automatic_fuzzy_match(self):
        result = self.enrich([{"species_heb": "שלגן חרמוני חדש", "group": "פרפרים", "kingdom": "Animalia"}])
        self.assertEqual(result.iloc[0].match_status, "needs_review")
        self.assertEqual(result.iloc[0].species_heb_corrected, "")
        review = app.make_review_table(result)
        editor = app.prepare_batch_editor_df(review, result, "butterflies", 90)
        self.assertIn("Mediterranean status", editor.columns)
        self.assertIn("Family", editor.columns)
        report = app.make_index_report(result, self.butterflies, "butterflies", app.BUTTERFLY_REPORT_COLUMNS)
        self.assertTrue(report.empty)

    def test_mediterranean_assessment_is_never_israeli_or_global(self):
        result = self.enrich([{"species_heb": "שלגן חרמוני"}])
        self.assertEqual(result.iloc[0].classification, "")
        self.assertEqual(result.iloc[0].mediterranean_conservation_status, "לא בסיכון")
        report = app.make_index_report(result, self.butterflies, "butterflies", app.BUTTERFLY_REPORT_COLUMNS)
        self.assertEqual(report.iloc[0]["סטטוס שימור אזורי"], "")
        self.assertEqual(report.iloc[0]["סטטוס שימור עולמי"], "")
        gis = app.make_gis_occurrence_export(result, ["species_heb"])
        self.assertEqual(gis.iloc[0].mediterranean_conservation_status, "לא בסיכון")
        self.assertEqual(gis.iloc[0].species_heb, "שלגן חרמוני")

    def test_other_groups_and_existing_statuses_are_preserved(self):
        result = self.enrich([
            {"species_heb": "כלנית מצויה"}, {"species_heb": "חסידה לבנה"},
            {"species_heb": "חיפושית", "kingdom": "Animalia", "clazz": "Insecta"},
            {"species_heb": "עש", "kingdom": "Animalia", "orderr": "Lepidoptera"},
            {"species_heb": "פטרייה", "kingdom": "Fungi"}, {"species_heb": "שם לא ידוע"},
        ])
        self.assertEqual(result.biogroup.tolist(), ["plants", "vertebrates", "invertebrates", "invertebrates", "fungi", "unknown"])
        self.assertEqual(result.classification.tolist()[:2], ["מוגן", "עתידו בסכנה"])
        self.assertEqual(result.iloc[0].endemism, "לא")
        self.assertEqual(result.mediterranean_conservation_status.tolist(), [""] * 6)

    def test_butterflies_are_not_duplicated_in_invertebrate_report(self):
        result = self.enrich([{"species_heb": "שלגן חרמוני", "kingdom": "Animalia"}, {"species_heb": "חיפושית", "kingdom": "Animalia"}])
        report = app.make_non_indexed_report(result, "invertebrates")
        self.assertEqual(len(report), 1)
        self.assertEqual(report.iloc[0]["שם המין"], "חיפושית")

    def test_hebrew_csv_headers_and_output_encodings(self):
        source = BytesIO("שם מדעי (עברית),ממלכה,קבוצה\nשלגן חרמוני,Animalia,פרפרים\n".encode("utf-8-sig"))
        occurrence = app.read_occurrence_csv(source)
        result = app.enrich_occurrence(occurrence, self.plants, self.animals, pd.DataFrame(), self.butterflies)
        report = app.make_index_report(result, self.butterflies, "butterflies", app.BUTTERFLY_REPORT_COLUMNS)
        encoded = app.to_csv_bytes(report)
        self.assertTrue(encoded.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(pd.read_csv(BytesIO(encoded)).iloc[0]["שם מדעי"], "Parnassius mnemosyne")
        workbook = app.to_excel_bytes({"butterflies_report": report})
        with ZipFile(BytesIO(workbook)) as archive:
            self.assertIn(b"butterflies_report", archive.read("xl/workbook.xml"))

    def test_ambiguous_and_placeholder_aliases_are_ignored(self):
        frame = pd.DataFrame([{"שם המין": "מין א", "שם עברי קודם": "כינוי"}, {"שם המין": "מין ב", "שם עברי קודם": "כינוי"}, {"שם המין": "מין ג", "שם עברי קודם": "ללא שם עברי קודם"}])
        lookup = app.build_index_lookup(frame)
        self.assertNotIn("כינוי", lookup)
        self.assertNotIn("ללא שם עברי קודם", lookup)

    def test_add_new_species_writes_butterfly_sheet_and_mapping(self):
        frame = pd.DataFrame([{"Save": True, "Action": app.ACTION_ADD_NEW, "BioGIS name": "פרפר חדש", "New Hebrew name": "פרפר חדש", "Scientific name": "Genus species", "Family": "משפחה", "Mediterranean status": "חסר מידע"}])
        with patch.object(app, "append_row_by_headers") as write:
            count, errors = app.save_batch_corrections(frame, "butterflies", "test.csv")
        self.assertEqual((count, errors), (1, []))
        sheet, values = write.call_args_list[0].args
        self.assertEqual(sheet, app.BUTTERFLIES_SHEET)
        self.assertEqual(values["סטטוס שימור ים-תיכוני"], "חסר מידע")
        self.assertEqual(values["סטטוס שימור אזורי"], "")
        self.assertEqual(values["סטטוס שימור עולמי"], "")
        self.assertEqual(write.call_args_list[1].args[1]["קבוצה"], "butterflies")

    def test_main_upload_preview_and_all_downloads(self):
        ui.reset_mock()
        ui.columns.side_effect = lambda count: [MagicMock() for _ in range(count)]
        ui.tabs.side_effect = lambda labels: [MagicMock() for _ in labels]
        source = BytesIO("species_heb\nשלגן חרמוני\n".encode("utf-8-sig"))
        source.name = "test.csv"
        ui.file_uploader.return_value = source
        frames = {app.PLANTS_SHEET: self.plants, app.ANIMALS_SHEET: self.animals, app.BUTTERFLIES_SHEET: self.butterflies}
        with patch.object(app, "load_sheet_as_dataframe", side_effect=frames.__getitem__), patch.object(app, "load_name_mapping", return_value=pd.DataFrame()):
            app.main()
        ui.exception.assert_not_called()
        downloads = {call.kwargs['file_name']: call.kwargs['data'] for call in ui.download_button.call_args_list}
        self.assertIn("butterflies_report.csv", downloads)
        with ZipFile(BytesIO(downloads['biogis_outputs.xlsx'])) as archive:
            self.assertIn(b"butterflies_report", archive.read('xl/workbook.xml'))


if __name__ == "__main__":
    unittest.main()
