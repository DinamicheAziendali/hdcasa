# See LICENSE file for full copyright and licensing details.

import logging

from odoo import models


_logger = logging.getLogger()


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def _prestashop_cancel_order(self, *args, **kw):
        _logger.info('SaleOrder _prestashop_cancel_order()')
        status = self.integration_id.sub_status_cancel_id
        self.sub_status_id = status.id

    def _prestashop_shipped_order(self, *args, **kw):
        _logger.info('SaleOrder _prestashop_shipped_order()')
        status = self.integration_id.sub_status_shipped_id
        self.sub_status_id = status.id

    def _prestashop_paid_order(self, *args, **kw):
        status = self.integration_id.sub_status_paid_id
        self.sub_status_id = status.id
