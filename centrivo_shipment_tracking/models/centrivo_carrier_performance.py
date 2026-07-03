# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""centrivo.carrier.performance — performance recente del corriere × zona.

Metriche su FINESTRA MOBILE (7/14 gg) per (corriere × zona × azienda),
ricalcolate da un cron giornaliero. Servono alla dashboard (mini-pagella) e —
in futuro — al modulo creazione etichette / efficienza dinamica per scegliere il
corriere migliore PER QUELLA destinazione.

Definizioni (spedizioni con ship_date nella finestra):
  - on_time_rate = % consegnate in tempo sul totale consegnate;
  - held_rate / exception_rate = % passate da giacenza/eccezione sul campione;
  - avg_transit_hours = media ore presa-in-carico→consegna (solo consegnate);
  - alert_rate = % con alert aperto (approssimazione: stato corrente);
  - score = voto 0-100 con PESI DI DEFAULT (non configurabili in v1).
Il `sample_size` è sempre esposto: con campioni piccoli il voto è solo indicativo.
"""
from datetime import timedelta

from odoo import api, fields, models

# Pesi di default del voto sintetico (v1, non configurabili da UI).
_W_ON_TIME = 0.5
_W_HELD = 0.2
_W_EXCEPTION = 0.2
_W_LATE = 0.1

_WINDOWS = (7, 14)


class CentrivoCarrierPerformance(models.Model):
    _name = "centrivo.carrier.performance"
    _description = "Performance recente corriere × zona"
    _order = "window_days, score desc"
    _rec_name = "tracker_code"

    tracker_code = fields.Char(string="Corriere (tracker)", index=True)
    zone_id = fields.Many2one("centrivo.sla.zone", string="Zona", index=True)
    window_days = fields.Integer(string="Finestra (giorni)", index=True)
    company_id = fields.Many2one("res.company", string="Azienda", index=True)

    sample_size = fields.Integer(string="Spedizioni (campione)")
    on_time_rate = fields.Float(string="% on-time")
    held_rate = fields.Float(string="% giacenza")
    exception_rate = fields.Float(string="% eccezione")
    late_rate = fields.Float(string="% in ritardo")
    avg_transit_hours = fields.Float(string="Ore transito (media)")
    alert_rate = fields.Float(string="% con alert")
    score = fields.Float(string="Voto (0-100)")
    computed_on = fields.Datetime(string="Calcolato il")

    @api.model
    def cron_compute_carrier_performance(self):
        """Ricalcola le metriche mobili per tutte le finestre (cron giornaliero).

        Strategia semplice e consistente: per ogni finestra cancella le righe e
        le ricrea dai bucket osservati (corriere × zona × azienda). Volumi modesti
        → operazione leggera.
        """
        Shipment = self.env["centrivo.shipment"]
        now = fields.Datetime.now()
        for window in _WINDOWS:
            since = now - timedelta(days=window)
            self.search([("window_days", "=", window)]).unlink()
            ships = Shipment.search([("ship_date", ">=", since)])
            buckets = {}
            for ship in ships:
                key = (ship.company_id.id,
                       ship.tracker_code or False,
                       ship.sla_zone_id.id or False)
                buckets.setdefault(key, Shipment.browse())
                buckets[key] |= ship
            rows = []
            for (company_id, tracker, zone_id), recs in buckets.items():
                rows.append(self._build_metrics(
                    company_id, tracker, zone_id, window, recs, now))
            if rows:
                self.create(rows)

    @api.model
    def _build_metrics(self, company_id, tracker, zone_id, window, recs, now):
        """Costruisce i vals di una riga performance da un recordset di spedizioni."""
        sample = len(recs)
        delivered = recs.filtered(lambda s: s.delivered_date)
        on_time = delivered.filtered(lambda s: not s.is_late)
        held = recs.filtered(lambda s: s.ever_held)
        exception = recs.filtered(lambda s: s.ever_exception)
        late = recs.filtered(lambda s: s.is_late)
        with_alert = recs.filtered(lambda s: s.has_open_alert)

        def pct(part, whole):
            return round(100.0 * len(part) / whole, 1) if whole else 0.0

        on_time_rate = pct(on_time, len(delivered))
        held_rate = pct(held, sample)
        exception_rate = pct(exception, sample)
        late_rate = pct(late, sample)
        alert_rate = pct(with_alert, sample)

        transit_vals = [
            (s.delivered_date - s.ship_date).total_seconds() / 3600.0
            for s in delivered if s.ship_date and s.delivered_date]
        avg_transit = round(sum(transit_vals) / len(transit_vals), 1) \
            if transit_vals else 0.0

        score = (
            _W_ON_TIME * on_time_rate
            + _W_HELD * (100.0 - held_rate)
            + _W_EXCEPTION * (100.0 - exception_rate)
            + _W_LATE * (100.0 - late_rate))
        score = round(max(0.0, min(100.0, score)), 1)

        return {
            "tracker_code": tracker or False,
            "zone_id": zone_id or False,
            "window_days": window,
            "company_id": company_id,
            "sample_size": sample,
            "on_time_rate": on_time_rate,
            "held_rate": held_rate,
            "exception_rate": exception_rate,
            "late_rate": late_rate,
            "avg_transit_hours": avg_transit,
            "alert_rate": alert_rate,
            "score": score,
            "computed_on": now,
        }
