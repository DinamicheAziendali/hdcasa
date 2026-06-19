# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""centrivo.shipment — la spedizione tracciata (fonte di verità del post-spedizione).

Una per picking. È il livello su cui si ragiona/analizza/(in futuro) si aprono
reclami. Lo stato `status_id` è AGGREGATO dagli stati dei colli (parcel_ids). La
spedizione nasce alla VALIDAZIONE del picking (merce affidata) quando esiste un
`carrier_tracking_ref` e un account di tracking mappa il corriere del picking.

Il polling è SOLA LETTURA verso il corriere (RestTransport interno del tracking),
isolato per spedizione, ed è esposto sia come bottone manuale sia come cron
adattivo (cadenza per stato). Lo stato terminale chiude il ciclo (lifecycle=closed)
e la spedizione esce dal set di polling.
"""
import logging

from odoo import api, fields, models

from ..connectors.base import TrackingConnector
from ..connectors.transport import TransportError

_logger = logging.getLogger(__name__)

# Cadenza di polling di sicurezza (ore) se lo stato non ne dichiara una.
FALLBACK_POLLING_HOURS = 12.0

# Codice dello stato AGGREGATO di pre-advice ("In attesa presa in carico"): la
# spedizione è creata ma il corriere non l'ha ancora ritirata. È il segnale di
# pre-advice a livello di spedizione quando gli eventi non portano un codice grezzo
# (es. lo storico del nodo pubblico GLS) e quindi la "prima lettura effettiva" non si
# può dedurre dal singolo evento.
PRE_ADVICE_STATUS_CODE = "pending_pickup"


class CentrivoShipment(models.Model):
    _name = "centrivo.shipment"
    _description = "Spedizione tracciata"
    # mail.thread + mail.activity.mixin: infrastruttura mail completa (TASK_64).
    # mail.activity.mixin abilita le attività native (mail.activity) usate dagli
    # alert SLA via activity_schedule (spec §6.3, TASK_59). mail.thread aggiunge la
    # chatter (messaggi/note/follower) e permette al widget attività di renderizzarsi
    # nel form: senza mail.thread il widget `mail_activity` triggerato dal mixin va in
    # "Missing widget" e rompe il form (TASK_62/63). La chatter è utile per tracciare
    # le comunicazioni con corriere/cliente sui reclami (anticipata qui dalla Fase 4).
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "create_date desc"

    name = fields.Char(
        string="Riferimento", compute="_compute_name", store=True)

    # --- Origine (picking nativo, sola lettura) ---------------------------
    picking_id = fields.Many2one(
        "stock.picking", string="Trasferimento", required=True,
        ondelete="cascade", index=True)
    sale_order_id = fields.Many2one(
        "sale.order", string="Ordine", related="picking_id.sale_id", store=True)
    partner_id = fields.Many2one(
        "res.partner", string="Destinatario", index=True,
        help="Destinatario della spedizione. Di norma è il partner del "
             "trasferimento; per i picking DROPSHIP è il CLIENTE FINALE preso dal "
             "purchase.order collegato (dest_address_id), non il fornitore. "
             "Valorizzato alla creazione (vedi stock.picking."
             "_centrivo_get_shipment_partner). Le dimensioni geografiche per la "
             "zona SLA derivano da questo campo.")
    delivery_carrier_id = fields.Many2one(
        "delivery.carrier", string="Metodo di consegna",
        related="picking_id.carrier_id", store=True)
    carrier_tracking_ref = fields.Char(
        string="Numero tracking", related="picking_id.carrier_tracking_ref",
        store=True,
        help="Numero di spedizione/tracking nativo del picking (sola lettura).")
    tracker_code = fields.Selection(
        selection="_selection_tracker_code", string="Corriere (tracker)",
        index=True,
        help="Connettore di tracking. Risolto alla creazione dal corriere del "
             "picking (campo sorgente configurabile + mappa corrieri); se non "
             "risolvibile resta VUOTO ('corriere da assegnare') e si può scegliere "
             "qui a mano.")
    tracker_pending = fields.Boolean(
        string="Corriere da assegnare", compute="_compute_tracker_pending",
        store=True,
        help="Vero quando il corriere/adattatore non è stato risolto "
             "automaticamente e va assegnato a mano.")

    # --- Stato e ciclo di vita --------------------------------------------
    # tracking=True sullo stato aggregato (TASK_64): ora che il modello eredita
    # mail.thread, ogni cambio dello stato reale finisce nella chatter, dando uno
    # storico leggibile delle transizioni. Limitato a questo solo campo chiave per
    # non generare rumore (gli altri campi sono per lo più related/tecnici).
    status_id = fields.Many2one(
        "centrivo.shipment.status", string="Stato tracking", index=True,
        tracking=True,
        help="Stato AGGREGATO della spedizione, calcolato dagli stati dei colli.")
    last_status_date = fields.Datetime(
        string="Stato aggiornato il", copy=False,
        help="Quando lo stato aggregato è cambiato l'ultima volta.")
    lifecycle = fields.Selection(
        selection=[("active", "Attiva"), ("closed", "Chiusa")],
        string="Ciclo di vita", default="active", required=True, index=True,
        help="Le spedizioni 'attive' vengono interrogate dal polling; le "
             "'chiuse' (consegnate/rese/terminali) escono dal polling.")
    active = fields.Boolean(string="Attivo", default=True)

    # --- Date e SLA --------------------------------------------------------
    ship_date = fields.Datetime(string="Data spedizione", copy=False)
    expected_delivery_date = fields.Date(string="Consegna prevista")
    delivered_date = fields.Datetime(string="Consegnato il", copy=False)
    last_poll_date = fields.Datetime(string="Ultimo aggiornamento tracking", copy=False)
    is_late = fields.Boolean(
        string="In ritardo", compute="_compute_is_late", store=True)
    delay_days = fields.Integer(
        string="Giorni di ritardo", compute="_compute_is_late", store=True)

    # --- Alert SLA (spec §6: dati che alimentano dashboard + filtri) -------
    alert_ids = fields.One2many(
        "centrivo.shipment.alert", "shipment_id", string="Alert")
    has_open_alert = fields.Boolean(
        string="Ha alert aperto", compute="_compute_alert_summary", store=True,
        help="Vero se la spedizione ha almeno un alert aperto (filtro/dashboard).")
    open_alert_count = fields.Integer(
        string="N. alert aperti", compute="_compute_alert_summary", store=True)
    alert_type = fields.Char(
        string="Tipo alert", compute="_compute_alert_summary", store=True,
        help="Chiave del tipo dell'alert aperto rappresentativo (per filtri).")
    is_claim_candidate = fields.Boolean(
        string="Candidata a reclamo", compute="_compute_alert_summary", store=True,
        help="Vero se un alert aperto suggerisce l'apertura di un reclamo "
             "(segnale-ponte verso la Fase 3).")

    # --- Dimensioni analitiche (related dal destinatario) ------------------
    dest_country_id = fields.Many2one(
        "res.country", string="Nazione",
        related="partner_id.country_id", store=True)
    dest_state_id = fields.Many2one(
        "res.country.state", string="Provincia/Stato",
        related="partner_id.state_id", store=True)
    dest_zip = fields.Char(string="CAP", related="partner_id.zip", store=True)

    # --- Colli ed eventi ---------------------------------------------------
    parcel_ids = fields.One2many(
        "centrivo.shipment.parcel", "shipment_id", string="Colli")
    parcel_count = fields.Integer(
        string="N. colli", compute="_compute_counts")
    event_ids = fields.One2many(
        "centrivo.shipment.event", "shipment_id", string="Eventi tracking")
    event_count = fields.Integer(
        string="N. eventi", compute="_compute_counts")

    # Azienda: campo REALE (non related). La fonte di verità è la company del
    # picking d'origine, valorizzata esplicitamente alla create
    # (stock.picking._centrivo_sync_tracking_shipment). Il default su env.company
    # è solo una rete di sicurezza: un related+required+store falliva il check di
    # obbligatorietà durante la create annidata da button_validate (il related si
    # calcola al flush, dopo il controllo required) bloccando la validazione del
    # trasferimento.
    company_id = fields.Many2one(
        "res.company", string="Azienda", required=True, index=True,
        default=lambda self: self.env.company)

    _sql_constraints = [
        ("uniq_picking", "unique(picking_id)",
         "Esiste già una spedizione tracciata per questo trasferimento."),
    ]

    # ==================================================================
    # Compute
    # ==================================================================
    @api.model
    def _selection_tracker_code(self):
        """Opzioni del Selection tracker_code: i connettori di tracking registrati."""
        options = TrackingConnector.get_selection()
        return options or [("none", "Nessun adattatore installato")]

    @api.depends("tracker_code")
    def _compute_tracker_pending(self):
        for ship in self:
            ship.tracker_pending = not ship.tracker_code

    @api.depends("picking_id.name", "carrier_tracking_ref")
    def _compute_name(self):
        for ship in self:
            ref = (ship.carrier_tracking_ref or "").strip()
            base = ship.picking_id.name or "Spedizione"
            ship.name = "%s · %s" % (base, ref) if ref else base

    @api.depends("parcel_ids", "event_ids")
    def _compute_counts(self):
        for ship in self:
            ship.parcel_count = len(ship.parcel_ids)
            ship.event_count = len(ship.event_ids)

    @api.depends("expected_delivery_date", "delivered_date", "status_id")
    def _compute_is_late(self):
        """Ritardo vs consegna prevista.

        Calcolato sul momento della consegna (se avvenuta) o su oggi. Stored:
        viene ricalcolato a ogni polling/scrittura rilevante; non si aggiorna da
        solo col passare del tempo finché non avviene un nuovo evento (accettabile
        in Fase 1, documentato).
        """
        today = fields.Date.context_today(self)
        for ship in self:
            late = False
            days = 0
            if ship.expected_delivery_date:
                if ship.delivered_date:
                    ref_date = fields.Datetime.context_timestamp(
                        ship, ship.delivered_date).date()
                else:
                    ref_date = today
                if ref_date > ship.expected_delivery_date:
                    # Ritardo sia se non ancora consegnata (oggi oltre la previsione)
                    # sia se consegnata in ritardo (data consegna oltre la previsione).
                    late = True
                    days = (ref_date - ship.expected_delivery_date).days
            ship.is_late = late
            ship.delay_days = days

    @api.depends("alert_ids.state", "alert_ids.suggested_claim", "alert_ids.alert_type")
    def _compute_alert_summary(self):
        """Riassunto degli alert APERTI: alimenta filtri in lista e dashboard."""
        for ship in self:
            opens = ship.alert_ids.filtered(lambda a: a.state == "open")
            ship.has_open_alert = bool(opens)
            ship.open_alert_count = len(opens)
            ship.alert_type = opens[:1].alert_type or False
            ship.is_claim_candidate = any(a.suggested_claim for a in opens)

    # ==================================================================
    # Risoluzione account/connettore
    # ==================================================================
    def _resolve_account(self):
        """Account di tracking per (tracker_code, azienda) di questa spedizione."""
        self.ensure_one()
        if not self.tracker_code:
            return self.env["centrivo.tracking.account"].browse()
        return self.env["centrivo.tracking.account"].search([
            ("tracker_code", "=", self.tracker_code),
            ("company_id", "=", self.company_id.id),
            ("active", "=", True),
        ], limit=1)

    # ==================================================================
    # Polling (manuale + cron)
    # ==================================================================
    def action_update_tracking(self):
        """Bottone/azione: interroga ORA il corriere per le spedizioni selezionate.

        Stessa logica del cron, invocabile a mano (form singolo + azione massiva).
        Errori isolati per spedizione: un fallimento non blocca gli altri.
        """
        for ship in self:
            try:
                ship._poll()
            except Exception as exc:  # noqa: BLE001 - isolamento per spedizione
                _logger.exception("Aggiornamento tracking fallito per %s", ship.name)
                ship._log("poll_tracking", "error",
                          "Aggiornamento tracking fallito: %s" % exc)
        return True

    def _poll(self):
        """Interroga il corriere (SOLA LETTURA) e applica il risultato.

        Risolve l'account/connettore, chiama fetch_tracking, accoda i nuovi eventi,
        aggiorna gli stati dei colli e ricalcola lo stato aggregato. Aggiorna
        sempre last_poll_date. Niente eccezioni propagate sui casi previsti.
        """
        self.ensure_one()
        if not self.tracker_code:
            # Ri-tenta la risoluzione automatica: copre il caso in cui il mapping
            # corriere è stato creato DOPO la validazione del picking (la spedizione
            # era nata "da assegnare"). Così "Aggiorna tracking ora" la auto-risolve.
            self._try_resolve_tracker()
        if not self.tracker_code:
            self._log("poll_tracking", "skip",
                      "Corriere da assegnare: nessun mapping risolve il corriere "
                      "del trasferimento. Scegli l'adattatore a mano o configura la "
                      "Mappa corrieri.")
            return False
        account = self._resolve_account()
        if not account:
            self._log("poll_tracking", "skip",
                      "Nessun account di tracking per il connettore '%s'. "
                      "Crea un account corriere (anche solo per endpoint/credenziali) "
                      "per questa azienda." % self.tracker_code)
            return False
        number = (self.carrier_tracking_ref or "").strip()
        if not number:
            self._log("poll_tracking", "skip",
                      "Spedizione senza numero di tracking: niente da interrogare.")
            return False

        try:
            connector = TrackingConnector.for_account(account)
        except NotImplementedError as exc:
            self._log("poll_tracking", "error", str(exc))
            return False

        try:
            result = connector.fetch_tracking(number)
        except TransportError as exc:
            self.last_poll_date = fields.Datetime.now()
            self._log("poll_tracking", "error",
                      "Errore di rete durante il tracking #%s: %s" % (number, exc))
            return False
        except Exception as exc:  # noqa: BLE001 - parsing/altro: non rompere
            self.last_poll_date = fields.Datetime.now()
            self._log("poll_tracking", "error",
                      "Errore tracking #%s: %s" % (number, exc))
            return False

        raw_excerpt = result.get("raw_excerpt") if isinstance(result, dict) else None
        self._apply_tracking_result(result)
        self.last_poll_date = fields.Datetime.now()
        self._log("poll_tracking", "ok",
                  "Tracking #%s aggiornato: stato '%s' (%s eventi)." % (
                      number, self.status_id.name or "—", len(self.event_ids)),
                  raw_excerpt=raw_excerpt)
        return True

    def _try_resolve_tracker(self):
        """Ri-tenta la risoluzione automatica del corriere dal picking (Part C).

        Se la spedizione è "da assegnare" e il picking risolve un adattatore (via
        campo sorgente + Mappa corrieri), lo imposta e lo logga. Usato dal polling e
        dal bottone manuale, così la spedizione si auto-risolve quando il mapping
        viene aggiunto dopo la validazione.
        """
        self.ensure_one()
        if self.tracker_code or not self.picking_id:
            return self.tracker_code
        tracker = self.picking_id._centrivo_resolve_tracker_code()
        if tracker:
            self.tracker_code = tracker
            self._log("resolve_carrier", "ok",
                      "Corriere risolto automaticamente dal mapping: %s." % tracker)
        return self.tracker_code

    def action_resolve_carrier(self):
        """Bottone/azione: ri-tenta la risoluzione automatica del corriere."""
        for ship in self:
            ship._try_resolve_tracker()
        return True

    def _apply_tracking_result(self, result):
        """Scrive eventi/colli/stato dal risultato grezzo del connettore."""
        self.ensure_one()
        StatusMap = self.env["centrivo.shipment.status.map"]
        result = result or {}

        # 1) EVENTI (append-only, deduplicati).
        for ev in result.get("events") or []:
            self._upsert_event(ev)

        # 2) COLLI + stato del collo.
        parcels = result.get("parcels") or []
        if parcels:
            for pdata in parcels:
                self._upsert_parcel(pdata, StatusMap)
        elif result.get("current_raw_code") or result.get("current_raw_code_fallback"):
            # Il corriere non espone i colli singoli (es. nodo pubblico GLS, Poste):
            # si aggiorna l'UNICO collo di default con lo stato sintetico. La normalize
            # a due livelli usa il fallback (Poste: phase) se il primario (status) non
            # è mappato. Se il poll NON porta dati (risposta vuota, es. Poste code 207)
            # NON si entra qui: lo stato corrente resta invariato, niente downgrade a
            # 'sconosciuto' di una spedizione già tracciata.
            current = StatusMap.normalize(
                self.tracker_code, result.get("current_raw_code"), self.company_id,
                raw_code_fallback=result.get("current_raw_code_fallback"),
                raw_description=result.get("current_raw_desc"))
            parcel = self.parcel_ids[:1] or self._ensure_default_parcel()
            parcel._set_status(current)

        # 3) STATO AGGREGATO della spedizione dai colli.
        self._recompute_aggregate_status()

        # 4) VALUTAZIONE SLA al polling (spec §5/§7): trigger su stato immediati E
        # soglie temporali. Valutare anche le soglie qui è importante perché una
        # spedizione che diventa terminale (consegnata) esce dal set del cron
        # (lifecycle=closed): è il polling che, all'atto della consegna, RISOLVE gli
        # eventuali alert da soglia ancora aperti. Il cron resta la rete di sicurezza
        # per le spedizioni attive. Isolato: un errore qui non invalida il polling.
        try:
            self._evaluate_sla()
        except Exception as exc:  # noqa: BLE001
            _logger.exception("Valutazione SLA al polling fallita per %s", self.name)
            self._log("sla_eval", "error",
                      "Valutazione SLA al polling fallita: %s" % exc)

    def _upsert_event(self, ev):
        """Crea un evento se non già presente (dedup su chiave naturale)."""
        Event = self.env["centrivo.shipment.event"]
        StatusMap = self.env["centrivo.shipment.status.map"]
        dt = ev.get("event_datetime")
        raw_code = ev.get("raw_code")
        raw_code_fallback = ev.get("raw_code_fallback")
        raw_desc = ev.get("raw_description")
        location = ev.get("location")
        domain = [
            ("shipment_id", "=", self.id),
            ("event_datetime", "=", dt or False),
            ("raw_description", "=", raw_desc or False),
            ("location", "=", location or False),
        ]
        if Event.search_count(domain):
            return Event.browse()
        # Normalize a due livelli (Poste: status + fallback phase); per gli altri
        # corrieri raw_code_fallback è None e il comportamento è invariato.
        status = StatusMap.normalize(
            self.tracker_code, raw_code, self.company_id,
            raw_code_fallback=raw_code_fallback, raw_description=raw_desc) \
            if (raw_code or raw_code_fallback) \
            else self.env["centrivo.shipment.status"].browse()
        return Event.create({
            "shipment_id": self.id,
            "event_datetime": dt or False,
            "raw_code": raw_code or False,
            "raw_description": raw_desc or False,
            "location": location or False,
            "branch_raw": ev.get("branch_raw") or False,
            "status_id": status.id if status else False,
            "company_id": self.company_id.id,
        })

    def _upsert_parcel(self, pdata, StatusMap):
        """Crea/aggiorna un collo per numero di tracking e ne imposta lo stato."""
        Parcel = self.env["centrivo.shipment.parcel"]
        number = (pdata.get("tracking_number") or "").strip()
        status = StatusMap.normalize(
            self.tracker_code, pdata.get("raw_code"), self.company_id)
        parcel = False
        if number:
            parcel = self.parcel_ids.filtered(
                lambda p: (p.tracking_number or "").strip() == number)[:1]
        if not parcel:
            parcel = self.parcel_ids[:1] if not number else Parcel.browse()
        if not parcel:
            parcel = Parcel.create({
                "shipment_id": self.id,
                "tracking_number": number or self.carrier_tracking_ref,
                "company_id": self.company_id.id,
            })
        parcel._set_status(status)
        return parcel

    def _ensure_default_parcel(self):
        """Garantisce l'esistenza del collo di default (= tracking di spedizione)."""
        self.ensure_one()
        parcel = self.parcel_ids[:1]
        if parcel:
            return parcel
        return self.env["centrivo.shipment.parcel"].create({
            "shipment_id": self.id,
            "tracking_number": self.carrier_tracking_ref,
            "company_id": self.company_id.id,
        })

    def _recompute_aggregate_status(self):
        """Calcola lo stato della spedizione dagli stati dei colli (spec §3.3).

        Regole:
          - tutti i colli consegnati → 'consegnato' (terminale);
          - alcuni consegnati, altri no → 'consegnato parzialmente' (NON terminale);
          - nessuno consegnato → vince lo stato a priorità di aggregazione più alta
            (gli stati problematici hanno priorità maggiore).
        La PRIORITÀ esatta quando coesistono stati diversi è una scelta da
        confermare (vedi report): qui usa il campo aggregation_priority.
        """
        self.ensure_one()
        Status = self.env["centrivo.shipment.status"]
        statuses = self.parcel_ids.mapped("status_id").filtered(lambda s: s)
        if not statuses:
            return
        delivered = Status._get_by_code("delivered")
        delivered_parcels = self.parcel_ids.filtered(
            lambda p: p.status_id and p.status_id.code == "delivered")
        sized = self.parcel_ids.filtered(lambda p: p.status_id)

        if delivered and len(delivered_parcels) == len(sized) and sized:
            new_status = delivered
        elif delivered_parcels:
            new_status = Status._get_by_code("partially_delivered") or delivered
        else:
            new_status = max(statuses, key=lambda s: s.aggregation_priority)

        self._set_aggregate_status(new_status)

    def _set_aggregate_status(self, new_status):
        """Imposta lo stato aggregato e gestisce le conseguenze (terminale/date)."""
        self.ensure_one()
        if not new_status:
            return
        vals = {}
        if self.status_id != new_status:
            vals["status_id"] = new_status.id
            vals["last_status_date"] = fields.Datetime.now()
        if new_status.code == "delivered" and not self.delivered_date:
            vals["delivered_date"] = fields.Datetime.now()
        if new_status.is_terminal:
            vals["lifecycle"] = "closed"
        if vals:
            self.write(vals)

    # ==================================================================
    # Cron — polling adattivo per stato
    # ==================================================================
    @api.model
    def cron_poll_active_shipments(self):
        """ir.cron: interroga le spedizioni ATTIVE la cui cadenza è scaduta.

        Cadenza per stato (centrivo.shipment.status.polling_hours): in consegna 1h,
        in transito 4h, giacenza/attesa 12h, terminali 0 (mai). Errori isolati per
        spedizione. Nasce DISATTIVO (data/ir_cron.xml): si accende da UI a regime.
        """
        now = fields.Datetime.now()
        shipments = self.search([
            ("lifecycle", "=", "active"),
            ("active", "=", True),
        ])
        polled = 0
        for ship in shipments:
            if not ship._is_due(now):
                continue
            try:
                ship._poll()
                polled += 1
            except Exception as exc:  # noqa: BLE001 - isolamento per spedizione
                _logger.exception("Cron tracking: errore su %s", ship.name)
                ship._log("poll_tracking", "error",
                          "Cron tracking fallito: %s" % exc)
        if shipments:
            _logger.info("Cron tracking: interrogate %s/%s spedizioni attive.",
                         polled, len(shipments))
        return True

    def _is_due(self, now):
        """True se la spedizione va reinterrogata ora (cadenza dello stato)."""
        self.ensure_one()
        status = self.status_id
        hours = status.polling_hours if status else FALLBACK_POLLING_HOURS
        # Stato terminale o cadenza nulla → non si interroga.
        if status and status.is_terminal:
            return False
        if hours <= 0:
            return False
        if not self.last_poll_date:
            return True
        delta_hours = (now - self.last_poll_date).total_seconds() / 3600.0
        return delta_hours >= hours

    # ==================================================================
    # Motore SLA — "prima lettura effettiva", soglie, trigger stato (spec §4–§7)
    # ==================================================================
    def _first_effective_event(self):
        """Primo evento (cronologico) che NON è un pre-advice (spec §4).

        La "prima lettura effettiva" è il primo evento la cui riga di mappatura
        (tracker_code, raw_code) ha is_pre_advice=False. Un evento il cui codice è
        marcato pre-advice (es. BRT 700, GLS PREADVICE) NON conta come inizio.

        Eventi SENZA codice grezzo: alcuni corrieri (es. lo storico del nodo pubblico
        GLS) non portano un codice sull'evento, e il pre-advice è leggibile solo dallo
        stato AGGREGATO della spedizione (`status_id`, code 'pending_pickup'). In quel
        caso un evento codeless NON è una lettura effettiva finché lo stato aggregato è
        pre-advice (altrimenti il pre-advice GLS verrebbe scambiato per presa in carico
        e missed_pickup non scatterebbe mai — TASK_67). Quando la spedizione supera il
        pre-advice (stato non più 'pending_pickup'), gli stessi eventi codeless tornano
        a contare come lettura reale.
        Ritorna il recordset evento (vuoto se non c'è ancora alcuna lettura reale).
        """
        self.ensure_one()
        StatusMap = self.env["centrivo.shipment.status.map"]
        status_is_pre_advice = bool(
            self.status_id and self.status_id.code == PRE_ADVICE_STATUS_CODE)
        events = self.event_ids.filtered(lambda e: e.event_datetime).sorted(
            key=lambda e: e.event_datetime)
        for ev in events:
            if ev.raw_code:
                row = StatusMap._get_row(self.tracker_code, ev.raw_code)
                if row and row.is_pre_advice:
                    continue
            elif status_is_pre_advice:
                # Evento senza codice su spedizione il cui stato aggregato è ancora
                # pre-advice: non è una presa in carico effettiva.
                continue
            return ev
        return self.env["centrivo.shipment.event"]

    def _last_event_datetime(self):
        """Data/ora dell'ultimo evento ricevuto (per la staleness). False se nessuno."""
        self.ensure_one()
        dates = self.event_ids.filtered("event_datetime").mapped("event_datetime")
        return max(dates) if dates else False

    def cron_evaluate_sla(self):
        """ir.cron: valuta gli SLA delle spedizioni ATTIVE (spec §7).

        Per ogni spedizione attiva: risolve la zona, seleziona la regola per
        specificità, verifica le tre soglie e apre/risolve gli alert; ri-valuta
        anche i trigger di stato (rete di sicurezza). Errori isolati per spedizione.
        Nasce DISATTIVO (data/ir_cron.xml): si accende dalla UI a regime.
        """
        shipments = self.search([
            ("lifecycle", "=", "active"),
            ("active", "=", True),
        ])
        for ship in shipments:
            try:
                ship._evaluate_sla()
            except Exception as exc:  # noqa: BLE001 - isolamento per spedizione
                _logger.exception("Cron SLA: errore su %s", ship.name)
                ship._log("sla_eval", "error", "Valutazione SLA fallita: %s" % exc)
        if shipments:
            _logger.info("Cron SLA: valutate %s spedizioni attive.", len(shipments))
        return True

    def _evaluate_sla(self):
        """Valuta soglie temporali + trigger di stato per questa spedizione."""
        self.ensure_one()
        self._evaluate_thresholds()
        self._evaluate_status_alert()

    def _evaluate_thresholds(self):
        """Apre/risolve gli alert da soglia temporale per i 3 tipi (spec §4)."""
        self.ensure_one()
        Rule = self.env["centrivo.sla.rule"]
        Alert = self.env["centrivo.shipment.alert"]
        now = fields.Datetime.now()
        terminal = bool(self.status_id and self.status_id.is_terminal)
        for ttype in ("missed_pickup", "late_delivery", "staleness"):
            rule = Rule._select_rule(self, ttype)
            breached = False
            if rule and not terminal:
                breached = self._threshold_breached(ttype, rule, now)
            if breached:
                Alert._open_alert(self, alert_type=ttype, kind="threshold",
                                  threshold_type=ttype, rule=rule)
            else:
                # Nessuna regola, terminale, o condizione decaduta → risolvi.
                Alert._resolve_open(self, alert_type=ttype)

    def _threshold_breached(self, ttype, rule, now):
        """True se la soglia `ttype` è superata per questa spedizione (spec §4)."""
        self.ensure_one()
        duration = rule.duration_hours or 0.0
        if duration <= 0:
            return False

        def _elapsed_hours(start):
            return (now - start).total_seconds() / 3600.0

        if ttype == "missed_pickup":
            # Da validazione/affido del picking (ship_date); scatta se NON c'è
            # ancora una prima lettura effettiva entro la durata.
            start = self.ship_date
            if not start:
                return False
            if self._first_effective_event():
                return False
            return _elapsed_hours(start) >= duration

        if ttype == "late_delivery":
            # Dalla prima lettura effettiva; scatta se non consegnato entro.
            ev = self._first_effective_event()
            if not ev or not ev.event_datetime:
                return False
            if self.status_id and self.status_id.code == "delivered":
                return False
            return _elapsed_hours(ev.event_datetime) >= duration

        if ttype == "staleness":
            # Dall'ultimo evento ricevuto; scatta se il corriere è muto oltre.
            last = self._last_event_datetime() or self.ship_date
            if not last:
                return False
            return _elapsed_hours(last) >= duration

        return False

    def _evaluate_status_alert(self):
        """Apre/risolve l'alert da trigger di stato (spec §5).

        Risolve gli alert di stato aperti che non corrispondono più allo stato
        corrente (es. uscita da giacenza, consegna) e apre un alert immediato se lo
        stato corrente è configurato con 'genera alert all'ingresso'.
        """
        self.ensure_one()
        Alert = self.env["centrivo.shipment.alert"]
        status = self.status_id
        open_status_alerts = self.alert_ids.filtered(
            lambda a: a.state == "open" and a.alert_kind == "status")
        for alert in open_status_alerts:
            if (not status or not status.alert_on_entry
                    or alert.trigger_status_id != status):
                alert._do_resolve()
        if status and status.alert_on_entry:
            Alert._open_alert(
                self, alert_type="status_%s" % status.code, kind="status",
                trigger_status=status, suggested_claim=status.alert_suggests_claim)

    def action_evaluate_sla(self):
        """Bottone/azione: valuta ORA gli SLA delle spedizioni selezionate."""
        for ship in self:
            try:
                ship._evaluate_sla()
            except Exception as exc:  # noqa: BLE001 - isolamento per spedizione
                _logger.exception("Valutazione SLA fallita per %s", ship.name)
                ship._log("sla_eval", "error", "Valutazione SLA fallita: %s" % exc)
        return True

    # ==================================================================
    # Navigazione
    # ==================================================================
    def action_view_picking(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "stock.picking",
            "res_id": self.picking_id.id,
            "view_mode": "form",
        }

    # ==================================================================
    # Log PROPRIO del modulo tracking (centrivo.shipment.log, NON job.log)
    # ==================================================================
    def _log(self, operation, result, message, raw_excerpt=None):
        """Scrive un record su centrivo.shipment.log (log interno del tracking).

        raw_excerpt: estratto TRONCATO della risposta grezza (mai segreti). Per il
        nodo pubblico GLS non ci sono credenziali; per BRT/Poste (futuri) il
        chiamante deve passare solo dati non sensibili.
        """
        self.ensure_one()
        self.env["centrivo.shipment.log"].create({
            "shipment_id": self.id,
            "tracker_code": self.tracker_code or False,
            "operation": operation,
            "result": result,
            "message": (message or "")[:4000],
            "raw_excerpt": (raw_excerpt or "")[:4000] or False,
            "company_id": self.company_id.id,
        })
