# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""centrivo.sla.rule — regola SLA configurabile per corriere × zona (spec §3.2, §4).

Ogni regola combina corriere (vuoto = tutti), zona (vuoto = tutte), tipo di soglia
e durata IN ORE oltre cui scatta l'alert. La risoluzione è per SPECIFICITÀ (come i
listini): per una spedizione data si sceglie la regola applicabile più specifica
nell'ordine (corriere+zona) > (corriere) > (zona) > (globale).

Tre tipi di soglia (spec §4), con punto di partenza PER-TIPO:
  - missed_pickup  : dalla validazione/affido del picking (ship_date); scatta se
                     scaduta la durata NON c'è ancora una "prima lettura effettiva"
                     (primo evento con riga di mappatura is_pre_advice=False);
  - late_delivery  : dalla prima lettura effettiva; scatta se non consegnato entro;
  - staleness      : dall'ultimo evento ricevuto; scatta se il corriere è muto oltre.

Modello transazionale di configurazione con company_id + record rule.
"""
from odoo import api, fields, models

from ..connectors.base import TrackingConnector


class CentrivoSlaRule(models.Model):
    _name = "centrivo.sla.rule"
    _description = "Regola SLA"
    _order = "sequence, id"

    name = fields.Char(string="Nome regola", required=True, translate=True)
    sequence = fields.Integer(
        string="Sequenza", default=10,
        help="A parità di specificità, vince la regola con sequenza più bassa.")
    active = fields.Boolean(string="Attivo", default=True)

    tracker_code = fields.Selection(
        selection="_selection_tracker_code", string="Corriere (tracker)",
        help="Connettore di tracking a cui si applica la regola. Vuoto = TUTTI i "
             "corrieri (regola globale per corriere).")
    zone_id = fields.Many2one(
        "centrivo.sla.zone", string="Zona", ondelete="cascade",
        help="Zona di consegna a cui si applica. Vuoto = TUTTE le zone.")
    threshold_type = fields.Selection(
        selection=[
            ("missed_pickup", "Mancata presa in carico effettiva"),
            ("late_delivery", "Consegna oltre tempo atteso"),
            ("staleness", "Mancato aggiornamento (staleness)"),
        ],
        string="Tipo di soglia", required=True, default="late_delivery",
        help="Vedi spec §4. Il punto di partenza del conteggio dipende dal tipo.")
    duration_hours = fields.Float(
        string="Durata (ore)", required=True, default=24.0,
        help="Ore oltre cui scatta l'alert (sempre in ore).")

    user_ids = fields.Many2many(
        "res.users", "centrivo_sla_rule_user_rel", "rule_id", "user_id",
        string="Destinatari notifica",
        help="Utenti a cui assegnare l'attività quando l'alert scatta.")
    suggest_claim = fields.Boolean(
        string="Suggerisce reclamo",
        help="Se attivo, l'alert generato marca la spedizione come candidata a "
             "reclamo (ponte verso la Fase 3).")

    company_id = fields.Many2one(
        "res.company", string="Azienda", required=True, index=True,
        default=lambda self: self.env.company)

    @api.model
    def _selection_tracker_code(self):
        """Opzioni del Selection tracker_code: i connettori di tracking registrati."""
        options = TrackingConnector.get_selection()
        return options or [("none", "Nessun adattatore installato")]

    # ==================================================================
    # Selezione per specificità (spec §3.2)
    # ==================================================================
    @api.model
    def _select_rule(self, shipment, threshold_type):
        """Regola applicabile più specifica per (spedizione, tipo soglia).

        Ordinamento di specificità DOCUMENTATO (punteggio):
          corriere+zona (3) > solo corriere (2) > solo zona (1) > globale (0).
        A parità di punteggio vince la sequenza più bassa (regole lette ordinate
        per sequence, id). Nessuna regola applicabile → recordset vuoto.
        """
        zone = self.env["centrivo.sla.zone"]._resolve_zone(shipment)
        rules = self.search([
            ("active", "=", True),
            ("threshold_type", "=", threshold_type),
            "|", ("company_id", "=", False),
            ("company_id", "=", shipment.company_id.id),
        ])
        best = self.browse()
        best_score = -1
        for rule in rules:
            if rule.tracker_code and rule.tracker_code != shipment.tracker_code:
                continue
            if rule.zone_id and rule.zone_id != zone:
                continue
            score = (2 if rule.tracker_code else 0) + (1 if rule.zone_id else 0)
            if score > best_score:
                best = rule
                best_score = score
        return best
