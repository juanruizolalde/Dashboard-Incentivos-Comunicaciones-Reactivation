#!/usr/bin/env python3
"""
Actualiza index.html (dashboard Reactivation) leyendo los datos directamente
desde el Google Sheet alimentado por Aleph.

Version gemela del script usado para el dashboard Activation. El dashboard de
Reactivation usa los mismos nombres de campo en mayusculas (RTS_COMUNICADOS,
SUBS_COMUNICADOS, etc.) -- unica diferencia real: usa date_range.from /
date_range.to (no start/end).

Requiere la variable de entorno GCP_SA_KEY con el JSON completo de la MISMA
service account de Google ya creada para el dashboard de Activation (se
reutiliza -- no hace falta crear una nueva). Ver GUIA.md de este dashboard.
"""
import os
import re
import sys
import json
import collections
import datetime

from google.oauth2 import service_account
from googleapiclient.discovery import build

# ---------------------------------------------------------------------------
# Configuracion
# ---------------------------------------------------------------------------
SHEET_ID = "12ESyFI5vrAzMRJ03FIzHnV8skkK4w4AQvZFcZ9MItVk"  # mismo sheet que Activation
TAB_Q1 = "Reactivation query 1"
TAB_Q2 = "Reactivation query 2"
# Headers en fila 13, datos desde fila 14, columnas desde B (16 y 6 columnas resp.)
RANGE_Q1 = f"'{TAB_Q1}'!B13:Q5000"
RANGE_Q2 = f"'{TAB_Q2}'!B13:G5000"
LAST_UPDATED_CELL = f"'{TAB_Q1}'!C7"

DASHBOARD_PATH = "index.html"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

NUM_FIELDS = [
    "NUM_INCENTIVOS", "INCENTIVOS_CON_COMMS", "RTS_COMUNICADOS",
    "RTS_NO_COMUNICADOS", "RTS_CONTROL", "SUBS_COMUNICADOS",
    "SUBS_NO_COMUNICADOS", "SUBS_CONTROL", "WINNERS_COMUNICADOS",
    "WINNERS_NO_COMUNICADOS", "WINNERS_CONTROL",
]


def get_sheets_service():
    key_json = os.environ.get("GCP_SA_KEY")
    if not key_json:
        print("ERROR: falta la variable de entorno GCP_SA_KEY", file=sys.stderr)
        sys.exit(1)
    info = json.loads(key_json)
    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    return build("sheets", "v4", credentials=creds)


def fetch_values(service, range_name):
    resp = service.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range=range_name
    ).execute()
    return resp.get("values", [])


def rows_as_dicts(values):
    """values[0] = headers, resto = filas. Descarta filas vacias (WEEK_START vacio)."""
    if not values:
        return []
    headers = [h.strip() for h in values[0]]
    out = []
    for row in values[1:]:
        if not row or not row[0].strip():
            continue
        padded = row + [""] * (len(headers) - len(row))
        out.append(dict(zip(headers, padded)))
    return out


def to_int(v):
    v = (v or "").strip().replace(",", "")
    if v == "":
        return 0
    return int(float(v))


def to_float_or_none(v):
    v = (v or "").strip()
    if v == "":
        return None
    return float(v)


def normtag(t):
    t = t.strip()
    return "Base" if t == "base" else t


def main():
    service = get_sheets_service()

    q1_values = fetch_values(service, RANGE_Q1)
    q2_values = fetch_values(service, RANGE_Q2)
    last_updated_values = fetch_values(service, LAST_UPDATED_CELL)
    last_updated_raw = (last_updated_values[0][0] if last_updated_values and last_updated_values[0] else "").strip()

    q1_rows = rows_as_dicts(q1_values)
    q2_rows = rows_as_dicts(q2_values)
    print(f"Query 1: {len(q1_rows)} filas leidas del sheet")
    print(f"Query 2: {len(q2_rows)} filas leidas del sheet")

    if not q1_rows or not q2_rows:
        print("ERROR: una de las dos hojas vino vacia, no se actualiza el dashboard", file=sys.stderr)
        sys.exit(1)

    # --- Agregacion Query 1: por (week, country, tag, incentive_type) ---
    agg = collections.defaultdict(lambda: collections.defaultdict(int))
    for r in q1_rows:
        cls = r.get("INCENTIVE_CLASSIFICATION", "").strip()
        tag = r.get("TAG", "").strip()
        itype = r.get("INCENTIVE_TYPE", "").strip()
        if cls == "Jarvis" or tag == "":
            continue
        tag = normtag(tag)
        week = r.get("WEEK_START", "").strip()
        country = r.get("COUNTRY", "").strip()
        key = (week, country, tag, itype)
        for nf in NUM_FIELDS:
            agg[key][nf] += to_int(r.get(nf, ""))

    metrics_rows = []
    for (week, country, tag, itype), vals in sorted(agg.items()):
        row = {"week": week, "country": country, "tag": tag, "incentive_type": itype}
        row.update({k: vals[k] for k in NUM_FIELDS})
        metrics_rows.append(row)

    weeks = sorted(set(r["week"] for r in metrics_rows))
    countries = sorted(set(r["country"] for r in metrics_rows))
    tags = sorted(set(r["tag"] for r in metrics_rows))
    incentive_types = sorted(set(r["incentive_type"] for r in metrics_rows))

    # --- Query 2: productividad ---
    prod_rows = []
    for r in q2_rows:
        prod_rows.append({
            "week": r.get("WEEK_START", "").strip(),
            "country": r.get("COUNTRY", "").strip(),
            "group": r.get("GROUP_TYPE", "").strip(),
            "rts": to_int(r.get("RTS", "")),
            "avg_hours": to_float_or_none(r.get("AVG_HOURS", "")),
            "avg_orders": to_float_or_none(r.get("AVG_ORDERS", "")),
        })

    date_range = {"from": min(weeks), "to": max(weeks)}  # este dashboard usa from/to, no start/end

    DATA = {
        "weeks": weeks,
        "countries": countries,
        "tags": tags,
        "incentive_types": incentive_types,
        "metrics": metrics_rows,
        "productivity": prod_rows,
        "date_range": date_range,
        "incentive_classification": "Reactivation",
        # nota: este dashboard no tiene widget de "Actualizado" todavia -- este
        # campo queda guardado en el DATA por si mas adelante se agrega (ver GUIA.md)
        "generated_at": last_updated_raw or datetime.date.today().isoformat(),
    }

    # --- Sanity checks basicos (no bloquean, solo loggean) ---
    tot_inc = sum(r["NUM_INCENTIVOS"] for r in metrics_rows)
    tot_subs_ctrl = sum(r["SUBS_CONTROL"] for r in metrics_rows)
    tot_win_ctrl = sum(r["WINNERS_CONTROL"] for r in metrics_rows)
    print(f"Total incentivos: {tot_inc}")
    print(f"Subs control (esperado ~0): {tot_subs_ctrl}")
    print(f"Winners control (esperado ~0): {tot_win_ctrl}")
    print(f"Semanas: {weeks}")
    print(f"Ultima actualizacion (sheet): {last_updated_raw}")

    # --- Reemplazar el bloque const DATA = {...}; en index.html ---
    if not os.path.exists(DASHBOARD_PATH):
        print(f"ERROR: no se encontro {DASHBOARD_PATH}", file=sys.stderr)
        sys.exit(1)

    with open(DASHBOARD_PATH, encoding="utf-8") as f:
        html = f.read()

    new_data_json = json.dumps(DATA, ensure_ascii=False, separators=(",", ":"))
    pattern = re.compile(r"const DATA = \{.*?\};\n", re.S)
    if not pattern.search(html):
        print("ERROR: no se encontro el bloque 'const DATA = {...};' en index.html", file=sys.stderr)
        sys.exit(1)

    new_html = pattern.sub(
        "const DATA = " + new_data_json.replace("\\", "\\\\") + ";\n", html, count=1
    )

    if new_html == html:
        print("Sin cambios en los datos, no se reescribe index.html")
        return

    with open(DASHBOARD_PATH, "w", encoding="utf-8") as f:
        f.write(new_html)
    print("index.html actualizado correctamente")


if __name__ == "__main__":
    main()
