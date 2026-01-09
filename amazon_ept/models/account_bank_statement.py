"""
This File will perform the settlement report's  bank statement operations and inherited
methods to update the settlement report state when bank statement state is updated.
"""

# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import models, fields
SETTLEMENT_REPORT_EPT = 'settlement.report.ept'


class AccountBankStatement(models.Model):
    """
    Inherited AccountBankStatement class to process settlement report's statement
    """
    _inherit = 'account.bank.statement'

    # this field is deprecated in the amazon v17
    settlement_ref = fields.Char(size=350, string='Amazon Settlement Ref')

