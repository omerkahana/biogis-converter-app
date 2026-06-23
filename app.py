from datetime import datetime
from io import BytesIO
from pathlib import Path
from difflib import SequenceMatcher

import gspread
import pandas as pd
import streamlit as st
from google.oauth2.service_account import Credentials

SHEET_ID = "1b6qgqHh6g3VifXtxDCG-g3zGSpqt8mkktJEruo0qpjQ"

PLANTS_SHEET = "plants_index"
ANIMALS_SHEET = "animals_index"
MAPPING_SHEET = "name_mapping"
UNMATCHED_SHEET = "unmatched_log"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

VERTEBRATE_CLASSES = {
    "mammalia",
    "aves",
    "reptilia",
    "amphibia",
    "actinopterygii",
    "chondrichthyes",
    "sarcopterygii",
    "elasmobranchii",
    "pisces",
    "יונקים",
    "עופות",
    "זוחלים",
    "דו-חיים",
    "דו חיים",
    "דגים",
    "דגי גרם",
    "דגי סחוס",
}

PLANT_REPORT_COLUMNS = [
    "שם המין",
    "שם מדעי",
    "משפחה",
    "טיפוס התפוצה",
    "שכיחות",
    "סיווג",
]

VERTEBRATE_REPORT_COLUMNS = [
    "שם המין",
    "שם מדעי",
    "מחלקה",
    "סטטוס שימור אזורי",
    "סטטוס שימור עולמי",
]

STATUS_LABELS = {
    "mapped": "תוקן לפי מילון תיקונים",
    "exact_index": "התאמה מדויקת באינדקס",
    "needs_review": "דורש בדיקה ידנית",
    "not_indexed_group": "קבוצה שאינה באינדקס",
    "unknown_group": "קבוצה לא מזוהה",
    "no_hebrew_name": "חסר שם עברי",
}


# -----------------------------------------------------------------------------
# Google Sheets connection
# -----------------------------------------------------------------------------


def get_google_client():
    """Create an authorized Google Sheets client."""
    if "gcp_service_account" in st.secrets:
        service_account_info = dict(st.secrets["gcp_service_account"])
        creds = Credentials.from_service_account_info(
            service_account_info,
            scopes=SCOPES,
        )
        return gspread.authorize(creds)

    local_key = Path("service_account.json")
    if local_key.exists():
        creds = Credentials.from_service_account_file(str(local_key), scopes=SCOPES)
        return gspread.authorize(creds)

    st.error("לא נמצאו הרשאות Google Sheets.")
    st.warning(
        "כדי שהאפליקציה תעבוד, צריך להגדיר Service Account ב-Streamlit Secrets "
        "או להוסיף קובץ service_account.json בהרצה מקומית."
    )
    st.stop()


def get_spreadsheet():
    """Open the main spreadsheet."""
    client = get_google_client()
    return client.open_by_key(SHEET_ID)


@st.cache_data(ttl=300)
def load_sheet_as_dataframe(sheet_name: str) -> pd.DataFrame:
    """Load a Google Sheets worksheet into a pandas DataFrame."""
    spreadsheet = get_spreadsheet()
    worksheet = spreadsheet.worksheet(sheet_name)
    rows = worksheet.get_all_records()
    return pd.DataFrame(rows)


def get_worksheet(sheet_name: str):
    """Return a worksheet object for writing."""
    spreadsheet = get_spreadsheet()
    return spreadsheet.worksheet(sheet_name)


def append_row_by_headers(sheet_name: str, row_values: dict):
    """Append a row to a worksheet using its existing header row."""
    worksheet = get_worksheet(sheet_name)
    headers = worksheet.row_values(1)

    if not headers:
        raise ValueError(f"Sheet {sheet_name} has no header row.")

    values = [row_values.get(header, "") for header in headers]
    worksheet.append_row(values, value_input_option="USER_ENTERED")


def clear_cache_and_rerun(message: str):
    """Clear cached Google Sheet reads and rerun the app."""
    st.cache_data.clear()
    st.success(message)
    st.rerun()


# -----------------------------------------------------------------------------
# Normalization and matching
# -----------------------------------------------------------------------------


def normalize_hebrew_name(value) -> str:
    """Normalize Hebrew species names for matching."""
    if pd.isna(value):
        return ""

    text = str(value).strip()
    replacements = {
        "־": "-",
        "–": "-",
        "—": "-",
        "_": " ",
        "\u200f": "",
        "\u200e": "",
        "\xa0": " ",
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    return " ".join(text.split())


def normalize_general(value) -> str:
    """Normalize general text values for classification."""
    if pd.isna(value):
        return ""
    return str(value).strip().lower()


def similarity_score(a, b) -> float:
    """Return similarity score between two strings."""
    if not a or not b:
        return 0

    return round(SequenceMatcher(None, str(a), str(b)).ratio() * 100, 1)


def find_best_match(name: str, choices: list[str]):
    """Return the best fuzzy match for a Hebrew species name."""
    if not name or not choices:
        return None, 0

    best_name = None
    best_score = 0

    for choice in choices:
        score = similarity_score(name, choice)
        if score > best_score:
            best_name = choice
            best_score = score

    return best_name, best_score


# -----------------------------------------------------------------------------
# Occurrence processing
# -----------------------------------------------------------------------------


def read_occurrence_csv(uploaded_file) -> pd.DataFrame:
    """Read BioGIS occurrence CSV with several possible encodings."""
    encodings = ["utf-8-sig", "utf-8", "cp1255", "iso-8859-8"]

    for encoding in encodings:
        try:
            uploaded_file.seek(0)
            return pd.read_csv(uploaded_file, encoding=encoding)
        except UnicodeDecodeError:
            continue

    uploaded_file.seek(0)
    return pd.read_csv(uploaded_file)


def classify_taxon(row: pd.Series) -> str:
    """
    Classify each occurrence row into one of:
    plants, vertebrates, invertebrates, fungi, unknown.
    """
    kingdom = normalize_general(row.get("kingdom", ""))
    phylum = normalize_general(row.get("phylum", ""))
    clazz = normalize_general(row.get("clazz", ""))
    group = normalize_general(row.get("group", ""))
    combined = " ".join([kingdom, phylum, clazz, group])

    if "fungi" in combined or "פטר" in combined:
        return "fungi"

    if (
        "plantae" in combined
        or "plant" in combined
        or "צומח" in combined
        or "צמחים" in combined
    ):
        return "plants"

    if (
        "animalia" in combined
        or "animal" in combined
        or "בעלי חיים" in combined
        or "בעל חיים" in combined
    ):
        if clazz in VERTEBRATE_CLASSES:
            return "vertebrates"
        if "chordata" in phylum or "vertebrata" in phylum or "מיתר" in phylum:
            return "vertebrates"
        if any(term in group for term in ["יונקים", "עופות", "זוחלים", "דו-חיים", "דו חיים", "דגים"]):
            return "vertebrates"
        return "invertebrates"

    if any(term in group for term in ["חרקים", "רכיכות", "עכביש", "חסרי חוליות", "פרוקי", "פרפרים"]):
        return "invertebrates"

    if any(term in group for term in ["יונקים", "עופות", "זוחלים", "דו-חיים", "דו חיים", "דגים"]):
        return "vertebrates"

    return "unknown"


def group_label(group_code: str) -> str:
    """Hebrew label for biological group code."""
    labels = {
        "plants": "צמחים",
        "vertebrates": "חולייתנים",
        "invertebrates": "חסרי חוליות",
        "fungi": "פטריות",
        "unknown": "לא ידוע",
    }
    return labels.get(group_code, group_code)


def build_index_lookup(index_df: pd.DataFrame) -> dict[str, str]:
    """Build normalized Hebrew name -> official Hebrew name lookup."""
    lookup = {}

    if "שם המין" not in index_df.columns:
        return lookup

    for value in index_df["שם המין"].dropna().astype(str):
        normalized = normalize_hebrew_name(value)
        if normalized and normalized not in lookup:
            lookup[normalized] = value

    return lookup


def load_name_mapping() -> pd.DataFrame:
    """Load the manual name mapping sheet if it exists."""
    try:
        return load_sheet_as_dataframe(MAPPING_SHEET)
    except Exception:
        return pd.DataFrame()


def build_mapping_lookup(mapping_df: pd.DataFrame) -> dict[tuple[str, str], str]:
    """Build lookup from original BioGIS name and group to corrected index name."""
    lookup = {}

    if mapping_df.empty:
        return lookup

    required_columns = ["שם מקורי מ-BioGIS", "שם מתוקן באינדקס"]
    if not all(column in mapping_df.columns for column in required_columns):
        return lookup

    for _, row in mapping_df.iterrows():
        original_name = normalize_hebrew_name(row.get("שם מקורי מ-BioGIS", ""))
        corrected_name = normalize_hebrew_name(row.get("שם מתוקן באינדקס", ""))
        mapping_group = normalize_general(row.get("קבוצה", ""))
        active_text = normalize_general(row.get("פעיל", True))

        if active_text in ["false", "0", "לא", "no"]:
            continue
        if not original_name or not corrected_name:
            continue

        group_options = {mapping_group, ""}

        if mapping_group in ["animals", "animal", "בעלי חיים"]:
            group_options.add("vertebrates")
        if mapping_group in ["plant", "plants", "צומח", "צמחים"]:
            group_options.add("plants")
        if mapping_group in ["vertebrates", "vertebrate", "חולייתנים"]:
            group_options.add("vertebrates")

        for group_code in group_options:
            lookup[(original_name, group_code)] = corrected_name

    return lookup


def count_active_mappings(mapping_df: pd.DataFrame) -> int:
    """Count real active mapping rows, excluding empty template rows."""
    if mapping_df.empty:
        return 0

    required_columns = ["שם מקורי מ-BioGIS", "שם מתוקן באינדקס"]
    if not all(column in mapping_df.columns for column in required_columns):
        return len(mapping_df)

    temp = mapping_df.copy()
    temp["_orig"] = temp["שם מקורי מ-BioGIS"].map(normalize_hebrew_name)
    temp["_corr"] = temp["שם מתוקן באינדקס"].map(normalize_hebrew_name)

    if "פעיל" in temp.columns:
        temp["_active"] = temp["פעיל"].map(normalize_general)
        temp = temp[~temp["_active"].isin(["false", "0", "לא", "no"])]

    return len(temp[(temp["_orig"] != "") & (temp["_corr"] != "")])


def get_mapping_match(normalized_name: str, biogroup: str, mapping_lookup: dict[tuple[str, str], str]):
    """Return a manual mapping match if one exists."""
    candidates = [(normalized_name, biogroup), (normalized_name, "")]

    if biogroup == "vertebrates":
        candidates.append((normalized_name, "animals"))
    if biogroup == "plants":
        candidates.append((normalized_name, "plant"))

    for key in candidates:
        if key in mapping_lookup:
            return mapping_lookup[key]

    return ""


def resolve_name(
    normalized_name: str,
    biogroup: str,
    plant_lookup: dict[str, str],
    animal_lookup: dict[str, str],
    mapping_lookup: dict[tuple[str, str], str],
):
    """Resolve one Hebrew species name according to group-specific indexes."""
    if not normalized_name:
        return {
            "species_heb_corrected": "",
            "match_status": "no_hebrew_name",
            "suggested_name": "",
            "match_score": 0,
            "matched_index": "",
        }

    mapped_name = get_mapping_match(normalized_name, biogroup, mapping_lookup)
    if mapped_name:
        return {
            "species_heb_corrected": mapped_name,
            "match_status": "mapped",
            "suggested_name": mapped_name,
            "match_score": 100,
            "matched_index": "name_mapping",
        }

    if biogroup == "plants":
        if normalized_name in plant_lookup:
            return {
                "species_heb_corrected": plant_lookup[normalized_name],
                "match_status": "exact_index",
                "suggested_name": plant_lookup[normalized_name],
                "match_score": 100,
                "matched_index": PLANTS_SHEET,
            }

        best_name, score = find_best_match(normalized_name, list(plant_lookup.keys()))
        return {
            "species_heb_corrected": "",
            "match_status": "needs_review",
            "suggested_name": plant_lookup.get(best_name, best_name or ""),
            "match_score": score,
            "matched_index": PLANTS_SHEET,
        }

    if biogroup == "vertebrates":
        if normalized_name in animal_lookup:
            return {
                "species_heb_corrected": animal_lookup[normalized_name],
                "match_status": "exact_index",
                "suggested_name": animal_lookup[normalized_name],
                "match_score": 100,
                "matched_index": ANIMALS_SHEET,
            }

        best_name, score = find_best_match(normalized_name, list(animal_lookup.keys()))
        return {
            "species_heb_corrected": "",
            "match_status": "needs_review",
            "suggested_name": animal_lookup.get(best_name, best_name or ""),
            "match_score": score,
            "matched_index": ANIMALS_SHEET,
        }

    if biogroup in ["invertebrates", "fungi"]:
        return {
            "species_heb_corrected": normalized_name,
            "match_status": "not_indexed_group",
            "suggested_name": "",
            "match_score": 0,
            "matched_index": "",
        }

    return {
        "species_heb_corrected": "",
        "match_status": "unknown_group",
        "suggested_name": "",
        "match_score": 0,
        "matched_index": "",
    }


def build_index_detail_lookup(index_df: pd.DataFrame) -> dict[str, dict]:
    """Build normalized Hebrew name -> full index row lookup."""
    lookup = {}

    if "שם המין" not in index_df.columns:
        return lookup

    for _, row in index_df.iterrows():
        normalized = normalize_hebrew_name(row.get("שם המין", ""))
        if normalized and normalized not in lookup:
            lookup[normalized] = row.to_dict()

    return lookup


def first_non_empty(record: dict, columns: list[str]) -> str:
    """Return the first non-empty value from a record."""
    for column in columns:
        value = record.get(column, "")
        if not pd.isna(value) and str(value).strip():
            return str(value).strip()
    return ""


def classification_from_index_or_occurrence(
    row: pd.Series,
    plant_details: dict[str, dict],
    animal_details: dict[str, dict],
) -> str:
    """Return the classification/status value to add to the GIS occurrence export.

    The value is taken from the Sheets indexes only:
    - plants_index: סיווג
    - animals_index: סטטוס שימור אזורי, with global status as fallback
    """
    corrected_name = normalize_hebrew_name(row.get("species_heb_corrected", ""))
    biogroup = row.get("biogroup", "")

    if biogroup == "plants" and corrected_name in plant_details:
        return first_non_empty(plant_details[corrected_name], ["סיווג"])

    if biogroup == "vertebrates" and corrected_name in animal_details:
        return first_non_empty(
            animal_details[corrected_name],
            ["סטטוס שימור אזורי", "סטטוס שימור עולמי"],
        )

    return ""


def make_gis_occurrence_export(enriched_df: pd.DataFrame, original_columns: list[str]) -> pd.DataFrame:
    """Create a clean occurrence table for GIS: original columns + corrected name + classification."""
    export_df = enriched_df[original_columns].copy()
    export_df["species_heb_corrected"] = enriched_df["species_heb_corrected"]
    export_df["classification"] = enriched_df["classification"]
    return export_df


def enrich_occurrence(
    occurrence_df: pd.DataFrame,
    plants_df: pd.DataFrame,
    animals_df: pd.DataFrame,
    mapping_df: pd.DataFrame,
) -> pd.DataFrame:
    """Add group, corrected Hebrew name, match status and GIS classification."""
    enriched_df = occurrence_df.copy()
    enriched_df["species_heb_normalized"] = enriched_df["species_heb"].apply(normalize_hebrew_name)
    enriched_df["biogroup"] = enriched_df.apply(classify_taxon, axis=1)
    enriched_df["biogroup_he"] = enriched_df["biogroup"].apply(group_label)

    plant_lookup = build_index_lookup(plants_df)
    animal_lookup = build_index_lookup(animals_df)
    mapping_lookup = build_mapping_lookup(mapping_df)

    resolved_rows = []
    for _, row in enriched_df.iterrows():
        resolved_rows.append(
            resolve_name(
                normalized_name=row["species_heb_normalized"],
                biogroup=row["biogroup"],
                plant_lookup=plant_lookup,
                animal_lookup=animal_lookup,
                mapping_lookup=mapping_lookup,
            )
        )

    resolved_df = pd.DataFrame(resolved_rows)
    enriched_df = pd.concat(
        [enriched_df.reset_index(drop=True), resolved_df.reset_index(drop=True)],
        axis=1,
    )
    enriched_df["match_status_he"] = enriched_df["match_status"].map(STATUS_LABELS)

    plant_details = build_index_detail_lookup(plants_df)
    animal_details = build_index_detail_lookup(animals_df)
    enriched_df["classification"] = enriched_df.apply(
        lambda row: classification_from_index_or_occurrence(row, plant_details, animal_details),
        axis=1,
    )

    return enriched_df


# -----------------------------------------------------------------------------
# Report tables
# -----------------------------------------------------------------------------


def make_review_table(enriched_df: pd.DataFrame) -> pd.DataFrame:
    """Create a unique-species table for names that require manual review."""
    review_df = enriched_df[enriched_df["match_status"] == "needs_review"].copy()
    columns = ["קבוצה", "קוד קבוצה", "שם מ-BioGIS", "הצעה קרובה", "ציון התאמה", "אינדקס", "מספר רשומות"]

    if review_df.empty:
        return pd.DataFrame(columns=columns)

    result = (
        review_df.groupby(
            ["biogroup_he", "biogroup", "species_heb_normalized", "suggested_name", "match_score", "matched_index"],
            dropna=False,
        )
        .size()
        .reset_index(name="מספר רשומות")
    )

    result = result.rename(
        columns={
            "biogroup_he": "קבוצה",
            "biogroup": "קוד קבוצה",
            "species_heb_normalized": "שם מ-BioGIS",
            "suggested_name": "הצעה קרובה",
            "match_score": "ציון התאמה",
            "matched_index": "אינדקס",
        }
    )

    return result[columns].sort_values(by=["קבוצה", "ציון התאמה"], ascending=[True, False])


def make_group_summary(enriched_df: pd.DataFrame) -> pd.DataFrame:
    """Summarize records and unique species by biological group."""
    count_column = "recordId" if "recordId" in enriched_df.columns else "species_heb_normalized"
    result = (
        enriched_df.groupby(["biogroup", "biogroup_he"], dropna=False)
        .agg(
            רשומות=(count_column, "count"),
            מינים_ייחודיים=("species_heb_normalized", "nunique"),
        )
        .reset_index()
        .rename(
            columns={
                "biogroup": "קוד קבוצה",
                "biogroup_he": "קבוצה",
                "מינים_ייחודיים": "מינים ייחודיים",
            }
        )
    )
    return result[["קבוצה", "רשומות", "מינים ייחודיים", "קוד קבוצה"]]


def make_match_summary(enriched_df: pd.DataFrame) -> pd.DataFrame:
    """Summarize match statuses."""
    result = (
        enriched_df.groupby(["match_status", "match_status_he"], dropna=False)
        .agg(
            רשומות=("species_heb_normalized", "count"),
            מינים_ייחודיים=("species_heb_normalized", "nunique"),
        )
        .reset_index()
        .rename(
            columns={
                "match_status": "קוד סטטוס",
                "match_status_he": "סטטוס",
                "מינים_ייחודיים": "מינים ייחודיים",
            }
        )
    )
    return result[["סטטוס", "רשומות", "מינים ייחודיים", "קוד סטטוס"]]


def make_index_report(
    enriched_df: pd.DataFrame,
    index_df: pd.DataFrame,
    biogroup: str,
    report_columns: list[str],
) -> pd.DataFrame:
    """Create report table for plants or vertebrates from resolved names."""
    filtered_df = enriched_df[
        (enriched_df["biogroup"] == biogroup)
        & (enriched_df["species_heb_corrected"] != "")
        & (enriched_df["match_status"].isin(["mapped", "exact_index"]))
    ].copy()

    if filtered_df.empty:
        return pd.DataFrame(columns=report_columns)

    names_df = pd.DataFrame(
        {"שם המין": sorted(filtered_df["species_heb_corrected"].dropna().astype(str).unique())}
    )
    names_df["_normalized_name"] = names_df["שם המין"].map(normalize_hebrew_name)

    index_copy = index_df.copy()
    index_copy["_normalized_name"] = index_copy["שם המין"].map(normalize_hebrew_name)

    report_df = names_df.merge(index_copy, on="_normalized_name", how="left", suffixes=("", "_index"))

    if "שם המין_index" in report_df.columns:
        report_df["שם המין"] = report_df["שם המין_index"].fillna(report_df["שם המין"])

    for column in report_columns:
        if column not in report_df.columns:
            report_df[column] = ""

    return report_df[report_columns].drop_duplicates()


def make_non_indexed_report(enriched_df: pd.DataFrame, biogroup: str) -> pd.DataFrame:
    """Create a simple unique-species report for fungi or invertebrates."""
    filtered_df = enriched_df[enriched_df["biogroup"] == biogroup].copy()
    columns = ["שם המין", "שם מדעי", "משפחה", "סדרה", "מחלקה", "מערכה", "ממלכה", "קבוצה מקורית", "מספר רשומות"]

    if filtered_df.empty:
        return pd.DataFrame(columns=columns)

    source_columns = {
        "species_heb_normalized": "שם המין",
        "species": "שם מדעי",
        "family": "משפחה",
        "orderr": "סדרה",
        "clazz": "מחלקה",
        "phylum": "מערכה",
        "kingdom": "ממלכה",
        "group": "קבוצה מקורית",
    }

    for source_column in source_columns:
        if source_column not in filtered_df.columns:
            filtered_df[source_column] = ""

    grouped = (
        filtered_df.groupby("species_heb_normalized", dropna=False)
        .agg(
            {
                "species": "first",
                "family": "first",
                "orderr": "first",
                "clazz": "first",
                "phylum": "first",
                "kingdom": "first",
                "group": "first",
            }
        )
        .reset_index()
    )

    counts = filtered_df.groupby("species_heb_normalized", dropna=False).size().reset_index(name="מספר רשומות")
    grouped = grouped.merge(counts, on="species_heb_normalized", how="left")
    grouped = grouped.rename(columns=source_columns)

    return grouped[columns].sort_values(by="שם המין")


def to_csv_bytes(df: pd.DataFrame) -> bytes:
    """Convert DataFrame to UTF-8-SIG CSV bytes for Hebrew-friendly Excel opening."""
    return df.to_csv(index=False).encode("utf-8-sig")


def to_excel_bytes(sheets: dict[str, pd.DataFrame]) -> bytes:
    """Create a multi-sheet Excel file in memory."""
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for sheet_name, df in sheets.items():
            df.to_excel(writer, index=False, sheet_name=sheet_name[:31])
    return output.getvalue()


# -----------------------------------------------------------------------------
# Writing corrections and new species
# -----------------------------------------------------------------------------


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def append_name_mapping(
    original_name: str,
    corrected_name: str,
    biogroup: str,
    score="",
):
    """Save a minimal correction to the name_mapping sheet."""
    row_values = {
        "שם מקורי מ-BioGIS": original_name,
        "שם מתוקן באינדקס": corrected_name,
        "קבוצה": biogroup,
        "מקור תיקון": "",
        "ציון התאמה": score,
        "אושר על ידי": "",
        "תאריך אישור": "",
        "פעיל": "TRUE",
        "הערות": "",
    }
    append_row_by_headers(MAPPING_SHEET, row_values)


def append_unmatched_log(
    original_name: str,
    biogroup: str,
    suggestions: str,
    score,
    source_file_name: str,
):
    """Save an unresolved name to the unmatched_log sheet."""
    row_values = {
        "תאריך": now_text(),
        "שם מקורי מ-BioGIS": original_name,
        "קבוצה משוערת": biogroup,
        "הצעות": suggestions,
        "ציון גבוה": score,
        "שם קובץ מקור": source_file_name,
        "סטטוס": "open",
        "הערות": "",
    }
    append_row_by_headers(UNMATCHED_SHEET, row_values)


def append_new_species_to_index(biogroup: str, species_values: dict):
    """Append a new species to plants_index or animals_index."""
    if biogroup == "plants":
        append_row_by_headers(PLANTS_SHEET, species_values)
    elif biogroup == "vertebrates":
        append_row_by_headers(ANIMALS_SHEET, species_values)
    else:
        raise ValueError("אפשר להוסיף כרגע רק צמח או חולייתן לאינדקס.")


def get_first_occurrence_for_name(enriched_df: pd.DataFrame, name: str, biogroup: str) -> pd.Series:
    """Return first occurrence row for a species name and group."""
    subset = enriched_df[
        (enriched_df["species_heb_normalized"] == name)
        & (enriched_df["biogroup"] == biogroup)
    ]
    if subset.empty:
        return pd.Series(dtype="object")
    return subset.iloc[0]


def render_name_pair(original_name: str, suggested_name: str, score) -> None:
    """Render a compact before/after correction block."""
    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**שם מתוך occurrence**")
        st.text_input(
            "שם מתוך occurrence",
            value=original_name,
            label_visibility="collapsed",
            disabled=True,
        )

    with col2:
        st.markdown("**שם מוצע מהאינדקס**")
        st.text_input(
            "שם מוצע מהאינדקס",
            value=suggested_name or "",
            label_visibility="collapsed",
            disabled=True,
        )

    st.caption(f"ציון התאמה: {score}")


def render_correction_panel(
    review_df: pd.DataFrame,
    enriched_df: pd.DataFrame,
    plants_df: pd.DataFrame,
    animals_df: pd.DataFrame,
    source_file_name: str,
):
    """Render the UI that lets users approve corrections and update Google Sheets."""
    st.subheader("4. תיקון שמות")

    if review_df.empty:
        st.success("אין כרגע שמות שדורשים תיקון ידני מול אינדקס הצומח או החולייתנים.")
        return

    st.info(
        "בחר שם לבדיקה, אשר את ההצעה, בחר שם אחר מהאינדקס, או הוסף מין חדש. "
        "תיקון שנשמר ייכנס ל-name_mapping וישמש אוטומטית בפעמים הבאות."
    )

    review_records = review_df.to_dict("records")
    option_labels = []
    label_to_record = {}

    for record in review_records:
        label = f"{record['קבוצה']} | {record['שם מ-BioGIS']} | הצעה: {record['הצעה קרובה']}"
        option_labels.append(label)
        label_to_record[label] = record

    selected_label = st.selectbox("בחר שם לבדיקה", option_labels)
    selected = label_to_record[selected_label]

    original_name = selected["שם מ-BioGIS"]
    biogroup = selected["קוד קבוצה"]
    suggested_name = selected["הצעה קרובה"]
    score = selected["ציון התאמה"]

    first_row = get_first_occurrence_for_name(enriched_df, original_name, biogroup)

    render_name_pair(original_name, suggested_name, score)

    action = st.radio(
        "מה לעשות?",
        [
            "אשר את ההצעה",
            "בחר שם אחר מהאינדקס",
            "הוסף מין חדש לאינדקס",
            "סמן כלא נמצא כרגע",
        ],
        horizontal=True,
    )

    if action == "אשר את ההצעה":
        disabled = not bool(suggested_name)
        if st.button("שמור תיקון", disabled=disabled, type="primary"):
            append_name_mapping(
                original_name=original_name,
                corrected_name=suggested_name,
                biogroup=biogroup,
                score=score,
            )
            clear_cache_and_rerun("התיקון נשמר ב-name_mapping.")

    elif action == "בחר שם אחר מהאינדקס":
        index_df = plants_df if biogroup == "plants" else animals_df
        index_names = sorted(index_df["שם המין"].dropna().astype(str).unique())

        default_index = 0
        if suggested_name in index_names:
            default_index = index_names.index(suggested_name)

        selected_index_name = st.selectbox(
            "בחר את השם התקין מתוך האינדקס",
            index_names,
            index=default_index,
        )

        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**שם מתוך occurrence**")
            st.text_input(
                "שם מתוך occurrence - בחירה ידנית",
                value=original_name,
                label_visibility="collapsed",
                disabled=True,
            )
        with col2:
            st.markdown("**שם שיישמר**")
            st.text_input(
                "שם שיישמר - בחירה ידנית",
                value=selected_index_name,
                label_visibility="collapsed",
                disabled=True,
            )

        if st.button("שמור תיקון", type="primary"):
            append_name_mapping(
                original_name=original_name,
                corrected_name=selected_index_name,
                biogroup=biogroup,
                score=score,
            )
            clear_cache_and_rerun("התיקון נשמר ב-name_mapping.")

    elif action == "הוסף מין חדש לאינדקס":
        occurrence_scientific = str(first_row.get("species", "") or "")
        occurrence_family = str(first_row.get("family", "") or "")
        occurrence_class = str(first_row.get("clazz", "") or "")

        if biogroup == "plants":
            with st.form("add_new_plant_form"):
                new_hebrew = st.text_input("שם המין", value=original_name)
                new_scientific = st.text_input("שם מדעי", value=occurrence_scientific)
                new_family = st.text_input("משפחה", value=occurrence_family)
                new_distribution = st.text_input("טיפוס התפוצה", value="")
                new_frequency = st.text_input("שכיחות", value="")
                new_classification = st.text_input("סיווג", value="")
                submitted = st.form_submit_button("הוסף לאינדקס ושמור תיקון")

                if submitted:
                    if not normalize_hebrew_name(new_hebrew):
                        st.error("חובה למלא שם מין בעברית.")
                    else:
                        species_values = {
                            "שם המין": new_hebrew,
                            "שם מדעי": new_scientific,
                            "משפחה": new_family,
                            "טיפוס התפוצה": new_distribution,
                            "שכיחות": new_frequency,
                            "סיווג": new_classification,
                        }
                        append_new_species_to_index("plants", species_values)
                        append_name_mapping(
                            original_name=original_name,
                            corrected_name=new_hebrew,
                            biogroup=biogroup,
                            score=100,
                        )
                        clear_cache_and_rerun("המין נוסף לאינדקס הצומח והתיקון נשמר.")

        elif biogroup == "vertebrates":
            with st.form("add_new_vertebrate_form"):
                new_hebrew = st.text_input("שם המין", value=original_name)
                new_scientific = st.text_input("שם מדעי", value=occurrence_scientific)
                new_class = st.text_input("מחלקה", value=occurrence_class)
                new_regional_status = st.text_input("סטטוס שימור אזורי", value="")
                new_global_status = st.text_input("סטטוס שימור עולמי", value="")
                submitted = st.form_submit_button("הוסף לאינדקס ושמור תיקון")

                if submitted:
                    if not normalize_hebrew_name(new_hebrew):
                        st.error("חובה למלא שם מין בעברית.")
                    else:
                        species_values = {
                            "שם המין": new_hebrew,
                            "שם מדעי": new_scientific,
                            "מחלקה": new_class,
                            "סטטוס שימור אזורי": new_regional_status,
                            "סטטוס שימור עולמי": new_global_status,
                        }
                        append_new_species_to_index("vertebrates", species_values)
                        append_name_mapping(
                            original_name=original_name,
                            corrected_name=new_hebrew,
                            biogroup=biogroup,
                            score=100,
                        )
                        clear_cache_and_rerun("המין נוסף לאינדקס החולייתנים והתיקון נשמר.")

    elif action == "סמן כלא נמצא כרגע":
        if st.button("שמור כלא נמצא", type="primary"):
            append_unmatched_log(
                original_name=original_name,
                biogroup=biogroup,
                suggestions=suggested_name,
                score=score,
                source_file_name=source_file_name,
            )
            clear_cache_and_rerun("השם נשמר ב-unmatched_log.")


# -----------------------------------------------------------------------------
# Main Streamlit app
# -----------------------------------------------------------------------------


def main():
    st.set_page_config(page_title="BioGIS Converter", layout="wide")

    st.title("BioGIS Converter")
    st.caption("סיווג קבוצות, תיקון שמות, עדכון מילון השמות, והכנת פלטים לדוח ול-GIS")

    st.subheader("1. בדיקת חיבור לאינדקסים")

    try:
        plants_df = load_sheet_as_dataframe(PLANTS_SHEET)
        animals_df = load_sheet_as_dataframe(ANIMALS_SHEET)
        mapping_df = load_name_mapping()
        st.success("החיבור ל-Google Sheets הצליח")
    except Exception as exc:
        st.error("החיבור ל-Google Sheets נכשל")
        st.exception(exc)
        st.stop()

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("מינים באינדקס צומח", len(plants_df))
    with col2:
        st.metric("מינים באינדקס חולייתנים", len(animals_df))
    with col3:
        st.metric("תיקוני שמות פעילים", count_active_mappings(mapping_df))

    with st.expander("הצג דוגמאות מהאינדקסים ומהמילון"):
        tab1, tab2, tab3 = st.tabs(["צומח", "חולייתנים", "name_mapping"])
        with tab1:
            st.dataframe(plants_df.head(10), use_container_width=True)
        with tab2:
            st.dataframe(animals_df.head(10), use_container_width=True)
        with tab3:
            st.dataframe(mapping_df.tail(20), use_container_width=True)

    st.subheader("2. העלאת קובץ occurrence מ-BioGIS")

    uploaded_file = st.file_uploader("העלה קובץ CSV", type=["csv"])
    if uploaded_file is None:
        st.info("כאן יופיע ניתוח ראשוני אחרי העלאת קובץ occurrence.")
        return

    occurrence_df = read_occurrence_csv(uploaded_file)
    st.success("קובץ occurrence נטען בהצלחה")

    required_columns = ["species_heb", "kingdom", "clazz", "phylum", "group"]
    missing_columns = [column for column in required_columns if column not in occurrence_df.columns]
    if missing_columns:
        st.error("חסרות עמודות נדרשות בקובץ occurrence:")
        st.code(", ".join(missing_columns))
        st.stop()

    with st.expander("עמודות בקובץ occurrence"):
        st.code(", ".join(occurrence_df.columns.astype(str)))

    enriched_df = enrich_occurrence(
        occurrence_df=occurrence_df,
        plants_df=plants_df,
        animals_df=animals_df,
        mapping_df=mapping_df,
    )
    gis_occurrence_df = make_gis_occurrence_export(enriched_df, list(occurrence_df.columns))

    group_summary_df = make_group_summary(enriched_df)
    match_summary_df = make_match_summary(enriched_df)
    review_df = make_review_table(enriched_df)

    plants_report_df = make_index_report(enriched_df, plants_df, "plants", PLANT_REPORT_COLUMNS)
    vertebrates_report_df = make_index_report(enriched_df, animals_df, "vertebrates", VERTEBRATE_REPORT_COLUMNS)
    invertebrates_report_df = make_non_indexed_report(enriched_df, "invertebrates")
    fungi_report_df = make_non_indexed_report(enriched_df, "fungi")
    unknown_report_df = make_non_indexed_report(enriched_df, "unknown")

    st.subheader("3. סיכום קובץ occurrence")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("רשומות occurrence", len(enriched_df))
    col2.metric("מינים ייחודיים", enriched_df["species_heb_normalized"].nunique())
    col3.metric(
        "דורשים בדיקה ידנית",
        enriched_df[enriched_df["match_status"] == "needs_review"]["species_heb_normalized"].nunique(),
    )
    col4.metric(
        "קבוצות מחוץ לאינדקס",
        enriched_df[enriched_df["match_status"] == "not_indexed_group"]["species_heb_normalized"].nunique(),
    )

    col1, col2 = st.columns(2)
    with col1:
        st.write("סיכום לפי קבוצה")
        st.dataframe(group_summary_df, use_container_width=True)
    with col2:
        st.write("סיכום לפי סטטוס התאמה")
        st.dataframe(match_summary_df, use_container_width=True)

    render_correction_panel(
        review_df=review_df,
        enriched_df=enriched_df,
        plants_df=plants_df,
        animals_df=animals_df,
        source_file_name=uploaded_file.name,
    )

    st.subheader("5. תצוגה מקדימה של פלטים לדוח")

    tabs = st.tabs(["צומח", "חולייתנים", "חסרי חוליות", "פטריות", "לא ידוע", "occurrence ל-GIS"])

    with tabs[0]:
        st.write("טבלת צומח לדוח - רק התאמות מדויקות או תיקונים שנשמרו במילון")
        st.dataframe(plants_report_df, use_container_width=True)
    with tabs[1]:
        st.write("טבלת חולייתנים לדוח - רק התאמות מדויקות או תיקונים שנשמרו במילון")
        st.dataframe(vertebrates_report_df, use_container_width=True)
    with tabs[2]:
        st.write("חסרי חוליות - פלט נפרד, ללא התאמה לאינדקס החולייתנים")
        st.dataframe(invertebrates_report_df, use_container_width=True)
    with tabs[3]:
        st.write("פטריות - פלט נפרד, ללא התאמה לאינדקס הצומח")
        st.dataframe(fungi_report_df, use_container_width=True)
    with tabs[4]:
        st.write("רשומות שלא סווגו")
        st.dataframe(unknown_report_df, use_container_width=True)
    with tabs[5]:
        st.write("קובץ occurrence ל-GIS - העמודות המקוריות בתוספת species_heb_corrected ו-classification")
        st.dataframe(gis_occurrence_df.head(200), use_container_width=True)

    st.subheader("6. הורדת פלטים")

    excel_bytes = to_excel_bytes(
        {
            "occurrences_enriched": gis_occurrence_df,
            "plants_report": plants_report_df,
            "vertebrates_report": vertebrates_report_df,
            "invertebrates_report": invertebrates_report_df,
            "fungi_report": fungi_report_df,
            "unknown_report": unknown_report_df,
            "review_needed": review_df,
        }
    )

    col1, col2, col3 = st.columns(3)
    with col1:
        st.download_button(
            label="הורד occurrence מועשר ל-GIS כ-CSV",
            data=to_csv_bytes(gis_occurrence_df),
            file_name="occurrences_enriched.csv",
            mime="text/csv",
        )
    with col2:
        st.download_button(
            label="הורד קובץ Excel עם כל הפלטים",
            data=excel_bytes,
            file_name="biogis_outputs.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    with col3:
        st.download_button(
            label="הורד רשימת שמות לבדיקה כ-CSV",
            data=to_csv_bytes(review_df),
            file_name="review_needed.csv",
            mime="text/csv",
        )


if __name__ == "__main__":
    main()
