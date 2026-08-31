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
    # Traduzione "corriere dell'anagrafica -> codice atteso dal marketplace".
    # Chiave = centrivo.carrier.brand.code (brt, gls, poste, ...), valore = un
    # codice che DEVE comparire in carrier_codes. La mantiene chi scrive il
    # connettore: l'utente non la vede e non la configura.
    carrier_brand_codes = {}

    def __init__(self, channel):
        # `channel` è un recordset centrivo.channel (singolo record).
        self.channel = channel
        self.env = channel.env

    # ------------------------------------------------------------------
    # Prezzo e giacenza — validi per tutti i connettori
    # ------------------------------------------------------------------
    # ⚠️ Questi due vivono qui perche' erano gia' scritti due volte in
    # ManoMano e in BricoBravo, e Kaufland sarebbe stata la terza copia.
    # Le due copie NON sono identiche alla lettera: differiscono nelle
    # docstring e nel testo di un warning (BricoBravo nomina il listino,
    # ManoMano no). Verificato confrontando l'AST senza docstring: a parita'
    # di input restituiscono gli stessi valori, l'unica differenza e' cosa
    # finisce nel log. Qui e' gia' stata portata la versione BricoBravo, la
    # piu' informativa, cosi' togliere i duplicati sara' una cancellazione
    # pura senza perdite di diagnostica.
    # Le due copie restano dove sono (una sottoclasse vince sulla base,
    # quindi nulla cambia per loro): toglierle e' un seguito a parte.
    def _pricelist_price(self, pricelist, product):
        """Prezzo unitario dal listino (API pubblica 18.0). None se assente."""
        if not pricelist:
            return None
        try:
            price = pricelist._get_product_price(product, 1.0)
        except Exception as exc:  # noqa: BLE001 - robustezza per singolo prodotto
            # Si usa pricelist.id e non display_name: se a rompersi e' proprio
            # il listino (record cancellato -> MissingError), leggerne il nome
            # qui dentro solleverebbe una seconda eccezione che sfuggirebbe
            # all'except. L'id e' gia' in memoria e non puo' fallire.
            _logger.warning("Prezzo non calcolabile per %s su listino id=%s: %s",
                            product.display_name, pricelist.id, exc)
            return None
        return price if isinstance(price, (int, float)) else None

    def _available_quantity(self, product, channel):
        """Quantità secondo channel.stock_quantity_type e stock_scope."""
        field_name = channel.stock_quantity_type or "free_qty"
        if channel.stock_scope == "warehouses" and channel.warehouse_ids:
            total = 0.0
            for warehouse in channel.warehouse_ids:
                total += getattr(
                    product.with_context(warehouse=warehouse.id), field_name)
            return total
        return getattr(product, field_name)

    # ------------------------------------------------------------------
    # Rete e transazioni — validi per tutti i connettori
    # ------------------------------------------------------------------
    # ⚠️ Questi attrezzi nascono in marketplace_kaufland, dove sono costati
    # quattro giri di correzioni. Stanno qui perche' Cdiscount li usa uguali
    # e la terza copia sarebbe il difetto che il repo ha gia' rifiutato una
    # volta. Kaufland tiene i suoi (la sottoclasse vince sulla base), quindi
    # il suo comportamento non cambia: toglierli di la' e' un seguito a
    # parte, non parte di questo lavoro.

    # Il codice PostgreSQL di «riga gia' bloccata da un altro».
    LOCK_OCCUPATO = "55P03"

    @staticmethod
    def _motivo_stato(stato, messaggio):
        """Il motivo di un rifiuto, con lo stato HTTP davanti.

        ⚠️ Senza il prefisso, un rifiuto a corpo vuoto lascia un motivo
        VUOTO: nel registro si legge «Motivi: 1 × » e lo stato non compare
        da nessuna parte. E' informazione che abbiamo gia' in mano, e serve
        anche a distinguere un 400 (colpa del dato) da un 502 (colpa del
        trasporto, ed esito IGNOTO).

        ⚠️ E il ripiego «nessun dettaglio» NON e' decorativo: i motivi
        vengono raggruppati e contati, e due connettori che formattano lo
        stesso rifiuto in due modi non lo raggrupperebbero mai insieme. La
        forma e' identica a quella di Kaufland, cosi' il giorno in cui la
        sua copia si toglie sara' una cancellazione pura, senza perdite.

        Kaufland ha due metodi, `_motivo_stato` e `_motivo_http`; qui ce n'e'
        uno solo, ed e' giusto cosi': chi arriva da Kaufland cercando
        `_motivo_http` non lo trova, perche' qui `_causa_incerta` chiama
        direttamente `_motivo_stato`.
        """
        dettaglio = (messaggio or "").strip()
        return "HTTP %s: %s" % (stato, dettaglio or "nessun dettaglio")

    def _causa_incerta(self, risposta):
        """Perche' di questa risposta non si puo' dire se il lavoro sia
        arrivato. Restituisce il motivo, oppure None se la risposta e' un
        verdetto certo.

        `risposta` deve esporre `.stato` (intero: lo stato HTTP, oppure `0`
        quando non e' arrivata risposta) e `.messaggio` (testo, eventualmente
        vuoto). NON e' il `TransportResponse` di transport.py (quello espone
        `status_code`, non `stato`): ogni connettore avvolge il trasporto
        nella propria classe risposta, come fa `RispostaKaufland`, e questo
        metodo lavora su quella.

        ⚠️ Due casi, e il secondo e' quello che si dimentica:

        - stato `0`: il trasporto non ha ricevuto risposta (rete caduta,
          tempo scaduto). La richiesta puo' essere arrivata lo stesso.
        - **stato 5xx**: 500, 502, 503, 504. Vuol dire ESATTAMENTE la stessa
          cosa. Trattarlo come un rifiuto certo fa proseguire il giro e
          invita implicitamente a RIPROVARE — e un riprovo cieco duplica il
          lavoro. Qui il trasporto sta dietro il proxy Traefik di Coolify:
          i 502 e i 504 non sono un'ipotesi di scuola.
        """
        try:
            stato = int(risposta.stato)
        except (TypeError, ValueError):
            # Uno stato che non e' un numero e' gia' di per se' un esito
            # ignoto: si tratta come lo zero, non si lascia esplodere il
            # confronto d'ordine qui sotto.
            stato = 0
        if stato == 0:
            return "la risposta si è persa per strada (%s)" % (
                (risposta.messaggio or "").strip() or "nessun dettaglio")
        if stato >= 500:
            return ("il marketplace ha risposto %s, cioè un guasto del suo "
                    "lato o del proxy che gli sta davanti"
                    % self._motivo_stato(stato, risposta.messaggio))
        return None

    def _al_riparo(self, funzione, *argomenti, **parole):
        """Esegue una scrittura dentro un savepoint. Rende True se e' andata.

        ⚠️ Catturare un errore del database in Python NON salva la
        transazione: PostgreSQL la mette in stato ABORTITO, da li' in poi
        ogni istruzione fallisce, e il commit finale della richiesta diventa
        un ROLLBACK silenzioso. Il savepoint e' l'unica cosa che la rimette
        in piedi. Serve a scrivere una traccia quando si e' gia' dentro un
        guasto.

        ⚠️ E siccome si e' GIA' dentro un guasto, non c'e' niente da
        salvare rilanciando: inghiotte tutto — compresi gli errori di
        concorrenza (40001, deadlock 40P01) e i rifiuti di regola di
        business, cosa che altrove in questo file si evita di proposito
        (vedi la docstring di `_prendi_il_turno`, piu' sotto in questa
        stessa classe). Conseguenza: il valore di ritorno va letto, perche'
        un `False` e' l'unica traccia che la scrittura di servizio non e'
        avvenuta.
        """
        try:
            with self.env.cr.savepoint():
                funzione(*argomenti, **parole)
            return True
        except Exception:  # noqa: BLE001
            _logger.exception("Scrittura di servizio fallita sul canale %s",
                              self.channel.id)
            return False

    def _prendi_il_turno(self, descrizione):
        """Un giro per volta su questo canale, o si spiega perche' no.

        ⚠️ Senza, due giri sovrapposti leggono lo stesso insieme e fanno lo
        stesso lavoro due volte sul marketplace. E un giro che dura decine di
        secondi senza ritorno a video invita al secondo clic: non e' uno
        scenario di laboratorio.

        `NOWAIT` e non un'attesa: un'attesa finirebbe uccisa dal worker.

        ⚠️ IL SAVEPOINT NON E' DECORATIVO: un'istruzione SQL fallita lascia
        la transazione ABORTITA, e da li' in poi perfino leggere
        `display_name` per comporre il messaggio esploderebbe con un
        `InFailedSqlTransaction` grezzo al posto della UserError chiara.

        ⚠️ E NON OGNI GUASTO E' «UN ALTRO GIRO IN CORSO»: un errore di
        serializzazione (40001) o un deadlock (40P01) sono altra cosa, e
        tradurli cosi' direbbe il falso sopprimendo il ritentativo che Odoo
        fa da solo. Si lascia risalire tutto cio' che non e' `55P03`.

        ⚠️ Si logga PRIMA di sollevare: la UserError la vede solo chi ha
        cliccato, in quel momento. Senza il log, un turno rifiutato non
        lascia traccia nel registro del worker e chi lo rilegge dopo non sa
        che e' successo.
        """
        from odoo.exceptions import UserError
        try:
            with self.env.cr.savepoint():
                # Il nome della tabella viene dal modello, non scritto a mano.
                self.env.cr.execute(  # noqa: S608 - `_table` e' interno
                    "SELECT id FROM %s WHERE id = %%s FOR UPDATE NOWAIT"
                    % self.channel._table, (self.channel.id,))
        except Exception as errore:  # noqa: BLE001
            if getattr(errore, "pgcode", None) != self.LOCK_OCCUPATO:
                raise
            _logger.warning(
                "Giro «%s» sul canale %s: turno non disponibile — %s",
                descrizione, self.channel.display_name, errore)
            raise UserError(
                "Un altro giro (%s) è già in corso su questo canale. Non se "
                "ne avvia un secondo: due giri sovrapposti leggerebbero le "
                "stesse righe e rifarebbero lo stesso lavoro due volte sul "
                "marketplace. Attendere che il primo finisca e riprovare."
                % descrizione)

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

    @classmethod
    def get_brand_codes_for(cls, code):
        """Traduzioni corriere -> codice del connettore con quel codice ({} se ignoto)."""
        klass = CONNECTOR_REGISTRY.get(code)
        return dict(getattr(klass, "carrier_brand_codes", {}) or {}) if klass else {}

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
