# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Trasporto HTTP interno della suite di tracking (autosufficiente).

Internalizzato in TASK_73 per rendere `centrivo_shipment_tracking` (e i suoi adapter
GLS/BRT/Poste) PIENAMENTE STAND-ALONE: la suite non dipende più dal trasporto di
integrations_core (core dei connettori marketplace), che con il tracking non c'entra.

Solo la parte REST (sola lettura verso i nodi/API dei corrieri): `TransportError`,
`TransportResponse`, `TransportBase`, `RestTransport`. La parte CSV (export marketplace)
non riguarda il tracking e NON è inclusa.

Puro Python di trasporto: nessun accoppiamento con Odoo (no env/modelli/fields).
`requests` è importato localmente dentro `RestTransport.request` (è disponibile
nell'immagine Odoo) per non imporre la dipendenza all'import del modulo.
"""
import logging
import time

_logger = logging.getLogger(__name__)

# Codici di stato HTTP considerati "ritentabili" (errore lato server).
RETRYABLE_STATUS = (500, 502, 503, 504)


class TransportError(Exception):
    """Errore di rete/trasporto (timeout, connessione, ecc.).

    Sollevata quando la richiesta non arriva nemmeno a una risposta HTTP, così
    il chiamante (connettore di tracking) può loggarla in modo chiaro.
    """


class TransportResponse(object):
    """Risposta di trasporto normalizzata.

    Espone:
      - status_code : codice HTTP (int)
      - json        : corpo JSON parsato (dict/list) oppure None se non parsabile
      - text        : corpo grezzo come testo (fallback diagnostico)
    """

    def __init__(self, status_code, json=None, text=None):
        self.status_code = status_code
        self.json = json
        self.text = text

    @property
    def ok(self):
        return 200 <= (self.status_code or 0) < 300


class TransportBase(object):
    """Contratto base di un trasporto."""

    def __init__(self, base_url=None, timeout=30):
        self.base_url = (base_url or "").rstrip("/")
        self.timeout = timeout

    def request(self, *args, **kwargs):
        """Esegue una richiesta. Da implementare nelle sottoclassi concrete."""
        raise NotImplementedError


class RestTransport(TransportBase):
    """Trasporto per API/nodi REST dei corrieri (GLS/BRT/Poste, sola lettura).

    Predisposto per:
      - header di autenticazione (es. {"sh-token": <token>} o credenziali del
        corriere), passati dal connettore che li legge dall'account (MAI dal codice);
      - retry automatico sugli errori 5xx con backoff;
      - logging dell'esito a cura del connettore (su centrivo.shipment.log).
    """

    def __init__(self, base_url=None, default_headers=None, timeout=30,
                 max_retries=3, backoff=2):
        super().__init__(base_url=base_url, timeout=timeout)
        self.default_headers = default_headers or {}
        self.max_retries = max_retries
        self.backoff = backoff

    def request(self, method, path, headers=None, params=None, json=None):
        """Esegue una richiesta REST reale con retry sui 5xx.

        Comportamento:
          - compone l'URL come base_url + path (oppure usa `path` se è già un URL);
          - imposta Accept/Content-Type application/json + gli header passati
            (es. il token di auth dal connettore);
          - timeout di self.timeout secondi;
          - su risposta 5xx ritenta fino a max_retries con backoff incrementale
            (1s, 2s, 4s, ...); su 4xx NON ritenta;
          - su errore di rete (timeout/connessione) solleva TransportError;
          - ritorna una TransportResponse (status_code, json parsato, text grezzo).

        NB: la api_key/token NON viene mai loggata: nel debug stampiamo solo
        metodo e URL, mai gli header.
        """
        # `requests` è disponibile nell'immagine Odoo. Import locale per non
        # imporre la dipendenza all'import del modulo.
        import requests

        url = path if path.startswith("http") else "%s/%s" % (
            self.base_url, path.lstrip("/"))
        merged_headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        merged_headers.update(self.default_headers)
        merged_headers.update(headers or {})

        attempt = 0
        while True:
            attempt += 1
            try:
                # NB: non logghiamo gli header (contengono il token).
                _logger.debug("RestTransport %s %s (tentativo %s)",
                              method, url, attempt)
                resp = requests.request(
                    method, url,
                    headers=merged_headers,
                    params=params,
                    json=json,
                    timeout=self.timeout,
                )
            except requests.exceptions.RequestException as exc:
                # Errore di rete: ritenta se ci sono tentativi residui, altrimenti
                # solleva un errore chiaro per il chiamante.
                if attempt <= self.max_retries:
                    time.sleep(self.backoff ** (attempt - 1))
                    continue
                raise TransportError(
                    "Errore di rete verso %s: %s" % (url, exc))

            # Retry solo sui 5xx ritentabili.
            if resp.status_code in RETRYABLE_STATUS and attempt <= self.max_retries:
                time.sleep(self.backoff ** (attempt - 1))
                continue

            # Parsing JSON tollerante: se non è JSON, teniamo il testo grezzo.
            parsed = None
            try:
                parsed = resp.json()
            except ValueError:
                parsed = None
            return TransportResponse(
                status_code=resp.status_code,
                json=parsed,
                text=resp.text,
            )
