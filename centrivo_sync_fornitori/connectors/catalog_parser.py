# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Lettura file fornitore (XLSX/CSV) → (intestazioni reali, righe).

Responsabilità: trasformare i byte di un file CARICATO A MANO (xlsx o csv) in una
lista di righe (dict header→valore), leggendo le **intestazioni reali** (riga 1)
senza assumere alcun formato. NON sa nulla dell'ORM Odoo né del mapping: la
mappatura colonna→destinazione è dinamica e vive nel wizard/canale (spec v2.x).

Riuso da TASK_78: i value-helper (normalize_code/to_text/to_float/clean_long_text)
e il parser xlsx (openpyxl). NOVITÀ v2: parser CSV con rilevamento separatore e un
dispatcher read_table(content, filename). Rimossi i nomi-colonna cloudhub cablati.
"""
import csv
import io


# ---------------------------------------------------------------------------
# Value helpers (riusati da TASK_78)
# ---------------------------------------------------------------------------
def normalize_code(value):
    """Codice articolo come STRINGA, preservando gli zeri iniziali (spec §5).

    - None → "".
    - float intero (es. 17002.0) → "17002".
    - se tutto cifre e più corto di 7 → zfill(7) (formato "7 cifre con zeri").
    """
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    if text.isdigit() and len(text) < 7:
        text = text.zfill(7)
    return text


def to_text(value):
    """Valore come testo pulito; float interi senza ".0"; None → ""."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def to_float(value):
    """Valore numerico tollerante (gestisce virgola decimale); None/vuoto → 0.0."""
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(" ", "")
    if "," in text and "." not in text:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return 0.0


def clean_long_text(value):
    """Ripulisce la descrizione lunga: marcatori _x000D_ (CR Windows) → newline."""
    if value is None:
        return ""
    text = str(value).replace("_x000D_", "\n")
    return text.strip()


# ---------------------------------------------------------------------------
# Parser XLSX (openpyxl) — riuso da TASK_78
# ---------------------------------------------------------------------------
def parse_xlsx(content_bytes):
    """Legge un xlsx e ritorna (headers, rows). Solleva ValueError se illeggibile."""
    import openpyxl

    try:
        workbook = openpyxl.load_workbook(
            io.BytesIO(content_bytes), read_only=True, data_only=True)
    except Exception as exc:
        raise ValueError("File non leggibile come xlsx: %s" % exc)

    worksheet = workbook.active
    row_iter = worksheet.iter_rows(values_only=True)
    header_row = next(row_iter, None)
    if not header_row:
        raise ValueError("File privo di riga di intestazione.")

    headers = [to_text(cell) for cell in header_row]
    rows = []
    for raw in row_iter:
        if raw is None:
            continue
        if all(cell is None or to_text(cell) == "" for cell in raw):
            continue
        record = {}
        for index, header in enumerate(headers):
            if not header:
                continue
            record[header] = raw[index] if index < len(raw) else None
        rows.append(record)

    try:
        workbook.close()
    except Exception:
        pass
    return headers, rows


# ---------------------------------------------------------------------------
# Parser CSV con rilevamento separatore (NOVITÀ v2)
# ---------------------------------------------------------------------------
_CSV_SEPARATORS = ("|", ";", ",", "\t")


def _decode_csv(content_bytes):
    """Decodifica i byte: prova UTF-8 (con BOM), fallback Latin-1 (nodo §18)."""
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return content_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    # Ultima spiaggia: latin-1 non solleva mai.
    return content_bytes.decode("latin-1", errors="replace")


def _detect_separator(header_line):
    """Sceglie il separatore più probabile contando le occorrenze nell'header.

    Ordine di preferenza a parità: | ; , TAB (i csv giacenze usano '|').
    """
    counts = {sep: header_line.count(sep) for sep in _CSV_SEPARATORS}
    best = max(_CSV_SEPARATORS, key=lambda s: counts[s])
    return best if counts[best] > 0 else ","


def parse_csv(content_bytes):
    """Legge un csv (separatore rilevato) e ritorna (headers, rows)."""
    text = _decode_csv(content_bytes)
    if not text.strip():
        raise ValueError("File CSV vuoto.")
    first_line = text.splitlines()[0] if text.splitlines() else text
    separator = _detect_separator(first_line)

    reader = csv.reader(io.StringIO(text), delimiter=separator)
    raw_rows = list(reader)
    if not raw_rows:
        raise ValueError("File CSV privo di righe.")

    headers = [to_text(cell) for cell in raw_rows[0]]
    rows = []
    for raw in raw_rows[1:]:
        if not any((cell or "").strip() for cell in raw):
            continue
        record = {}
        for index, header in enumerate(headers):
            if not header:
                continue
            record[header] = raw[index] if index < len(raw) else None
        rows.append(record)
    return headers, rows


# ---------------------------------------------------------------------------
# Dispatcher: sceglie il parser dall'estensione, con fallback
# ---------------------------------------------------------------------------
def read_table(content_bytes, filename=None):
    """Legge un file caricato a mano e ritorna (headers, rows).

    Sceglie il parser dall'estensione del filename; se ambigua, prova xlsx e poi
    csv. Le righe sono dict {header: valore}; le intestazioni sono quelle REALI
    della riga 1.
    """
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xlsm", ".xls")):
        return parse_xlsx(content_bytes)
    if name.endswith((".csv", ".txt", ".tsv")):
        return parse_csv(content_bytes)
    # Estensione ignota: tenta xlsx (firma ZIP), poi csv.
    try:
        return parse_xlsx(content_bytes)
    except Exception:
        return parse_csv(content_bytes)
