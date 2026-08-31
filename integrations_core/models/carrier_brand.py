# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""centrivo.carrier.brand — anagrafica dei corrieri reali.

Un corriere (BRT) raggruppa i vari vettori con cui compare nel gestionale
(BRT 010, BRT 100, BRT Euroexpress). È l'unica cosa che i marketplace vogliono
sapere, e l'unica che l'utente configura.
"""
from odoo import fields, models


class CentrivoCarrierBrand(models.Model):
    _name = "centrivo.carrier.brand"
    _description = "Corriere (anagrafica)"
    _order = "name"

    name = fields.Char(string="Corriere", required=True)
    code = fields.Char(
        string="Codice interno", required=True,
        help="Chiave tecnica stabile (es. brt, gls, poste). È con questa che i "
             "connettori dei marketplace dichiarano come si chiama il corriere "
             "a casa loro: non va cambiata dopo la configurazione.")
    tracking_url_template = fields.Char(
        string="URL di tracciamento",
        help="Indirizzo di tracciamento del corriere con il segnaposto "
             "{tracking}, che viene sostituito dal numero di spedizione. "
             "Esempio: https://vivi.brt.it/?tracking={tracking}")
    active = fields.Boolean(string="Attivo", default=True)

    _sql_constraints = [
        ("carrier_brand_code_uniq", "unique(code)",
         "Esiste già un corriere con questo codice interno."),
    ]
