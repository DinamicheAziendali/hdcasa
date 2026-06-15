# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Contratto astratto dei connettori marketplace (Famiglia A).

Un "connettore" è la logica specifica di un marketplace (es. BricoBravo): sa
come parlare con la sua API e come tradurre i suoi ordini in dati Odoo. Questa
classe definisce SOLO il contratto (quali metodi deve avere ogni connettore); le
sottoclassi concrete vivono nei moduli marketplace_* e lo implementano.

Punto chiave di architettura:
- Il connettore NON inventa prodotti: Odoo è la fonte di verità. Se un prodotto
  dell'ordine esterno non esiste in Odoo, l'ordine va in errore (non si crea il
  prodotto).
- Il connettore scrive SEMPRE attraverso i modelli pubblici di Odoo
  (self.env['sale.order'].create(...)), mai con SQL diretto.
- Il trasporto (come si fanno le chiamate HTTP/CSV) è separato: vedi transport.py.
"""
import logging

_logger = logging.getLogger(__name__)

# Registro dei connettori disponibili: codice -> classe.
# I moduli marketplace_* registrano qui la propria classe con @register_connector.
CONNECTOR_REGISTRY = {}


def register_connector(code, label):
    """Decoratore per registrare un connettore concreto nel registro globale.

    Esempio (nel modulo marketplace_bricobravo):
        @register_connector("bricobravo", "BricoBravo")
        class BricoBravoConnector(MarketplaceConnector):
            ...
    """
    def _wrap(cls):
        cls._connector_code = code
        cls._connector_label = label
        CONNECTOR_REGISTRY[code] = cls
        _logger.info("Connettore registrato: %s (%s)", code, label)
        return cls
    return _wrap


class MarketplaceConnector(object):
    """Classe base astratta per tutti i connettori marketplace della Famiglia A.

    Una sottoclasse concreta riceve il record `centrivo.channel` che la
    configura (credenziali, ambiente sandbox/produzione, ecc.) e lavora sul
    relativo `env` Odoo, rispettando sempre la company del channel.
    """

    # Valorizzati dal decoratore @register_connector.
    _connector_code = None
    _connector_label = None
    # URL di default dell'API del connettore (sovrascrivibile dal channel).
    # Le sottoclassi concrete lo valorizzano (es. BricoBravo).
    default_base_url = None
    # Lista CHIUSA dei codici corriere ammessi dal marketplace, come coppie
    # (codice, etichetta). È una proprietà DEL CONNETTORE: ogni connettore
    # concreto la dichiara (es. BricoBravo i suoi 16 codici). Il modello di
    # mapping corrieri (centrivo.carrier.map) interroga questa lista per
    # popolare il Selection dinamico, SENZA hardcodare nulla nel modello.
    carrier_codes = []

    def __init__(self, channel):
        # `channel` è un recordset centrivo.channel (singolo record).
        self.channel = channel
        self.env = channel.env

    # ------------------------------------------------------------------
    # Helpers del registro
    # ------------------------------------------------------------------
    @classmethod
    def get_selection(cls):
        """Ritorna la lista [(codice, etichetta)] per il campo Selection del channel."""
        return [(code, klass._connector_label or code)
                for code, klass in sorted(CONNECTOR_REGISTRY.items())]

    @classmethod
    def for_channel(cls, channel):
        """Istanzia il connettore concreto giusto a partire dal channel."""
        klass = CONNECTOR_REGISTRY.get(channel.connector_code)
        if not klass:
            raise NotImplementedError(
                "Nessun connettore registrato per il codice '%s'. "
                "Verifica che il modulo marketplace_* relativo sia installato."
                % channel.connector_code)
        return klass(channel)

    @classmethod
    def get_default_base_url(cls, code):
        """Ritorna l'URL di default del connettore con quel codice (o None)."""
        klass = CONNECTOR_REGISTRY.get(code)
        return getattr(klass, "default_base_url", None) if klass else None

    @classmethod
    def get_carrier_codes(cls):
        """Lista [(codice, etichetta)] dei corrieri ammessi da QUESTO connettore."""
        return list(cls.carrier_codes or [])

    @classmethod
    def get_carrier_codes_for(cls, code):
        """Lista [(codice, etichetta)] dei corrieri del connettore con quel codice.

        Ritorna [] se il codice connettore non è registrato (es. canale senza
        connettore valido). Usata dal modello centrivo.carrier.map per sapere
        quali codici sono validi per un dato canale.
        """
        klass = CONNECTOR_REGISTRY.get(code)
        return klass.get_carrier_codes() if klass else []

    @classmethod
    def get_all_carrier_codes(cls):
        """Unione (deduplicata, ordinata) dei codici corriere di TUTTI i connettori.

        Serve al Selection dinamico di centrivo.carrier.map: un campo
        Selection deve dichiarare TUTTI i valori che possono essere memorizzati
        (su qualunque canale/connettore), altrimenti Odoo considera invalidi i
        valori non in lista. La restrizione "su un canale BricoBravo si scelgono
        SOLO i codici BricoBravo" è applicata a parte (onchange + vincolo) nel
        modello. Oggi è registrato il solo BricoBravo, quindi l'unione coincide
        con i suoi 16 codici.
        """
        seen = {}
        for klass in CONNECTOR_REGISTRY.values():
            for code, label in klass.get_carrier_codes():
                seen.setdefault(code, label)
        return sorted(seen.items(), key=lambda kv: kv[1].lower())

    # ------------------------------------------------------------------
    # CONTRATTO: metodi che ogni connettore concreto DEVE implementare.
    # Qui sollevano NotImplementedError di proposito.
    # ------------------------------------------------------------------
    def pull_orders(self):
        """Scarica gli ordini nuovi dal marketplace e ne avvia l'import.

        Tipicamente: chiama l'API (via trasporto), itera le pagine, e per ogni
        ordine esterno invoca self.import_order(...). Deve gestire la
        paginazione e affidarsi a import_order per l'idempotenza.
        """
        raise NotImplementedError

    def import_order(self, external_order):
        """Traduce UN ordine esterno in un vero sale.order Odoo (idempotente).

        Ordine delle operazioni (vincolante, vedi connettore concreto):
          1) verifica idempotenza su centrivo.order.map (channel + external_id);
          2) mappa prodotti/partner/indirizzi sui modelli Odoo;
          3) crea il sale.order via self.env['sale.order'].create(...);
          4) registra l'esito in centrivo.order.map;
          5) SOLO dopo il successo chiama mark_acquired(external_id).
        Se un prodotto non esiste in Odoo: NON crearlo, segnare errore.
        """
        raise NotImplementedError

    def mark_acquired(self, external_id):
        """Segnala al marketplace che l'ordine è stato preso in carico.

        Da chiamare SOLO dopo che il sale.order è stato creato con successo e
        registrato in centrivo.order.map.
        """
        raise NotImplementedError

    def push_shipment(self, order_map):
        """Comunica al marketplace la spedizione (corriere + tracking) di UN ordine.

        Riceve il record `centrivo.order.map` e legge da solo i dati che gli
        servono dai modelli PUBBLICI Odoo: il sale.order collegato, il suo
        stock.picking in stato done e il campo nativo `carrier_tracking_ref`, e
        il mapping corriere (centrivo.carrier.map) per tradurre il
        delivery.carrier Odoo nel codice del marketplace. Trigger MANUALE: nessun
        automatismo qui.
        """
        raise NotImplementedError

    def generate_stock_feed(self):
        """Genera e SALVA sul canale il feed CSV prezzi/giacenze (EXPORT).

        Legge soltanto i prodotti esistenti (Odoo è la fonte di verità: non si
        crea/modifica nulla) secondo la configurazione export del canale, e
        salva il CSV pre-generato sul canale, così il controller può servirlo
        senza rigenerarlo. Da implementare nel connettore concreto.
        """
        raise NotImplementedError

    def generate_catalog_feed(self):
        """Genera e SALVA sul canale il feed CSV catalogo completo (EXPORT).

        Come generate_stock_feed ma con le colonne descrittive del prodotto
        (categoria, brand, nome, url, descrizione, immagini, IVA, ...) secondo la
        mappatura configurabile del canale. Sola lettura dei prodotti. Da
        implementare nel connettore concreto.
        """
        raise NotImplementedError
