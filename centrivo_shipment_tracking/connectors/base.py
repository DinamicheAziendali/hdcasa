# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Contratto astratto dei TrackingConnector (gemello di MarketplaceConnector).

Un "tracking connector" è la logica specifica di un corriere (es. GLS): sa come
interrogare il suo nodo/API di tracking e come tradurre la risposta grezza in una
struttura comune (eventi + codice stato grezzo). NON conosce gli stati Centrivo:
la NORMALIZZAZIONE (codice grezzo → stato Centrivo) vive nel BASE, via la tabella
centrivo.shipment.status.map. Separazione netta:

  - connettore  = HTTP (sola lettura) + parsing → eventi/codici GREZZI;
  - base        = normalizzazione via tabella + aggregazione colli + persistenza.

Il trasporto HTTP è interno al tracking (connectors/transport.py: RestTransport con
retry sui 5xx, backoff, timeout, segreti MAI loggati). La suite è autosufficiente:
nessuna dipendenza da integrations_core.
"""
import logging

_logger = logging.getLogger(__name__)

# Registro dei connettori di tracking: codice -> classe.
# I moduli centrivo_track_* registrano qui la propria classe con @register_tracker.
TRACKER_REGISTRY = {}


def register_tracker(code, label):
    """Decoratore per registrare un connettore di tracking concreto.

    Esempio (nel modulo centrivo_track_gls):
        @register_tracker("gls", "GLS")
        class GlsTracker(TrackingConnector):
            ...
    """
    def _wrap(cls):
        cls._tracker_code = code
        cls._tracker_label = label
        TRACKER_REGISTRY[code] = cls
        _logger.info("Tracking connector registrato: %s (%s)", code, label)
        return cls
    return _wrap


class TrackingConnector(object):
    """Classe base astratta per tutti i connettori di tracking corriere.

    Una sottoclasse concreta riceve il record centrivo.tracking.account che la
    configura (endpoint, credenziali, ambiente) e lavora sul relativo env Odoo.
    """

    # Valorizzati dal decoratore @register_tracker.
    _tracker_code = None
    _tracker_label = None
    # Endpoint di default del nodo/API (sovrascrivibile dall'account). Le
    # sottoclassi concrete lo valorizzano (es. GLS nodo pubblico).
    default_endpoint = None
    # Il connettore richiede credenziali (utente/password/token)? Le API private
    # (BRT/Poste) sì; i nodi pubblici (GLS) no. Pilota la UI dell'account: i campi
    # credenziali si nascondono quando non servono.
    requires_credentials = True

    def __init__(self, account):
        # `account` è un recordset centrivo.tracking.account (singolo record).
        self.account = account
        self.env = account.env

    # ------------------------------------------------------------------
    # Helpers del registro
    # ------------------------------------------------------------------
    @classmethod
    def get_selection(cls):
        """Lista [(codice, etichetta)] per il campo Selection tracker_code."""
        return [(code, klass._tracker_label or code)
                for code, klass in sorted(TRACKER_REGISTRY.items())]

    @classmethod
    def for_account(cls, account):
        """Istanzia il connettore concreto giusto a partire dall'account."""
        klass = TRACKER_REGISTRY.get(account.tracker_code)
        if not klass:
            raise NotImplementedError(
                "Nessun connettore di tracking registrato per il codice '%s'. "
                "Verifica che il modulo centrivo_track_* relativo sia installato."
                % account.tracker_code)
        return klass(account)

    @classmethod
    def get_default_endpoint(cls, code):
        """Endpoint di default del connettore con quel codice (o None)."""
        klass = TRACKER_REGISTRY.get(code)
        return getattr(klass, "default_endpoint", None) if klass else None

    @classmethod
    def get_requires_credentials(cls, code):
        """True se il connettore con quel codice richiede credenziali.

        Per i codici non registrati ritorna True (prudente: meglio mostrare i
        campi che nasconderli su un connettore sconosciuto).
        """
        klass = TRACKER_REGISTRY.get(code)
        return bool(getattr(klass, "requires_credentials", True)) if klass else True

    # ------------------------------------------------------------------
    # CONTRATTO: ogni connettore concreto DEVE implementarlo.
    # ------------------------------------------------------------------
    def fetch_tracking(self, tracking_number):
        """Interroga il corriere (SOLA LETTURA) e ritorna i dati grezzi normalizzati.

        Deve ritornare un dict con questa forma (i codici sono GREZZI del corriere,
        la traduzione in stato Centrivo la fa il base):

            {
                "current_raw_code": "INTRANSIT",   # codice stato sintetico, o None
                "current_raw_desc": "In transito", # descrizione sintetica, opzionale
                "events": [                          # checkpoint storici (timeline)
                    {
                        "event_datetime": <datetime|None>,
                        "raw_code": <str|None>,
                        "raw_description": <str|None>,
                        "location": <str|None>,
                        "branch_raw": <str|None>,
                    },
                    ...
                ],
                "parcels": [                          # colli individuali, [] se non esposti
                    {"tracking_number": "...", "raw_code": "..."},
                    ...
                ],
            }

        Solleva transport.TransportError sugli errori di rete (gestiti dal base).
        """
        raise NotImplementedError
