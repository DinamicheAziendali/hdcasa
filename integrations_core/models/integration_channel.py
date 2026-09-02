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

from odoo import _, api, fields, models

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

    # Responsabile degli errori di import (TASK_70): se valorizzato, le attività di
    # segnalazione errore (ordine non importato) vengono assegnate a questo utente; se
    # vuoto, l'attività è assegnata all'utente che esegue il pull (fallback).
    error_activity_user_id = fields.Many2one(
        "res.users", string="Responsabile errori import",
        help="Utente a cui assegnare l'attività Odoo quando l'import di un ordine "
             "fallisce. Se vuoto, l'attività va all'utente che esegue il pull.")

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
    # pubblici sono COSTRUITI dai blob nativi Odoo su web.base.url (vedi
    # connettore BricoBravo, _image_urls). Nessun campo di mapping immagini.

    # Risoluzione servita dalla rotta immagini. Odoo tiene GIÀ pronte le versioni
    # ridimensionate (image.mixin): servirne una più piccola non costa nulla in
    # CPU e alleggerisce di molto il trasferimento.
    #
    # Perché è una scelta per canale e non un valore fisso: il 2026-07-28
    # ManoMano ha scaricato 23.101 immagini dalla produzione e l'originale a
    # 1920px ha prodotto timeout e "Server limit reached"; dopo qualche
    # fallimento di fila il loro scaricatore ha interrotto TUTTO, lasciando
    # 5.247 schede senza immagine. Serviva poter alleggerire ManoMano SENZA
    # cambiare BricoBravo, che è vivo in produzione e funziona.
    feed_image_resolution = fields.Selection(
        [("1920", "Originale (1920 px) — la più pesante"),
         ("1024", "Media (1024 px) — consigliata"),
         ("512", "Piccola (512 px)")],
        string="Risoluzione immagini del feed", default="1024", required=True,
        help="Dimensione dell'immagine servita ai marketplace sulla rotta "
             "pubblica. Odoo tiene già pronte tutte queste versioni, quindi "
             "scegliere la più piccola non rallenta nulla: riduce solo il peso "
             "dello scaricamento. 1024 px è ampiamente sufficiente per una "
             "scheda di marketplace. Alzare a 1920 solo se il marketplace "
             "lamenta immagini di qualità insufficiente.")

    catalog_feed_content = fields.Text(
        string="Feed catalogo (CSV)", copy=False, readonly=True)
    catalog_feed_generated_at = fields.Datetime(
        string="Feed catalogo generato il", copy=False, readonly=True)
    # TASK_85: la generazione del feed catalogo è ASINCRONA in background. Il
    # bottone marca il canale 'in coda' (pending) e un cron esecutore ATTIVO lo
    # elabora fuori dal worker web (la generazione sincrona, sull'intero catalogo,
    # rischiava il timeout della richiesta e la memoria del worker).
    catalog_feed_pending = fields.Boolean(
        string="Feed catalogo in coda", copy=False, readonly=True, default=False,
        help="Quando attivo, la generazione del feed catalogo è stata accodata e "
             "verrà eseguita in background dal cron esecutore.")

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

    def _canale_registra(self, channel, nome, operazione, messaggio,
                         external_id=None, esito="error"):
        """Una riga nel registro delle operazioni, che non porti via il giro.

        Serve tutti i giri automatici che promettono l'isolamento per canale,
        qui e nei moduli marketplace_* che ereditano questo modello.

        ⚠️ SCRIVERE IL REGISTRO E' L'ULTIMA COSA CHE DEVE POTER FAR CADERE UN
        GIRO. Se la riga non si scrive — una transazione gia' abortita da un
        guasto vero del database, per dire — resta comunque il log di sistema,
        e gli altri canali vanno serviti lo stesso.

        ⚠️ IL NOME ARRIVA COME PAROLA, gia' letta da chi chiama quando la
        transazione era sana. Leggere `channel.name` qui dentro sarebbe una
        lettura SQL dentro un gestore d'errore, cioe' la stessa eccezione che
        si sta cercando di sopravvivere. `channel.id` e `channel.company_id`
        si leggono invece DENTRO il savepoint, dove un guasto e' contenuto.

        ⚠️ E IL SAVEPOINT NON E' DECORATIVO, benche' l'eccezione sia gia'
        catturata qui sotto. Catturare un errore del database in Python NON
        salva la transazione: PostgreSQL la lascia ABORTITA, e il canale
        successivo esploderebbe gia' nel flush d'ingresso del suo savepoint.
        E' la stessa cura dei gemelli gia' in casa,
        `kaufland_channel._kaufland_registra` e `cdiscount._cdiscount_registra`.
        """
        try:
            with self.env.cr.savepoint():
                valori = {
                    "channel_id": channel.id,
                    "operation": operazione,
                    "result": esito,
                    "message": messaggio,
                    "company_id": channel.company_id.id,
                }
                if external_id is not None:
                    valori["external_id"] = external_id
                self.env["centrivo.job.log"].create(valori)
        except Exception:  # noqa: BLE001 - il registro non porta via il giro
            _logger.exception(
                "Non si e' potuta scrivere la riga di registro (%s/%s) del "
                "canale %s", operazione, esito, nome)

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
    # COSA MOSTRARE su questo canale — lo decide il suo connettore
    # ------------------------------------------------------------------
    # ⚠️ Non memorizzati e senza `@api.depends` su nient'altro che il codice
    # connettore: servono solo alla schermata, e un campo memorizzato
    # significherebbe una colonna in piu' su una tabella di produzione per
    # decidere se disegnare un riquadro.
    usa_api_key = fields.Boolean(compute="_compute_cosa_usa")
    usa_ambienti = fields.Boolean(compute="_compute_cosa_usa")
    usa_indirizzo_base = fields.Boolean(compute="_compute_cosa_usa")
    usa_feed_csv = fields.Boolean(compute="_compute_cosa_usa")
    usa_immagini_feed = fields.Boolean(compute="_compute_cosa_usa")
    usa_mappa_catalogo = fields.Boolean(compute="_compute_cosa_usa")

    @api.depends("connector_code")
    def _compute_cosa_usa(self):
        """Chiede al connettore cosa usa, e la scheda si adatta.

        ⚠️ Nessun nome di marketplace qui dentro: il tronco chiede, i moduli
        rispondono. Un connettore nuovo dichiara le sue e la schermata lo
        segue senza che questo file cambi.
        """
        for canale in self:
            for nome in ("usa_api_key", "usa_ambienti", "usa_indirizzo_base",
                         "usa_feed_csv", "usa_immagini_feed",
                         "usa_mappa_catalogo"):
                canale[nome] = MarketplaceConnector.usa_per(
                    canale.connector_code, nome)

    @api.model
    def action_centrivo_automazioni(self):
        """Le azioni pianificate DEL PACCHETTO, in una schermata sola.

        ⚠️ Perche' esiste. Le automazioni di questo pacchetto **nascono tutte
        spente**, ed e' voluto: nessun automatismo parte da solo su un
        marketplace vero. Ma accenderle una per una da Impostazioni → Tecnico →
        Azioni pianificate vuol dire entrare nel menu dove stanno anche le
        automazioni di Odoo — fatture, magazzino, posta — e spegnere per errore
        una di quelle e' un guaio che nessuno collega a noi.

        Qui si vedono **solo le nostre**, con l'interruttore e la prossima
        esecuzione. Riservata agli amministratori.

        ⚠️ L'elenco si ricava dai MODULI (`ir.model.data`), non dai modelli:
        due automazioni del pacchetto girano su modelli di Odoo
        (`stock.picking` e `account.move`), e un filtro sul modello le
        perderebbe per strada — proprio le due che nessuno andrebbe a cercare.
        """
        dati = self.env["ir.model.data"].sudo().search([("model", "=", "ir.cron")])
        nostri = dati.filtered(
            lambda d: d.module == "integrations_core"
            or d.module.startswith(("marketplace_", "centrivo_")))
        return {
            "type": "ir.actions.act_window",
            "name": _("Automazioni Centrivo"),
            "res_model": "ir.cron",
            "view_mode": "list,form",
            "views": [(self.env.ref(
                "integrations_core.view_centrivo_cron_list").id, "list"),
                (False, "form")],
            "search_view_id": self.env.ref(
                "integrations_core.view_centrivo_cron_search").id,
            "domain": [("id", "in", nostri.mapped("res_id"))],
            # ⚠️ `active_test: False`, e senza questo la schermata NON SERVE A
            # NIENTE. `ir.cron` ha il campo `active`, e Odoo nasconde da solo
            # i record disattivati in qualunque elenco: le automazioni del
            # pacchetto nascono TUTTE SPENTE, quindi si vedevano solo le poche
            # gia' accese — cioe' l'esatto contrario dello scopo, che e'
            # accendere quelle spente.
            #
            # ⚠️ E il difetto si presentava bene: nessun errore, elenco non
            # vuoto, numeri plausibili. L'ha fermato Angelo aprendo la
            # schermata e contando: «vedo solo 5 azioni».
            "context": {"create": False, "delete": False,
                        "active_test": False},
            "help": _("<p class='o_view_nocontent_smiling_face'>"
                      "Nessuna automazione del pacchetto risulta installata."
                      "</p>"),
        }

    def _picking_carrier_source(self, picking):
        """Vettore del trasferimento come (modello, id, nome per esteso).

        Legge il campo configurato in Configurazione integrazioni, in modo
        DINAMICO: se il campo non esiste (modulo di terzi non installato), non è
        relazionale, o è vuoto sul trasferimento, si ripiega sul corriere
        nativo `carrier_id`. Così le spedizioni nate fuori dal flusso di terzi
        continuano a funzionare.

        Ritorna ("", 0, "") se non c'è alcun vettore.
        """
        self.ensure_one()
        name = self.env["centrivo.integration.config"].get_carrier_source_field_name()
        record = False
        field = picking._fields.get(name)
        if field is not None and field.type == "many2one":
            record = picking[name]
        if not record:
            record = picking.carrier_id
        if not record:
            return "", 0, ""
        return record._name, record.id, record.display_name or ""

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
            # ⚠️ Il nome si legge ORA, mentre la transazione e' sana: vedi
            # `_canale_registra`.
            nome = channel.name
            try:
                # ⚠️ IL SAVEPOINT, e perche' intercettare l'eccezione NON
                # basta: un errore che viene dal DATABASE lascia la
                # transazione ABORTITA, e da li' in poi ogni canale
                # successivo fallisce — mentre il commit finale del giro
                # diventa un ROLLBACK silenzioso che si porta via anche i
                # canali gia' andati bene. E' il rollback A QUESTO savepoint
                # che rimette la transazione in piedi.
                with self.env.cr.savepoint():
                    channel._get_connector().generate_stock_feed()
            except NotImplementedError:
                _logger.info(
                    "Il connettore del canale %s non espone un feed prezzi/giacenze.",
                    nome)
            except Exception as exc:  # noqa: BLE001 - isolamento per canale
                _logger.exception("Generazione feed fallita per il canale %s",
                                  nome)
                self._canale_registra(channel, nome, "export_stock_feed",
                                      str(exc)[:2000])
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
        """Bottone: ACCODA la generazione del feed catalogo in BACKGROUND (TASK_85).

        La generazione del catalogo è pesante (intero catalogo + risoluzione delle
        immagini): se girasse nel worker WEB della richiesta rischierebbe il limite
        di tempo (limit_time_real) e, su cataloghi grandi, la memoria del worker.
        Qui marchiamo il canale come 'in coda' e lasciamo che il cron esecutore
        (ATTIVO) la elabori fuori dal worker web — gemello del job di import
        asincrono. Il file pronto si vede da 'Feed catalogo generato il' e dal Log
        operazioni.
        """
        queued = self.filtered("active")
        if not queued:
            return self._catalog_feed_notification(
                "Nessun canale attivo da generare.", kind="warning")
        queued.write({"catalog_feed_pending": True})
        self._arm_catalog_feed_cron()
        return self._catalog_feed_notification(
            "Generazione del feed catalogo avviata in background. Il file sarà "
            "pronto a breve (vedi 'generato il' e il Log operazioni).")

    def _catalog_feed_notification(self, message, kind="info"):
        """Notifica non bloccante per il bottone (UX dell'accodamento)."""
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Feed catalogo",
                "message": message,
                "type": kind,
                "sticky": False,
            },
        }

    @api.model
    def _arm_catalog_feed_cron(self):
        """Triggera il cron esecutore il prima possibile (dopo il commit)."""
        cron = self.env.ref(
            "integrations_core.cron_run_catalog_feed_jobs",
            raise_if_not_found=False)
        if cron:
            cron._trigger()

    def _run_generate_catalog_feed(self):
        """Genera DAVVERO il feed catalogo per i canali (sola lettura).

        Delega al connettore (generate_catalog_feed) e isola gli errori per canale
        su job.log. Eseguito SEMPRE su un worker cron (mai web): dal cron esecutore
        background o dal cron pianificato.
        """
        for channel in self:
            if not channel.active:
                continue
            nome = channel.name  # letto mentre la transazione e' sana
            try:
                # ⚠️ Il savepoint per canale: vedi il gemello sopra.
                with self.env.cr.savepoint():
                    channel._get_connector().generate_catalog_feed()
            except NotImplementedError:
                _logger.info(
                    "Il connettore del canale %s non espone un feed catalogo.",
                    nome)
            except Exception as exc:  # noqa: BLE001 - isolamento per canale
                _logger.exception("Generazione feed catalogo fallita per il canale %s",
                                  nome)
                self._canale_registra(channel, nome, "export_catalog_feed",
                                      str(exc)[:2000])
        return True

    @api.model
    def cron_run_catalog_feed_jobs(self):
        """Cron ESECUTORE (ATTIVO): genera il feed catalogo dei canali 'in coda'.

        Elabora UN canale 'pending' per tick (poi si ri-arma) così ogni esecuzione
        resta breve e la memoria piatta (la generazione è a blocchi, vedi
        connettore). Pulisce eventuali pending rimasti su canali non più attivi.
        """
        channel = self.search(
            [("catalog_feed_pending", "=", True), ("active", "=", True)],
            order="write_date", limit=1)
        if not channel:
            stale = self.search([("catalog_feed_pending", "=", True)])
            if stale:
                stale.write({"catalog_feed_pending": False})
            return
        channel._run_generate_catalog_feed()
        channel.write({"catalog_feed_pending": False})
        self.env.cr.commit()
        # Ri-arma finché restano canali in coda: generazioni back-to-back.
        if self.search_count(
                [("catalog_feed_pending", "=", True), ("active", "=", True)]):
            self._arm_catalog_feed_cron()

    @api.model
    def cron_generate_catalog_feeds(self):
        """ir.cron pianificato (frequenza bassa, INATTIVO di default): rigenera il
        feed catalogo di tutti i canali attivi.

        Il catalogo cambia meno spesso del feed prezzi/giacenze, quindi è un cron
        separato con intervallo più ampio. Gira già su un worker cron, quindi
        genera DIRETTAMENTE (non riaccoda).
        """
        channels = self.search([("active", "=", True)])
        channels._run_generate_catalog_feed()
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
            nome = channel.name  # letto mentre la transazione e' sana
            try:
                # ⚠️ Il savepoint per canale: vedi `_canale_registra`.
                with self.env.cr.savepoint():
                    channel.action_pull_orders()
            except Exception as exc:  # noqa: BLE001 - isolamento per canale
                _logger.exception("Pull fallito per il canale %s", nome)
                self._canale_registra(channel, nome, "pull_orders",
                                      str(exc)[:2000])
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
            nome = channel.name  # letto mentre la transazione e' sana
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
                # ⚠️ Il codice esterno si legge QUI, mentre la transazione e'
                # certamente sana: dentro il gestore potrebbe essere ABORTITA,
                # e li' anche solo LEGGERE un campo esplode.
                esterno = order_map.external_id
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
                    # ⚠️ IL SAVEPOINT PER ORDINE. Qui l'isolamento promesso e'
                    # doppio — un ordine non blocca gli altri, un canale non
                    # blocca gli altri — e senza savepoint non regge nessuno
                    # dei due quando il guasto viene dal database.
                    with self.env.cr.savepoint():
                        connector.push_shipment(order_map)
                except NotImplementedError:
                    break  # connettore senza push: inutile iterare gli altri ordini
                except Exception as exc:  # noqa: BLE001 - isolamento per ordine
                    _logger.exception(
                        "Push spedizione fallito per l'ordine %s del canale %s",
                        esterno, nome)
                    self._canale_registra(channel, nome, "push_shipment",
                                          str(exc)[:2000],
                                          external_id=esterno)
        return True
