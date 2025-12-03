# Copyright 2024 Simone Rubino - Aion Tech
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from odoo import _, api, fields, models


class ResPartnerInherit(models.Model):
    _inherit = "res.partner"

    @api.constrains(
        "fiscalcode",
        "company_type",
    )
    def check_fiscalcode(self):
        return True
