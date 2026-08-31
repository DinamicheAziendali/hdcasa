# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Quando il token Cdiscount scade, e quando conviene rinnovarlo.

Il collegamento e' OAuth2 `client_credentials` sul realm Keycloak `maas`, e
il token dura **due ore**. Non c'e' nessun aggiornamento incrementale: si
richiede daccapo.

⚠️ Rinnovarlo ESATTAMENTE alla scadenza non basta. Una richiesta parte con un
token valido e arriva quando non lo e' piu': Cdiscount risponde 401, e un 401
si legge come «credenziali sbagliate» — cioe' si va a cercare il guasto nel
posto sbagliato. Da qui il margine.

Questo file NON importa Odoo: si prova con tools/test_cdiscount_token.py.
"""

# Cinque minuti. Abbondante per una chiamata che ne dura al massimo trenta
# secondi, e trascurabile su un token che ne dura settemiladuecento.
MARGINE_RINNOVO = 300


def scadenza(adesso, dura_secondi):
    """Il momento in cui il token smette di valere.

    Una durata assente o negativa da' «gia' scaduto», non «per sempre»: se
    Cdiscount non dice quanto dura, non lo si indovina.
    """
    durata = dura_secondi or 0
    if durata < 0:
        durata = 0
    return adesso + durata


def serve_rinnovo(scade_a, adesso):
    """Se conviene chiedere un token nuovo prima di partire.

    ⚠️ `scade_a` puo' arrivare come `False`: in Odoo un campo non valorizzato
    si legge cosi', e `0 == False`. Qualunque valore falso significa «non ho
    una scadenza», cioe' **rinnova**, non «vale per sempre».
    """
    if not scade_a:
        return True
    return (scade_a - adesso) <= MARGINE_RINNOVO
