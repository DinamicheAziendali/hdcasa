# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Firma delle chiamate Temu (funzione PURA: nessun import Odoo, nessuna rete).

Algoritmo dichiarato dalla documentazione Temu (Developer Guide, "Signature
Method for API request"):

  1. si prendono TUTTI i parametri della richiesta (comuni + di business);
  2. si ordinano per nome in ASCII crescente;
  3. si concatenano `nome` + `valore` senza alcun separatore;
  4. si mette l'app secret in TESTA e in CODA alla stringa ottenuta;
  5. si calcola l'MD5 e lo si scrive in MAIUSCOLO: quello e' il campo `sign`.

Regola d'oro: si firma ESATTAMENTE la stringa che verra' spedita. Per questo la
serializzazione dei valori complessi (liste/dizionari) vive qui e il client usa
queste stesse funzioni sia per firmare sia per costruire il corpo.
"""
import hashlib
import json

# La chiave `sign` non entra mai nel proprio calcolo.
EXCLUDED_KEYS = ("sign",)


def to_param_value(value):
    """Serializza un valore come lo vuole la firma (e come lo spediremo).

    - liste e dizionari: JSON COMPATTO (nessuno spazio), accenti non sfuggiti e
      ordine delle chiavi preservato (l'esempio ufficiale non le ordina);
    - booleani: `true`/`false` come in JSON, non `True`/`False` di Python;
    - None: stringa vuota;
    - tutto il resto: `str(value)`.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    return str(value)


def canonical_string(params):
    """Concatena `chiave+valore` in ordine ASCII crescente.

    Esclude la chiave `sign` e i parametri con valore None (non vanno spediti,
    quindi non vanno firmati).
    """
    parts = []
    for key in sorted(params):
        if key in EXCLUDED_KEYS:
            continue
        value = params[key]
        if value is None:
            continue
        parts.append("%s%s" % (key, to_param_value(value)))
    return "".join(parts)


def sign_params(params, app_secret):
    """Firma MD5 maiuscola dei parametri, con l'app secret in testa e in coda."""
    blob = "%s%s%s" % (app_secret or "", canonical_string(params),
                       app_secret or "")
    return hashlib.md5(blob.encode("utf-8")).hexdigest().upper()
