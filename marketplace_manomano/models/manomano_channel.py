# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Estensione di centrivo.channel per ManoMano (campi + bottone + cron).

Vive nel modulo ManoMano (non in integrations_core) per non toccare il core: i
campi/azioni specifici del marketplace stanno nel modulo del marketplace.
"""
import logging

from odoo import api, exceptions, fields, models

_logger = logging.getLogger(__name__)


class CentrivoChannel(models.Model):
    _inherit = "centrivo.channel"

    # Contract (mercati) ManoMano su cui pubblicare le offerte. Multi-mercato:
    # più ID separati da virgola. Il contract ManoMano è un ID NUMERICO
    # (seller_contract_id, es. 11781522), non un codice mercato "IT". Un Char
    # libero accetta comunque qualunque formato: la conversione a intero (dove
    # richiesta nel body JSON) avviene nel connettore.
    manomano_contract_codes = fields.Char(
        string="Contract ManoMano",
        help="ID contratto ManoMano (seller_contract_id), separati da virgola "
             "se più di uno (es. '11781522' o '11781522,124578'). Le offerte "
             "vengono inviate a TUTTI i contract elencati.")

    manomano_thirdparty_name = fields.Char(
        string="Thirdparty name",
        help="Identificativo dell'integrazione inviato nell'header "
             "x-thirdparty-name, es. \"HDcasa_1.0\".")

    manomano_carrier_grid_name = fields.Char(
        string="Griglia di spedizione",
        help="Nome della griglia di spedizione configurata nel back-office "
             "ManoMano, usato nella creazione offerte "
             "(shipping.carrier_grid[].name).")

    manomano_transit_days_min = fields.Integer(
        string="Transito corriere (minimo giorni)", default=1,
        help="Giorni di TRANSITO del corriere (non il tempo di evasione del "
             "prodotto, che è un campo a parte): quanto impiega il pacco ad "
             "arrivare dopo essere stato affidato al corriere. Si somma al "
             "tempo di evasione del prodotto per formare la finestra di "
             "consegna inviata a ManoMano. ManoMano conta questi giorni come "
             "GIORNI LAVORATIVI e aggiunge da sé un giorno in più agli ordini "
             "ricevuti dopo l'orario limite configurato nel loro pannello: "
             "non serve gonfiare questo valore per tenerne conto.")
    manomano_transit_days_max = fields.Integer(
        string="Transito corriere (massimo giorni)", default=3,
        help="Come sopra, ma il massimo dei giorni di transito del corriere. "
             "Deve essere maggiore del minimo: ManoMano richiede un "
             "intervallo, non un singolo numero, altrimenti rifiuta "
             "l'offerta (non riesce a calcolare una promessa di consegna).")

    manomano_default_weight = fields.Float(
        string="Peso di ripiego (kg)", default=0.0,
        help="Peso usato SOLO per i prodotti che in Odoo non ne hanno uno. "
             "ManoMano rifiuta le offerte con peso a zero "
             "(ERR_PIM_OFFER_API_REQUEST_VALIDATION: 'display_weight must be "
             "greater than 0').\n\n"
             "Lasciato a 0 (impostazione predefinita) i prodotti senza peso "
             "vengono SALTATI e contati nel log: è la scelta prudente, perché "
             "il peso determina il costo di spedizione calcolato da ManoMano e "
             "un valore inventato lo falserebbe. Impostare un valore solo se si "
             "preferisce pubblicare comunque quei prodotti, sapendo che la "
             "spedizione sarà calcolata su un peso approssimato.")

    @api.constrains("manomano_transit_days_min", "manomano_transit_days_max")
    def _check_manomano_transit_days(self):
        """Il vincolo vale SOLO sui canali ManoMano.

        `centrivo.channel` è condiviso con gli altri marketplace: senza questo
        filtro il controllo scatterebbe anche sui canali BricoBravo, che questi
        campi non li hanno mai valorizzati (quindi 0 e 0) e che sono in
        PRODUZIONE — il primo salvataggio di quei canali fallirebbe.
        """
        for channel in self:
            if channel.connector_code != "manomano":
                continue
            minimo = channel.manomano_transit_days_min
            massimo = channel.manomano_transit_days_max
            if minimo < 0 or massimo < 0:
                raise exceptions.ValidationError(
                    "I giorni di transito del corriere (ManoMano) non "
                    "possono essere negativi.")
            if massimo <= minimo:
                raise exceptions.ValidationError(
                    "Il transito massimo del corriere (ManoMano) deve "
                    "essere maggiore del minimo. Con una forbice a zero (es. "
                    "2 e 2) ManoMano non riesce a calcolare una promessa di "
                    "consegna e rifiuta l'offerta con l'errore "
                    "«ALL_GRID_NOT_ELIGIBLE_FOR_OFFER».")

    manomano_invoice_auto = fields.Boolean(
        string="Invia le fatture in automatico", default=False,
        help="Se attivo, alla conferma di una fattura collegata a un ordine "
             "ManoMano il PDF viene inviato al marketplace. Parte SPENTO: "
             "accendilo solo dopo aver visto funzionare l'invio manuale.")

    manomano_product_feed_content = fields.Text(
        string="Feed prodotto ManoMano (CSV)", copy=False, readonly=True)
    manomano_product_feed_generated_at = fields.Datetime(
        string="Feed prodotto generato il", copy=False, readonly=True)
    manomano_feed_map_ids = fields.One2many(
        "centrivo.manomano.feed.map", "channel_id",
        string="Mappatura feed prodotto")
    manomano_product_feed_url = fields.Char(
        string="URL feed prodotto", compute="_compute_manomano_feed_url",
        help="URL (con token) da incollare nella Toolbox ManoMano "
             "(Catalogo → importazione automatica).")

    @api.depends("export_token")
    def _compute_manomano_feed_url(self):
        base_url = (self.env["ir.config_parameter"].sudo()
                    .get_param("web.base.url") or "").rstrip("/")
        for channel in self:
            if base_url and channel.id and channel.export_token:
                channel.manomano_product_feed_url = (
                    "%s/integrations/feed/manomano-products/%s?token=%s"
                    % (base_url, channel.id, channel.export_token))
            else:
                channel.manomano_product_feed_url = False

    def _manomano_contracts(self):
        """Lista pulita dei codici contract (dedup, senza vuoti, ordine stabile)."""
        self.ensure_one()
        raw = self.manomano_contract_codes or ""
        seen = []
        for part in raw.split(","):
            code = part.strip()
            if code and code not in seen:
                seen.append(code)
        return seen

    def action_manomano_push_offers(self):
        """Bottone/azione: sincronizza le offerte ManoMano per i canali.

        Delega al connettore concreto (push_offers). Isola gli errori per canale
        su centrivo.job.log: un canale non blocca gli altri. Pattern gemello di
        action_generate_stock_feed.
        """
        for channel in self:
            if not channel.active:
                continue
            try:
                channel._get_connector().push_offers()
            except NotImplementedError:
                _logger.info(
                    "Il connettore del canale %s non espone push_offers.",
                    channel.name)
            except Exception as exc:  # noqa: BLE001 - isolamento per canale
                _logger.exception(
                    "push_offers fallito per il canale %s", channel.name)
                self.env["centrivo.job.log"].create({
                    "channel_id": channel.id,
                    "operation": "push_offers",
                    "result": "error",
                    "message": str(exc)[:2000],
                    "company_id": channel.company_id.id,
                })
        return True

    def action_manomano_check_offers(self):
        """Bottone: chiede a ManoMano lo stato delle offerte. SOLA LETTURA.

        Strumento di diagnosi: non modifica nulla né su Odoo né su ManoMano.
        L'esito (prezzo/giacenza registrati da loro, stato, errori) finisce su
        centrivo.job.log con operazione `check_offers`, corpo grezzo compreso.
        """
        single = len(self) == 1
        for channel in self:
            if not channel.active:
                if single:
                    return self._manomano_notification(
                        "Canale non attivo.", kind="warning")
                continue
            try:
                count = channel._get_connector().check_offers()
            except NotImplementedError:
                _logger.info(
                    "Il connettore del canale %s non espone check_offers.",
                    channel.name)
                if single:
                    return self._manomano_notification(
                        "Il connettore di questo canale non verifica le "
                        "offerte.", kind="warning")
            except Exception as exc:  # noqa: BLE001 - isolamento per canale
                _logger.exception(
                    "check_offers fallito per il canale %s", channel.name)
                self.env["centrivo.job.log"].create({
                    "channel_id": channel.id,
                    "operation": "check_offers",
                    "result": "error",
                    "message": str(exc)[:2000],
                    "company_id": channel.company_id.id,
                })
                if single:
                    return self._manomano_notification(
                        "Verifica fallita: %s" % str(exc)[:200], kind="danger")
            else:
                if single:
                    # count puo essere False (fallimento di precondizione),
                    # 0 (chiamata riuscita ma nulla da verificare) o un
                    # numero positivo di SKU interrogati: False == 0 in
                    # Python, quindi il controllo e per identita, non per
                    # verita, altrimenti un fallimento risulterebbe verde.
                    if count is False:
                        return self._manomano_notification(
                            "Verifica NON eseguita: leggi il dettaglio nel "
                            "Log operazioni (operazione «check_offers»).",
                            kind="danger")
                    if count == 0:
                        return self._manomano_notification(
                            "Verifica: nessuna offerta da controllare (nessun "
                            "tag prodotti o nessuno SKU). Dettaglio nel Log "
                            "operazioni.", kind="warning")
                    return self._manomano_notification(
                        "Verifica eseguita su %s SKU: leggi l'esito nel Log "
                        "operazioni (operazione «check_offers»)." % count,
                        kind="success")
        return True

    @api.model
    def cron_manomano_push_offers(self):
        """ir.cron (SPENTO di default): push offerte per i canali ManoMano attivi.

        Circoscritto a ManoMano (connector_code='manomano'). Parte disattivo:
        Angelo lo attiva quando il flusso è validato in sandbox/produzione.
        """
        channels = self.search([
            ("active", "=", True),
            ("connector_code", "=", "manomano"),
        ])
        channels.action_manomano_push_offers()
        return True

    def action_manomano_sync_taxonomy(self):
        """Bottone: scarica da ManoMano l'elenco dei campi del feed prodotto.

        SOLA LETTURA verso ManoMano; in Odoo aggiorna il catalogo dei campi e dei
        valori ammessi. Non tocca prodotti né mappature.
        """
        TITLE = "Campi feed ManoMano"
        single = len(self) == 1
        for channel in self:
            if not channel.active:
                if single:
                    return self._manomano_notification(
                        "Canale non attivo.", kind="warning", title=TITLE)
                continue
            try:
                count = channel._get_connector().sync_taxonomy()
            except NotImplementedError:
                _logger.info("Canale %s: connettore senza taxonomy.", channel.name)
                if single:
                    return self._manomano_notification(
                        "Il connettore di questo canale non scarica i campi.",
                        kind="warning", title=TITLE)
            except Exception as exc:  # noqa: BLE001 - isolamento per canale
                _logger.exception("sync_taxonomy fallito per %s", channel.name)
                self.env["centrivo.job.log"].create({
                    "channel_id": channel.id,
                    "operation": "taxonomy_sync",
                    "result": "error",
                    "message": str(exc)[:2000],
                    "company_id": channel.company_id.id,
                })
                if single:
                    return self._manomano_notification(
                        "Scarico fallito: %s" % str(exc)[:200], kind="danger",
                        title=TITLE)
            else:
                if single:
                    # count puo essere False (sync_taxonomy fallita per rete/
                    # HTTP/troncamento, senza sollevare eccezioni), 0 (chiamata
                    # riuscita ma nessun campo pertinente ricevuto) o un
                    # numero positivo: False == 0 in Python, quindi il
                    # controllo e per identita, non per verita, altrimenti un
                    # fallimento risulterebbe verde.
                    if count is False:
                        return self._manomano_notification(
                            "Scarico fallito: leggi il dettaglio nel Log "
                            "operazioni (operazione «taxonomy_sync»).",
                            kind="danger", title=TITLE)
                    if count == 0:
                        return self._manomano_notification(
                            "Nessun campo pertinente ricevuto: il feed "
                            "continua a usare l'elenco di riserva. Dettaglio "
                            "nel Log operazioni.", kind="warning", title=TITLE)
                    return self._manomano_notification(
                        "Campi ManoMano aggiornati: %s. Dettaglio nel Log "
                        "operazioni." % count, kind="success", title=TITLE)
        return True

    def action_manomano_generate_product_feed(self):
        """Genera SUBITO il feed prodotto ManoMano (isolamento errori per canale).

        Su un singolo canale (click interattivo del bottone) restituisce una
        notifica con l'esito, così un utente non tecnico vede se ha funzionato.
        Il connettore si TIENE (non si scarta) per leggere, dopo la
        generazione, quanti avvisi ha raccolto (`product_feed_warnings_count`,
        vedi Fix controlli feed): con avvisi la notifica è `warning`, invece
        del `success` sempre verde di prima, altrimenti un utente non tecnico
        non aprirebbe mai il Log operazioni per vederli.
        Su un recordset (uso batch o cron, es. cron_manomano_product_feeds)
        mantiene il comportamento storico: loop silenzioso + log su
        centrivo.job.log, return True.
        """
        single = len(self) == 1
        for channel in self:
            if not channel.active:
                if single:
                    return self._manomano_feed_notification(
                        "Canale non attivo.", kind="warning")
                continue
            connector = channel._get_connector()
            try:
                rows = connector.generate_product_feed()
            except NotImplementedError:
                _logger.info("Canale %s: connettore senza feed prodotto.",
                             channel.name)
                if single:
                    return self._manomano_feed_notification(
                        "Il connettore di questo canale non genera un feed "
                        "prodotto.", kind="warning")
            except Exception as exc:  # noqa: BLE001 - isolamento per canale
                _logger.exception("Feed prodotto ManoMano fallito per %s",
                                  channel.name)
                self.env["centrivo.job.log"].create({
                    "channel_id": channel.id,
                    "operation": "manomano_product_feed",
                    "result": "error",
                    "message": str(exc)[:2000],
                    "company_id": channel.company_id.id,
                })
                if single:
                    return self._manomano_feed_notification(
                        "Generazione feed fallita: %s" % str(exc)[:200],
                        kind="danger")
            else:
                if single:
                    righe = ("Feed prodotto generato: %s righe." % rows if rows
                             else "Feed prodotto generato.")
                    avvisi = getattr(
                        connector, "product_feed_warnings_count", 0)
                    if avvisi:
                        return self._manomano_feed_notification(
                            "%s %s celle da rivedere: apri il Log operazioni "
                            "(operazione «manomano_product_feed»)."
                            % (righe, avvisi), kind="warning")
                    return self._manomano_feed_notification(
                        righe, kind="success")
        return True

    def _manomano_feed_notification(self, message, kind="info"):
        """Notifica non bloccante per il bottone (esito generazione feed prodotto)."""
        return self._manomano_notification(
            message, kind=kind, title="Feed prodotto ManoMano")

    def _manomano_notification(self, message, kind="info", title="ManoMano"):
        """Notifica non bloccante generica per i bottoni ManoMano."""
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": title,
                "message": message,
                "type": kind,
                "sticky": False,
            },
        }

    @api.model
    def cron_manomano_product_feeds(self):
        """ir.cron (SPENTO): rigenera il feed prodotto dei canali ManoMano attivi."""
        channels = self.search([
            ("active", "=", True), ("connector_code", "=", "manomano")])
        channels.action_manomano_generate_product_feed()
        return True
