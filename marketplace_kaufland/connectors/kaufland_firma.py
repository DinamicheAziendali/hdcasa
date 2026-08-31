# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""La firma delle chiamate a Kaufland.

Quattro righe unite da un ritorno a capo — metodo, indirizzo completo, corpo,
orario — e un HMAC-SHA256 con il segreto del venditore. Niente token, niente
scadenze: la firma si rifa' a ogni chiamata.

⚠️ La documentazione di Kaufland dice "base64", il loro esempio ufficiale e'
ESADECIMALE, e l'API vera accetta l'esadecimale (provato contro il negozio il
2026-08-22). Vince quel che si puo' verificare.

⚠️ Il segreto e' una STRINGA che somiglia a un numero esadecimale: va usato
com'e'. Interpretarlo come numero da' una firma sbagliata, ed e' l'errore che
la loro guida segnala come piu' comune di tutti.

Questo file NON importa Odoo: si prova con tools/test_kaufland_firma.py.
"""
import hashlib
import hmac

UTENTE_DICHIARATO = "Inhouse_development"


def firma(metodo, uri, corpo, quando, segreto):
    """L'HMAC della chiamata, in esadecimale.

    `uri` e' l'indirizzo COMPLETO, con https, dominio e stringa di ricerca:
    firmare il solo percorso da' una firma che il server rifiuta senza dire
    perche'.

    `corpo` e' il corpo esattamente come verra' spedito, byte per byte. Se chi
    spedisce riserializza il JSON diversamente da come e' stato firmato, la
    firma non torna e l'errore sembra un problema di credenziali.
    """
    testo = "\n".join([metodo, uri, corpo or "", str(quando)])
    return hmac.new(segreto.encode(), testo.encode(), hashlib.sha256).hexdigest()


def intestazioni(metodo, uri, corpo, quando, chiave, segreto):
    """Le cinque intestazioni obbligatorie, piu' il tipo di contenuto se serve.

    ⚠️ `Shop-Timestamp` deve essere l'orario vero: Kaufland accetta cinque
    minuti di scarto in avanti e cinque indietro, poi rifiuta. L'orologio
    sbagliato di un contenitore si presenta come "credenziali non valide".
    """
    teste = {
        "Accept": "application/json",
        "Shop-Client-Key": chiave,
        "Shop-Timestamp": str(quando),
        "Shop-Signature": firma(metodo, uri, corpo, quando, segreto),
        "User-Agent": UTENTE_DICHIARATO,
    }
    if corpo:
        teste["Content-Type"] = "application/json"
    return teste
