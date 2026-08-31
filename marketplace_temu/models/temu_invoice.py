# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Aggancio della fattura Odoo all'ordine Temu."""
from odoo import fields, models
from odoo.exceptions import UserError

from odoo.addons.integrations_core.connectors.base import MarketplaceConnector


class AccountMove(models.Model):
    _inherit = "account.move"

    temu_document_sent = fields.Boolean(
        string="Inviata a Temu", default=False, copy=False, readonly=True,
        help="Vero quando il documento e' stato caricato su Temu. Serve a non "
             "caricarlo due volte: un doppione su Temu non si cancella da qui.")
    temu_document_url = fields.Char(
        string="Indirizzo del file su Temu", copy=False, readonly=True)

    def action_temu_push_invoice(self):
        """Invia a Temu questa fattura o nota di credito.

        Bottone MANUALE. L'invio automatico dentro la conferma della fattura
        non si fa, ed e' una lezione gia' pagata su ManoMano: generare il PDF e
        fare una chiamata HTTP mentre i lock della numerazione sono aperti
        significa rischiare di bloccare la contabilita' per un problema di
        marketplace. Peggio ancora, un annullamento dopo un caricamento
        riuscito lascerebbe su Temu un documento doppio.
        """
        for invoice in self:
            canale = invoice._temu_channel()
            if not canale:
                raise UserError(
                    "Questa fattura non e' collegata a nessun ordine Temu.")
            connettore = MarketplaceConnector.for_channel(canale)
            connettore.push_invoice(invoice)
        return True

    def _temu_channel(self):
        """Il canale Temu da cui viene l'ordine di questa fattura, o False."""
        self.ensure_one()
        ordini = self.line_ids.sale_line_ids.order_id
        if not ordini:
            return False
        mappa = self.env["centrivo.order.map"].search([
            ("sale_order_id", "in", ordini.ids),
            ("channel_id.connector_code", "=", "temu"),
        ], limit=1)
        return mappa.channel_id if mappa else False

