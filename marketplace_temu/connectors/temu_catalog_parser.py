# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Lettura delle risposte di catalogo Temu e aggancio ai prodotti Odoo.

Funzioni PURE: nessun import Odoo, nessuna rete, tutto testabile a parte.

NOTA SUI NOMI DEI CAMPI: la documentazione elenca le operazioni e gli stati, ma
il nome esatto della lista dentro la risposta non e' garantito. Per questo la
lettura prova piu' nomi noti e, se non trova nulla, restituisce una lista vuota
SENZA sollevare eccezioni: il corpo grezzo viene comunque scritto nel log
operazioni, che resta la misura vera. Alla prima risposta reale si riallinea
CANDIDATE_LIST_KEYS e basta.
"""

# Nomi possibili della lista di righe dentro `result`, in ordine di preferenza.
CANDIDATE_LIST_KEYS = (
    "subOrderForSaleList",
    "skuList",
    "goodsSkuList",
    "goodsList",
    "dataList",
    "list",
)

# Nomi possibili del totale.
CANDIDATE_TOTAL_KEYS = ("totalCount", "total", "totalItemNum")

# Nomi dei campi dentro la singola riga. I PRIMI di ogni terna sono quelli
# osservati nella risposta vera del negozio HDcasa (2026-08-20); gli altri
# erano quelli dedotti dalla documentazione e si tengono come ripiego, perche'
# costano nulla e coprono l'ipotesi che Temu li riporti in futuro.
# Lezione della prima chiamata reale: il codice venditore NON si chiama
# `outSkuSn` ma `skuSn`, e leggere il nome sbagliato non da' errore — da'
# semplicemente righe senza codice, che diventano tutte "orfane".
CANDIDATE_SKU_CODE_KEYS = ("skuSn", "outSkuSn", "extCode")
CANDIDATE_STATUS_KEYS = ("status4VO", "skuStatus")
# Il sottostato: `skuShowSubStatus4VO` porta il codice a quattro cifre (3001),
# l'unico che ha la forma delle etichette qui sotto; `subStatus4VO` porta un
# numero a una cifra (2 o 3) che non e' mappabile. ATTENZIONE, punto aperto:
# sul negozio vero tutte le righe lette riportano 3001, che la tabella qui
# sotto chiama "Fuori vendita per sanzione", eppure Temu ne segna 38 su 100
# come in vendita (`goodsIsOnSale`). O l'etichetta e' sbagliata, o 3001
# significa altro: finche' non e' chiarito, le etichette NON vanno prese per
# buone in nessuna decisione automatica.
CANDIDATE_SUB_STATUS_KEYS = ("skuShowSubStatus4VO", "subStatus4VO",
                             "skuSubStatus")

# Il dettaglio operativo `subStatus4VO`: una scala DIVERSA, da 1 a 14
# (`StatusFilterEnum` nella Product Listing and Delisting Guide). Non e' un
# doppione del sottostato qui sopra: dove quello dice "fuori vendita per
# sanzione", questo distingue se la sanzione e' in corso, finita, o se si e'
# in attesa di una revisione supplementare. Sul negozio HDcasa le 740 SKU si
# ripartivano fra 2, 3 e 12: tre situazioni molto diverse che il solo
# sottostato "da mostrare" appiattiva in un unico 3001.
CANDIDATE_DETAIL_KEYS = ("subStatus4VO",)

DETAIL_LABELS = {
    "1": "Esaurito",
    "2": "Delistato a mano, o sanzione finita/annullata",
    "3": "Rimosso per sanzione",
    "4": "Servono documenti di qualifica entro la scadenza",
    "5": "In elaborazione",
    "6": "Revisione della qualifica",
    "7": "Pubblicazione fallita",
    "8": "A scaffale",
    "9": "Prezzo non accettato",
    "10": "Valutazione prezzo in corso",
    "11": "Revisione in corso",
    "12": "Revisione supplementare",
    "13": "Eliminato",
    "14": "Eliminazione rifiutata per valutazione prezzo",
}

# Sottostati "da mostrare" (`skuShowSubStatus4VO`). Tabella presa dalla
# documentazione ufficiale — Product Listing and Delisting Guide, sezione
# "skuShowSubStatus4VO / goodsShowSubStatus" — letta il 2026-08-20.
# ⚠️ NON confondere con `subStatus4VO`, che e' un'altra scala (1..14) e porta
# un dettaglio operativo diverso: vedi docs/temu-api-reference.md.
STATUS_LABELS = {
    "2001": "In vendita",
    "2002": "In vendita, servono documenti di qualifica entro la scadenza",
    "2003": "In vendita, sanzione in arrivo",
    "2004": "In vendita, avviso di sanzione",
    "2005": "In vendita, categoria da rettificare",
    "2006": "In vendita, varianti da rettificare",
    "2101": "In vendita, traffico basso: prezzo da rivedere",
    "3001": "Fuori vendita per sanzione",
    "3002": "Fuori vendita per operazione (ferie, scelta del venditore, operativita')",
    "3003": "Esaurita",
    "3004": "Delistata per prezzo alto",
    "4001": "Valutazione prezzo in corso",
    "4002": "Revisione in corso",
    "4003": "Prezzo non accettato da Temu",
    "4004": "Scheda incompleta",
    "5001": "Bozza mai inviata",
    "6001": "Eliminata",
    "6002": "Eliminata dopo rifiuto del prezzo",
}

# Sottostati in cui Temu NON accetta aggiornamenti della scheda: chi si trova
# qui va saltato scrivendo il motivo, non forzato.
NON_UPDATABLE_SUB_STATUS = ("4001", "4002", "6001", "6002")


def _text(value):
    """Valore come stringa pulita ('' se assente). Gli identificativi Temu sono
    numeri lunghi: si trattano SEMPRE come stringhe per non perdere cifre."""
    if value is None:
        return ""
    return str(value).strip()


def _first_text(raw, keys):
    """Primo dei nomi indicati che porta un valore non vuoto ('' se nessuno)."""
    for key in keys:
        value = _text(raw.get(key))
        if value:
            return value
    return ""


def parse_sku_page(result_data):
    """Righe normalizzate, totale dichiarato ed elementi grezzi da una pagina.

    Ritorna la terna `(righe, totale_dichiarato, elementi_grezzi)`:
    - `righe`: gli elementi della pagina che erano un oggetto riconoscibile,
      normalizzati nei campi usati dal resto del connettore;
    - `totale_dichiarato`: il campo totale che Temu riporta nella risposta
      (0 se assente);
    - `elementi_grezzi`: quanti elementi conteneva la lista PRIMA di scartare
      quelli non riconoscibili (non un dizionario). E' questo il numero da
      usare per decidere se la pagina era piena o se l'elenco e' finito:
      `len(righe)` può essere più basso a causa di scarti, e trattarlo come
      fine-elenco farebbe fermare la ricognizione in silenzio a metà.
    """
    data = result_data if isinstance(result_data, dict) else {}
    raw_rows = []
    for key in CANDIDATE_LIST_KEYS:
        value = data.get(key)
        if isinstance(value, list):
            raw_rows = value
            break
    total = 0
    for key in CANDIDATE_TOTAL_KEYS:
        if isinstance(data.get(key), int):
            total = data[key]
            break
    rows = []
    for raw in raw_rows:
        if not isinstance(raw, dict):
            continue
        rows.append({
            "goods_id": _text(raw.get("goodsId")),
            "sku_id": _text(raw.get("skuId")),
            "out_sku_sn": _first_text(raw, CANDIDATE_SKU_CODE_KEYS).upper(),
            "goods_name": _text(raw.get("goodsName")),
            "sku_status": _first_text(raw, CANDIDATE_STATUS_KEYS),
            "sku_sub_status": _first_text(raw, CANDIDATE_SUB_STATUS_KEYS),
            "sku_detail_status": _first_text(raw, CANDIDATE_DETAIL_KEYS),
        })
    return rows, total, len(raw_rows)


def match_rows(rows, by_default_code):
    """Divide le righe fra agganciate e orfane.

    La corrispondenza fra il codice venditore dalla riga e il codice Odoo è
    insensibile a spazi e maiuscole su ENTRAMBI i lati.

    `by_default_code` e' un dizionario {riferimento interno: id prodotto Odoo}.
    Le righe agganciate ricevono la chiave `product_id`.
    Nessun prodotto viene mai creato: e' un principio del progetto.
    """
    # Normalizza le chiavi del dizionario Odoo: applica la stessa pulizia
    # usata sui codici delle righe. Se due chiavi diverse normalizzano allo
    # stesso valore, tiene la PRIMA incontrata.
    normalized_code_map = {}
    for original_code, product_id in by_default_code.items():
        norm_code = str(original_code).strip().upper()
        if norm_code and norm_code not in normalized_code_map:
            normalized_code_map[norm_code] = product_id

    agganciate, orfane = [], []
    for row in rows:
        code = (row.get("out_sku_sn") or "").strip().upper()
        product_id = normalized_code_map.get(code) if code else None
        if product_id:
            enriched = dict(row)
            enriched["product_id"] = product_id
            agganciate.append(enriched)
        else:
            orfane.append(row)
    return agganciate, orfane
