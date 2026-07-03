# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""centrivo.shipment.report — analisi spedizioni (report SQL read-only).

Modello `_auto = False` (pattern `sale.report`): una SELECT read-only su
`centrivo_shipment` che alimenta viste **pivot + grafico** native (uguali su
Community ed Enterprise). Una riga per spedizione, con dimensioni (corriere,
zona, nazione, provincia, canale vendita, periodo) e misure (conteggi 0/1 +
tempo di transito + giorni di ritardo).

Le percentuali (% on-time, % giacenza…) si leggono come rapporto di conteggi nel
pivot; il valore "ufficiale" calcolato per la scelta corriere vive in
`centrivo.carrier.performance` (medie su finestra mobile), per non incappare in
medie-di-medie fuorvianti.
"""
from odoo import fields, models, tools


class CentrivoShipmentReport(models.Model):
    _name = "centrivo.shipment.report"
    _description = "Analisi spedizioni"
    _auto = False
    _rec_name = "shipment_id"
    _order = "ship_date desc"

    shipment_id = fields.Many2one(
        "centrivo.shipment", string="Spedizione", readonly=True)
    tracker_code = fields.Char(string="Corriere (tracker)", readonly=True)
    delivery_carrier_id = fields.Many2one(
        "delivery.carrier", string="Vettore", readonly=True)
    sla_zone_id = fields.Many2one(
        "centrivo.sla.zone", string="Zona SLA", readonly=True)
    dest_country_id = fields.Many2one(
        "res.country", string="Nazione", readonly=True)
    dest_state_id = fields.Many2one(
        "res.country.state", string="Provincia/Stato", readonly=True)
    status_id = fields.Many2one(
        "centrivo.shipment.status", string="Stato", readonly=True)
    team_id = fields.Many2one(
        "crm.team", string="Canale di vendita", readonly=True)
    company_id = fields.Many2one(
        "res.company", string="Azienda", readonly=True)

    ship_date = fields.Datetime(string="Data spedizione", readonly=True)
    delivered_date = fields.Datetime(string="Consegnato il", readonly=True)

    transit_hours = fields.Float(
        string="Ore di transito", readonly=True,
        aggregator="avg",
        help="Ore tra presa in carico e consegna (vuoto se non consegnata).")
    delay_days = fields.Integer(
        string="Giorni di ritardo", readonly=True, group_operator="avg")

    nbr = fields.Integer(string="N. spedizioni", readonly=True)
    delivered_count = fields.Integer(string="Consegnate", readonly=True)
    on_time_count = fields.Integer(string="On-time", readonly=True)
    late_count = fields.Integer(string="In ritardo", readonly=True)
    held_count = fields.Integer(string="Con giacenza", readonly=True)
    exception_count = fields.Integer(string="Con eccezione", readonly=True)
    returned_count = fields.Integer(string="Resi", readonly=True)
    alert_count = fields.Integer(string="Con alert", readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW %s AS (
                SELECT
                    s.id AS id,
                    s.id AS shipment_id,
                    s.tracker_code AS tracker_code,
                    s.delivery_carrier_id AS delivery_carrier_id,
                    s.sla_zone_id AS sla_zone_id,
                    s.dest_country_id AS dest_country_id,
                    s.dest_state_id AS dest_state_id,
                    s.status_id AS status_id,
                    so.team_id AS team_id,
                    s.company_id AS company_id,
                    s.ship_date AS ship_date,
                    s.delivered_date AS delivered_date,
                    s.delay_days AS delay_days,
                    CASE
                        WHEN s.delivered_date IS NOT NULL AND s.ship_date IS NOT NULL
                        THEN EXTRACT(EPOCH FROM (s.delivered_date - s.ship_date)) / 3600.0
                    END AS transit_hours,
                    1 AS nbr,
                    CASE WHEN s.delivered_date IS NOT NULL THEN 1 ELSE 0 END AS delivered_count,
                    CASE WHEN s.delivered_date IS NOT NULL
                              AND COALESCE(s.is_late, FALSE) = FALSE
                         THEN 1 ELSE 0 END AS on_time_count,
                    CASE WHEN COALESCE(s.is_late, FALSE) THEN 1 ELSE 0 END AS late_count,
                    CASE WHEN COALESCE(s.ever_held, FALSE) THEN 1 ELSE 0 END AS held_count,
                    CASE WHEN COALESCE(s.ever_exception, FALSE) THEN 1 ELSE 0 END AS exception_count,
                    CASE WHEN st.code = 'returned' THEN 1 ELSE 0 END AS returned_count,
                    CASE WHEN COALESCE(s.has_open_alert, FALSE) THEN 1 ELSE 0 END AS alert_count
                FROM centrivo_shipment s
                LEFT JOIN sale_order so ON so.id = s.sale_order_id
                LEFT JOIN centrivo_shipment_status st ON st.id = s.status_id
                WHERE s.active = TRUE
            )
        """ % (self._table,))
