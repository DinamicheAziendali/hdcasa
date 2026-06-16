# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""centrivo.channel — il record di configurazione di un marketplace.

Ogni canale (es. "BricoBravo Sandbox") è un record con le sue credenziali e il
suo ambiente (sandbox/produzione). È il punto da cui parte il pull ricorrente
degli ordini. Le credenziali sono CAMPI del record, valorizzati in ambiente:
nessun valore reale vive nel codice.
"""
import logging
import secrets

from odoo import api, fields, models

from ..connectors.base import MarketplaceConnector

_logger = logging.getLogger(__name__)


class IntegrationChannel(models.Model):
    _name = "centrivo.channel"
    _description = "Canale di integrazione (marketplace configurato)"

    name = fields.Char(string="Nome", required=True)

    # Codice del connettore: la lista è dinamica e si popola con i moduli
    # marketplace_* installati (vedi registro in connectors/base.py).
    connector_code = fields.Selection(
        selection="_get_connector_selection",
        string="Connettore",
        required=True,
        help="Tipo di marketplace. Le opzioni dipendono dai moduli "
             "marketplace_* installati (es. BricoBravo).")

    active = fields.Boolean(string="Attivo", default=True)

    # Ambiente: la sandbox BricoBravo resetta gli ordini ogni ora. Il flag
    # permette di puntare alla sandbox in fase di test e alla produzione dopo.
    environment = fields.Selection(
        selection=[("sandbox", "Sandbox (test)"), ("production", "Produzione")],
        string="Ambiente", default="sandbox", required=True)

    # Base URL dell'API (di default quella del connettore; sovrascrivibile).
    base_url = fields.Char(string="Base URL API")

    # Opzione operativa: gli ordini marketplace sono GIÀ PAGATI, quindi di
    # default l'ordine importato viene confermato (action_confirm), non lasciato
    # in bozza. Configurabile per canale. Vive nel tab "Impostazioni" del form.
    confirm_on_import = fields.Boolean(
        string="Conferma ordine all'import", default=True,
        help="Se attivo, l'ordine importato viene confermato automaticamente "
             "(action_confirm): gli ordini marketplace sono già pagati. Se "
             "disattivo, l'ordine resta in bozza (quotation).")

    # Team di vendita opzionale: se valorizzato, gli ordini importati da questo
    # canale vengono assegnati a questo crm.team (campo nativo sale.order.team_id).
    # Se vuoto, l'ordine entra senza team (comportamento Odoo standard). Il team
    # NON viene creato dal connettore: si sceglie tra i crm.team esistenti.
    team_id = fields.Many2one(
        "crm.team", string="Team di vendita",
        help="Team di vendita (opzionale) a cui vengono assegnati gli ordini "
             "importati da questo canale. Se vuoto, l'ordine entra senza team "
             "(comportamento standard). Seleziona un team esistente: il "
             "connettore non lo crea.")

    # Credenziale: SOLO il campo, mai il valore nel codice. Va inserita qui
    # nell'istanza (o iniettata in ambiente). La chiave reale è fornita
    # separatamente, non versionata.
    api_key = fields.Char(
        string="API Key",
        help="Chiave API del marketplace. NON viene salvata nel codice: "
             "inserirla qui nell'ambiente. Per BricoBravo va nell'header "
             "'sh-token'.")

    # ------------------------------------------------------------------
    # EXPORT (feed prezzi/giacenze, TASK_23) — config raggruppata nel tab "Export".
    # ------------------------------------------------------------------
    # Token segreto che protegge gli URL pubblici del feed. Generato a caso alla
    # creazione; MAI loggato. Rigenerabile dall'apposito bottone.
    export_token = fields.Char(
        string="Token feed (segreto)", copy=False,
        default=lambda self: self._default_export_token(),
        help="Token che protegge gli URL pubblici del feed. Va passato come "
             "parametro ?token=... nell'URL. Tienilo riservato: NON viene mai "
             "scritto nei log.")

    pricelist_selling_id = fields.Many2one(
        "product.pricelist", string="Listino prezzo pieno",
        help="Listino usato per la colonna 'Selling Price (Price to GPP)' del feed.")
    pricelist_discounted_id = fields.Many2one(
        "product.pricelist", string="Listino prezzo scontato",
        help="Listino usato per la colonna 'Discounted Price' del feed.")

    stock_quantity_type = fields.Selection(
        selection=[
            ("qty_available", "Reale (On-hand)"),
            ("virtual_available", "Previsionale (Forecast)"),
            ("free_qty", "Disponibile (libera)"),
        ],
        string="Tipo quantità", default="free_qty", required=True,
        help="Quale quantità di stock esportare nel feed (campi computed nativi "
             "di Odoo).")
    stock_scope = fields.Selection(
        selection=[
            ("company", "Totale azienda"),
            ("warehouses", "Magazzini specifici"),
        ],
        string="Ambito quantità", default="company", required=True,
        help="Ambito su cui leggere la quantità: tutta l'azienda oppure la somma "
             "dei magazzini selezionati.")
    warehouse_ids = fields.Many2many(
        "stock.warehouse", string="Magazzini del feed",
        help="Usato solo se Ambito = Magazzini specifici. Le quantità dei "
             "magazzini selezionati vengono SOMMATE.")

    processing_time_default = fields.Integer(
        string="Processing Time di default", default=3,
        help="Valore della colonna 'Processing Time' quando il Customer Lead "
             "Time (sale_delay) del prodotto è 0/vuoto.")

    # Criterio di selezione prodotti: via TAG PRODOTTO (product.tag). Scelta
    # chiara e robusta: solo i prodotti il cui template ha uno dei tag indicati
    # finiscono nel feed. Se vuoto, il feed è VUOTO (mai un dump dell'intero
    # catalogo per errore).
    export_product_tag_ids = fields.Many2many(
        "product.tag", string="Tag prodotti da esportare",
        help="Solo i prodotti il cui template ha uno di questi tag finiscono nel "
             "feed. Se vuoto, il feed resta VUOTO (non si esporta tutto il "
             "catalogo).")

    # Storage del feed pre-generato: il cron/bottone genera e salva qui; il
    # controller serve quest'ultima versione senza rigenerare.
    stock_feed_content = fields.Text(
        string="Feed prezzi/giacenze (CSV)", copy=False, readonly=True)
    stock_feed_generated_at = fields.Datetime(
        string="Feed generato il", copy=False, readonly=True)

    # ------------------------------------------------------------------
    # MAPPING CATALOGO (feed catalogo completo, TASK_24) — colonne descrittive
    # configurabili. Many2one a ir.model.fields filtrati sui modelli prodotto:
    # l'utente sceglie QUALE campo Odoo alimenta la colonna. Se non impostato,
    # la colonna esce VUOTA (mai errore). Brand e immagini sono letti in modo
    # DINAMICO/OPZIONALE (nessuna dipendenza hard da moduli di terzi/OCA).
    # ------------------------------------------------------------------
    _PRODUCT_FIELD_DOMAIN = "[('model', 'in', ['product.template', 'product.product'])]"

    map_field_name = fields.Many2one(
        "ir.model.fields", string="Campo → ProductName",
        domain=_PRODUCT_FIELD_DOMAIN, ondelete="set null",
        help="Campo Odoo che alimenta la colonna 'ProductName'. Se vuoto, usa il "
             "nome del prodotto (name).")
    map_field_category = fields.Many2one(
        "ir.model.fields", string="Campo → Category",
        domain=_PRODUCT_FIELD_DOMAIN, ondelete="set null",
        help="Campo Odoo che alimenta la colonna 'Category' (testo libero; se è "
             "una relazione si usa il display_name). Vuoto → colonna vuota.")
    map_field_url = fields.Many2one(
        "ir.model.fields", string="Campo → Url",
        domain=_PRODUCT_FIELD_DOMAIN, ondelete="set null",
        help="Campo Odoo che alimenta la colonna 'Url'. Vuoto → colonna vuota.")
    map_field_description = fields.Many2one(
        "ir.model.fields", string="Campo → Product Description",
        domain=_PRODUCT_FIELD_DOMAIN, ondelete="set null",
        help="Campo Odoo che alimenta la colonna 'Product Description' (l'HTML "
             "viene mantenuto). Vuoto → colonna vuota.")

    use_product_brand = fields.Boolean(
        string="Usa Brand (OCA product_brand)", default=True,
        help="Se attivo e il modulo OCA product_brand è presente, la colonna "
             "'Brand' è alimentata da product_brand_id. Se il modulo è assente, "
             "la colonna resta vuota (nessun errore).")

    vat_default = fields.Integer(
        string="IVA di default (%)", default=22,
        help="Aliquota IVA usata nella colonna 'Vat' quando il prodotto non ha "
             "un'imposta cliente percentuale.")

    # NB: le immagini del feed catalogo NON usano un campo URL esterno. Gli URL
    # pubblici sono COSTRUITI dai blob nativi Odoo (image_1920) su web.base.url
    # (vedi connettore BricoBravo, _image_urls). Nessun campo di mapping immagini.

    catalog_feed_content = fields.Text(
        string="Feed catalogo (CSV)", copy=False, readonly=True)
    catalog_feed_generated_at = fields.Datetime(
        string="Feed catalogo generato il", copy=False, readonly=True)

    # URL pronti da copiare nel pannello BricoBravo (TASK_27): computed NON
    # stored, ricalcolati sempre freschi e dipendenti da export_token (se l'utente
    # rigenera il token, gli URL si aggiornano da soli). base_url da config.
    stock_feed_url = fields.Char(
        string="URL feed prezzi/giacenze", compute="_compute_feed_urls",
        help="URL completo (con token) da incollare nel pannello BricoBravo per "
             "il feed prezzi/giacenze. Se rigeneri il token, va re-incollato.")
    catalog_feed_url = fields.Char(
        string="URL feed catalogo", compute="_compute_feed_urls",
        help="URL completo (con token) da incollare nel pannello BricoBravo per "
             "il feed catalogo. Se rigeneri il token, va re-incollato.")

    # Multi-tenant: ogni canale appartiene a una company.
    company_id = fields.Many2one(
        "res.company", string="Azienda", required=True,
        default=lambda self: self.env.company)

    last_pull = fields.Datetime(string="Ultimo pull ordini", readonly=True)

    order_map_count = fields.Integer(
        string="Ordini registrati", compute="_compute_counts")
    job_log_count = fields.Integer(
        string="Log operazioni", compute="_compute_counts")

    @api.model
    def _get_connector_selection(self):
        """Selezione dinamica dei connettori registrati dai moduli marketplace_*."""
        options = MarketplaceConnector.get_selection()
        # Se nessun connettore è ancora installato, evitiamo una selezione vuota.
        return options or [("none", "Nessun connettore installato")]

    @api.onchange("connector_code")
    def _onchange_connector_code(self):
        """Popola automaticamente base_url col default del connettore se vuoto.

        Es. scegliendo BricoBravo, base_url viene precompilato con l'URL ufficiale
        (uguale per sandbox e produzione: la differenza la fa la API key). Il
        campo resta visibile e sovrascrivibile.
        """
        if self.connector_code and not self.base_url:
            default_url = MarketplaceConnector.get_default_base_url(
                self.connector_code)
            if default_url:
                self.base_url = default_url

    def _compute_counts(self):
        OrderMap = self.env["centrivo.order.map"]
        JobLog = self.env["centrivo.job.log"]
        for channel in self:
            channel.order_map_count = OrderMap.search_count(
                [("channel_id", "=", channel.id)])
            channel.job_log_count = JobLog.search_count(
                [("channel_id", "=", channel.id)])

    @api.depends("export_token")
    def _compute_feed_urls(self):
        """URL pronti dei feed (stock/catalogo) con base_url da config + token.

        Non-stored: dipende da export_token e dal web.base.url corrente. Vuoto se
        il canale non è ancora salvato (no id) o se manca il token.
        """
        base_url = (self.env["ir.config_parameter"].sudo()
                    .get_param("web.base.url") or "").rstrip("/")
        for channel in self:
            if base_url and channel.id and channel.export_token:
                token = channel.export_token
                channel.stock_feed_url = "%s/integrations/feed/stock/%s?token=%s" % (
                    base_url, channel.id, token)
                channel.catalog_feed_url = "%s/integrations/feed/catalog/%s?token=%s" % (
                    base_url, channel.id, token)
            else:
                channel.stock_feed_url = False
                channel.catalog_feed_url = False

    # ------------------------------------------------------------------
    # Connettore
    # ------------------------------------------------------------------
    def _get_connector(self):
        """Ritorna l'istanza del connettore concreto per questo channel."""
        self.ensure_one()
        return MarketplaceConnector.for_channel(self)

    def action_pull_orders(self):
        """Pulsante/azione: scarica gli ordini per questo canale."""
        for channel in self:
            if not channel.active:
                continue
            connector = channel._get_connector()
            connector.pull_orders()
            channel.last_pull = fields.Datetime.now()
        return True

    # ------------------------------------------------------------------
    # EXPORT feed prezzi/giacenze (TASK_23)
    # ------------------------------------------------------------------
    @api.model
    def _default_export_token(self):
        """Genera un token segreto robusto per proteggere gli URL del feed."""
        return secrets.token_urlsafe(32)

    def action_regenerate_export_token(self):
        """Bottone: rigenera il token del feed (invalida gli URL precedenti)."""
        for channel in self:
            channel.export_token = self._default_export_token()
        return True

    def action_generate_stock_feed(self):
        """Bottone/azione: rigenera SUBITO il feed prezzi/giacenze per i canali.

        Delega al connettore concreto (generate_stock_feed). Isola gli errori per
        canale con log su centrivo.job.log: un canale non blocca gli altri.
        """
        for channel in self:
            if not channel.active:
                continue
            try:
                channel._get_connector().generate_stock_feed()
            except NotImplementedError:
                _logger.info(
                    "Il connettore del canale %s non espone un feed prezzi/giacenze.",
                    channel.name)
            except Exception as exc:  # noqa: BLE001 - isolamento per canale
                _logger.exception("Generazione feed fallita per il canale %s",
                                  channel.name)
                self.env["centrivo.job.log"].create({
                    "channel_id": channel.id,
                    "operation": "export_stock_feed",
                    "result": "error",
                    "message": str(exc)[:2000],
                    "company_id": channel.company_id.id,
                })
        return True

    @api.model
    def cron_generate_stock_feeds(self):
        """ir.cron: rigenera il feed prezzi/giacenze per tutti i canali attivi.

        Circoscritto all'export: è separato dal CRON del ciclo ordini. Parte
        DISATTIVO di default (vedi data/ir_cron.xml); Angelo lo attiva quando vuole.
        """
        channels = self.search([("active", "=", True)])
        channels.action_generate_stock_feed()
        return True

    # ------------------------------------------------------------------
    # EXPORT feed catalogo completo (TASK_24)
    # ------------------------------------------------------------------
    def action_generate_catalog_feed(self):
        """Bottone/azione: rigenera SUBITO il feed catalogo per i canali.

        Gemello di action_generate_stock_feed: delega al connettore
        (generate_catalog_feed) e isola gli errori per canale su job.log.
        """
        for channel in self:
            if not channel.active:
                continue
            try:
                channel._get_connector().generate_catalog_feed()
            except NotImplementedError:
                _logger.info(
                    "Il connettore del canale %s non espone un feed catalogo.",
                    channel.name)
            except Exception as exc:  # noqa: BLE001 - isolamento per canale
                _logger.exception("Generazione feed catalogo fallita per il canale %s",
                                  channel.name)
                self.env["centrivo.job.log"].create({
                    "channel_id": channel.id,
                    "operation": "export_catalog_feed",
                    "result": "error",
                    "message": str(exc)[:2000],
                    "company_id": channel.company_id.id,
                })
        return True

    @api.model
    def cron_generate_catalog_feeds(self):
        """ir.cron (gemello, frequenza più bassa): rigenera il feed catalogo.

        Il catalogo cambia meno spesso del feed prezzi/giacenze, quindi è un cron
        separato con intervallo più ampio. Anch'esso DISATTIVO di default.
        """
        channels = self.search([("active", "=", True)])
        channels.action_generate_catalog_feed()
        return True

    # ------------------------------------------------------------------
    # Infrastruttura job: pull ricorrente via ir.cron
    # ------------------------------------------------------------------
    @api.model
    def cron_pull_all_channels(self):
        """Metodo invocato dall'ir.cron: scorre i canali attivi e fa il pull.

        Ogni canale è isolato: un errore su un canale non blocca gli altri
        (try/except per canale, con log su centrivo.job.log). Ossatura del
        retry/logging predisposta; la chiamata reale arriva dai connettori.
        """
        channels = self.search([("active", "=", True)])
        for channel in channels:
            try:
                channel.action_pull_orders()
            except Exception as exc:  # noqa: BLE001 - isolamento per canale
                _logger.exception("Pull fallito per il canale %s", channel.name)
                self.env["centrivo.job.log"].create({
                    "channel_id": channel.id,
                    "operation": "pull_orders",
                    "result": "error",
                    "message": str(exc)[:2000],
                    "company_id": channel.company_id.id,
                })
        return True

    @api.model
    def cron_push_shipments(self):
        """ir.cron: comunica le spedizioni pronte ai marketplace (Strato 3b in batch).

        Per ogni canale attivo cerca gli centrivo.order.map con state=imported e
        shipment_pushed=False il cui sale.order ha ALMENO un picking `done` con
        `carrier_tracking_ref` valorizzato (spedizione pronta), e invoca
        push_shipment (che applica precondizioni e idempotenza). La logica di
        business è riusata dal connettore: il cron è solo un orchestratore in batch.

        NB OPERATIVA: oggi questo cron NON ha nulla da pushare finché ShipTracker
        non scrive `carrier_tracking_ref` sui picking (ShipTracker non ancora
        integrato). È predisposto e corretto, ma operativo solo quando il tracking
        sarà popolato. Il pre-filtro sul picking pronto evita di loggare errori per
        gli ordini non ancora spedibili.

        Isola gli errori per ordine/canale: un fallimento non blocca gli altri.
        """
        OrderMap = self.env["centrivo.order.map"]
        for channel in self.search([("active", "=", True)]):
            try:
                connector = channel._get_connector()
            except Exception:  # noqa: BLE001 - canale senza connettore valido
                continue
            candidates = OrderMap.search([
                ("channel_id", "=", channel.id),
                ("state", "=", "imported"),
                ("shipment_pushed", "=", False),
            ])
            for order_map in candidates:
                sale_order = order_map.sale_order_id
                if not sale_order:
                    continue
                # Solo ordini con una spedizione PRONTA (picking done + tracking):
                # evita di loggare errori per quelli non ancora spedibili.
                ready = sale_order.picking_ids.filtered(
                    lambda p: p.state == "done" and p.carrier_tracking_ref)
                if not ready:
                    continue
                try:
                    connector.push_shipment(order_map)
                except NotImplementedError:
                    break  # connettore senza push: inutile iterare gli altri ordini
                except Exception as exc:  # noqa: BLE001 - isolamento per ordine
                    _logger.exception(
                        "Push spedizione fallito per l'ordine %s del canale %s",
                        order_map.external_id, channel.name)
                    self.env["centrivo.job.log"].create({
                        "channel_id": channel.id,
                        "operation": "push_shipment",
                        "external_id": order_map.external_id,
                        "result": "error",
                        "message": str(exc)[:2000],
                        "company_id": channel.company_id.id,
                    })
        return True
