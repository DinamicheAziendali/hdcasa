# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""stock.picking (_inherit) — stato reale di tracking come SPECCHIO sul picking.

Il record centrivo.shipment è la fonte di verità; il picking ne è uno specchio in
sola lettura, così lo stato fisico è visibile/filtrabile dove l'operatore lavora.
Il nativo `state` NON si tocca: stato logistico (draft/ready/done) e stato fisico
(in transito/consegnato/giacenza) sono assi diversi.

Regola d'ingresso (spec §4): la spedizione tracciata nasce alla VALIDAZIONE del
picking, quando esiste un carrier_tracking_ref. Il corriere/adattatore si risolve
in due livelli (TASK_40): (1) si legge il corriere dal CAMPO SORGENTE configurabile
(centrivo.tracking.config — di default carrier_id nativo, ma può essere
transport_carrier_id di terzi, letto in modo DINAMICO/OPZIONALE); (2) si traduce il
valore in un adattatore via la mappa centrivo.tracking.carrier.map. Se non si
risolve, la spedizione nasce comunque in stato "corriere da assegnare"
(tracker_code vuoto, editabile a mano): nessun blocco, nessuna eccezione. Se manca
il tracking, non si crea nulla (non-regressione).
"""
import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class StockPicking(models.Model):
    _inherit = "stock.picking"

    centrivo_shipment_ids = fields.One2many(
        "centrivo.shipment", "picking_id", string="Spedizioni tracciate")
    centrivo_shipment_id = fields.Many2one(
        "centrivo.shipment", string="Spedizione tracciata",
        compute="_compute_centrivo_shipment_id", store=True, compute_sudo=True,
        help="Spedizione tracciata principale del trasferimento (la prima).")
    centrivo_shipment_count = fields.Integer(
        string="N. spedizioni tracciate",
        compute="_compute_centrivo_shipment_count", compute_sudo=True)

    # Specchio dello stato reale (related/stored): visibile e filtrabile sul picking.
    centrivo_tracking_status_id = fields.Many2one(
        "centrivo.shipment.status", string="Stato spedizione (reale)",
        related="centrivo_shipment_id.status_id", store=True,
        help="Stato fisico reale della spedizione dal corriere. Diverso dal "
             "campo 'Stato' nativo (logistico).")
    centrivo_tracking_status_date = fields.Datetime(
        string="Stato spedizione aggiornato il",
        related="centrivo_shipment_id.last_status_date", store=True)
    centrivo_is_late = fields.Boolean(
        string="Spedizione in ritardo",
        related="centrivo_shipment_id.is_late", store=True)

    @api.depends("centrivo_shipment_ids")
    def _compute_centrivo_shipment_id(self):
        for picking in self:
            picking.centrivo_shipment_id = picking.centrivo_shipment_ids[:1].id

    @api.depends("centrivo_shipment_ids")
    def _compute_centrivo_shipment_count(self):
        for picking in self:
            picking.centrivo_shipment_count = len(picking.centrivo_shipment_ids)

    # ------------------------------------------------------------------
    # Creazione/attivazione della spedizione tracciata
    # ------------------------------------------------------------------
    def button_validate(self):
        """Alla validazione, crea la spedizione tracciata se applicabile."""
        res = super().button_validate()
        # super può ritornare un'azione (wizard backorder, ecc.): in tal caso il
        # picking non è ancora done, l'aggancio avverrà al write di state=done.
        if res is True:
            self._centrivo_sync_tracking_shipment()
        return res

    def write(self, vals):
        """Crea la spedizione tracciata quando il picking è done e ha il tracking.

        Copre il caso in cui il carrier_tracking_ref viene scritto DOPO la
        validazione (es. tracking inserito a mano su un picking già done): lo
        scenario di test del modulo.
        """
        res = super().write(vals)
        if {"state", "carrier_tracking_ref", "carrier_id"} & set(vals):
            self.filtered(lambda p: p.state == "done")._centrivo_sync_tracking_shipment()
        return res

    def _centrivo_sync_tracking_shipment(self):
        """Crea la spedizione tracciata per i picking idonei (idempotente).

        Crea la spedizione ogni volta che il picking è done e ha un
        carrier_tracking_ref. Il corriere/adattatore si risolve best-effort; se non
        risolvibile, la spedizione nasce in "corriere da assegnare" (tracker vuoto).
        """
        Shipment = self.env["centrivo.shipment"]
        Parcel = self.env["centrivo.shipment.parcel"]
        for picking in self:
            ref = (picking.carrier_tracking_ref or "").strip()
            if not ref or picking.state != "done":
                continue
            if picking.centrivo_shipment_ids:
                continue  # idempotenza
            tracker = picking._centrivo_resolve_tracker_code()
            # company_id EREDITATO dal picking d'origine (fonte di verità),
            # con fallback all'azienda corrente se il picking non l'avesse.
            company_id = picking.company_id.id or self.env.company.id
            shipment = Shipment.create({
                "picking_id": picking.id,
                "tracker_code": tracker or False,
                "ship_date": picking.date_done or fields.Datetime.now(),
                "lifecycle": "active",
                "company_id": company_id,
            })
            # Collo di default = tracking di spedizione (i colli singoli, se esposti
            # dal corriere, vengono popolati al primo polling).
            Parcel.create({
                "shipment_id": shipment.id,
                "tracking_number": ref,
                "company_id": shipment.company_id.id or company_id,
            })
            _logger.info("Spedizione tracciata creata per %s (tracker %s).",
                         picking.name, tracker or "da assegnare")
            # Diagnostica risoluzione corriere sul log del tracking.
            if tracker:
                shipment._log("resolve_carrier", "ok",
                              "Corriere risolto dal mapping: %s." % tracker)
            else:
                shipment._log("resolve_carrier", "skip",
                              picking._centrivo_resolution_diagnostic())

    def _centrivo_resolution_diagnostic(self):
        """Messaggio diagnostico sul perché il corriere non si è risolto (per il log)."""
        self.ensure_one()
        field_name = self.env["centrivo.tracking.config"].get_source_field_name()
        src = self._centrivo_source_carrier(field_name)
        if src:
            looked = "%s,%s ('%s')" % (src._name, src.id, src.display_name)
        else:
            looked = "campo '%s' assente/vuoto/non relazionale sul trasferimento" % field_name
        return ("Corriere NON risolto (da assegnare). Campo sorgente '%s' → %s; "
                "nessuna riga della Mappa corrieri corrisponde. Verifica il mapping "
                "o assegna il corriere a mano." % (field_name, looked))

    def _centrivo_resolve_tracker_code(self):
        """Risolve l'adattatore di tracking dal corriere del picking. '' se non risolto.

        Livello 1: legge il corriere dal CAMPO SORGENTE configurabile (dinamico).
        Livello 2: traduce il valore (modello, id) via centrivo.tracking.carrier.map.
        Compatibilità: se il campo sorgente è il delivery.carrier nativo e la mappa
        non risolve, si ripiega sull'account che dichiara quel corriere
        (centrivo.tracking.account.delivery_carrier_ids, comportamento TASK_39).
        """
        self.ensure_one()
        Config = self.env["centrivo.tracking.config"]
        CarrierMap = self.env["centrivo.tracking.carrier.map"]
        field_name = Config.get_source_field_name()
        carrier_rec = self._centrivo_source_carrier(field_name)
        if carrier_rec:
            tracker = CarrierMap.resolve_tracker(
                carrier_rec._name, carrier_rec.id, self.company_id)
            if tracker:
                return tracker
        # Compatibilità con il meccanismo TASK_39 (delivery.carrier nativo).
        if self.carrier_id:
            account = self.env["centrivo.tracking.account"].search([
                ("company_id", "=", self.company_id.id),
                ("active", "=", True),
                ("delivery_carrier_ids", "in", self.carrier_id.id),
            ], limit=1)
            if account:
                return account.tracker_code
        return ""

    def _centrivo_source_carrier(self, field_name):
        """Legge dinamicamente il record corriere dal campo sorgente configurato.

        Ritorna il recordset Many2one (singolo) o False. Robusto: se il campo non
        esiste sul picking (es. transport_carrier_id dopo la disinstallazione di
        DA), o non è un Many2one, o è vuoto → False (si va all'assegnazione manuale).
        Nessuna dipendenza hard: il campo è letto solo se presente.
        """
        self.ensure_one()
        if not field_name or field_name not in self._fields:
            return False
        field = self._fields[field_name]
        if field.type != "many2one":
            # Campi non relazionali (Char/Selection) non mappabili per (modello,id).
            return False
        value = self[field_name]
        return value[:1] if value else False

    def action_centrivo_create_tracking(self):
        """Bottone: crea/attiva la spedizione tracciata per i picking done idonei.

        Comodità per attivare il tracking su un picking già validato a cui si è
        aggiunto il tracking dopo. Idempotente.
        """
        self._centrivo_sync_tracking_shipment()
        return True

    def action_view_centrivo_shipment(self):
        """Smart button 'Tracking': apre la/e spedizione/i tracciata/e del picking."""
        self.ensure_one()
        shipments = self.centrivo_shipment_ids
        action = {
            "type": "ir.actions.act_window",
            "name": _("Tracking spedizione"),
            "res_model": "centrivo.shipment",
        }
        if len(shipments) == 1:
            action.update({"view_mode": "form", "res_id": shipments.id})
        else:
            action.update({
                "view_mode": "list,form",
                "domain": [("picking_id", "=", self.id)],
            })
        return action
