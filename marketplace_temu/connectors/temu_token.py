# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Cosa dice il token Temu di se stesso.

`bg.open.accesstoken.info.get` risponde con la scadenza vera e l'elenco dei
permessi. ⚠️ E' la chiamata con cui e' stato diagnosticato il blocco degli
importi: confrontando i permessi del token con le chiamate che il modulo usa
davvero si vede subito quale non e' coperta, invece di scoprirlo al primo
ordine.

Questo file NON importa Odoo: si prova con tools/test_temu_token.py.
"""

API_TOKEN_INFO = "bg.open.accesstoken.info.get"


def leggi_token(corpo):
    """Scadenza e permessi. Rende `(None, None)` se non si capisce.

    ⚠️ Un elenco ASSENTE non e' un elenco VUOTO: il primo vuol dire «non lo
    so», il secondo «nessun permesso». Confonderli farebbe dire che nessuna
    chiamata e' coperta, cioe' un allarme falso su tutto.
    """
    if not isinstance(corpo, dict):
        return None, None
    scadenza = corpo.get("expiredTime")
    elenco = corpo.get("apiScopeList")
    if not isinstance(elenco, (list, tuple)):
        return scadenza, None
    return scadenza, [str(v) for v in elenco]


def chiamate_scoperte(permessi, chiamate):
    """Quali `chiamate` non stanno fra i `permessi`. `None` se non si sa.

    ⚠️ L'elenco delle chiamate NON si scrive a mano: va preso dalle costanti
    dei connettori, o il giorno che se ne aggiunge una questo controllo mente
    dicendo «tutto coperto».
    """
    if permessi is None:
        return None
    avuti = {str(p) for p in permessi}
    return [c for c in chiamate if c not in avuti]
