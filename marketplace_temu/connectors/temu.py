# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Connettore Temu (Famiglia A). FASE 1 parte A: sola lettura.

Costruisce il client firmato a partire dalle credenziali del canale. Le
operazioni di catalogo vivono in temu_catalog.py (Task 7); qui c'e' solo
l'ossatura e la registrazione nel registro dei connettori.
"""
import logging

from odoo.addons.integrations_core.connectors.base import (
    MarketplaceConnector, register_connector)
from odoo.addons.integrations_core.connectors.transport import RestTransport

from .temu_catalog import TemuCatalogMixin
from .temu_orders import TemuOrdersMixin
from .temu_pricing import TemuPricingMixin
from .temu_shipping import TemuShippingMixin
from .temu_client import TemuClient, TEMU_URL_EU

_logger = logging.getLogger(__name__)


@register_connector("temu", "Temu")
class TemuConnector(TemuCatalogMixin, TemuOrdersMixin, TemuShippingMixin,
                    TemuPricingMixin, MarketplaceConnector):

    # ⚠️ Cosa Temu NON usa della scheda del canale: le credenziali sono le sue
    # (chiave, segreto, gettone) nel suo tab, e non fa feed CSV.
    usa_api_key = False
    usa_ambienti = False
    usa_feed_csv = False
    usa_immagini_feed = False
    usa_mappa_catalogo = False
    usa_presa_in_carico = False   # non esiste, su questo marketplace
    """Connettore Temu: un solo endpoint per tutta l'area UE, Italia inclusa."""

    default_base_url = TEMU_URL_EU

    # Traduzione corriere dell'anagrafica -> identificativo Temu del fornitore
    # logistico. ⚠️ Temu NON vuole una sigla come ManoMano ("BRT"): vuole il
    # NUMERO. Gli identificativi sono stati letti dal negozio vero con
    # bg.logistics.companies.get per regionId 98 (Italia) il 2026-08-20:
    # 71 corrieri ammessi, questi sono i nostri.
    #
    # Su GLS Temu elenca DUE fornitori distinti, "GLS" (547987123) e "GLS(IT)"
    # (998264874): Angelo ha scelto GLS(IT).
    #
    # "dpd" resta VOLUTAMENTE non tradotto: nella lista italiana non esiste un
    # DPD generico (ci sono DPD DE, PL, CZ, FR, IE, NL, AT, SK, LT, RO) e in
    # Italia il servizio e' proprio BRT Bartolini(DPD). Tradurlo a caso
    # manderebbe i pacchi sotto il corriere sbagliato: meglio che la Copertura
    # corrieri lo mostri come "Manca" e che sia una persona a decidere.
    carrier_brand_codes = {
        "brt": "998264994",     # BRT Bartolini(DPD)
        "gls": "998264874",     # GLS(IT)
        "poste": "784213879",   # Poste Italiane
        "sda": "998265084",     # SDA
        "dhl": "141252268",     # DHL
        "ups": "314439762",     # UPS
        "fedex": "699272611",   # FedEx
        "tnt": "998265159",     # TNT (IT)
    }

    def __init__(self, channel):
        super().__init__(channel)
        self.transport = RestTransport(base_url=channel.base_url or TEMU_URL_EU)
        self.client = TemuClient(
            self.transport,
            app_key=channel.temu_app_key or "",
            app_secret=channel.temu_app_secret or "",
            access_token=channel.temu_access_token or "",
        )

    # ------------------------------------------------------------------
    # IL TOKEN INTERROGATO
    # ------------------------------------------------------------------
    def verifica_collegamento(self):
        """Interroga il token: scadenza vera e chiamate scoperte.

        ⚠️ La scadenza SI SCRIVE sul canale, al posto di quella digitata a
        mano: un campo che diverge dalla verita' e' peggio di un campo assente.
        """
        from .temu_token import API_TOKEN_INFO, leggi_token, chiamate_scoperte
        from .temu_orders import INTERFACCE_DA_CHIEDERE

        risposta = self.client.call(API_TOKEN_INFO, {})
        # ⚠️ Un 502 o una rete caduta NON sono un token rifiutato: dire
        # «il token non e' valido» perche' non e' arrivata risposta manda a
        # rifare l'autorizzazione per un guasto di rete.
        incerto = self._causa_incerta(risposta)
        if incerto:
            self._log("temu_verifica", "error",
                      "Non si e' potuto interrogare il token: %s" % incerto)
            return {"scoperte": None, "scadenza": None,
                    "scadenza_scritta": False, "motivo": incerto}
        if not risposta.ok:
            motivo = "%s %s" % (risposta.error_code or "",
                                risposta.error_msg or "")
            self._log("temu_verifica", "error",
                      "Il token non si e' lasciato interrogare: %s" % motivo)
            return {"scoperte": None, "scadenza": None,
                    "scadenza_scritta": False, "motivo": motivo}

        scadenza, permessi = leggi_token(risposta.data or {})
        # ⚠️ L'elenco delle chiamate arriva dalla costante del ciclo ordini,
        # NON da una copia scritta qui: il giorno che se ne aggiunge una,
        # una copia direbbe «tutto coperto» proprio sulla nuova.
        scoperte = chiamate_scoperte(permessi, INTERFACCE_DA_CHIEDERE)
        quando = self._data_da_epoch(scadenza)
        scritta = False
        if quando:
            # ⚠️ Passa da `_al_riparo`: e' una scrittura di servizio, e se il
            # canale non si lascia scrivere la verifica deve comunque dire
            # cosa ha visto invece di morire a meta'.
            #
            # ⚠️ E il valore di ritorno SI LEGGE, come prescrive la docstring
            # della classe base: un `False` e' l'unica traccia che la scrittura
            # non e' avvenuta. Senza leggerlo, il campo continuerebbe a
            # mostrare il promemoria digitato a mano mesi fa mentre il popup
            # dice che va tutto bene — cioe' esattamente il campo che diverge
            # dalla verita' che questo metodo esiste per togliere di mezzo.
            scritta = self._al_riparo(self.channel.sudo().write,
                                      {"temu_token_expiry": quando})
        quante = "ignoti" if permessi is None else str(len(permessi))
        messaggio = ("Token: %s permessi, scadenza %s. " % (quante, scadenza))
        if scoperte is None:
            messaggio += ("⚠️ La risposta non porta l'elenco dei permessi: "
                          "non si puo' dire quali chiamate siano coperte.")
        elif scoperte:
            # ⚠️ DAL CENTRO PARTNER NON SI CHIEDE NIENTE, ed e' il vicolo
            # cieco che questo modulo ha gia' pagato una volta: li' si
            # cambiano solo indirizzo IP, negozi e Paesi, e nessuna delle tre
            # riguarda le interfacce. La stessa verita', misurata, sta nel
            # messaggio degli importi in `temu_orders.py`: si tiene allineata
            # a quella, non riscritta a senso.
            messaggio += (
                "⚠️ NON coperte: %s. Dal Centro Partner si cambiano solo tre "
                "cose — indirizzo IP, negozi, Paesi autorizzati — e le "
                "interfacce non sono fra queste. Vanno sollecitate sul ticket "
                "gia' aperto presso l'assistenza Temu, che si segue dal Seller "
                "Center → Assistenza, e tutte insieme: la domanda si fa una "
                "volta sola." % ", ".join(scoperte))
        else:
            # ⚠️ NON «tutte le chiamate»: il confronto ha guardato solo le
            # interfacce dell'elenco, cioe' quelle non dichiarate al momento
            # della registrazione dell'app. Le altre che il modulo chiama non
            # sono in questo confronto, e dire «tutte coperte» le darebbe per
            # buone anche il giorno che una venisse revocata.
            messaggio += ("Le %s interfacce dell'elenco — quelle non "
                          "dichiarate alla registrazione dell'app — risultano "
                          "coperte. ⚠️ Le altre chiamate del modulo non sono "
                          "in questo confronto: se una fosse stata revocata, "
                          "qui non si vedrebbe."
                          % len(INTERFACCE_DA_CHIEDERE))
        if not scritta:
            messaggio += (" ⚠️ La scadenza NON e' finita sul campo del canale: "
                          "li' si continua a leggere la data digitata a mano, "
                          "che puo' essere sbagliata.")
        # ⚠️ VERDE SOLO SE NON C'E' NIENTE DA SEGNALARE, e il confronto e' con
        # la lista vuota, non con la verita' di `scoperte`: su ogni uscita
        # d'errore — 502, rete caduta, token rifiutato — `scoperte` vale None,
        # che e' falsy. Un `if scoperte` tingerebbe di verde proprio i tre casi
        # in cui non si e' saputo niente.
        tutto_a_posto = scoperte == [] and scritta
        self._log("temu_verifica", "success" if tutto_a_posto else "error",
                  messaggio)
        return {"scoperte": scoperte, "scadenza": scadenza,
                "scadenza_scritta": scritta, "motivo": messaggio}

    @staticmethod
    def _data_da_epoch(secondi):
        """La data di scadenza a partire dai secondi epoch di Temu.

        ⚠️ Il campo del canale e' un `fields.Date`: la scadenza del token si
        legge a giorni, non a secondi. Rende None se il valore non e' un
        numero, cosi' una risposta storta non azzera una data buona.

        ⚠️ Si passa il fuso esplicito invece di `utcfromtimestamp`, che
        Python 3.12 deprecata: qui gira un 3.9 e non direbbe niente, ma
        Odoo.sh no, e un avviso di deprecazione nel registro di produzione e'
        rumore che nasconde gli avvisi veri.
        """
        import datetime
        try:
            return datetime.datetime.fromtimestamp(
                int(secondi), datetime.timezone.utc).date()
        except (TypeError, ValueError, OverflowError, OSError):
            return None

    def _log(self, operation, result, message, payload=None, external_id=None):
        """Scrive nel log operazioni. Il segreto non ci finisce MAI."""
        self.env["centrivo.job.log"].sudo().create({
            "channel_id": self.channel.id,
            "operation": operation,
            "external_id": external_id or False,
            "result": result,
            "message": message,
            "payload": (payload or "")[:20000] or False,
            "company_id": self.channel.company_id.id,
        })
