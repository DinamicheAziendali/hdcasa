# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Connettore di tracking BRT (Bartolini) — API REST ufficiale (sola lettura).

Endpoint (GET, risposta JSON):
  {base_url}/rest/v1/tracking/parcelID/{parcelID}

  - base_url : URL base BRT (account.endpoint; default https://api.brt.it).
  - parcelID : tracking reference (carrier_tracking_ref del picking), URL-encoded.

Autenticazione STATICA a header (nessun token/login preliminare):
  - header "userID"   ← account.api_user
  - header "password" ← account.api_password
  (NON si usa "Authorization"/Bearer.)

Struttura risposta usata dal parser (wrapper unico ttParcelIdResponse):
    ttParcelIdResponse:
      - executionMessage{code, codeDesc, ...}, esito  → code<0/esito<0 = errore logico
      - lista_eventi[] = {evento:{data, ora, id, descrizione, filiale}}  (più recente prima)
        ⚠️ l'array è PADDATO con placeholder vuoti: gli evento privi di id/data/descrizione
        vengono SCARTATI.
      - bolla.dati_consegna.data_consegna_merce → se valorizzato, OVERRIDE "consegnato".

Lo stato sintetico corrente è ricavato dall'evento più recente valido: priorità al
codice `evento.id` (es. 704, MIC); in subordine una keyword estratta da `evento.descrizione`
(rete di sicurezza per i codici id non ancora mappati). I codici/keyword grezzi sono
tradotti dal BASE via la tabella di normalizzazione (seed in data/status_map_brt.xml);
i non mappati → sconosciuto + log.

Multi-collo: una richiesta = un parcelID = un collo. Il nodo non espone i singoli colli
→ parcels=[] (il base aggiorna l'unico collo di default con lo stato sintetico).
"""
import logging
from datetime import datetime
from urllib.parse import quote

from odoo.addons.centrivo_shipment_tracking.connectors.base import (
    TrackingConnector,
    register_tracker,
)
from odoo.addons.centrivo_shipment_tracking.connectors.transport import RestTransport

_logger = logging.getLogger(__name__)

# URL base di default dell'API BRT (sovrascrivibile da account.endpoint).
BRT_DEFAULT_BASE_URL = "https://api.brt.it"
# Path REST del tracking per parcel id (il parcelID viene accodato URL-encoded).
BRT_TRACKING_PATH = "/rest/v1/tracking/parcelID/"
# Lunghezza dell'estratto grezzo salvato nel log (diagnostica). MAI credenziali.
RAW_EXCERPT_LEN = 2000

# Formati data/ora BRT: separatore PUNTO (GG.MM.AAAA + HH.MM). Tolleranti per sicurezza.
_DATE_FORMATS = ("%d.%m.%Y", "%d/%m/%Y", "%Y-%m-%d")
_TIME_FORMATS = ("%H.%M.%S", "%H.%M", "%H:%M:%S", "%H:%M")

# Codici evento BRT (id) riconosciuti, dedotti dal comportamento osservato (NON dal
# manuale ufficiale). I non elencati passano comunque al base → sconosciuto + log.
KNOWN_EVENT_IDS = {"700", "701", "702", "703", "704", "MIC"}

# Codice id di consegna: usato per l'override "data_consegna_merce valorizzata".
DELIVERED_EVENT_ID = "704"

# Rete di sicurezza: estrazione di una keyword canonica dalla descrizione evento
# quando l'id non è tra i KNOWN_EVENT_IDS. La keyword canonica COINCIDE con un
# raw_code seminato in status_map_brt.xml. Ordine = priorità (consegnato prima delle
# eccezioni; "in consegna" per ultimo per non rubare "CONSEGNAT…").
_DESC_KEYWORDS = (
    ("CONSEGNAT", "CONSEGNATO"),
    ("GIACENZA", "GIACENZA"),
    ("MANCATA CONSEGNA", "MANCATA CONSEGNA"),
    ("TENTATIVO", "TENTATIVO"),
    ("ANOMAL", "ANOMALIA"),
    ("SOSPES", "SOSPESA"),
    ("INESITAT", "INESITATA"),
    ("FERMO DEPOSITO", "FERMO DEPOSITO"),
    ("RIFIUT", "RIFIUTATA"),
    ("IN CONSEGNA", "IN CONSEGNA"),
)


@register_tracker("brt", "BRT")
class BrtTracker(TrackingConnector):
    """Connettore concreto BRT (API REST ufficiale, sola lettura)."""

    default_endpoint = BRT_DEFAULT_BASE_URL
    # BRT richiede credenziali (userID/password): i campi restano visibili sull'account.
    requires_credentials = True

    def __init__(self, account):
        super().__init__(account)
        # Trasporto REST interno del tracking (retry 5xx, backoff, timeout).
        self.transport = RestTransport(base_url=None)

    # ------------------------------------------------------------------
    def fetch_tracking(self, tracking_number):
        """Interroga l'API BRT e ritorna il dict normalizzato (vedi base).

        GET sola lettura, auth a header userID/password. Allega sempre un estratto
        grezzo (raw_excerpt) per la diagnosi sul log (MAI le credenziali: i segreti
        viaggiano negli header, che RestTransport non logga).
        """
        number = (tracking_number or "").strip()
        # L'endpoint dell'account DEVE essere il solo URL base BRT (es.
        # https://api.brt.it), senza path né slash finale: il path di tracking è
        # accodato qui sotto. (Un endpoint errato — es. l'URL di un altro corriere —
        # fa comporre una chiamata verso il server sbagliato: configurare con cura.)
        base = (self.account.endpoint or self.default_endpoint or "").rstrip("/")
        url = "%s%s%s" % (base, BRT_TRACKING_PATH, quote(number, safe=""))
        # Forma minima e sufficiente (allineata al curl/probe che funziona): Accept
        # JSON + credenziali a header. RestTransport gestisce il resto (retry, timeout).
        headers = {
            "Accept": "application/json",
            "userID": self.account.api_user or "",
            "password": self.account.api_password or "",
        }
        # Solleva TransportError sugli errori di rete (gestito dal base).
        response = self.transport.request("GET", url, headers=headers)
        result = self._parse(response.json)
        raw = response.text if response.text else ""
        result["raw_excerpt"] = raw[:RAW_EXCERPT_LEN]
        return result

    # ------------------------------------------------------------------
    def _parse(self, body):
        """Estrae stato sintetico ed eventi dal payload BRT (ttParcelIdResponse).

        Navigazione robusta per chiave (.get), nessuna eccezione su payload
        inattesi; accetta sia un dict sia una lista (primo elemento)."""
        empty = {"current_raw_code": None, "current_raw_desc": None,
                 "events": [], "parcels": []}
        if isinstance(body, list):
            body = body[0] if (body and isinstance(body[0], dict)) else {}
        if not isinstance(body, dict):
            return dict(empty)
        resp = body.get("ttParcelIdResponse")
        if not isinstance(resp, dict):
            return dict(empty)

        # Errore logico BRT (es. spedizione non trovata): code<0 o esito<0 → vuoto
        # (il base risolverà a 'sconosciuto'). Nessuna eccezione.
        exec_msg = resp.get("executionMessage") or {}
        if self._to_int(exec_msg.get("code")) < 0 or self._to_int(resp.get("esito")) < 0:
            return dict(empty)

        # Eventi: lista_eventi[] = {evento:{...}}, scartando i placeholder vuoti.
        events = []
        for row in resp.get("lista_eventi") or []:
            evento = (row or {}).get("evento") if isinstance(row, dict) else None
            if not isinstance(evento, dict):
                continue
            ev_id = (evento.get("id") or "").strip()
            desc = (evento.get("descrizione") or "").strip()
            data = (evento.get("data") or "").strip()
            if not (ev_id or desc or data):
                continue  # placeholder di padding: nessun dato utile
            events.append({
                "event_datetime": self._parse_datetime(data, evento.get("ora")),
                "raw_code": self._event_raw_code(ev_id, desc) or None,
                "raw_description": desc or None,
                "location": (evento.get("filiale") or "").strip() or None,
                "branch_raw": (evento.get("filiale") or "").strip() or None,
            })

        # Stato sintetico corrente.
        current_raw_code = self._current_raw_code(resp, events)

        return {
            "current_raw_code": current_raw_code,
            "current_raw_desc": events[0]["raw_description"] if events else None,
            "events": events,
            "parcels": [],
        }

    # ------------------------------------------------------------------
    @classmethod
    def _current_raw_code(cls, resp, events):
        """Determina il codice stato sintetico corrente.

        1) Override consegna: se bolla.dati_consegna.data_consegna_merce è valorizzato
           → forza il codice di consegna (704).
        2) Altrimenti l'evento più recente valido (lista ordinata dal più recente).
        """
        bolla = resp.get("bolla") or {}
        dati_consegna = bolla.get("dati_consegna") or {}
        if (dati_consegna.get("data_consegna_merce") or "").strip():
            return DELIVERED_EVENT_ID
        return events[0]["raw_code"] if events else None

    @classmethod
    def _event_raw_code(cls, ev_id, desc):
        """Codice grezzo canonico per un evento: id se noto, altrimenti keyword.

        Priorità all'id BRT (es. 704, MIC). Se l'id non è tra quelli riconosciuti, si
        cerca una keyword canonica nella descrizione (rete di sicurezza). Se nessuna
        keyword corrisponde, si ritorna l'id grezzo (così finisce in 'sconosciuto' +
        log ed è individuabile). Il valore ritornato coincide con un raw_code seminato
        nella tabella di normalizzazione; la traduzione in stato Centrivo la fa il base.
        """
        code = (ev_id or "").strip().upper()
        if code in KNOWN_EVENT_IDS:
            return code
        keyword = cls._match_keyword(desc)
        return keyword or code

    @staticmethod
    def _match_keyword(desc):
        """Cerca una keyword canonica nella descrizione (uppercase, per substring)."""
        text = (desc or "").upper()
        for needle, canonical in _DESC_KEYWORDS:
            if needle in text:
                return canonical
        return None

    @staticmethod
    def _to_int(value):
        """Converte a int in modo tollerante (0 se non convertibile)."""
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _parse_datetime(date_str, time_str):
        """Combina data (GG.MM.AAAA) + ora (HH.MM) BRT in un datetime.

        Best-effort: None se la data non è parsabile. Orari locali IT memorizzati
        as-is (nessuna conversione di fuso, come per GLS in Fase 1).
        """
        date_str = (date_str or "").strip()
        time_str = (time_str or "").strip()
        if not date_str:
            return None
        parsed_date = None
        for fmt in _DATE_FORMATS:
            try:
                parsed_date = datetime.strptime(date_str, fmt)
                break
            except ValueError:
                continue
        if parsed_date is None:
            return None
        if not time_str:
            return parsed_date
        for tfmt in _TIME_FORMATS:
            try:
                t = datetime.strptime(time_str, tfmt).time()
                return parsed_date.replace(
                    hour=t.hour, minute=t.minute, second=t.second)
            except ValueError:
                continue
        return parsed_date
