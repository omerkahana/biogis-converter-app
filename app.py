from __future__ import annotations

from datetime import datetime, timezone
from difflib import SequenceMatcher
from io import BytesIO
from pathlib import Path

import gspread
import pandas as pd
import streamlit as st
from google.oauth2.service_account import Credentials


SHEET_ID = "1b6qgqHh6g3VifXtxDCG-g3zGSpqt8mkktJEruo0qpjQ"

PLANTS_SHEET = "plants_index"
ANIMALS_SHEET = "animals_index"
MAPPING_SHEET = "name_mapping"
UNMATCHED_SHEET = "unmatched_log"

CORRECTED_NAME_COLUMN = "species_heb_corrected"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

VERTEBRATE_CLASSES = {
    "mammalia": "יונקים",
    "aves": "עופות",
    "reptilia": "זוחלים",
    "amphibia": "דו-חיים",
    "actinopterygii": "דגים",
    "chondrichthyes": "דגים",
    "cephalaspidomorphi": "דגים",
    "myxini": "דגים",
}

BIOGROUP_LABELS = {
    "plant": "צומח",
    "vertebrate": "חולייתנים",
    "invertebrate": "חסרי חוליות",
    "fungi": "פטריות",
    "unknown": "לא ידוע",
}

MATCH_STATUS_LABELS = {
    "exact": "התאמה מדויקת",
    "mapped": "תוקן ממילון שמות",
    "needs_review": "דורש בדיקה",
    "not_indexed": "לא נבדק מול אינדקס",
    "unknown_group": "קבוצה לא מזוהה",
    "missing_name": "חסר שם עברי",
}

REPORT_PLANT_COLUMNS = [
    "שם המין",
    "שם מדעי",
    "משפחה",
    "טיפוס התפוצה",
    "שכיחות",
    "סיווג",
]

REPORT_ANIMAL_COLUMNS = [
    "שם המין",
    "שם מדעי",
    "מחלקה",
    "סטטוס שימור אזורי",
    "סטטוס שימור עולמי",
]

MAPPING_COLUMNS = [
    "שם מקורי מ-BioGIS",
    "שם מתוקן באינדקס",
    "קבוצה",
    "מקור תיקון",
    "ציון התאמה",
    "אושר על ידי",
    "תאריך אישור",
    "פעיל",
    "הערות",
]

UNMATCHED_COLUMNS = [
    "תאריך",
    "שם מקורי מ-BioGIS",
    "קבוצה משוערת",
    "הצעות",
    "ציון גבוה",
    "שם קובץ מקור",
    "סטטוס",
    "הערות",
]

METADATA_COLUMNS = [
    "source",
    "added_by",
    "added_at",
    "notes",
]


def get_now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def safe_str(value) -> str:
    if value is None:
        return ""
    if pd.isna(value):
        return ""
    return str(value).strip()


@st.cache_resource
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
        creds = Credentials.from_service_account_file(
            str(local_key),
            scopes=SCOPES,
        )
        return gspread.authorize(creds)

    st.error("לא נמצאו הרשאות Google Sheets.")
    st.warning(
        "בהרצה מקומית צריך קובץ service_account.json. "
        "ב-Streamlit Cloud צריך להגדיר את המפתח תחת Secrets."
    )
    st.stop()


def get_spreadsheet():
    client = get_google_client()
    return client.open_by_key(SHEET_ID)


def get_worksheet(sheet_name: str):
    return get_spreadsheet().worksheet(sheet_name)


@st.cache_data(ttl=300)
def load_sheet_as_dataframe(sheet_name: str) -> pd.DataFrame:
    worksheet = get_worksheet(sheet_name)
    rows = worksheet.get_all_records()
    df = pd.DataFrame(rows)

    # The original Excel file may contain a first index column with an empty header.
    if "" in df.columns:
        df = df.drop(columns=[""])

    return df


def clear_data_cache():
    st.cache_data.clear()


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

    text = " ".join(text.split())

    return text


def normalize_key(value) -> str:
    return normalize_hebrew_name(value).lower()


def similarity_score(a, b) -> float:
    if not a or not b:
        return 0

    return round(
        SequenceMatcher(None, str(a), str(b)).ratio() * 100,
        1,
    )


def find_best_match(name: str, choices: list[str]):
    """Return the best fuzzy match from a list of normalized names."""

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


def read_occurrence_csv(uploaded_file) -> pd.DataFrame:
    """Read BioGIS occurrence CSV with several possible encodings."""

    encodings = [
        "utf-8-sig",
        "utf-8",
        "cp1255",
        "iso-8859-8",
    ]

    for encoding in encodings:
        try:
            uploaded_file.seek(0)
            return pd.read_csv(uploaded_file, encoding=encoding)
        except UnicodeDecodeError:
            continue

    uploaded_file.seek(0)
    return pd.read_csv(uploaded_file)


def classify_row(row: pd.Series) -> str:
    """Classify a BioGIS occurrence row into a report group."""

    kingdom = safe_str(row.get("kingdom")).lower()
    phylum = safe_str(row.get("phylum")).lower()
    clazz = safe_str(row.get("clazz")).lower()
    group = safe_str(row.get("group")).lower()

    if kingdom == "plantae" or "plant" in group or "צומח" in group:
        return "plant"

    if kingdom == "fungi" or "fung" in group or "פטר" in group:
        return "fungi"

    if kingdom == "animalia" or "animal" in group or "חי" in group:
        if clazz in VERTEBRATE_CLASSES:
            return "vertebrate"

        # In most BioGIS exports, Chordata with a known class is a vertebrate record.
        if phylum == "chordata" and clazz:
            return "vertebrate"

        return "invertebrate"

    return "unknown"


def add_biogroup_columns(occurrence_df: pd.DataFrame) -> pd.DataFrame:
    occurrence_df = occurrence_df.copy()
    occurrence_df["biogroup"] = occurrence_df.apply(classify_row, axis=1)
    occurrence_df["biogroup_he"] = occurrence_df["biogroup"].map(BIOGROUP_LABELS)
    return occurrence_df


def build_index_lookup(index_df: pd.DataFrame) -> dict[str, str]:
    """Return normalized Hebrew name -> canonical Hebrew name."""

    lookup: dict[str, str] = {}

    if "שם המין" not in index_df.columns:
        return lookup

    for value in index_df["שם המין"].dropna().astype(str):
        canonical = normalize_hebrew_name(value)
        key = normalize_key(canonical)

        if key and key not in lookup:
            lookup[key] = canonical

    return lookup


def build_index_row_lookup(index_df: pd.DataFrame) -> dict[str, dict]:
    """Return normalized Hebrew name -> full index row."""

    row_lookup: dict[str, dict] = {}

    if "שם המין" not in index_df.columns:
        return row_lookup

    for _, row in index_df.iterrows():
        canonical = normalize_hebrew_name(row.get("שם המין", ""))
        key = normalize_key(canonical)

        if key and key not in row_lookup:
            row_lookup[key] = row.to_dict()

    return row_lookup


def load_active_mappings(mapping_df: pd.DataFrame) -> dict[tuple[str, str], str]:
    """Return (normalized original name, group) -> corrected name."""

    lookup: dict[tuple[str, str], str] = {}

    if mapping_df.empty:
        return lookup

    required = {"שם מקורי מ-BioGIS", "שם מתוקן באינדקס", "קבוצה"}
    if not required.issubset(set(mapping_df.columns)):
        return lookup

    for _, row in mapping_df.iterrows():
        original = normalize_key(row.get("שם מקורי מ-BioGIS"))
        corrected = normalize_hebrew_name(row.get("שם מתוקן באינדקס"))
        group = safe_str(row.get("קבוצה")).lower()
        active_value = safe_str(row.get("פעיל")).lower()

        is_active = active_value in {"", "true", "yes", "1", "כן"}

        if original and corrected and is_active:
            lookup[(original, group)] = corrected

    return lookup


def get_mapping_for_name(
    name_norm: str,
    biogroup: str,
    mapping_lookup: dict[tuple[str, str], str],
) -> str:
    """Return a corrected name from mapping, if available."""

    key = normalize_key(name_norm)

    # Prefer exact group mapping.
    if (key, biogroup) in mapping_lookup:
        return mapping_lookup[(key, biogroup)]

    # Allow old mappings with Hebrew group labels.
    group_label = BIOGROUP_LABELS.get(biogroup, "")
    if (key, group_label.lower()) in mapping_lookup:
        return mapping_lookup[(key, group_label.lower())]

    # Allow generic mapping without group, if one exists.
    if (key, "") in mapping_lookup:
        return mapping_lookup[(key, "")]

    return ""


def analyze_occurrence(
    occurrence_df: pd.DataFrame,
    plants_df: pd.DataFrame,
    animals_df: pd.DataFrame,
    mapping_df: pd.DataFrame,
):
    """Analyze occurrence records against indexes and existing name mappings."""

    occurrence_df = occurrence_df.copy()

    occurrence_df["species_heb_original"] = occurrence_df["species_heb"]
    occurrence_df["species_heb_normalized"] = occurrence_df["species_heb"].apply(
        normalize_hebrew_name
    )
    occurrence_df = add_biogroup_columns(occurrence_df)

    plant_lookup = build_index_lookup(plants_df)
    animal_lookup = build_index_lookup(animals_df)
    mapping_lookup = load_active_mappings(mapping_df)

    plant_choices = sorted(plant_lookup.keys())
    animal_choices = sorted(animal_lookup.keys())

    corrected_names = []
    match_statuses = []
    match_status_he = []
    suggested_names = []
    match_scores = []
    matched_indexes = []

    for _, row in occurrence_df.iterrows():
        original_name = normalize_hebrew_name(row.get("species_heb_normalized"))
        original_key = normalize_key(original_name)
        biogroup = safe_str(row.get("biogroup"))

        if not original_name:
            corrected_names.append("")
            match_statuses.append("missing_name")
            match_status_he.append(MATCH_STATUS_LABELS["missing_name"])
            suggested_names.append("")
            match_scores.append(0)
            matched_indexes.append("")
            continue

        if biogroup == "plant":
            lookup = plant_lookup
            choices = plant_choices
            matched_index = PLANTS_SHEET

        elif biogroup == "vertebrate":
            lookup = animal_lookup
            choices = animal_choices
            matched_index = ANIMALS_SHEET

        elif biogroup in {"invertebrate", "fungi"}:
            corrected_names.append(original_name)
            match_statuses.append("not_indexed")
            match_status_he.append(MATCH_STATUS_LABELS["not_indexed"])
            suggested_names.append("")
            match_scores.append(0)
            matched_indexes.append("")
            continue

        else:
            corrected_names.append(original_name)
            match_statuses.append("unknown_group")
            match_status_he.append(MATCH_STATUS_LABELS["unknown_group"])
            suggested_names.append("")
            match_scores.append(0)
            matched_indexes.append("")
            continue

        mapped_name = get_mapping_for_name(original_name, biogroup, mapping_lookup)

        if mapped_name:
            corrected_names.append(mapped_name)
            match_statuses.append("mapped")
            match_status_he.append(MATCH_STATUS_LABELS["mapped"])
            suggested_names.append(mapped_name)
            match_scores.append(100)
            matched_indexes.append(matched_index)
            continue

        if original_key in lookup:
            corrected_names.append(lookup[original_key])
            match_statuses.append("exact")
            match_status_he.append(MATCH_STATUS_LABELS["exact"])
            suggested_names.append(lookup[original_key])
            match_scores.append(100)
            matched_indexes.append(matched_index)
            continue

        best_key, score = find_best_match(original_key, choices)
        best_name = lookup.get(best_key, "") if best_key else ""

        corrected_names.append(original_name)
        match_statuses.append("needs_review")
        match_status_he.append(MATCH_STATUS_LABELS["needs_review"])
        suggested_names.append(best_name)
        match_scores.append(score)
        matched_indexes.append(matched_index)

    occurrence_df[CORRECTED_NAME_COLUMN] = corrected_names
    occurrence_df["match_status"] = match_statuses
    occurrence_df["match_status_he"] = match_status_he
    occurrence_df["suggested_name"] = suggested_names
    occurrence_df["match_score"] = match_scores
    occurrence_df["matched_index"] = matched_indexes

    review_df = build_review_table(occurrence_df)

    return occurrence_df, review_df


def first_non_empty(series: pd.Series) -> str:
    for value in series:
        text = safe_str(value)
        if text:
            return text
    return ""


def build_review_table(occurrence_df: pd.DataFrame) -> pd.DataFrame:
    """Build a species-level table of indexed groups that need manual review."""

    subset = occurrence_df[
        occurrence_df["match_status"].eq("needs_review")
        & occurrence_df["biogroup"].isin(["plant", "vertebrate"])
    ].copy()

    if subset.empty:
        return pd.DataFrame(
            columns=[
                "קבוצה",
                "קבוצה קוד",
                "שם מ-BioGIS",
                "שם מדעי",
                "משפחה",
                "מחלקה",
                "הצעה קרובה",
                "ציון התאמה",
                "אינדקס",
                "מספר רשומות",
            ]
        )

    grouped_rows = []

    for (biogroup, original_name), group_df in subset.groupby(
        ["biogroup", "species_heb_normalized"], dropna=False
    ):
        grouped_rows.append(
            {
                "קבוצה": BIOGROUP_LABELS.get(biogroup, biogroup),
                "קבוצה קוד": biogroup,
                "שם מ-BioGIS": original_name,
                "שם מדעי": first_non_empty(group_df.get("species", pd.Series(dtype=str))),
                "משפחה": first_non_empty(group_df.get("family", pd.Series(dtype=str))),
                "מחלקה": first_non_empty(group_df.get("clazz", pd.Series(dtype=str))),
                "הצעה קרובה": first_non_empty(group_df["suggested_name"]),
                "ציון התאמה": group_df["match_score"].max(),
                "אינדקס": first_non_empty(group_df["matched_index"]),
                "מספר רשומות": len(group_df),
            }
        )

    review_df = pd.DataFrame(grouped_rows)

    if not review_df.empty:
        review_df = review_df.sort_values(
            by=["קבוצה", "ציון התאמה", "שם מ-BioGIS"],
            ascending=[True, False, True],
        )

    return review_df


def build_summary_by_group(occurrence_df: pd.DataFrame) -> pd.DataFrame:
    summary = (
        occurrence_df.groupby(["biogroup", "biogroup_he"], dropna=False)
        .agg(
            מספר_רשומות=("recordId", "count"),
            מינים_ייחודיים=("species_heb_normalized", "nunique"),
        )
        .reset_index()
        .rename(
            columns={
                "biogroup": "קוד קבוצה",
                "biogroup_he": "קבוצה",
                "מספר_רשומות": "מספר רשומות",
                "מינים_ייחודיים": "מינים ייחודיים",
            }
        )
    )

    order = ["plant", "vertebrate", "invertebrate", "fungi", "unknown"]
    summary["sort_order"] = summary["קוד קבוצה"].apply(
        lambda value: order.index(value) if value in order else 99
    )

    return summary.sort_values("sort_order").drop(columns=["sort_order"])


def build_summary_by_match_status(occurrence_df: pd.DataFrame) -> pd.DataFrame:
    summary = (
        occurrence_df.groupby(["match_status", "match_status_he"], dropna=False)
        .agg(
            מספר_רשומות=("recordId", "count"),
            מינים_ייחודיים=("species_heb_normalized", "nunique"),
        )
        .reset_index()
        .rename(
            columns={
                "match_status": "קוד סטטוס",
                "match_status_he": "סטטוס",
                "מספר_רשומות": "מספר רשומות",
                "מינים_ייחודיים": "מינים ייחודיים",
            }
        )
    )

    return summary.sort_values(["קוד סטטוס"])


def build_plants_report(occurrence_df: pd.DataFrame, plants_df: pd.DataFrame) -> pd.DataFrame:
    row_lookup = build_index_row_lookup(plants_df)

    names = sorted(
        set(
            normalize_key(name)
            for name in occurrence_df.loc[
                occurrence_df["biogroup"].eq("plant")
                & occurrence_df["match_status"].isin(["exact", "mapped"]),
                CORRECTED_NAME_COLUMN,
            ]
            if normalize_key(name)
        )
    )

    rows = []

    for name_key in names:
        row = row_lookup.get(name_key, {})
        rows.append({column: row.get(column, "") for column in REPORT_PLANT_COLUMNS})

    return pd.DataFrame(rows, columns=REPORT_PLANT_COLUMNS)


def build_animals_report(occurrence_df: pd.DataFrame, animals_df: pd.DataFrame) -> pd.DataFrame:
    row_lookup = build_index_row_lookup(animals_df)

    names = sorted(
        set(
            normalize_key(name)
            for name in occurrence_df.loc[
                occurrence_df["biogroup"].eq("vertebrate")
                & occurrence_df["match_status"].isin(["exact", "mapped"]),
                CORRECTED_NAME_COLUMN,
            ]
            if normalize_key(name)
        )
    )

    rows = []

    for name_key in names:
        row = row_lookup.get(name_key, {})
        rows.append({column: row.get(column, "") for column in REPORT_ANIMAL_COLUMNS})

    return pd.DataFrame(rows, columns=REPORT_ANIMAL_COLUMNS)


def build_other_report(occurrence_df: pd.DataFrame, biogroup: str) -> pd.DataFrame:
    subset = occurrence_df[occurrence_df["biogroup"].eq(biogroup)].copy()

    if subset.empty:
        return pd.DataFrame(
            columns=[
                "שם המין",
                "שם מדעי",
                "משפחה",
                "סדרה",
                "מחלקה",
                "מערכה",
                "ממלכה",
                "מספר רשומות",
            ]
        )

    rows = []

    for name, group_df in subset.groupby("species_heb_normalized", dropna=False):
        rows.append(
            {
                "שם המין": normalize_hebrew_name(name),
                "שם מדעי": first_non_empty(group_df.get("species", pd.Series(dtype=str))),
                "משפחה": first_non_empty(group_df.get("family", pd.Series(dtype=str))),
                "סדרה": first_non_empty(group_df.get("orderr", pd.Series(dtype=str))),
                "מחלקה": first_non_empty(group_df.get("clazz", pd.Series(dtype=str))),
                "מערכה": first_non_empty(group_df.get("phylum", pd.Series(dtype=str))),
                "ממלכה": first_non_empty(group_df.get("kingdom", pd.Series(dtype=str))),
                "מספר רשומות": len(group_df),
            }
        )

    return pd.DataFrame(rows).sort_values("שם המין")


def build_unknown_report(occurrence_df: pd.DataFrame) -> pd.DataFrame:
    subset = occurrence_df[
        occurrence_df["biogroup"].eq("unknown")
        | occurrence_df["match_status"].isin(["missing_name", "unknown_group"])
    ].copy()

    if subset.empty:
        return pd.DataFrame()

    return subset.drop_duplicates(subset=["species_heb_normalized", "species"]).copy()


def csv_download_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8-sig")


def create_excel_bytes(sheets: dict[str, pd.DataFrame]) -> bytes:
    output = BytesIO()

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for sheet_name, df in sheets.items():
            safe_sheet_name = sheet_name[:31]
            df.to_excel(writer, sheet_name=safe_sheet_name, index=False)

    return output.getvalue()


def ensure_columns(worksheet, required_columns: list[str]):
    headers = worksheet.row_values(1)

    for column in required_columns:
        if column not in headers:
            headers.append(column)
            worksheet.update_cell(1, len(headers), column)


def append_dict_row(sheet_name: str, row_data: dict):
    worksheet = get_worksheet(sheet_name)
    headers = worksheet.row_values(1)

    if not headers:
        raise ValueError(f"הגיליון {sheet_name} אינו כולל כותרות.")

    values = []

    # If the original Excel index column exists, keep filling it.
    all_values = worksheet.get_all_values()
    next_index = max(0, len(all_values) - 1)

    for header in headers:
        if header == "":
            values.append(str(next_index))
        else:
            values.append(row_data.get(header, ""))

    worksheet.append_row(values, value_input_option="USER_ENTERED")


def append_mapping_row(
    original_name: str,
    corrected_name: str,
    biogroup: str,
    source: str,
    score,
    approved_by: str,
    notes: str = "",
):
    ensure_columns(get_worksheet(MAPPING_SHEET), MAPPING_COLUMNS)

    append_dict_row(
        MAPPING_SHEET,
        {
            "שם מקורי מ-BioGIS": normalize_hebrew_name(original_name),
            "שם מתוקן באינדקס": normalize_hebrew_name(corrected_name),
            "קבוצה": biogroup,
            "מקור תיקון": source,
            "ציון התאמה": score,
            "אושר על ידי": approved_by,
            "תאריך אישור": get_now_iso(),
            "פעיל": "TRUE",
            "הערות": notes,
        },
    )


def append_unmatched_log_row(
    original_name: str,
    biogroup: str,
    suggestion: str,
    score,
    source_filename: str,
    status: str,
    notes: str,
):
    ensure_columns(get_worksheet(UNMATCHED_SHEET), UNMATCHED_COLUMNS)

    append_dict_row(
        UNMATCHED_SHEET,
        {
            "תאריך": get_now_iso(),
            "שם מקורי מ-BioGIS": normalize_hebrew_name(original_name),
            "קבוצה משוערת": biogroup,
            "הצעות": suggestion,
            "ציון גבוה": score,
            "שם קובץ מקור": source_filename,
            "סטטוס": status,
            "הערות": notes,
        },
    )


def append_new_plant_to_index(
    hebrew_name: str,
    scientific_name: str,
    family: str,
    distribution_type: str,
    rarity: str,
    classification: str,
    approved_by: str,
    notes: str,
):
    worksheet = get_worksheet(PLANTS_SHEET)
    ensure_columns(worksheet, REPORT_PLANT_COLUMNS + METADATA_COLUMNS)

    append_dict_row(
        PLANTS_SHEET,
        {
            "שם המין": normalize_hebrew_name(hebrew_name),
            "שם מדעי": scientific_name,
            "משפחה": family,
            "טיפוס התפוצה": distribution_type,
            "שכיחות": rarity,
            "סיווג": classification,
            "source": "manual_app_addition",
            "added_by": approved_by,
            "added_at": get_now_iso(),
            "notes": notes,
        },
    )


def append_new_animal_to_index(
    hebrew_name: str,
    scientific_name: str,
    animal_class: str,
    regional_status: str,
    global_status: str,
    approved_by: str,
    notes: str,
):
    worksheet = get_worksheet(ANIMALS_SHEET)
    ensure_columns(worksheet, REPORT_ANIMAL_COLUMNS + METADATA_COLUMNS)

    append_dict_row(
        ANIMALS_SHEET,
        {
            "שם המין": normalize_hebrew_name(hebrew_name),
            "שם מדעי": scientific_name,
            "מחלקה": animal_class,
            "סטטוס שימור אזורי": regional_status,
            "סטטוס שימור עולמי": global_status,
            "source": "manual_app_addition",
            "added_by": approved_by,
            "added_at": get_now_iso(),
            "notes": notes,
        },
    )


def get_index_choices_for_group(
    biogroup: str,
    plants_df: pd.DataFrame,
    animals_df: pd.DataFrame,
) -> list[str]:
    if biogroup == "plant":
        return sorted(
            normalize_hebrew_name(value)
            for value in plants_df["שם המין"].dropna().astype(str)
            if normalize_hebrew_name(value)
        )

    if biogroup == "vertebrate":
        return sorted(
            normalize_hebrew_name(value)
            for value in animals_df["שם המין"].dropna().astype(str)
            if normalize_hebrew_name(value)
        )

    return []


def render_review_editor(
    review_df: pd.DataFrame,
    plants_df: pd.DataFrame,
    animals_df: pd.DataFrame,
    uploaded_filename: str,
):
    st.subheader("6. תיקון שמות ושמירה למילון")

    if review_df.empty:
        st.success("אין כרגע שמות של צמחים או חולייתנים שדורשים בדיקה ידנית.")
        return

    st.write(
        "כאן אפשר לאשר הצעה, לבחור שם קיים מהאינדקס, או להוסיף מין חדש לאינדקס. "
        "כל תיקון נשמר ב-name_mapping וישמש בריצות הבאות."
    )

    approved_by = st.text_input(
        "שם המאשר / אימייל",
        value="",
        placeholder="לדוגמה: Omer / ecologist@company.co.il",
    )

    display_options = []
    for idx, row in review_df.reset_index(drop=True).iterrows():
        label = (
            f"{row['קבוצה']} | {row['שם מ-BioGIS']} "
            f"→ {row['הצעה קרובה']} ({row['ציון התאמה']})"
        )
        display_options.append((label, idx))

    selected_label = st.selectbox(
        "בחר שם לטיפול",
        options=[label for label, _ in display_options],
    )

    selected_idx = dict(display_options)[selected_label]
    selected = review_df.reset_index(drop=True).iloc[selected_idx]

    biogroup = selected["קבוצה קוד"]
    original_name = selected["שם מ-BioGIS"]
    suggestion = selected["הצעה קרובה"]
    score = selected["ציון התאמה"]
    scientific_name = selected.get("שם מדעי", "")
    family = selected.get("משפחה", "")
    clazz = selected.get("מחלקה", "")

    st.markdown("#### פרטי השם שנבחר")
    st.dataframe(pd.DataFrame([selected]), use_container_width=True)

    index_choices = get_index_choices_for_group(biogroup, plants_df, animals_df)

    action = st.radio(
        "מה לעשות עם השם הזה?",
        options=[
            "אשר את ההצעה הקרובה",
            "בחר שם אחר מתוך האינדקס",
            "הוסף מין חדש לאינדקס",
            "סמן כלא נמצא / לבדיקה עתידית",
        ],
        horizontal=False,
    )

    notes = st.text_area("הערות", value="", height=80)

    if action == "אשר את ההצעה הקרובה":
        st.info(f"התיקון שיישמר: {original_name} → {suggestion}")

        if st.button("שמור תיקון למילון", type="primary"):
            if not approved_by.strip():
                st.error("צריך למלא שם מאשר / אימייל.")
                return

            if not suggestion:
                st.error("אין הצעה לשמירה.")
                return

            append_mapping_row(
                original_name=original_name,
                corrected_name=suggestion,
                biogroup=biogroup,
                source="fuzzy_approved",
                score=score,
                approved_by=approved_by,
                notes=notes,
            )
            clear_data_cache()
            st.success("התיקון נשמר ב-name_mapping.")
            st.rerun()

    elif action == "בחר שם אחר מתוך האינדקס":
        default_index = 0

        if suggestion in index_choices:
            default_index = index_choices.index(suggestion)

        corrected_name = st.selectbox(
            "בחר שם תקין מתוך האינדקס",
            options=index_choices,
            index=default_index if index_choices else 0,
        )

        st.info(f"התיקון שיישמר: {original_name} → {corrected_name}")

        if st.button("שמור בחירה ידנית למילון", type="primary"):
            if not approved_by.strip():
                st.error("צריך למלא שם מאשר / אימייל.")
                return

            append_mapping_row(
                original_name=original_name,
                corrected_name=corrected_name,
                biogroup=biogroup,
                source="manual_existing_index",
                score=score,
                approved_by=approved_by,
                notes=notes,
            )
            clear_data_cache()
            st.success("התיקון הידני נשמר ב-name_mapping.")
            st.rerun()

    elif action == "הוסף מין חדש לאינדקס":
        st.warning(
            "האפשרות הזו מוסיפה שורה חדשה לאינדקס המרכזי. "
            "מומלץ להשתמש בה רק כאשר המין באמת חסר באינדקס."
        )

        if biogroup == "plant":
            with st.form("add_new_plant_form"):
                new_hebrew_name = st.text_input("שם המין", value=original_name)
                new_scientific_name = st.text_input("שם מדעי", value=scientific_name)
                new_family = st.text_input("משפחה", value=family)
                distribution_type = st.text_input("טיפוס התפוצה", value="")
                rarity = st.text_input("שכיחות", value="")
                classification = st.text_input("סיווג", value="")

                submitted = st.form_submit_button("הוסף צמח לאינדקס ושמור מיפוי")

            if submitted:
                if not approved_by.strip():
                    st.error("צריך למלא שם מאשר / אימייל.")
                    return

                if not new_hebrew_name.strip():
                    st.error("צריך למלא שם מין.")
                    return

                append_new_plant_to_index(
                    hebrew_name=new_hebrew_name,
                    scientific_name=new_scientific_name,
                    family=new_family,
                    distribution_type=distribution_type,
                    rarity=rarity,
                    classification=classification,
                    approved_by=approved_by,
                    notes=notes,
                )

                append_mapping_row(
                    original_name=original_name,
                    corrected_name=new_hebrew_name,
                    biogroup=biogroup,
                    source="manual_new_plant",
                    score=100,
                    approved_by=approved_by,
                    notes=notes,
                )

                clear_data_cache()
                st.success("הצמח נוסף ל-plants_index והמיפוי נשמר.")
                st.rerun()

        elif biogroup == "vertebrate":
            class_default = VERTEBRATE_CLASSES.get(safe_str(clazz).lower(), clazz)

            with st.form("add_new_animal_form"):
                new_hebrew_name = st.text_input("שם המין", value=original_name)
                new_scientific_name = st.text_input("שם מדעי", value=scientific_name)
                animal_class = st.text_input("מחלקה", value=class_default)
                regional_status = st.text_input("סטטוס שימור אזורי", value="")
                global_status = st.text_input("סטטוס שימור עולמי", value="")

                submitted = st.form_submit_button("הוסף חולייתן לאינדקס ושמור מיפוי")

            if submitted:
                if not approved_by.strip():
                    st.error("צריך למלא שם מאשר / אימייל.")
                    return

                if not new_hebrew_name.strip():
                    st.error("צריך למלא שם מין.")
                    return

                append_new_animal_to_index(
                    hebrew_name=new_hebrew_name,
                    scientific_name=new_scientific_name,
                    animal_class=animal_class,
                    regional_status=regional_status,
                    global_status=global_status,
                    approved_by=approved_by,
                    notes=notes,
                )

                append_mapping_row(
                    original_name=original_name,
                    corrected_name=new_hebrew_name,
                    biogroup=biogroup,
                    source="manual_new_vertebrate",
                    score=100,
                    approved_by=approved_by,
                    notes=notes,
                )

                clear_data_cache()
                st.success("החולייתן נוסף ל-animals_index והמיפוי נשמר.")
                st.rerun()

        else:
            st.info("הוספת מין חדש לאינדקס זמינה כרגע רק לצמחים ולחולייתנים.")

    elif action == "סמן כלא נמצא / לבדיקה עתידית":
        st.info("השם יישמר בלוג unmatched_log, אך לא יתווסף למילון התיקונים.")

        if st.button("שמור ללוג לבדיקה עתידית"):
            if not approved_by.strip():
                st.error("צריך למלא שם מאשר / אימייל.")
                return

            append_unmatched_log_row(
                original_name=original_name,
                biogroup=biogroup,
                suggestion=suggestion,
                score=score,
                source_filename=uploaded_filename,
                status="open",
                notes=notes,
            )
            clear_data_cache()
            st.success("השם נשמר ב-unmatched_log.")
            st.rerun()


def main():
    st.set_page_config(
        page_title="BioGIS Converter",
        layout="wide",
    )

    st.title("BioGIS Converter")
    st.caption("שלב 3: סיווג קבוצות, תיקון שמות, עדכון מילון והפקת פלטים")

    st.subheader("1. בדיקת חיבור לאינדקסים")

    try:
        plants_df = load_sheet_as_dataframe(PLANTS_SHEET)
        animals_df = load_sheet_as_dataframe(ANIMALS_SHEET)
        mapping_df = load_sheet_as_dataframe(MAPPING_SHEET)

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
        active_mappings = load_active_mappings(mapping_df)
        st.metric("תיקוני שמות פעילים", len(active_mappings))

    with st.expander("הצג דוגמאות מהאינדקסים"):
        col1, col2 = st.columns(2)

        with col1:
            st.write("צומח")
            st.dataframe(plants_df.head(10), use_container_width=True)

        with col2:
            st.write("חולייתנים")
            st.dataframe(animals_df.head(10), use_container_width=True)

    st.subheader("2. העלאת קובץ occurrence מ-BioGIS")

    uploaded_file = st.file_uploader(
        "העלה קובץ CSV",
        type=["csv"],
    )

    if uploaded_file is None:
        st.info("כאן יופיע ניתוח אחרי העלאת קובץ occurrence.")
        return

    occurrence_df = read_occurrence_csv(uploaded_file)

    st.success("קובץ occurrence נטען בהצלחה")

    with st.expander("עמודות בקובץ occurrence"):
        st.code(", ".join(occurrence_df.columns.astype(str)))

    required_columns = {"species_heb", "kingdom", "clazz", "phylum", "group"}

    missing_columns = sorted(required_columns - set(occurrence_df.columns))

    if missing_columns:
        st.error(
            "חסרות עמודות נדרשות בקובץ occurrence: "
            + ", ".join(missing_columns)
        )
        st.stop()

    occurrence_df, review_df = analyze_occurrence(
        occurrence_df,
        plants_df,
        animals_df,
        mapping_df,
    )

    st.subheader("3. סיכום לפי קבוצה")

    group_summary = build_summary_by_group(occurrence_df)
    st.dataframe(group_summary, use_container_width=True)

    metric_cols = st.columns(5)

    for idx, group_code in enumerate(["plant", "vertebrate", "invertebrate", "fungi", "unknown"]):
        label = BIOGROUP_LABELS[group_code]
        species_count = occurrence_df.loc[
            occurrence_df["biogroup"].eq(group_code),
            "species_heb_normalized",
        ].nunique()
        metric_cols[idx].metric(label, species_count)

    st.subheader("4. סיכום לפי סטטוס התאמה")

    status_summary = build_summary_by_match_status(occurrence_df)
    st.dataframe(status_summary, use_container_width=True)

    st.subheader("5. שמות שדורשים בדיקה ידנית")

    if review_df.empty:
        st.success("אין שמות של צמחים או חולייתנים שדורשים בדיקה ידנית.")
    else:
        st.dataframe(review_df, use_container_width=True)

    render_review_editor(
        review_df=review_df,
        plants_df=plants_df,
        animals_df=animals_df,
        uploaded_filename=getattr(uploaded_file, "name", ""),
    )

    st.subheader("7. תצוגה מקדימה של פלטים לדוח")

    plants_report = build_plants_report(occurrence_df, plants_df)
    animals_report = build_animals_report(occurrence_df, animals_df)
    invertebrates_report = build_other_report(occurrence_df, "invertebrate")
    fungi_report = build_other_report(occurrence_df, "fungi")
    unknown_report = build_unknown_report(occurrence_df)

    tabs = st.tabs(
        [
            "צומח",
            "חולייתנים",
            "חסרי חוליות",
            "פטריות",
            "לא ידוע",
            "occurrence מועשר",
        ]
    )

    with tabs[0]:
        st.write("טבלת צומח לדוח - רק התאמות מדויקות או תיקונים קיימים")
        st.dataframe(plants_report, use_container_width=True)

    with tabs[1]:
        st.write("טבלת חולייתנים לדוח - רק התאמות מדויקות או תיקונים קיימים")
        st.dataframe(animals_report, use_container_width=True)

    with tabs[2]:
        st.write("חסרי חוליות - לא נבדקים מול אינדקס החולייתנים")
        st.dataframe(invertebrates_report, use_container_width=True)

    with tabs[3]:
        st.write("פטריות - לא נבדקות מול אינדקס הצמחים")
        st.dataframe(fungi_report, use_container_width=True)

    with tabs[4]:
        st.write("רשומות שלא סווגו או שחסר להן מידע")
        st.dataframe(unknown_report, use_container_width=True)

    with tabs[5]:
        st.write("קובץ occurrence מועשר לשימוש כשכבת GIS")
        st.dataframe(occurrence_df.head(500), use_container_width=True)

    st.subheader("8. הורדת פלטים")

    excel_bytes = create_excel_bytes(
        {
            "occurrence_enriched": occurrence_df,
            "plants_report": plants_report,
            "vertebrates_report": animals_report,
            "invertebrates_report": invertebrates_report,
            "fungi_report": fungi_report,
            "unknown": unknown_report,
            "review_needed": review_df,
        }
    )

    col1, col2, col3 = st.columns(3)

    with col1:
        st.download_button(
            "הורד occurrence מועשר CSV",
            data=csv_download_bytes(occurrence_df),
            file_name="occurrences_enriched.csv",
            mime="text/csv",
        )

    with col2:
        st.download_button(
            "הורד כל הפלטים Excel",
            data=excel_bytes,
            file_name="biogis_outputs.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    with col3:
        st.download_button(
            "הורד שמות לבדיקה CSV",
            data=csv_download_bytes(review_df),
            file_name="names_for_review.csv",
            mime="text/csv",
        )


if __name__ == "__main__":
    main()
