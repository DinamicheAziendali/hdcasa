# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Kaufland conosce questo prodotto? E in che stato?

⚠️ La domanda ha TRE risposte, non due, e la terza e' una trappola: Kaufland
risponde `200` anche quando del prodotto esiste solo il codice a barre e
nessuna pagina — titolo vuoto, `is_valid: false`. Misurato il 2026-08-22:
65 prodotti su 549 rispondono cosi'. Verificato aprendo la pagina pubblica di
uno di questi: «Questa pagina non esiste».

Contarli come «gia' a catalogo» significherebbe creare offerte agganciate a
schede che non esistono: non vendono niente, e non danno alcun errore.
**Il titolo vuoto vale quanto un 404.**

E c'e' una quarta risposta che NON e' un verdetto: se Kaufland e' guasta o
rifiuta il codice, lo stato resta sconosciuto. Trasformare un guasto in
«serve la scheda» farebbe scrivere centinaia di schede che non servivano.

Questo file NON importa Odoo: si prova con tools/test_kaufland_catalogo.py.
"""
PRONTA = "pronta"      # la scheda c'e': basta l'offerta
GUSCIO = "guscio"      # esiste solo il codice, senza pagina: serve la scheda
ASSENTE = "assente"    # non lo conosce affatto: serve la scheda


def stato_scheda(stato_http, dati, messaggio_errore=""):
    """Lo stato della scheda, letto da una risposta gia' spacchettata.

    `stato` a None significa «non lo so», e allora `errore` dice perche'.
    Chi chiama NON deve trattare «non lo so» come «assente».
    """
    vuoto = {"stato": None, "id_product": None, "titolo": None,
             "id_categoria": None, "errore": None}

    if stato_http == 404:
        return dict(vuoto, stato=ASSENTE)

    if not (200 <= stato_http < 300):
        # Compreso il 400 "Invalid EAN provided": non e' «non lo conosce»,
        # e' «quel codice non lo accetto». Due cose diverse, due rimedi
        # diversi — la scheda non risolverebbe il secondo.
        return dict(vuoto, errore="HTTP %s: %s" % (stato_http,
                                                   messaggio_errore))

    dati = dati or {}
    titolo = (dati.get("title") or "").strip()
    stato = PRONTA if (titolo and dati.get("is_valid")) else GUSCIO
    return {"stato": stato,
            "id_product": dati.get("id_product"),
            "titolo": titolo or None,
            "id_categoria": dati.get("id_category"),
            "errore": None}
