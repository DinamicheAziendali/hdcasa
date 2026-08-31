# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Client delle chiamate Temu: parametri comuni, firma, ritmo, lettura esito.

Fatti dalla documentazione ufficiale (partner-eu.temu.com, Developer Guide):
  - un solo endpoint per tutta l'area UE, Italia inclusa;
  - SOLO metodo POST, corpo JSON;
  - il nome dell'operazione viaggia nel campo `type` del corpo;
  - parametri comuni obbligatori: app_key, access_token, data_type, timestamp, sign;
  - limite iniziale dichiarato: ~20 chiamate al secondo per app_key.

Questo modulo non importa Odoo: riceve un `transport` gia' pronto (in Odoo sara'
`RestTransport` di integrations_core, nei test un oggetto finto).
"""
import logging
import time

from .temu_signature import sign_params

_logger = logging.getLogger(__name__)

TEMU_URL_EU = "https://openapi-b-eu.temu.com"
TEMU_ROUTER_PATH = "/openapi/router"

# Intervallo minimo fra due chiamate: 20 al secondo e' il tetto dichiarato,
# 0,06 s ne fa circa 16 e lascia margine.
DEFAULT_MIN_INTERVAL = 0.06

# Codici di errore che significano "stai andando troppo forte": si aspetta e si
# riprova invece di insistere. La lista e' volutamente PRUDENTE e va rivista
# alla prima frenata vera (il codice esatto va confermato sul reale).
RATE_LIMIT_CODES = {"4000005", "4000006"}
RATE_LIMIT_HINTS = ("frequent", "too many", "rate limit", "qps")

# Attesa fra un tentativo e l'altro dopo una frenata.
RETRY_PAUSE = 1.0


class TemuResult(object):
    """Esito normalizzato di una chiamata Temu."""

    def __init__(self, ok, data=None, error_code=None, error_msg=None,
                 raw=None, http_status=None):
        self.ok = ok
        self.data = data if data is not None else {}
        self.error_code = error_code
        self.error_msg = error_msg
        self.raw = raw if raw is not None else {}
        self.http_status = http_status

    @property
    def stato(self):
        """Lo stato HTTP, col nome che si aspetta la classe base.

        ⚠️ Non e' un doppione di `http_status`: e' il CONTRATTO che
        `_causa_incerta` di integrations_core dichiara nella sua docstring, e
        che RispostaKaufland e RispostaCdiscount gia' rispettano. Con questo
        nome TemuResult diventa una risposta di casa e il metodo della base
        funziona su di lei senza che Temu ne scriva una terza copia.

        ⚠️ Resta `None` quando la rete e' caduta: la base lo normalizza a 0,
        che e' il suo valore per «non e' arrivata risposta». Non anticipare
        quella normalizzazione qui, o si perde la distinzione.
        """
        return self.http_status

    @property
    def messaggio(self):
        """Il motivo, col nome che si aspetta la classe base. Mai None."""
        return self.error_msg or ""

    def __repr__(self):
        return "<TemuResult ok=%s code=%s msg=%s>" % (
            self.ok, self.error_code, self.error_msg)


class TemuClient(object):
    """Esegue le chiamate Temu firmate. Non sa nulla di prodotti o ordini."""

    def __init__(self, transport, app_key, app_secret, access_token,
                 min_interval=DEFAULT_MIN_INTERVAL, max_retries=2):
        self.transport = transport
        self.app_key = app_key
        self.app_secret = app_secret
        self.access_token = access_token
        self.min_interval = min_interval
        self.max_retries = max_retries
        self._last_call_at = 0.0

    # ------------------------------------------------------------------
    def build_body(self, api_type, params=None, timestamp=None):
        """Corpo completo e FIRMATO della richiesta.

        Il segreto non entra mai nel corpo: serve solo a calcolare `sign`.
        """
        body = dict(params or {})
        body["type"] = api_type
        body["app_key"] = self.app_key
        body["access_token"] = self.access_token
        body["data_type"] = "JSON"
        body["timestamp"] = int(timestamp if timestamp is not None
                                else time.time())
        body["sign"] = sign_params(body, self.app_secret)
        return body

    def call(self, api_type, params=None):
        """Esegue la chiamata e ritorna un TemuResult. Non solleva eccezioni."""
        attempts = self.max_retries + 1
        result = None
        for attempt in range(attempts):
            self._respect_pace()
            body = self.build_body(api_type, params)
            try:
                response = self.transport.request(
                    "POST", TEMU_ROUTER_PATH,
                    headers={"Content-Type": "application/json"},
                    json=body)
            except Exception as err:  # rete/timeout: non deve mai propagare
                _logger.warning("Temu %s: errore di rete: %s", api_type, err)
                return TemuResult(False, error_code="RETE", error_msg=str(err))
            result = self._read(response)
            if result.ok or not self._is_rate_limited(result):
                return result
            if attempt < attempts - 1:
                _logger.info("Temu %s: frenata dal marketplace, riprovo fra "
                             "%s s", api_type, RETRY_PAUSE)
                time.sleep(RETRY_PAUSE)
        return result

    # ------------------------------------------------------------------
    def _respect_pace(self):
        """Aspetta quel tanto che basta a non superare il limite dichiarato."""
        if not self.min_interval:
            return
        elapsed = time.time() - self._last_call_at
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_call_at = time.time()

    @staticmethod
    def _read(response):
        """Traduce la risposta grezza in TemuResult.

        Attenzione: Temu risponde HTTP 200 anche quando l'operazione e' fallita.
        L'esito vero sta nel campo `success` del corpo, non nello stato HTTP:
        leggere solo lo stato HTTP e' esattamente l'errore che su ManoMano ha
        fatto sembrare riuscite chiamate che non lo erano.
        """
        body = response.json if isinstance(response.json, dict) else {}
        status = getattr(response, "status_code", None)
        if status is None or not (200 <= status < 300):
            return TemuResult(False, error_code="HTTP_%s" % status,
                              error_msg=(getattr(response, "text", "") or "")[:1000],
                              raw=body, http_status=status)
        if body.get("success") is True:
            return TemuResult(True, data=body.get("result") or {}, raw=body,
                              http_status=status)
        code = body.get("errorCode")
        return TemuResult(False,
                          error_code=str(code) if code is not None else None,
                          error_msg=body.get("errorMsg"),
                          raw=body, http_status=status)

    @staticmethod
    def _is_rate_limited(result):
        """Vero se l'errore dice 'stai chiamando troppo spesso'.

        ⚠️ GLI INDIZI TESTUALI VALGONO SOLO SE TEMU HA RISPOSTO 2xx. Su una
        risposta non-2xx `error_msg` non e' un messaggio di Temu: e' il testo
        grezzo della pagina d'errore del proxy che gli sta davanti. Una pagina
        502 che contenga per caso "too many requests" faceva partire DUE invii
        in piu' dentro la stessa `call` — con lo stesso numero di tracking, che
        su Temu si usa UNA VOLTA SOLA. Misurato: 502 con corpo "Bad Gateway" =
        1 invio, 502 con corpo "502 Bad Gateway: too many requests" = 3 invii.

        Una frenata VERA di Temu arriva come 200 con `errorCode` 4000005 o
        4000006, che il ramo per codice qui sotto intercetta gia'. Il ritmo
        resta protetto; a sparire e' solo l'indovinello sul testo di un proxy.
        """
        if result.error_code and str(result.error_code) in RATE_LIMIT_CODES:
            return True
        stato = result.http_status
        if not isinstance(stato, int) or not (200 <= stato < 300):
            return False
        msg = (result.error_msg or "").lower()
        return any(hint in msg for hint in RATE_LIMIT_HINTS)
