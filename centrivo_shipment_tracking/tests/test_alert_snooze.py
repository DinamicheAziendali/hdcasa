# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Test dello snooze degli alert (Risolvi/Ignora) — regressione riapertura.

Bug storico: premendo "Risolvi" l'alert veniva risolto ma, se la condizione SLA era
ANCORA attiva, al polling/valutazione successiva veniva riaperto (l'anti-duplicato di
_open_alert non considerava lo stato 'resolved'). Fix: "Risolvi" e "Ignora" mettono
l'alert in SNOOZE a tempo; entro la finestra la stessa condizione non riapre l'alert,
scaduta la finestra sì. Le auto-risoluzioni (condizione decaduta) non usano snooze.
"""
from datetime import datetime, timedelta

from odoo import fields
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "centrivo_tracking")
class TestAlertSnooze(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Alert = cls.env["centrivo.shipment.alert"]
        cls.Shipment = cls.env["centrivo.shipment"]
        partner = cls.env["res.partner"].create({"name": "Cliente Test Snooze"})
        picking = cls.env["stock.picking"].create({
            "picking_type_id": cls.env.ref("stock.picking_type_out").id,
            "location_id": cls.env.ref("stock.stock_location_stock").id,
            "location_dest_id": cls.env.ref("stock.stock_location_customers").id,
            "partner_id": partner.id,
        })
        cls.shipment = cls.Shipment.create({
            "picking_id": picking.id,
            "company_id": cls.env.company.id,
        })

    def _open(self):
        """Simula il motore SLA: apre (o riusa) l'alert late_delivery."""
        return self.Alert._open_alert(
            self.shipment, alert_type="late_delivery", kind="threshold",
            threshold_type="late_delivery")

    def _count(self):
        return self.Alert.search_count([
            ("shipment_id", "=", self.shipment.id),
            ("alert_type", "=", "late_delivery"),
        ])

    # ------------------------------------------------------------------
    # Il cuore del bug: Risolvi manuale NON deve riaprire durante lo snooze
    # ------------------------------------------------------------------
    def test_manual_resolve_snoozes_reopen(self):
        alert = self._open()
        self.assertEqual(alert.state, "open")
        self.assertEqual(self._count(), 1)

        alert.action_resolve()
        self.assertEqual(alert.state, "resolved")
        self.assertTrue(alert.snooze_until, "Risolvi deve impostare lo snooze")
        self.assertGreater(alert.snooze_until, fields.Datetime.now())

        # Polling/valutazione successiva con condizione ANCORA attiva: NON riapre.
        same = self._open()
        self.assertEqual(same, alert, "Durante lo snooze deve tornare lo stesso alert")
        self.assertEqual(self._count(), 1, "Nessun nuovo alert durante lo snooze")

    def test_ignore_also_snoozes_reopen(self):
        alert = self._open()
        alert.action_ignore()
        self.assertEqual(alert.state, "ignored")
        self.assertTrue(alert.snooze_until, "Ignora deve impostare lo snooze")

        self._open()
        self.assertEqual(self._count(), 1, "Nessun nuovo alert durante lo snooze")

    # ------------------------------------------------------------------
    # Scaduto lo snooze, se la condizione persiste, l'alert torna
    # ------------------------------------------------------------------
    def test_reopens_after_snooze_expiry(self):
        alert = self._open()
        alert.action_resolve()
        # Forza la scadenza dello snooze nel passato.
        alert.snooze_until = fields.Datetime.now() - timedelta(hours=1)

        self._open()
        self.assertEqual(
            self._count(), 2,
            "Scaduto lo snooze e con condizione attiva deve nascere un nuovo alert")
        self.assertEqual(
            self.Alert.search_count([
                ("shipment_id", "=", self.shipment.id),
                ("alert_type", "=", "late_delivery"),
                ("state", "=", "open"),
            ]), 1, "Il nuovo alert è aperto")

    # ------------------------------------------------------------------
    # Auto-risoluzione (condizione decaduta) NON snooza: la recidiva rigenera
    # ------------------------------------------------------------------
    def test_auto_resolve_does_not_snooze(self):
        alert = self._open()
        # Percorso automatico usato dal motore quando la condizione rientra.
        self.Alert._resolve_open(self.shipment, "late_delivery")
        self.assertEqual(alert.state, "resolved")
        self.assertFalse(
            alert.snooze_until, "L'auto-risoluzione non deve impostare snooze")

        # Recidiva successiva: deve rigenerare subito un nuovo alert.
        self._open()
        self.assertEqual(
            self._count(), 2,
            "Senza snooze, una recidiva deve far scattare un nuovo alert")

    # ------------------------------------------------------------------
    # Helper ore lavorative (snooze weekend-safe)
    # ------------------------------------------------------------------
    def test_add_working_hours_skips_weekend(self):
        # Venerdì 2024-01-05 10:00 + 48 ore lavorative → Martedì 2024-01-09 10:00.
        start = datetime(2024, 1, 5, 10, 0, 0)
        end = self.Shipment._add_working_hours(start, 48.0)
        self.assertEqual(end, datetime(2024, 1, 9, 10, 0, 0))

    def test_add_working_hours_noop_on_zero(self):
        start = datetime(2024, 1, 5, 10, 0, 0)
        self.assertEqual(self.Shipment._add_working_hours(start, 0.0), start)
        self.assertEqual(self.Shipment._add_working_hours(False, 48.0), False)
