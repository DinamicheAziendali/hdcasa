# -*- coding: utf-8 -*-
# See LICENSE file for full copyright and licensing details.

"""
Added Replacement Orders Reason Code class to store the amazon replacement orders reason
"""
from odoo import models, fields


class ReplacementOrderReasonCode(models.Model):
    """
    Added class to store the replacement orders reason code
    """
    _name = "amazon.replacement.order.reason.code"
    _description = 'amazon.replacement.order.reason.code'
    _rec_name = 'replace_order_reason'

    replace_order_reason = fields.Char(size=50, string='Replace Order Reason', help="Replacement order reason")
    replace_order_code = fields.Integer(string="Replace Order Code", required=True,
                                        help="Used to identify the replacement order reason.")

    _sql_constraints = [('replacement_order_reason_unique_constraint', 'unique(replace_order_code,replace_order_reason)',
                         "Fulfillment center must be unique by seller.")]
