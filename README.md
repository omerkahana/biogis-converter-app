# BioGIS Converter - Step 1

אפליקציית Streamlit ראשונית לחיבור אל Google Sheets ולבדיקת קובץ occurrence מ-BioGIS.

## הרצה מקומית

```bash
pip install -r requirements.txt
streamlit run app.py
```

## הרשאות Google Sheets

האפליקציה קוראת את הגיליון לפי SHEET_ID בתוך app.py.
כדי שקריאה תעבוד, צריך:

1. ליצור Service Account ב-Google Cloud.
2. להוריד JSON key.
3. להעתיק את פרטי ה-JSON אל `.streamlit/secrets.toml` לפי הדוגמה בקובץ `.streamlit/secrets.example.toml`.
4. לשתף את Google Sheet עם כתובת ה-client_email של ה-Service Account בהרשאת Editor.
