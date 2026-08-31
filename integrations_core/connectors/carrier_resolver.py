# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Motore di risoluzione dei corrieri (funzioni PURE: nessun import Odoo).

Il disegno: l'utente collega il proprio vettore (anche quello di un modulo di
terzi) a un CORRIERE dell'anagrafica; ogni connettore marketplace dichiara come
si chiama quel corriere a casa sua. Qui vivono le due sole operazioni di
traduzione, isolate perché sono il punto in cui un errore manderebbe al cliente
un tracking sbagliato.
"""

# Cause di fallimento, usate per scegliere il messaggio da mostrare: sono due
# problemi diversi e vanno detti in modo diverso, altrimenti si cerca nel posto
# sbagliato.
NON_COLLEGATO = "vettore_non_collegato"
NON_TRADOTTO = "corriere_non_tradotto"


def code_for_brand(brand_code, connector_brand_codes, override_code=None):
    """Codice del corriere atteso dal marketplace, o None se non traducibile.

    L'eccezione configurata dall'utente vince sempre sulla tabella del
    connettore: è la via di fuga per il giorno in cui un marketplace cambia un
    codice e non si può attendere un rilascio.
    """
    if override_code:
        return override_code
    if not brand_code:
        return None
    return (connector_brand_codes or {}).get(brand_code) or None


def brand_for_code(external_code, connector_brand_codes):
    """Dal codice del marketplace al corriere. None se assente o AMBIGUO.

    Serve alla migrazione: il codice già configurato dall'utente è un fatto
    confermato, il nome del vettore sarebbe un'interpretazione. Se due corrieri
    diversi puntano allo stesso codice non si sceglie a caso: si ritorna None e
    il chiamante logga la riga come non convertibile.
    """
    if not external_code:
        return None
    trovati = sorted({brand for brand, code in (connector_brand_codes or {}).items()
                      if code == external_code})
    if len(trovati) != 1:
        return None
    return trovati[0]
