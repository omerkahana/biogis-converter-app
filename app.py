import json
from pathlib import Path

import gspread
import pandas as pd
import streamlit as st
from google.oauth2.service_account import Credentials
from rapidfuzz import process, fuzz

SHEET_ID = "1b6qgqHh6g3VifXtxDCG-g3zGSpqt8mkktJEruo0qpjQ"

PLANTS_SHEET = "plants_index"
ANIMALS_SHEET = "animals_index"
MAPPING_SHEET = "name_mapping"
UNMATCHED_SHEET = "unmatched_log"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def get_google_client():
    """Create an authorized gspread client from Streamlit secrets or local JSON."""
    if "gcp_service_account" in st.secrets:
        service_account_info = dict(st.secrets["gcp_service_account"])
        creds = Credentials.from_service_account_info(service_account_info, scopes=SCOPES)
        return gspread.authorize(creds)

    local_key = Path("service_account.json")
    if local_key.exists():
        creds = Credentials.from_service_account_file(str(local_key), scopes=SCOPES)
        return gspread.authorize(creds)

    st.error(
        "לא נמצאו הרשאות Google. צריך ליצור service_account.json מקומי "
        "או להגדיר .streamlit/secrets.toml."
    )
    st.stop()


@st.cache_data(ttl=300)
def load_sheet_as_dataframe(sheet_name: str) -> pd.DataFrame:
    client = get_google_client()
    spreadsheet = client.open_by_key(SHEET_ID)
    worksheet = spreadsheet.worksheet(sheet_name)
    rows = worksheet.get_all_records()
    return pd.DataFrame(rows)


def normalize_hebrew_name(value: str) -> str:
    """Basic normalization for Hebrew species names."""
    if pd.isna(value):
        return ""
    text = str(value).strip()
    replacements = {
        "־": "-",
        "–": "-",
        "—": "-",
        "\u200f": "",
        "\u200e": "",
        "\xa0": " ",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = " ".join(text.split())
    return text


def find_best_match(name: str, choices: list[str]):
    """Return best fuzzy match for a Hebrew name."""
    if not name or not choices:
        return None, 0
    result = process.extractOne(name, choices, scorer=fuzz.WRatio)
    if result is None:
        return None, 0
    match_name, score, _ = result
    return match_name, score


def read_occurrence_csv(uploaded_file) -> pd.DataFrame:
    """Read BioGIS CSV with a few common encodings."""
    for encoding in ["utf-8-sig", "utf-8", "cp1255", "iso-8859-8"]:
        try:
            uploaded_file.seek(0)
            return pd.read_csv(uploaded_file, encoding=encoding)
        except UnicodeDecodeError:
            continue
    uploaded_file.seek(0)
    return pd.read_csv(uploaded_file)


def main():
    st.set_page_config(page_title="BioGIS Converter", layout="wide")

    st.title("BioGIS Converter")
    st.caption("שלב 1: חיבור ל-Google Sheets ובדיקת occurrence")

    st.subheader("1. בדיקת חיבור לאינדקסים")

    try:
        plants_df = load_sheet_as_dataframe(PLANTS_SHEET)
        animals_df = load_sheet_as_dataframe(ANIMALS_SHEET)
        st.success("החיבור ל-Google Sheets הצליח")
    except Exception as exc:
        st.error("החיבור ל-Google Sheets נכשל")
        st.exception(exc)
        st.stop()

    col1, col2 = st.columns(2)
    with col1:
        st.metric("מינים באינדקס צומח", len(plants_df))
        st.dataframe(plants_df.head(10), use_container_width=True)
    with col2:
        st.metric("מינים באינדקס בעלי חיים", len(animals_df))
        st.dataframe(animals_df.head(10), use_container_width=True)

    st.subheader("2. העלאת קובץ occurrence מ-BioGIS")
    uploaded_file = st.file_uploader("העלה קובץ CSV", type=["csv"])

    if uploaded_file is None:
        st.info("כאן יופיע ניתוח ראשוני אחרי העלאת קובץ occurrence.")
        return

    occurrence_df = read_occurrence_csv(uploaded_file)
    st.success("קובץ occurrence נטען בהצלחה")

    st.write("עמודות בקובץ:")
    st.code(", ".join(occurrence_df.columns.astype(str)))

    if "species_heb" not in occurrence_df.columns:
        st.error("לא נמצאה עמודה בשם species_heb בקובץ occurrence.")
        st.stop()

    occurrence_df["species_heb_original"] = occurrence_df["species_heb"]
    occurrence_df["species_heb_normalized"] = occurrence_df["species_heb"].apply(normalize_hebrew_name)

    unique_names = sorted(
        name for name in occurrence_df["species_heb_normalized"].dropna().unique() if name
    )

    plant_names = plants_df["שם המין"].dropna().astype(str).map(normalize_hebrew_name).tolist()
    animal_names = animals_df["שם המין"].dropna().astype(str).map(normalize_hebrew_name).tolist()
    all_index_names = sorted(set(plant_names + animal_names))

    exact_matches = [name for name in unique_names if name in all_index_names]
    unmatched = [name for name in unique_names if name not in all_index_names]

    col1, col2, col3 = st.columns(3)
    col1.metric("רשומות occurrence", len(occurrence_df))
    col2.metric("מינים ייחודיים", len(unique_names))
    col3.metric("לא נמצאה התאמה מדויקת", len(unmatched))

    st.subheader("3. שמות ללא התאמה מדויקת - הצעות ראשוניות")

    suggestions = []
    for name in unmatched:
        best_name, score = find_best_match(name, all_index_names)
        suggestions.append(
            {
                "שם מ-BioGIS": name,
                "הצעה קרובה": best_name or "",
                "ציון התאמה": score,
            }
        )

    suggestions_df = pd.DataFrame(suggestions).sort_values(
        by="ציון התאמה", ascending=False
    )
    st.dataframe(suggestions_df, use_container_width=True)

    st.subheader("4. תצוגת occurrence")
    st.dataframe(occurrence_df.head(100), use_container_width=True)


if __name__ == "__main__":
    main()
