# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Lettura delle risposte ordini Temu. Funzioni PURE: niente Odoo, niente rete.

MODELLO A DUE LIVELLI (Order Guide, letta il 2026-08-20): ogni acquisto e' un
`parentOrderSn` che contiene una o piu' righe (`orderSn`), e ogni riga vale
esattamente una SKU. Le spedizioni si confermano sul PARENT, non sulla riga.

NOMI DEI CAMPI: come per il catalogo, si provano piu' nomi noti e si va avanti
senza sollevare eccezioni. Su Temu una richiesta o una lettura sbagliata non
danno errore: danno un risultato vuoto, che e' molto piu' insidioso.
"""

# Nomi possibili della lista di ordini dentro `result`.
CANDIDATE_LIST_KEYS = ("pageItems", "orderList", "list", "dataList")

# Nomi possibili del totale dichiarato.
CANDIDATE_TOTAL_KEYS = ("totalItemNum", "totalCount", "total")

# Stati del PARENT ORDER (bg.order.list.v2.get, campo parentOrderStatus).
# Enumerazione ufficiale, verificata sul negozio HDcasa il 2026-08-20 (i 385
# ordini storici si ripartivano fra 5 = consegnato e 3 = annullato).
ORDER_STATUS_LABELS = {
    "1": "In attesa",
    "2": "Da spedire",
    "3": "Annullato",
    "4": "Spedito",
    "5": "Consegnato",
    "41": "Spedito in parte",
    "51": "Consegnato in parte",
}

# Lo stato da cui si importa. La Order Guide e' esplicita: finche' l'ordine e'
# "In attesa" il cliente puo' ancora cambiare indirizzo e quantita', e Temu
# raccomanda di non lavorarlo. Importare un ordine in attesa significa creare in
# Odoo un ordine che puo' ancora cambiare sotto i piedi.
STATUS_DA_SPEDIRE = "2"


def _text(value):
    """Valore come stringa pulita ('' se assente).

    Gli identificativi Temu sono numeri molto lunghi: si trattano SEMPRE come
    stringhe, altrimenti si perdono cifre per strada.
    """
    if value is None:
        return ""
    return str(value).strip()


def _intero(value, default=0):
    """Intero tollerante: accetta numeri, stringhe e virgole decimali."""
    if value is None or value == "":
        return default
    try:
        return int(float(str(value).replace(",", ".")))
    except (TypeError, ValueError):
        return default


def parse_order_page(result_data):
    """Ordini normalizzati, totale dichiarato ed elementi grezzi di una pagina.

    Ritorna la terna `(ordini, totale_dichiarato, elementi_grezzi)`.
    `elementi_grezzi` e' quanti elementi conteneva la lista PRIMA di scartare i
    non riconoscibili: e' quello il numero da usare per decidere se la pagina
    era piena, perche' `len(ordini)` puo' essere piu' basso per via degli scarti
    e trattarlo come fine-elenco fermerebbe lo scarico a meta', in silenzio.
    """
    data = result_data if isinstance(result_data, dict) else {}
    grezzi = []
    for key in CANDIDATE_LIST_KEYS:
        value = data.get(key)
        if isinstance(value, list):
            grezzi = value
            break
    totale = 0
    for key in CANDIDATE_TOTAL_KEYS:
        if isinstance(data.get(key), int):
            totale = data[key]
            break

    ordini = []
    for raw in grezzi:
        ordine = parse_order(raw)
        if ordine:
            ordini.append(ordine)
    return ordini, totale, len(grezzi)


def parse_order(raw):
    """Un singolo ordine normalizzato, o None se la struttura non e' leggibile.

    Accetta sia la forma dell'ELENCO (`parentOrderMap` + `orderList`) sia quella
    del DETTAGLIO, che ha la stessa forma annidata dentro `result`.
    """
    if not isinstance(raw, dict):
        return None
    testa = raw.get("parentOrderMap")
    if not isinstance(testa, dict):
        # Il dettaglio annida la stessa struttura un livello piu' sotto.
        interno = raw.get("result")
        if isinstance(interno, dict) and isinstance(
                interno.get("parentOrderMap"), dict):
            return parse_order(interno)
        return None

    parent_sn = _text(testa.get("parentOrderSn"))
    if not parent_sn:
        return None

    righe = []
    for r in (raw.get("orderList") or []):
        if not isinstance(r, dict):
            continue
        riga = parse_order_line(r)
        if riga:
            righe.append(riga)

    return {
        "parent_order_sn": parent_sn,
        "status": _text(testa.get("parentOrderStatus")),
        "status_label": ORDER_STATUS_LABELS.get(
            _text(testa.get("parentOrderStatus")), ""),
        "region_id": _text(testa.get("regionId")),
        "site_id": _text(testa.get("siteId")),
        "order_time": _intero(testa.get("parentOrderTime")),
        "confirm_time": _intero(testa.get("parentConfirmTime")),
        "expect_ship_latest": _intero(testa.get("expectShipLatestTime")),
        "has_shipping_fee": bool(testa.get("hasShippingFee")),
        "righe": righe,
    }


def parse_order_line(raw):
    """Una riga d'ordine normalizzata, o None se manca l'essenziale.

    Il codice venditore puo' arrivare come `extCode` dentro `productList`: nel
    catalogo lo stesso dato si chiama `skuSn`, quindi non si da' per scontato il
    nome e si prova anche li'.
    """
    order_sn = _text(raw.get("orderSn"))
    sku_id = _text(raw.get("skuId"))
    if not order_sn or not sku_id:
        return None

    codice = _text(raw.get("extCode")) or _text(raw.get("skuSn"))
    if not codice:
        for p in (raw.get("productList") or []):
            if isinstance(p, dict):
                codice = _text(p.get("extCode")) or _text(p.get("outSkuSn"))
                if codice:
                    break

    # La quantita' venduta: `quantity` e' quella corrente, `originalOrderQuantity`
    # quella iniziale. Se il cliente ha annullato dei pezzi prima della
    # spedizione, la differenza sta in `canceledQuantityBeforeShipment`. Fa fede
    # la quantita' CORRENTE: e' quella che dobbiamo spedire.
    return {
        "order_sn": order_sn,
        "sku_id": sku_id,
        "goods_id": _text(raw.get("goodsId")),
        "goods_name": _text(raw.get("goodsName")),
        "spec": _text(raw.get("spec")),
        "ext_code": codice.upper(),
        "qty": _intero(raw.get("quantity"), 0),
        "qty_originale": _intero(raw.get("originalOrderQuantity"), 0),
        "qty_annullata": _intero(raw.get("canceledQuantityBeforeShipment"), 0),
        "status": _text(raw.get("orderStatus")),
    }


def righe_da_spedire(ordine):
    """Le righe con quantita' maggiore di zero.

    Una riga interamente annullata prima della spedizione resta nell'elenco con
    quantita' zero: portarla in Odoo creerebbe una riga d'ordine fantasma.
    """
    return [r for r in (ordine.get("righe") or []) if r.get("qty", 0) > 0]
