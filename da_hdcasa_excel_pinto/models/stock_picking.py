# -*- coding: utf-8 -*-
# Copyright (C) 2022-Today:
#     Dinamiche Aziendali srl (<http://www.dinamicheaziendali.it/>)
# @author: Gianmarco Conte (gconte@dinamicheaziendali.it)
# License GPL-3.0 or later (http://www.gnu.org/licenses/gpl.html).
from odoo import api, fields, models, _


class StockPickingInherit(models.Model):
    _inherit = 'stock.picking'

    exported_pinto = fields.Boolean(string='Esportato per Pinto')
