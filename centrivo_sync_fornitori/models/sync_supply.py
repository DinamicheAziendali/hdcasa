# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""centrivo.sync.supply — disponibilità dropship per prodotto × fornitore.

Lo stock dropship vive QUI, per-fornitore (NON come numero globale sul prodotto).
Modello N:1 fin da subito: oggi 1:1, ma predisposto a più fornitori sullo stesso
prodotto (spec §4.3).

In QUESTO task il record viene CREATO in fase di onboarding catalogo con
qty_available = 0; la quantità reale la popolerà il Flusso B (task successivo).
"""
from odoo import fields, models


class CentrivoSyncSupply(models.Model):
    _name = "centrivo.sync.supply"
    _description = "Disponibilità dropship per prodotto e fornitore"
    _rec_name = "supplier_sku"

    product_id = fields.Many2one(
        "product.product", string="Prodotto", required=True,
        ondelete="cascade", index=True)
    dropship_supplier_id = fields.Many2one(
        "res.partner", string="Fornitore", required=True, index=True)
    channel_id = fields.Many2one(
        "centrivo.sync.channel", string="Canale", ondelete="set null",
        help="Canale di sync che ha creato/aggiornato questa riga (tracciabilità).")
    supplier_sku = fields.Char(
        string="Codice fornitore", required=True, index=True,
        help="Codice articolo del fornitore (chiave di match, spec §5).")
    qty_available = fields.Float(
        string="Disponibilità fornitore", default=0.0,
        help="Disponibilità comunicata dal fornitore. Popolata dal feed "
             "giacenze (Flusso B, task successivo). In onboarding resta 0.")
    last_sync = fields.Datetime(string="Ultimo aggiornamento")

    company_id = fields.Many2one(
        "res.company", string="Azienda", required=True,
        default=lambda self: self.env.company)

    _sql_constraints = [
        ("uniq_product_supplier_company",
         "unique(product_id, dropship_supplier_id, company_id)",
         "Esiste già una riga di disponibilità per questo prodotto e fornitore "
         "nella stessa azienda."),
    ]
