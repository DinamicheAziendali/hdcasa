# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Leggere una risposta di Kaufland: l'identificativo e il perche' di un rifiuto.

Questo file NON importa Odoo: si prova con tools/test_kaufland_risposta.py.
"""
import json

# Quanto dettaglio si tiene di una risposta d'errore. Finisce nel registro e a
# video: un corpo enorme riempirebbe la schermata delle operazioni.
MAX_DETTAGLIO = 500


def id_unit(teste, dati):
    """L'identificativo dell'offerta appena creata, o None.

    ⚠️ Kaufland lo mette DI SOLITO nell'intestazione `Location`. Il
    2026-08-25 ha accettato 165 offerte rispondendo senza: quelle offerte
    sono rimaste vive e non aggiornabili, perche' l'allineamento sceglie le
    righe per identificativo. Da allora si guarda anche nel corpo, e si
    accetta sia `id_unit` sia `unit_id` — la documentazione usa l'uno in un
    punto e l'altro altrove.

    ⚠️ I nomi delle intestazioni HTTP sono insensibili al maiuscolo per
    specifica, e chi chiama puo' passare un dizionario normale invece della
    struttura di `requests`: si normalizza a minuscolo prima di cercare.

    ⚠️ Le due chiavi del corpo si provano IN SEQUENZA, ciascuna col suo
    controllo. Con un solo `.get` e il suo valore predefinito, un `id_unit`
    esplicitamente nullo — cosa normalissima nel JSON — coprirebbe un
    `unit_id` valido, e l'offerta tornerebbe orfana.
    """
    intestazioni = {str(c).lower(): v for c, v in (teste or {}).items()}
    posizione = intestazioni.get("location") or ""
    if posizione:
        coda = posizione.rstrip("/").rsplit("/", 1)[-1].strip()
        if coda.isdigit():
            return coda
    if isinstance(dati, dict):
        for chiave in ("id_unit", "unit_id"):
            valore = dati.get(chiave)
            if valore not in (None, ""):
                return str(valore)
    return None


def messaggio(stato, corpo, testo=""):
    """Il messaggio d'errore, DETTAGLIO COMPRESO.

    ⚠️ Il difetto che questa funzione chiude, capitato sul vero il
    2026-08-25: al primo tentativo di creare le 166 offerte italiane Kaufland
    le ha rifiutate tutte, e sono comparse 166 volte le stesse parole «HTTP
    400: Validation Failed» — cioe' il solo campo `message`, che e' generico.
    Il motivo vero (quale campo, e perche') Kaufland lo scrive ALTROVE nel
    corpo, e quel resto veniva buttato via. Davanti a 166 righe identiche
    l'unica cosa che si puo' fare e' indovinare.

    Non si nomina la chiave del dettaglio (`violations`, `errors`, ...): si
    prende tutto quel che c'e' oltre `message`. Cosi' se Kaufland cambia il
    nome del campo, il dettaglio continua ad arrivare.
    """
    if isinstance(corpo, dict):
        base = str(corpo.get("message") or "").strip()
        resto = {c: v for c, v in corpo.items() if c != "message"}
        if resto:
            dettaglio = json.dumps(resto, ensure_ascii=False,
                                   default=str)[:MAX_DETTAGLIO]
            return ("%s — %s" % (base, dettaglio)).strip(" —")
        if base:
            return base
    return (testo or "")[:300]
