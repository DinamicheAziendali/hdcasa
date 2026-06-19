# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Connettore di tracking GLS — infoweb XML (Track & Trace self-service, sola lettura).

FONTE (TASK_69): abbandonato il vecchio nodo pubblico `gls-group.com/.../rstt030`
(JSON, NON esponeva la giacenza) in favore del servizio infoweb documentato nel
manuale GLS MU40, che fornisce un CODICE EVENTO STRUTTURATO per la tratta italiana
(quindi stato di giacenza/eccezione affidabile).

Endpoint (GET, risposta XML ISO-8859-1):
  https://infoweb.gls-italy.com/XML/get_xml_track.php
  ?locpartenza=<siglasede>&NumSped=<numero>&CodCli=<codicecontratto>

  - locpartenza : sigla sede GLS (account.gls_sender_code, es. SA).
  - NumSped     : numero spedizione SENZA il prefisso sigla sede. Il waybill reale è
                  tipo "SA662402545" → si toglie il prefisso di 2 lettere se coincide
                  con locpartenza, ottenendo "662402545"; se non c'è prefisso, si usa
                  il ref così com'è.
  - CodCli      : Codice CONTRATTO GLS (account.gls_contract_code, es. 4665), NON il
                  Codice Cliente. Nessuna autenticazione user/password.

Struttura risposta (radice <ELENCO><SPEDIZIONE>):
  - testata: <NumSped>, <StatoSpedizione>, <Note>, <DataConsegna>, ... (CDATA);
  - <TRACKING> (ripetuto): evento tratta ITALIANA con <Data> <Ora> <Luogo> <Stato>
    <Codice> (codice strutturato) <Note>;
  - <ColloInternazionale><TRACKINGINT> (ripetuto): evento tratta ESTERA, con <Data>
    <Ora> <Luogo> <Stato> ma SENZA <Codice> (la Holding GLS non fornisce codici) →
    normalizzazione via match testuale conservativo su <Stato>;
  - <SPEDIZIONEDIRIENTRO>: presente in caso di reso (sub-spedizione col proprio
    <TRACKING>) → lo stato corrente diventa 'returned';
  - <ERRORE><TESTOERRORE>Spedizione non trovata</TESTOERRORE></ERRORE>: spedizione
    inesistente → trattata come "nessun dato" (nessuna eccezione, stato invariato).

Stato corrente = evento più recente in ordine cronologico (IT + estero), coerente con
la regola generale (Poste/BRT, TASK_68). I codici grezzi vengono tradotti dal BASE via
la tabella di normalizzazione (seed in data/status_map_gls.xml); i non mappati →
'sconosciuto' + auto-discovery.
"""
import logging
import re
from datetime import datetime
from xml.etree import ElementTree

from odoo.addons.centrivo_shipment_tracking.connectors.base import (
    TrackingConnector,
    register_tracker,
)
from odoo.addons.centrivo_shipment_tracking.connectors.transport import RestTransport

_logger = logging.getLogger(__name__)

# Endpoint infoweb GLS Italia (sovrascrivibile da account.endpoint).
GLS_DEFAULT_ENDPOINT = "https://infoweb.gls-italy.com/XML/get_xml_track.php"
# Lunghezza dell'estratto grezzo salvato nel log (diagnostica; niente segreti).
RAW_EXCERPT_LEN = 2000
# Codice canonico GLS per "restituita al mittente" (reso) — usato come segnale di
# stato quando è presente il blocco <SPEDIZIONEDIRIENTRO>.
GLS_RETURNED_CODE = "29"

# Formati data/ora GLS infoweb: data GG/MM/AA (anno a 2 cifre), ora HH:MM.
_DATE_FORMATS = ("%d/%m/%y", "%d/%m/%Y")
_TIME_FORMATS = ("%H:%M:%S", "%H:%M")

# Regole di match testuale per la tratta ESTERA (eventi senza <Codice>): sottostringhe
# (lowercase) → codice canonico GLS riusato dal seed. Match CONSERVATIVO, primo che
# combacia vince; l'ordine conta (es. "parcel"/"ferma" prima di "consegnat"). Ciò che
# non combacia resta non mappato → unknown + auto-discovery (filosofia conservativa:
# meglio sconosciuto che un falso stato problematico).
_FOREIGN_TEXT_RULES = (
    (("non riuscita", "indirizzo incompleto"), "3"),       # exception
    (("parcel",), "66"),                                   # held (parcelshop)
    (("ferma",), "66"),                                    # held
    (("consegnat",), "906"),                               # delivered
    (("non ancora affidata", "spedizione creata"), "909"),  # pending_pickup
    (("consegna prevista", "in transito", "sede gls",
      "partita dalla sede", "affidata a gls", "affidata"), "902"),  # in_transit
)


@register_tracker("gls", "GLS")
class GlsTracker(TrackingConnector):
    """Connettore concreto GLS (infoweb XML, sola lettura)."""

    default_endpoint = GLS_DEFAULT_ENDPOINT
    # Niente user/password: l'accesso usa sigla sede + codice contratto in query.
    requires_credentials = False

    def __init__(self, account):
        super().__init__(account)
        self.transport = RestTransport(base_url=None)

    # ------------------------------------------------------------------
    # Helper di configurazione
    # ------------------------------------------------------------------
    def _sender_code(self):
        return (getattr(self.account, "gls_sender_code", None) or "").strip()

    def _contract_code(self):
        return (getattr(self.account, "gls_contract_code", None) or "").strip()

    def _numsped(self, tracking_number):
        """Numero spedizione senza il prefisso sigla sede (se presente).

        Il waybill reale è tipo "SA662402545": se inizia con la sigla sede
        (locpartenza), il prefisso va tolto → "662402545". Se non c'è prefisso, si usa
        il ref così com'è (robusto).
        """
        ref = (tracking_number or "").replace(" ", "").strip()
        sender = self._sender_code().upper()
        if sender and ref.upper().startswith(sender):
            return ref[len(sender):]
        return ref

    # ------------------------------------------------------------------
    def fetch_tracking(self, tracking_number):
        """Interroga infoweb GLS e ritorna il dict normalizzato (vedi base).

        GET con i tre parametri; risposta XML (ISO-8859-1). Allega sempre un estratto
        grezzo (raw_excerpt) per la diagnosi sul log. Nessun segreto in gioco.
        """
        endpoint = self.account.endpoint or self.default_endpoint
        params = {
            "locpartenza": self._sender_code(),
            "NumSped": self._numsped(tracking_number),
            "CodCli": self._contract_code(),
        }
        headers = {"Accept": "application/xml"}
        # GET sola lettura. Solleva TransportError sugli errori di rete (gestito dal
        # base). `requests` decodifica il testo (text/xml → ISO-8859-1).
        response = self.transport.request(
            "GET", endpoint, headers=headers, params=params)
        result = self._parse(response.text)
        raw = response.text if response.text else ""
        result["raw_excerpt"] = raw[:RAW_EXCERPT_LEN]
        return result

    # ------------------------------------------------------------------
    def _parse(self, text):
        """Estrae stato sintetico ed eventi dal payload XML GLS infoweb.

        Mai un'eccezione su payload inattesi: degrada a vuoto (→ stato invariato nel
        base). Gestisce <ERRORE> (spedizione non trovata), tratta IT (con <Codice>),
        tratta estera (<TRACKINGINT>, senza codice → match testuale) e reso
        (<SPEDIZIONEDIRIENTRO> → stato 'returned').
        """
        empty = {"current_raw_code": None, "current_raw_code_fallback": None,
                 "current_raw_desc": None, "events": [], "parcels": []}
        root = self._xml_root(text)
        if root is None:
            return dict(empty)
        # Caso errore: <ERRORE>/<TESTOERRORE> → nessun dato, non è un errore HTTP.
        if root.tag.upper() == "ERRORE" or root.find(".//TESTOERRORE") is not None:
            _logger.info("GLS infoweb: spedizione non trovata / nessun dato.")
            return dict(empty)
        sped = root if root.tag.upper() == "SPEDIZIONE" else root.find(".//SPEDIZIONE")
        if sped is None:
            return dict(empty)

        events = []
        # Tratta ITALIANA: <TRACKING> con <Codice> strutturato.
        for tr in sped.findall("TRACKING"):
            ev = self._event_from_node(tr, with_code=True)
            if ev:
                events.append(ev)
        # Tratta ESTERA: <ColloInternazionale><TRACKINGINT> senza codice.
        for tr in sped.findall(".//ColloInternazionale/TRACKINGINT"):
            ev = self._event_from_node(tr, with_code=False)
            if ev:
                events.append(ev)
        # Reso: <SPEDIZIONEDIRIENTRO> con il suo <TRACKING> interno → eventi del
        # rientro inclusi nella timeline (sono parte della storia della spedizione).
        ritorno = sped.find(".//SPEDIZIONEDIRIENTRO")
        if ritorno is not None:
            for tr in ritorno.findall(".//TRACKING"):
                ev = self._event_from_node(tr, with_code=True)
                if ev:
                    events.append(ev)

        # Stato corrente = evento più recente per data/ora (IT + estero + rientro).
        latest = self._latest_event(events)
        current_code = latest["raw_code"] if latest else None
        current_fallback = latest["raw_code_fallback"] if latest else None
        current_desc = latest["raw_description"] if latest else None
        # Segnale forte di RESO: la presenza del blocco rientro porta lo stato a
        # 'returned' (codice 29), anche se l'ultimo evento del rientro non lo esplicita.
        if ritorno is not None:
            current_code = GLS_RETURNED_CODE
            current_fallback = None
            current_desc = current_desc or "Spedizione resa al mittente."

        return {
            "current_raw_code": current_code,
            "current_raw_code_fallback": current_fallback,
            "current_raw_desc": current_desc,
            "events": events,
            "parcels": [],
        }

    # ------------------------------------------------------------------
    def _event_from_node(self, node, with_code):
        """Costruisce un evento normalizzato da un nodo <TRACKING>/<TRACKINGINT>.

        IT (with_code=True): raw_code = <Codice>. Estero (with_code=False): nessun
        codice → si prova il match testuale su <Stato>; se combacia il codice canonico
        va nel FALLBACK (raw_code resta None, onesto: l'estero non ha codice), se NON
        combacia il testo stesso diventa raw_code (così l'auto-discovery crea una riga
        'da mappare' con quel testo). Ritorna None se il nodo non porta nulla.
        """
        stato = self._text(node, "Stato")
        data = self._text(node, "Data")
        ora = self._text(node, "Ora")
        luogo = self._text(node, "Luogo")
        dt = self._parse_datetime(data, ora)
        if with_code:
            code = self._text(node, "Codice")
            if not (code or stato or dt):
                return None
            return {
                "event_datetime": dt,
                "raw_code": code or None,
                "raw_code_fallback": None,
                "raw_description": stato or None,
                "location": luogo or None,
                "branch_raw": None,
            }
        # Tratta estera: nessun codice.
        if not (stato or dt):
            return None
        canonical = self._foreign_text_to_code(stato)
        return {
            "event_datetime": dt,
            "raw_code": None if canonical else (stato.upper() or None),
            "raw_code_fallback": canonical,
            "raw_description": stato or None,
            "location": luogo or None,
            "branch_raw": None,
        }

    @staticmethod
    def _foreign_text_to_code(stato):
        """Traduce il testo <Stato> estero in un codice canonico GLS (conservativo).

        Match case-insensitive su sottostringhe inequivocabili (primo che combacia
        vince). None se nessuna regola combacia → l'evento resterà 'da mappare'.
        """
        text = (stato or "").lower()
        if not text:
            return None
        for needles, code in _FOREIGN_TEXT_RULES:
            if any(n in text for n in needles):
                return code
        return None

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
    def _xml_root(text):
        """Parsa l'XML (tollerante): rimuove la dichiarazione, None su fallimento.

        `text` è già una str (decodificata da requests, text/xml → ISO-8859-1). La
        dichiarazione <?xml ... encoding=...?> va rimossa perché ElementTree rifiuta
        una str con encoding dichiarato. I valori CDATA sono restituiti come testo.
        """
        if not text or not text.strip():
            return None
        cleaned = re.sub(r"<\?xml[^>]*\?>", "", text, count=1).strip()
        try:
            return ElementTree.fromstring(cleaned)
        except ElementTree.ParseError:
            _logger.warning("GLS infoweb: XML non parsabile.")
            return None

    @staticmethod
    def _text(node, tag):
        """Testo di un sotto-tag, trimmato (stringa vuota se assente)."""
        child = node.find(tag)
        if child is None or child.text is None:
            return ""
        return child.text.strip()

    @staticmethod
    def _parse_datetime(date_str, time_str):
        """Combina <Data> (GG/MM/AA) e <Ora> (HH:MM) in un datetime (None se non valida).

        Ora vuota → 00:00. Orari locali IT memorizzati as-is (come gli altri adattatori).
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
