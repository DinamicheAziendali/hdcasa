# -*- coding: utf-8 -*-pack
# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""
Added a class to process specific order.
"""


from odoo import models, fields


class SpecificOrderProcessWizard(models.TransientModel):
    """
    Added a class to process specific order.
    """
    _name = "specific.order.process.wizard"
    _description = "Specific Order Process Wizard"

    order_ids = fields.Char(string="Amazon Orders Ids")

    def action_process_specific_orders(self):
        """
        Define this method to process specific orders selected in the wizard.
        :return: True
        """
        active_id = self.env.context.get('active_id', False)
        if not active_id:
            return False
        amz_order_ids = set(self.order_ids.split(','))
        rec = self.env['shipping.report.request.history'].browse(active_id)
        rec.with_context(amz_order_ids=amz_order_ids).process_shipment_file()
        return True
