# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""L'éco-participation di un prodotto: il dato che bloccava le offerte.

⚠️ E' un campo del PRODOTTO, non del canale: l'éco-participation (legge AGEC,
filiere EPR) e' una proprieta' della merce, e Cdiscount la vuole dichiarata in
OGNI offerta come tassa `Ecotax` (`docs/cdiscount-offerte-contratto.md` §3).
Il valore in euro lo sa il consulente o Citeo, non il codice: qui si mette il
posto in cui scriverlo, e il canale decide se pretenderlo
(`cdiscount_ecotax_obbligatoria`).
"""
from odoo import fields, models


class ProductTemplate(models.Model):
    _inherit = "product.template"

    cdiscount_ecotax = fields.Float(
        string="Éco-participation Cdiscount (€)", digits=(16, 2),
        company_dependent=False,
        help="L'éco-participation da dichiarare in ogni offerta Cdiscount, "
             "in euro per pezzo. Finche' il canale la pretende, un prodotto "
             "a zero NON parte: la riga viene saltata e lo dice.")
