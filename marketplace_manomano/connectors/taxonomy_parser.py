# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Interpretazione della Taxonomy ManoMano (GET /api/v2/feeds/fields).

Modulo PURO: nessun import Odoo, così la lettura del formato esterno si prova in
isolamento (tools/test_manomano_taxonomy.py). Rif. docs/manomano-api-reference.md.
"""

# Lingua dei valori che finiscono nel CSV e negli attributi Odoo.
DEFAULT_LOCALE = "it_IT"
FALLBACK_LOCALE = "en_GB"

# Tipo di contratto di HD Casa: vende e spedisce da sé (non ManoFulfillment).
# I campi marcati "mf" riguardano solo la logistica di ManoMano.
SELLER_CONTRACT_TYPE = "non_mf"


def _text(mapping, locale=DEFAULT_LOCALE):
    """Valore localizzato con ripiego sull'inglese, poi su qualunque lingua."""
    if not isinstance(mapping, dict) or not mapping:
        return ""
    for key in (locale, FALLBACK_LOCALE):
        value = mapping.get(key)
        if value:
            return value
    for value in mapping.values():
        if value:
            return value
    return ""


def parse_field(entry, locale=DEFAULT_LOCALE):
    """Un campo della risposta API → valori per centrivo.manomano.feed.field."""
    return {
        "mm_id": str(entry.get("id") or "").strip(),
        "name": (entry.get("name") or "").strip(),
        "label": _text(entry.get("names"), locale) or (entry.get("name") or ""),
        "description": _text(entry.get("descriptions"), locale),
        "mandatory": bool(entry.get("mandatory")),
        "datatype": (entry.get("datatype") or "").strip(),
        "contract_type": (entry.get("contract_type") or "").strip(),
        "is_variant": bool(entry.get("variant")),
        "units_list_id": (entry.get("field_id_for_units_list") or "").strip(),
    }


def pair_values(entry, locale=DEFAULT_LOCALE):
    """Valori ammessi → [(nome_localizzato, nome_sorgente)], più un'anomalia.

    `accepted_values` e `localized_accepted_values[locale]` sono liste PARALLELE
    nello stesso ordine: l'accoppiata è POSIZIONALE. Se la traduzione manca o ha
    lunghezza diversa (caso reale: unit_count_type ha solo en_GB) si usa il
    valore sorgente e si RESTITUISCE un'anomalia: mai accoppiare a indovinare.
    """
    source = entry.get("accepted_values") or []
    if not source:
        return [], None
    localized = (entry.get("localized_accepted_values") or {}).get(locale) or []
    if len(localized) != len(source):
        anomaly = (
            "Campo %s: %s valori ammessi ma %s traduzioni in %s — si usano i "
            "valori originali." % (entry.get("name"), len(source),
                                   len(localized), locale))
        return [(str(v), str(v)) for v in source], anomaly
    return [(str(loc), str(src)) for loc, src in zip(localized, source)], None


def is_relevant(entry, contract=SELLER_CONTRACT_TYPE):
    """Il campo riguarda il nostro tipo di contratto? ('all' vale per tutti)."""
    field_contract = (entry.get("contract_type") or "all").strip()
    return field_contract in ("all", contract)
