# -*- coding: utf-8 -*-
# See LICENSE file for full copyright and licensing details.

"""
Inherited class to create log lines and relate with the queue log record
"""
from odoo import models, fields, api
from datetime import datetime


class CommonLogLineEpt(models.Model):
    """
    Inherited class to add common method to create order and product log lines and relate with
    the order queue
    """
    _inherit = "common.log.lines.ept"
    _rec_name = "log_book_id"
    _order = 'write_date'

    order_queue_data_id = fields.Many2one('shipped.order.data.queue.ept', string='Shipped Order Data Queue')
    fulfillment_by = fields.Selection([('FBA', 'Amazon Fulfillment Network'), ('FBM', 'Merchant Fullfillment Network')],
                                      string="Fulfillment By", help="Fulfillment Center by Amazon or Merchant")
    product_title = fields.Char(string="Product Title", default=False, help="Product Title")
    amz_instance_ept = fields.Many2one(comodel_name='amazon.instance.ept', string="Amazon Instance")
    amz_seller_ept = fields.Many2one(comodel_name='amazon.seller.ept', string="Amazon Seller")
    stock_move_id = fields.Many2one(comodel_name='stock.move', string="Stock Move")
    odoo_internal_reference = fields.Char(string='Internal Reference')

    def amz_find_mismatch_details_log_lines(self, res_id, model_name, mismatch=False):
        """
        This method will search the mismatch details log lines based on resource id.
        :param: res_id : int
        :param: model_name: str
        :return: list of common.log.lines.ept() objects or []
        """
        model = self._get_model_id(model_name)
        domain = [('model_id', '=', model.id if model else False), ('res_id', '=', res_id)]
        if mismatch:
            domain.append(('mismatch_details', '=', True))
        return self.search(domain)

    def download_mismatched_product(self):
        return self.env['active.product.listing.report.ept'].process_mismatched_product()

    @api.model_create_multi
    def create(self, vals_list):
        if vals_list and vals_list[0].get('module') == 'amazon_ept':
            domain_keys = ['operation_type', 'message', 'res_id', 'amz_seller_ept', 'amz_instance_ept', 'module',
                           'model_id']
            new_vals_list = []
            for val in vals_list:
                domain = [(key, '=', val[key]) for key in domain_keys if key in val]
                existing_log = self.env['common.log.lines.ept'].search(domain, limit=1)
                if existing_log:
                    existing_log.write_date = datetime.now()
                else:
                    new_vals_list.append(val)
            return super().create(new_vals_list)
        return super().create(vals_list)

