# -*- coding: utf-8 -*-
# See LICENSE file for full copyright and licensing details.

"""
Added class and fields to do removal order configurations.
"""
from odoo import models, fields, api


class RemovalOrderConfiguration(models.Model):
    """
    Added class to do removal order configurations and added constraint to set unique
    disposition per instance.
    """
    _name = "removal.order.config.ept"
    _description = "removal.order.config.ept"
    _rec_name = "removal_disposition"

    @api.depends('instance_id')
    def _compute_removal_order_config_company(self):
        """
        Compute method for get company id based on instance company
        :return:
        """
        for record in self:
            company_id = record.instance_id.company_id.id if record.instance_id else self.env['res.company']
            if not company_id:
                company_id = self.env.company.id
            record.company_id = company_id

    removal_disposition = fields.Selection([('Return', 'Return'), ('Disposal', 'Disposal'),
                                            ('Liquidations', 'Liquidations')],
                                           default='Return', required=True, string="Disposition")
    company_id = fields.Many2one('res.company', string="Company", copy=False,
                                 compute="_compute_removal_order_config_company",
                                 store=True)
    picking_type_id = fields.Many2one("stock.picking.type", string="Picking Type")
    location_id = fields.Many2one("stock.location", string="Location")
    unsellable_route_id = fields.Many2one("stock.route", string="UnSellable Route")
    sellable_route_id = fields.Many2one("stock.route", string="Sellable Route")
    instance_id = fields.Many2one("amazon.instance.ept", string="Marketplace")

    _sql_constraints = [('amazon_removal_order_unique_constraint', 'unique(removal_disposition,instance_id)',
                         "Disposition must be unique per Instance.")]
