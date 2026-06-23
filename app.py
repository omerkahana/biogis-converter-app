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
    "mapped": "Resolved from name mapping",
    "exact_index": "Exact index match",
    "needs_review": "Needs manual review",
    "not_indexed_group": "Group not indexed",
    "unknown_group": "Unknown group",
    "no_hebrew_name": "Missing Hebrew name",
}

GROUP_LABELS = {
    "plants": "Plants",
    "vertebrates": "Vertebrates",
    "invertebrates": "Invertebrates",
    "fungi": "Fungi",
    "unknown": "Unknown",
}

ACTION_SAVE_INDEX = "Save selected index name"
ACTION_ADD_NEW = "Add as new species to index"
ACTION_MARK_UNRESOLVED = "Mark as unresolved for now"
ACTION_SKIP = "Skip"

BATCH_ACTIONS = [
    ACTION_SAVE_INDEX,
    ACTION_ADD_NEW,
    ACTION_MARK_UNRESOLVED,
    ACTION_SKIP,
]


# -----------------------------------------------------------------------------
# Page style
# -----------------------------------------------------------------------------


def apply_page_style() -> None:
    """Make the app feel like a clean English/LTR web app."""
    st.markdown(
        """
        <style>
        html, body, [class*="css"], .stApp {
            direction: ltr;
            text-align: left;
        }
        h1, h2, h3, h4, h5, h6, p, label, span, div {
            text-align: left;
        }
        section[data-testid="stSidebar"] {
            direction: ltr;
            text-align: left;
        }
        div[data-testid="stMetric"] {
            background: #f8fafc;
            border: 1px solid #e5e7eb;
            border-radius: 12px;
            padding: 14px 16px;
        }
        div[data-testid="stDataFrame"], div[data-testid="stTable"] {
            direction: ltr;
        }
        .block-container {
            padding-top: 2rem;
            padding-bottom: 3rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


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

    st.error("Google Sheets credentials were not found.")
    st.warning(
        "Define the Service Account in Streamlit Secrets, or add service_account.json "
        "when running locally."
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
    """Classify each occurrence row into plants, vertebrates, invertebrates, fungi, or unknown."""
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
    """English label for biological group code."""
    return GROUP_LABELS.get(group_code, group_code)


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
    """Return the classification/status value to add to the GIS occurrence export."""
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
    enriched_df["group_label"] = enriched_df["biogroup"].apply(group_label)

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
    enriched_df["match_status_label"] = enriched_df["match_status"].map(STATUS_LABELS)

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
    columns = [
        "Group",
        "Group code",
        "BioGIS name",
        "Suggested index name",
        "Score",
        "Index",
        "Records",
    ]

    if review_df.empty:
        return pd.DataFrame(columns=columns)

    result = (
        review_df.groupby(
            ["group_label", "biogroup", "species_heb_normalized", "suggested_name", "match_score", "matched_index"],
            dropna=False,
        )
        .size()
        .reset_index(name="Records")
    )

    result = result.rename(
        columns={
            "group_label": "Group",
            "biogroup": "Group code",
            "species_heb_normalized": "BioGIS name",
            "suggested_name": "Suggested index name",
            "match_score": "Score",
            "matched_index": "Index",
        }
    )

    return result[columns].sort_values(by=["Group", "Score"], ascending=[True, False])


def make_group_summary(enriched_df: pd.DataFrame) -> pd.DataFrame:
    """Summarize records and unique species by biological group."""
    count_column = "recordId" if "recordId" in enriched_df.columns else "species_heb_normalized"
    result = (
        enriched_df.groupby(["biogroup", "group_label"], dropna=False)
        .agg(
            Records=(count_column, "count"),
            **{"Unique species": ("species_heb_normalized", "nunique")},
        )
        .reset_index()
        .rename(columns={"biogroup": "Group code", "group_label": "Group"})
    )
    return result[["Group", "Records", "Unique species", "Group code"]]


def make_match_summary(enriched_df: pd.DataFrame) -> pd.DataFrame:
    """Summarize match statuses."""
    result = (
        enriched_df.groupby(["match_status", "match_status_label"], dropna=False)
        .agg(
            Records=("species_heb_normalized", "count"),
            **{"Unique species": ("species_heb_normalized", "nunique")},
        )
        .reset_index()
        .rename(columns={"match_status": "Status code", "match_status_label": "Status"})
    )
    return result[["Status", "Records", "Unique species", "Status code"]]


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
    source: str = "",
    score="",
):
    """Save a correction to the name_mapping sheet."""
    row_values = {
        "שם מקורי מ-BioGIS": original_name,
        "שם מתוקן באינדקס": corrected_name,
        "קבוצה": biogroup,
        "מקור תיקון": source,
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
        raise ValueError("New species can currently be added only to Plants or Vertebrates.")


def get_first_occurrence_for_name(enriched_df: pd.DataFrame, name: str, biogroup: str) -> pd.Series:
    """Return first occurrence row for a species name and group."""
    subset = enriched_df[
        (enriched_df["species_heb_normalized"] == name)
        & (enriched_df["biogroup"] == biogroup)
    ]
    if subset.empty:
        return pd.Series(dtype="object")
    return subset.iloc[0]


def to_float(value, default=0.0) -> float:
    try:
        if pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def prepare_batch_editor_df(
    review_df: pd.DataFrame,
    enriched_df: pd.DataFrame,
    biogroup: str,
    default_save_threshold: float,
) -> pd.DataFrame:
    """Create a compact editable table for batch corrections."""
    subset = review_df[review_df["Group code"] == biogroup].copy()

    if subset.empty:
        return pd.DataFrame()

    rows = []
    for _, record in subset.iterrows():
        original_name = record.get("BioGIS name", "")
        first_row = get_first_occurrence_for_name(enriched_df, original_name, biogroup)
        score = to_float(record.get("Score", 0))
        suggested = str(record.get("Suggested index name", "") or "")

        base = {
            "Save": score >= default_save_threshold,
            "Action": ACTION_SAVE_INDEX,
            "BioGIS name": original_name,
            "Suggested index name": suggested,
            "Correction from index": suggested,
            "Score": score,
            "Records": int(record.get("Records", 0) or 0),
            "New Hebrew name": original_name,
            "Scientific name": str(first_row.get("species", "") or ""),
        }

        if biogroup == "plants":
            base.update(
                {
                    "Family": str(first_row.get("family", "") or ""),
                    "Distribution type": "",
                    "Frequency": "",
                    "Classification": "",
                }
            )
        else:
            base.update(
                {
                    "Class": str(first_row.get("clazz", "") or ""),
                    "Regional status / classification": "",
                    "Global status": "",
                }
            )

        rows.append(base)

    return pd.DataFrame(rows)


def save_batch_corrections(
    edited_df: pd.DataFrame,
    biogroup: str,
    source_file_name: str,
) -> tuple[int, list[str]]:
    """Save all checked rows from a batch correction editor."""
    saved_count = 0
    errors = []

    if edited_df.empty:
        return saved_count, errors

    selected_rows = edited_df[edited_df["Save"] == True].copy()  # noqa: E712

    for row_index, row in selected_rows.iterrows():
        original_name = normalize_hebrew_name(row.get("BioGIS name", ""))
        action = row.get("Action", ACTION_SAVE_INDEX)
        score = row.get("Score", "")

        if not original_name:
            errors.append(f"Row {row_index + 1}: missing BioGIS name.")
            continue

        try:
            if action == ACTION_SAVE_INDEX:
                corrected_name = normalize_hebrew_name(row.get("Correction from index", ""))
                if not corrected_name:
                    errors.append(f"{original_name}: choose a correction from the index.")
                    continue

                append_name_mapping(
                    original_name=original_name,
                    corrected_name=corrected_name,
                    biogroup=biogroup,
                    source="batch_index_selection",
                    score=score,
                )
                saved_count += 1

            elif action == ACTION_ADD_NEW:
                new_hebrew = normalize_hebrew_name(row.get("New Hebrew name", "")) or original_name
                scientific_name = str(row.get("Scientific name", "") or "")

                if biogroup == "plants":
                    species_values = {
                        "שם המין": new_hebrew,
                        "שם מדעי": scientific_name,
                        "משפחה": str(row.get("Family", "") or ""),
                        "טיפוס התפוצה": str(row.get("Distribution type", "") or ""),
                        "שכיחות": str(row.get("Frequency", "") or ""),
                        "סיווג": str(row.get("Classification", "") or ""),
                    }
                else:
                    species_values = {
                        "שם המין": new_hebrew,
                        "שם מדעי": scientific_name,
                        "מחלקה": str(row.get("Class", "") or ""),
                        "סטטוס שימור אזורי": str(row.get("Regional status / classification", "") or ""),
                        "סטטוס שימור עולמי": str(row.get("Global status", "") or ""),
                    }

                append_new_species_to_index(biogroup, species_values)
                append_name_mapping(
                    original_name=original_name,
                    corrected_name=new_hebrew,
                    biogroup=biogroup,
                    source="batch_new_species",
                    score=100,
                )
                saved_count += 1

            elif action == ACTION_MARK_UNRESOLVED:
                append_unmatched_log(
                    original_name=original_name,
                    biogroup=biogroup,
                    suggestions=str(row.get("Suggested index name", "") or ""),
                    score=score,
                    source_file_name=source_file_name,
                )
                saved_count += 1

            elif action == ACTION_SKIP:
                continue

        except Exception as exc:
            errors.append(f"{original_name}: {exc}")

    return saved_count, errors


def render_batch_editor(
    title: str,
    biogroup: str,
    review_df: pd.DataFrame,
    enriched_df: pd.DataFrame,
    index_df: pd.DataFrame,
    source_file_name: str,
    default_save_threshold: float,
):
    """Render one group-specific batch correction table."""
    editor_df = prepare_batch_editor_df(
        review_df=review_df,
        enriched_df=enriched_df,
        biogroup=biogroup,
        default_save_threshold=default_save_threshold,
    )

    st.markdown(f"### {title}")

    if editor_df.empty:
        st.success(f"No {title.lower()} names need manual review.")
        return

    index_names = sorted(index_df["שם המין"].dropna().astype(str).unique())
    for value in editor_df["Suggested index name"].dropna().astype(str).unique():
        if value and value not in index_names:
            index_names.append(value)
    index_names = [""] + sorted(set(index_names))

    common_disabled = ["BioGIS name", "Suggested index name", "Score", "Records"]
    if biogroup == "plants":
        column_config = {
            "Save": st.column_config.CheckboxColumn("Save", help="Rows checked here will be saved when you press the save button."),
            "Action": st.column_config.SelectboxColumn("Action", options=BATCH_ACTIONS),
            "Correction from index": st.column_config.SelectboxColumn("Correction from index", options=index_names),
            "Classification": st.column_config.TextColumn("Classification", help="Used only when adding a new plant to the index."),
        }
        disabled = common_disabled
    else:
        column_config = {
            "Save": st.column_config.CheckboxColumn("Save", help="Rows checked here will be saved when you press the save button."),
            "Action": st.column_config.SelectboxColumn("Action", options=BATCH_ACTIONS),
            "Correction from index": st.column_config.SelectboxColumn("Correction from index", options=index_names),
            "Regional status / classification": st.column_config.TextColumn(
                "Regional status / classification",
                help="Used only when adding a new vertebrate to the index.",
            ),
        }
        disabled = common_disabled

    edited_df = st.data_editor(
        editor_df,
        key=f"batch_editor_{biogroup}",
        use_container_width=True,
        hide_index=True,
        num_rows="fixed",
        disabled=disabled,
        column_config=column_config,
    )

    checked_count = int((edited_df["Save"] == True).sum())  # noqa: E712
    st.caption(
        f"Rows checked for saving: {checked_count}. "
        "For existing species, keep 'Save selected index name'. "
        "For species missing from the index, choose 'Add as new species to index' and fill the new-species fields."
    )

    if st.button(f"Save checked {title.lower()} corrections", type="primary", key=f"save_{biogroup}"):
        saved_count, errors = save_batch_corrections(
            edited_df=edited_df,
            biogroup=biogroup,
            source_file_name=source_file_name,
        )

        if errors:
            st.error("Some rows were not saved:")
            for error in errors:
                st.write(f"- {error}")

        if saved_count > 0 and not errors:
            clear_cache_and_rerun(f"Saved {saved_count} corrections.")
        elif saved_count > 0:
            st.success(f"Saved {saved_count} corrections. Fix the remaining rows and save again.")
            st.cache_data.clear()


def render_batch_correction_panel(
    review_df: pd.DataFrame,
    enriched_df: pd.DataFrame,
    plants_df: pd.DataFrame,
    animals_df: pd.DataFrame,
    source_file_name: str,
):
    """Render all manual correction controls as batch-editable tables."""
    st.subheader("Batch name correction")

    if review_df.empty:
        st.success("No plant or vertebrate names need manual review.")
        return

    st.write(
        "Review all suggested corrections in a table. The proposed correction is editable as a dropdown. "
        "If a species is missing from the index, choose 'Add as new species to index' and fill the manual fields in the same row."
    )

    default_save_threshold = st.slider(
        "Pre-check rows with score at least",
        min_value=0,
        max_value=100,
        value=90,
        step=1,
        help="Rows with a score at or above this value will be checked automatically. You can uncheck any row before saving.",
    )

    tab1, tab2, tab3 = st.tabs(["Plants", "Vertebrates", "All names needing review"])

    with tab1:
        render_batch_editor(
            title="Plants",
            biogroup="plants",
            review_df=review_df,
            enriched_df=enriched_df,
            index_df=plants_df,
            source_file_name=source_file_name,
            default_save_threshold=default_save_threshold,
        )

    with tab2:
        render_batch_editor(
            title="Vertebrates",
            biogroup="vertebrates",
            review_df=review_df,
            enriched_df=enriched_df,
            index_df=animals_df,
            source_file_name=source_file_name,
            default_save_threshold=default_save_threshold,
        )

    with tab3:
        st.dataframe(review_df, use_container_width=True)


# -----------------------------------------------------------------------------
# Main Streamlit app
# -----------------------------------------------------------------------------


def main():
    st.set_page_config(page_title="BioGIS Converter", layout="wide")
    apply_page_style()

    st.title("BioGIS Converter")
    st.caption("Species-name correction, index updates, and outputs for reports and GIS")

    st.subheader("Index connection")

    try:
        plants_df = load_sheet_as_dataframe(PLANTS_SHEET)
        animals_df = load_sheet_as_dataframe(ANIMALS_SHEET)
        mapping_df = load_name_mapping()
        st.success("Connected to Google Sheets")
    except Exception as exc:
        st.error("Google Sheets connection failed")
        st.exception(exc)
        st.stop()

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Plant index species", len(plants_df))
    with col2:
        st.metric("Vertebrate index species", len(animals_df))
    with col3:
        st.metric("Active name corrections", count_active_mappings(mapping_df))

    with st.expander("Show index and mapping samples"):
        tab1, tab2, tab3 = st.tabs(["Plants", "Vertebrates", "Name mapping"])
        with tab1:
            st.dataframe(plants_df.head(10), use_container_width=True)
        with tab2:
            st.dataframe(animals_df.head(10), use_container_width=True)
        with tab3:
            st.dataframe(mapping_df.tail(20), use_container_width=True)

    st.subheader("Upload BioGIS occurrence CSV")

    uploaded_file = st.file_uploader("Upload CSV file", type=["csv"])
    if uploaded_file is None:
        st.info("Upload an occurrence CSV to start processing.")
        return

    occurrence_df = read_occurrence_csv(uploaded_file)
    st.success("Occurrence file loaded")

    required_columns = ["species_heb", "kingdom", "clazz", "phylum", "group"]
    missing_columns = [column for column in required_columns if column not in occurrence_df.columns]
    if missing_columns:
        st.error("The occurrence file is missing required columns:")
        st.code(", ".join(missing_columns))
        st.stop()

    with st.expander("Occurrence columns"):
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

    st.subheader("Occurrence summary")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Occurrence records", len(enriched_df))
    col2.metric("Unique species", enriched_df["species_heb_normalized"].nunique())
    col3.metric(
        "Need manual review",
        enriched_df[enriched_df["match_status"] == "needs_review"]["species_heb_normalized"].nunique(),
    )
    col4.metric(
        "Outside indexed groups",
        enriched_df[enriched_df["match_status"] == "not_indexed_group"]["species_heb_normalized"].nunique(),
    )

    col1, col2 = st.columns(2)
    with col1:
        st.write("By biological group")
        st.dataframe(group_summary_df, use_container_width=True)
    with col2:
        st.write("By matching status")
        st.dataframe(match_summary_df, use_container_width=True)

    render_batch_correction_panel(
        review_df=review_df,
        enriched_df=enriched_df,
        plants_df=plants_df,
        animals_df=animals_df,
        source_file_name=uploaded_file.name,
    )

    st.subheader("Output preview")

    tabs = st.tabs(["Plants", "Vertebrates", "Invertebrates", "Fungi", "Unknown", "GIS occurrence"])

    with tabs[0]:
        st.write("Plant report table. Only exact matches and saved corrections are included.")
        st.dataframe(plants_report_df, use_container_width=True)
    with tabs[1]:
        st.write("Vertebrate report table. Only exact matches and saved corrections are included.")
        st.dataframe(vertebrates_report_df, use_container_width=True)
    with tabs[2]:
        st.write("Invertebrates are exported separately and are not matched against the vertebrate index.")
        st.dataframe(invertebrates_report_df, use_container_width=True)
    with tabs[3]:
        st.write("Fungi are exported separately and are not matched against the plant index.")
        st.dataframe(fungi_report_df, use_container_width=True)
    with tabs[4]:
        st.write("Records that could not be classified.")
        st.dataframe(unknown_report_df, use_container_width=True)
    with tabs[5]:
        st.write("Original occurrence columns + species_heb_corrected + classification")
        st.dataframe(gis_occurrence_df.head(200), use_container_width=True)

    st.subheader("Downloads")

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
            label="Download GIS occurrence CSV",
            data=to_csv_bytes(gis_occurrence_df),
            file_name="occurrences_enriched.csv",
            mime="text/csv",
        )
    with col2:
        st.download_button(
            label="Download all outputs as Excel",
            data=excel_bytes,
            file_name="biogis_outputs.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    with col3:
        st.download_button(
            label="Download review list CSV",
            data=to_csv_bytes(review_df),
            file_name="review_needed.csv",
            mime="text/csv",
        )


if __name__ == "__main__":
    main()
