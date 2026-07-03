# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Estensione product.template con i campi dropship (spec §4.1).

Core read-only: solo _inherit, nessuna modifica al core Odoo. I prodotti
dropship restano SEPARATI dal catalogo proprio (is_dropship) così il principio
"Odoo è fonte di verità" resta vero per i prodotti propri (spec §2).

NB (v2.1): rimosso `dropship_supplier_type` (manufacturer/warehouse) — i
fornitori gestiti hanno tutti giacenza, la distinzione non serve a questo modulo
(la colonna DB orfana resta innocua dopo l'update).
"""
from odoo import fields, models


class ProductTemplate(models.Model):
    _inherit = "product.template"

    is_dropship = fields.Boolean(
        string="Dropship", default=False, index=True,
        help="Prodotto la cui fonte anagrafica è il fornitore dropship (Odoo ne "
             "è il mirror). Separato dal catalogo proprio HD CASA.")
    dropship_supplier_id = fields.Many2one(
        "res.partner", string="Fornitore dropship",
        help="Fornitore d'origine del prodotto dropship.")

    # Categorie del fornitore (3 livelli grezzi). Campi di APPOGGIO: NON forzano
    # la categ_id Odoo. La mappatura verso le categorie native è Fase B (spec §19).
    dropship_macro = fields.Char(string="Macrocategoria fornitore")
    dropship_categoria = fields.Char(string="Categoria fornitore")
    dropship_gerarchia = fields.Char(string="Gerarchia fornitore")
