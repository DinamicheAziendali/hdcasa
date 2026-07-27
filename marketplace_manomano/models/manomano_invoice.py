# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Estensione di account.move: invio della fattura a ManoMano.

La contabilità viene prima del marketplace: un errore verso ManoMano non deve
MAI impedire la conferma di una fattura in Odoo.
"""
import logging

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    _inherit = "account.move"

    manomano_document_id = fields.Char(
        string="Documento ManoMano", readonly=True, copy=False,
        help="Identificativo restituito da ManoMano al caricamento della "
             "fattura. Se valorizzato, la fattura è già stata inviata.")
    manomano_order_map_id = fields.Many2one(
        "centrivo.order.map", string="Ordine ManoMano",
        compute="_compute_manomano_order_map",
        help="Ordine marketplace da cui discende questa fattura.")
    manomano_invoice_pending = fields.Boolean(
        string="In coda per ManoMano", readonly=True, copy=False,
        help="La fattura è in attesa di essere inviata a ManoMano: il "
             "caricamento avviene fuori dalla conferma contabile, tramite "
             "il cron dedicato, per non tenere bloccata la fattura durante "
             "la generazione del PDF e la chiamata di rete.")

    @api.depends("invoice_line_ids.sale_line_ids.order_id")
    def _compute_manomano_order_map(self):
        """Risale dalla fattura all'ordine marketplace, se ne ha uno.

        Non memorizzato: si ricalcola quando serve ed è vuoto sulle fatture che
        non vengono da un marketplace (la stragrande maggioranza).
        """
        OrderMap = self.env["centrivo.order.map"]
        for move in self:
            orders = move.invoice_line_ids.sale_line_ids.order_id
            order_map = OrderMap.browse()
            if orders:
                order_map = OrderMap.search([
                    ("sale_order_id", "in", orders.ids),
                    ("channel_id.connector_code", "=", "manomano"),
                ], limit=1)
            move.manomano_order_map_id = order_map

    def action_manomano_send_invoice(self):
        """Bottone: invia subito il PDF della fattura a ManoMano.

        Resta sincrono: è l'utente a premerlo, sa di dover aspettare, ed è il
        modo con cui si valida l'invio la prima volta.
        """
        self.ensure_one()
        if not self.manomano_order_map_id:
            raise UserError(
                "Questa fattura non è collegata a un ordine ManoMano.")
        if self.state != "posted":
            raise UserError(
                "Conferma prima la fattura: si invia solo una fattura confermata.")
        channel = self.manomano_order_map_id.channel_id
        try:
            inviata = channel._get_connector().push_invoice(self)
        except Exception as exc:  # noqa: BLE001 - notifica curata, non un traceback
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": "Fattura ManoMano",
                    "message": "Invio non riuscito: %s" % str(exc)[:200],
                    "type": "danger",
                    "sticky": False,
                },
            }
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Fattura ManoMano",
                "message": ("Fattura inviata a ManoMano."
                            if inviata else
                            "Invio non riuscito: leggi il Log operazioni."),
                "type": "success" if inviata else "danger",
                "sticky": False,
            },
        }

    def action_post(self):
        """Conferma la fattura e, se serve, la mette in coda per ManoMano.

        Non invia nulla: si limita a marcare `manomano_invoice_pending`. Il
        caricamento (PDF + chiamata HTTP) avviene fuori dalla transazione
        contabile, dal cron `cron_manomano_push_invoices`. Farlo qui, dentro
        action_post, terrebbe l'utente bloccato per il rendering del PDF e il
        giro di rete con i lock di numerazione del sezionale aperti; inoltre
        `manomano_document_id` è una scrittura transazionale mentre il
        caricamento su ManoMano non lo è, quindi un rollback dopo un invio
        riuscito produrrebbe un documento duplicato al ritentativo.

        Tutto ciò che tocca dati del marketplace sta dentro il try: un
        errore qui (anche nella risoluzione dell'ordine o nella lettura
        dell'interruttore) non deve mai propagarsi dopo il super() e annullare
        la conferma della fattura.
        """
        result = super().action_post()
        for move in self:
            try:
                if move.move_type != "out_invoice" or move.manomano_document_id:
                    continue
                order_map = move.manomano_order_map_id
                if not order_map or not order_map.channel_id.manomano_invoice_auto:
                    continue
                move.manomano_invoice_pending = True
            except Exception:  # noqa: BLE001 - mai bloccare la conferma
                _logger.exception(
                    "Messa in coda della fattura %s per ManoMano fallita",
                    move.name)
        return result

    @api.model
    def cron_manomano_push_invoices(self):
        """Cron: invia a ManoMano le fatture messe in coda da action_post.

        Gira fuori dalla transazione di conferma contabile. Ogni fattura è
        isolata dalle altre: un errore su una non deve impedire l'invio delle
        successive. A esito riuscito azzera il flag; su fallimento lo lascia
        a True perché il tentativo si ripeta al giro successivo (l'errore è
        già loggato dal connettore su centrivo.job.log).
        """
        moves = self.search([
            ("manomano_invoice_pending", "=", True),
            ("state", "=", "posted"),
            ("manomano_document_id", "=", False),
        ])
        for move in moves:
            order_map = move.manomano_order_map_id
            if not order_map:
                move.manomano_invoice_pending = False
                continue
            try:
                inviata = order_map.channel_id._get_connector().push_invoice(move)
                if inviata:
                    move.manomano_invoice_pending = False
            except Exception:  # noqa: BLE001 - isolamento per fattura
                _logger.exception(
                    "Invio della fattura %s a ManoMano fallito, riproverà "
                    "al prossimo giro", move.name)
        return True
