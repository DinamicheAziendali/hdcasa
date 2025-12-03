# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import models, fields


class AmazonSettlementQueue(models.Model):
    _name = "settlement.process.queue.line.ept"
    _description = 'Settlement Process Queue Line Ept'

    settlement_queue_id = fields.Many2one('settlement.process.queue.ept', string='Settlement Queue',
                                          help="Settlement Queue Reference", ondelete='cascade', copy=False)
    queue_type = fields.Selection([('order', 'Order'), ('refund', 'Refund'),
                                   ('fees', 'Amazon Fees'), ('other', 'Other Transactions'),
                                   ('refund_invoces', 'Refund Invoices')],
                                  string="Queue Type", help="Identify an queue type")
    settlement_data = fields.Text(help="Data of settlement report.", copy=False)
