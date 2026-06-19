# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Connettore di tracking Poste Italiane — API REST con OAuth2 (sola lettura).

Differenza rispetto a GLS/BRT: l'autenticazione è OAuth2 *client_credentials* a due
passi (token + uso del token), gestita interamente QUI (il base non la conosce).

Endpoint (il base URL contiene già /gp/internet; i path si concatenano SENZA
riaggiungerlo — lezione TASK_55):
  - AUTH    (POST): {base}/user/sessions
  - TRACKING (GET): {base}/postalandlogistics/parcel/tracking

AUTH (passo 1):
  POST {base}/user/sessions
  header  POSTE_clientID: <client_id>, Content-Type: application/json
  body    {"clientId","secretId","scope","grantType":"client_credentials"}
  → {"token_type":"Bearer","expires_in":3599,"access_token":"<JWT>", ...}

TRACKING (passo 2):
  GET {base}/postalandlogistics/parcel/tracking
      ?waybillNumber=<num>&lastTracingState=N&statusDescription=E&customerType=DQ
  header  POSTE_clientID: <client_id>, Accept: application/json,
          Authorization: <access_token>   ← VALORE GREZZO, SENZA prefisso "Bearer"

Risposta tracking (parser): return.outcome=="OK" e return.code in {0,"0"} →
  return.shipment[0].tracking[]. Per ogni evento (TASK_68):
    - raw_code = `status` (granulare, es. 003/009/RRR/000): è il codice PRIMARIO;
    - raw_code_fallback = `phase` (grossolano, es. IN CONSEGNA): fallback stabile.
  Il `status` "tradisce" a volte il `phase` (es. status 003 = giacenza ma phase IN
  CONSEGNA), quindi è il codice primario; la normalizzazione a due livelli del base
  prova prima lo `status`, poi ripiega sul `phase` (seed in data/status_map_poste.xml).
  Lo STATO CORRENTE è l'evento più recente in ordine cronologico (regola generale,
  non "lo stato più grave mai visto"). Colli singoli non esposti → parcels=[].
  Risposta valida ma SENZA eventi (es. messages code 207 "tracking senza risultati"):
  NON è un errore → si degrada a vuoto, nessuna eccezione, stato corrente invariato.

Sicurezza: client_id/secret e access_token NON vengono MAI loggati; il raw_excerpt
è preso SOLO dalla risposta di tracking (che non contiene segreti), mai dall'auth.
"""
import logging
import time
from datetime import datetime

from odoo.addons.centrivo_shipment_tracking.connectors.base import (
    TrackingConnector,
    register_tracker,
)
from odoo.addons.centrivo_shipment_tracking.connectors.transport import RestTransport

_logger = logging.getLogger(__name__)

# URL base di default Poste (produzione). Include già /gp/internet.
POSTE_DEFAULT_BASE_URL = "https://apiw.gp.posteitaliane.it/gp/internet"
# Scope OAuth2 di produzione (default tecnico, NON un segreto). Sovrascrivibile
# sull'account (campo poste_scope).
POSTE_DEFAULT_SCOPE = (
    "https://postemarketplace.onmicrosoft.com/"
    "d6a78063-5570-4a87-bbd7-07326e6855d1/.default")
# Path relativi (al base URL, che contiene già /gp/internet).
POSTE_AUTH_PATH = "/user/sessions"
POSTE_TRACKING_PATH = "/postalandlogistics/parcel/tracking"
# Parametri fissi della query di tracking (verificati dal vivo).
POSTE_TRACKING_PARAMS = {
    "lastTracingState": "N",
    "statusDescription": "E",
    "customerType": "DQ",
}
# Margine di sicurezza (secondi) sulla scadenza del token, per evitare di usarlo
# proprio sul filo della scadenza.
TOKEN_MARGIN_SECONDS = 60
# Durata di fallback del token se Poste non restituisce expires_in.
TOKEN_FALLBACK_TTL = 3599
# Lunghezza dell'estratto grezzo salvato nel log (solo risposta tracking, niente segreti).
RAW_EXCERPT_LEN = 2000

# Formati data/ora Poste (ISO locale, niente Z). Orari locali memorizzati as-is.
_DATE_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
                 "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d")


@register_tracker("poste", "Poste")
class PosteTracker(TrackingConnector):
    """Connettore concreto Poste Italiane (OAuth2, sola lettura)."""

    default_endpoint = POSTE_DEFAULT_BASE_URL
    requires_credentials = True

    def __init__(self, account):
        super().__init__(account)
        self.transport = RestTransport(base_url=None)
        # Cache token IN MEMORIA (non persistita): vale per la durata dell'istanza.
        self._token = None
        self._token_expiry = 0.0

    # ------------------------------------------------------------------
    # Helper di configurazione
    # ------------------------------------------------------------------
    def _base(self):
        return (self.account.endpoint or self.default_endpoint or "").rstrip("/")

    def _client_id(self):
        return self.account.api_user or ""

    def _scope(self):
        return getattr(self.account, "poste_scope", None) or POSTE_DEFAULT_SCOPE

    # ------------------------------------------------------------------
    # OAuth2 — token con cache + refresh
    # ------------------------------------------------------------------
    def _token_valid(self):
        return bool(self._token) and time.time() < (
            self._token_expiry - TOKEN_MARGIN_SECONDS)

    def _get_token(self, force=False):
        """Ritorna un access_token valido (dalla cache o richiedendone uno nuovo).

        Solleva TransportError sugli errori di rete (gestito dal base). Token/segreti
        non loggati. Ritorna None se Poste non restituisce un access_token.
        """
        if not force and self._token_valid():
            return self._token
        client_id = self._client_id()
        url = "%s%s" % (self._base(), POSTE_AUTH_PATH)
        headers = {
            "POSTE_clientID": client_id,
            "Content-Type": "application/json",
        }
        body = {
            "clientId": client_id,
            "secretId": self.account.api_password or "",
            "scope": self._scope(),
            "grantType": "client_credentials",
        }
        response = self.transport.request("POST", url, headers=headers, json=body)
        data = response.json if isinstance(response.json, dict) else {}
        token = data.get("access_token")
        if token:
            self._token = token
            try:
                ttl = float(data.get("expires_in") or TOKEN_FALLBACK_TTL)
            except (TypeError, ValueError):
                ttl = TOKEN_FALLBACK_TTL
            self._token_expiry = time.time() + ttl
        return token

    # ------------------------------------------------------------------
    def fetch_tracking(self, tracking_number):
        """Interroga Poste e ritorna il dict normalizzato (vedi base).

        OAuth2: ottiene il token (cache/refresh), chiama il tracking; se la GET
        risponde 401 rigenera il token UNA volta e ritenta UNA volta (no loop). Il
        raw_excerpt è preso SOLO dalla risposta di tracking (mai dall'auth).
        """
        number = (tracking_number or "").strip()
        token = self._get_token()
        response = self._call_tracking(number, token)
        if response.status_code == 401:
            # Token scaduto/invalido: rigenera una volta e ritenta una volta.
            token = self._get_token(force=True)
            response = self._call_tracking(number, token)
        result = self._parse(response.json)
        raw = response.text if response.text else ""
        result["raw_excerpt"] = raw[:RAW_EXCERPT_LEN]
        return result

    def _call_tracking(self, number, token):
        """Esegue la GET di tracking con il token (Authorization GREZZO, no Bearer)."""
        url = "%s%s" % (self._base(), POSTE_TRACKING_PATH)
        headers = {
            "POSTE_clientID": self._client_id(),
            "Accept": "application/json",
            # VALORE GREZZO, SENZA "Bearer " (verificato dal vivo).
            "Authorization": token or "",
        }
        params = dict(POSTE_TRACKING_PARAMS, waybillNumber=number)
        # GET sola lettura; TransportError di rete propagato al base.
        return self.transport.request("GET", url, headers=headers, params=params)

    # ------------------------------------------------------------------
    def _parse(self, body):
        """Estrae stato sintetico ed eventi dal payload Poste.

        Navigazione robusta per chiave; accetta dict o lista (primo elemento); mai
        un'eccezione su payload inattesi (degrada a vuoto → 'sconosciuto' nel base).
        """
        empty = {"current_raw_code": None, "current_raw_desc": None,
                 "events": [], "parcels": []}
        if isinstance(body, list):
            body = body[0] if (body and isinstance(body[0], dict)) else {}
        if not isinstance(body, dict):
            return dict(empty)
        ret = body.get("return")
        if not isinstance(ret, dict):
            return dict(empty)
        # Validità: outcome OK e code in {0,"0"}.
        outcome = str(ret.get("outcome") or "").strip().upper()
        code = str(ret.get("code"))
        if outcome != "OK" or code not in ("0",):
            return dict(empty)
        shipments = ret.get("shipment") or []
        if not shipments or not isinstance(shipments[0], dict):
            return dict(empty)
        tracking = shipments[0].get("tracking") or []
        if not tracking:
            # Risposta valida ma senza eventi (es. messages code 207): nessun dato
            # disponibile, NON è un errore. Si degrada a vuoto: il base non fa
            # downgrade dello stato corrente (vedi _apply_tracking_result).
            _logger.info("Poste: tracking valido senza eventi (nessun dato).")
            return dict(empty)

        events = []
        for ev in tracking:
            if not isinstance(ev, dict):
                continue
            status = (ev.get("status") or "").strip()
            phase = (ev.get("phase") or "").strip()
            data = (ev.get("data") or "").strip()
            desc = (ev.get("StatusDescription") or "").strip()
            if not (status or phase or data or desc):
                continue
            events.append({
                "event_datetime": self._parse_datetime(data),
                # PRIMARIO = status (granulare); FALLBACK = phase (grossolano). Il
                # base normalizza prima lo status, poi ripiega sul phase (TASK_68).
                "raw_code": status.upper() or None,
                "raw_code_fallback": phase.upper() or None,
                "raw_description": desc or None,
                "location": (ev.get("officeDescription") or "").strip() or None,
                # branch grezzo = `of` (sigla filiale dal vivo); fallback officeId.
                "branch_raw": (ev.get("of") or ev.get("officeId") or "").strip()
                or None,
            })

        # Stato corrente = evento più RECENTE in ordine cronologico (non assume
        # l'ordine dell'array: prende il max per data/ora; se nessun evento è datato,
        # ricade sul primo elemento, che Poste fornisce dal più recente).
        latest = self._latest_event(events)
        return {
            "current_raw_code": latest["raw_code"] if latest else None,
            "current_raw_code_fallback": latest["raw_code_fallback"]
            if latest else None,
            "current_raw_desc": latest["raw_description"] if latest else None,
            "events": events,
            "parcels": [],
        }

    @staticmethod
    def _latest_event(events):
        """Evento più recente per data/ora (None se la lista è vuota)."""
        if not events:
            return None
        dated = [e for e in events if e.get("event_datetime")]
        if dated:
            return max(dated, key=lambda e: e["event_datetime"])
        return events[0]

    @staticmethod
    def _parse_datetime(date_str):
        """Converte 'YYYY-MM-DD HH:MM:SS' in datetime (best-effort; None se non parsabile).

        Orari locali Poste memorizzati as-is (nessuna conversione di fuso, come GLS/BRT).
        """
        date_str = (date_str or "").strip()
        if not date_str:
            return None
        for fmt in _DATE_FORMATS:
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue
        return None
