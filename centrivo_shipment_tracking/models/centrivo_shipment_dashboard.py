# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""centrivo.shipment.dashboard.tile — cruscotto operativo a SCHEDE colorate.

Pattern CROSS-VERSIONE (Community + Enterprise, niente componenti solo-Enterprise):
ogni riquadro "da gestire oggi" è un RECORD (tile) mostrato come scheda kanban
colorata; il kanban dispone le schede a griglia responsive. Cliccando una scheda
si apre la LISTA FILTRATA di `centrivo.shipment` col dominio corrispondente. I
tile vengono RICOSTRUITI a ogni apertura del cruscotto (conteggi sempre freschi).
Nessun JavaScript custom.
"""
from datetime import datetime, timedelta

from odoo import api, fields, models

# Definizione dei tile: chiave → (sequenza, etichetta, icona FA, classe colore BS5).
_TILES = [
    ("late", 10, "In ritardo", "fa-clock-o", "text-bg-danger"),
    ("held", 20, "In giacenza", "fa-archive", "text-bg-warning"),
    ("exception", 30, "In eccezione", "fa-exclamation-triangle", "text-bg-warning"),
    ("returned", 40, "Resi in corso", "fa-reply", "text-bg-secondary"),
    ("alert", 50, "Alert aperti", "fa-bell", "text-bg-primary"),
    ("unassigned", 60, "Corriere da assegnare", "fa-question-circle", "text-bg-secondary"),
    ("stale", 70, "Mute da 3+ gg", "fa-pause-circle", "text-bg-secondary"),
    ("delivered_today", 80, "Consegnate oggi", "fa-check-circle", "text-bg-success"),
]


class CentrivoShipmentDashboardTile(models.Model):
    _name = "centrivo.shipment.dashboard.tile"
    _description = "Riquadro cruscotto spedizioni"
    _order = "sequence, id"

    sequence = fields.Integer(string="Sequenza", default=10)
    key = fields.Char(string="Chiave", required=True, index=True)
    name = fields.Char(string="Etichetta")
    value = fields.Integer(string="Conteggio")
    icon = fields.Char(string="Icona")
    card_class = fields.Char(string="Classe colore")
    company_id = fields.Many2one(
        "res.company", string="Azienda", default=lambda self: self.env.company)

    # ------------------------------------------------------------------
    # Domini (unica fonte: conteggi + azione di apertura)
    # ------------------------------------------------------------------
    @api.model
    def _domain(self, key):
        now = fields.Datetime.now()
        stale_before = now - timedelta(days=3)
        today = fields.Date.context_today(self)
        day_start = datetime.combine(today, datetime.min.time())
        day_end = day_start + timedelta(days=1)
        return {
            "late": [("is_late", "=", True), ("lifecycle", "=", "active")],
            "held": [("status_id.code", "=", "held")],
            "exception": [("status_id.code", "=", "exception")],
            "returned": [("status_id.code", "=", "returned")],
            "alert": [("has_open_alert", "=", True)],
            "unassigned": [("tracker_pending", "=", True),
                           ("lifecycle", "=", "active")],
            "stale": [("lifecycle", "=", "active"),
                      "|", ("last_poll_date", "=", False),
                      ("last_poll_date", "<", stale_before)],
            "delivered_today": [("delivered_date", ">=", day_start),
                                ("delivered_date", "<", day_end)],
        }[key]

    # ------------------------------------------------------------------
    # Ricostruzione tile + apertura cruscotto
    # ------------------------------------------------------------------
    @api.model
    def _rebuild(self):
        """Ricalcola i conteggi e riallinea i record-tile (upsert per chiave)."""
        Shipment = self.env["centrivo.shipment"]
        for key, sequence, label, icon, card_class in _TILES:
            count = Shipment.search_count(self._domain(key))
            tile = self.search([("key", "=", key)], limit=1)
            vals = {
                "sequence": sequence, "name": label, "value": count,
                "icon": icon, "card_class": card_class,
            }
            if tile:
                tile.write(vals)
            else:
                self.create(dict(vals, key=key))

    @api.model
    def action_open_dashboard(self):
        """Voce di menu: ricostruisce i tile e apre il cruscotto a schede."""
        self._rebuild()
        return {
            "type": "ir.actions.act_window",
            "name": "Cruscotto spedizioni",
            "res_model": "centrivo.shipment.dashboard.tile",
            "view_mode": "kanban",
            "target": "current",
        }

    def action_open(self):
        """Click su una scheda: apre la lista filtrata corrispondente."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.name or "Spedizioni",
            "res_model": "centrivo.shipment",
            "view_mode": "list,form",
            "domain": self._domain(self.key),
            "target": "current",
        }

    def action_open_performance(self):
        """Apre la pagella corrieri (finestra 7 giorni)."""
        return {
            "type": "ir.actions.act_window",
            "name": "Pagella corrieri (7 giorni)",
            "res_model": "centrivo.carrier.performance",
            "view_mode": "list,pivot",
            "domain": [("window_days", "=", 7)],
            "target": "current",
        }
