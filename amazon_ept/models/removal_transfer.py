# -*- coding: utf-8 -*-
# See LICENSE file for full copyright and licensing details.

"""
Added class, methods and fields to store and process amazon removal transfer.
"""

from odoo import models, fields, api, _
from odoo.exceptions import UserError

PROCUREMENT_GROUP = 'procurement.group'
STOCK_PICKING = 'stock.picking'


class StockPicking(models.Model):
    _name = 'amazon.removal.transfer.ept'
    _description = "Removal Transfer"
    _inherit = ['mail.thread']
    _order = 'id desc'

    name = fields.Char()
    removal_disposition = fields.Selection([('Return', 'Return'), ('Disposal', 'Disposal'),
                                            ('Liquidations', 'Liquidations')], default='Return', required=True,
                                           help="This Fields relocates type of disposition.")
    ship_address_id = fields.Many2one('res.partner', string='Ship Address', readonly=True,
                                      help="This Fields relocates partner.")
    instance_id = fields.Many2one('amazon.instance.ept', string='Marketplace', required=True,
                                  readonly=True, help="This Fields relocates amazon instance.")
    warehouse_id = fields.Many2one("stock.warehouse", string="Destination Warehouse",
                                   help="This Fields relocates stock warehouse.")
    disposition_location_id = fields.Many2one("stock.location",
                                              related="instance_id.fba_warehouse_id.unsellable_location_id",
                                              readonly=True,
                                              help="This Fields relocates stock destination location.")
    company_id = fields.Many2one('res.company', string='Company', required=True, readonly=True,
                                 help="This Fields relocates company id.")
    state = fields.Selection([('draft', 'Draft'),
                              ('plan_approved', 'Removal Plan Approved'),
                              ('Cancelled', 'Cancelled'),
                              ('In Process', 'In Process'),
                              ('Completed', 'Completed')
                              ], default='draft',
                             help="This Fields relocates state.")
    removal_transfer_lines_ids = fields.One2many("removal.picking.lines.ept", 'removal_transfer_id',
                                                 string="Removal Transfer Lines",
                                                 help="This Fields relocates removal order lines ids.")
    removal_order_picking_ids = fields.One2many(STOCK_PICKING, 'removal_picking_id', string="Removal Pickings",
                                                help="This fields relocates removal order picking ids.")
    removal_count = fields.Integer("Removal Order Pickings", compute="_compute_removal_records",
                                   help="This Fields relocates removal count.")
    removal_transfer_move_type = fields.Selection([
        ('direct', 'As soon as possible'), ('one', 'When all products are ready')], 'Shipping Policy', default='one',
        required=True)

    def _compute_removal_records(self):
        """
        This Method relocates removal count records.
        """
        for record in self:
            record.removal_count = len(record.removal_order_picking_ids.ids)

    def list_of_transfer_removal_pickings(self):
        """
        This Method return list of transfer removal pickings.
        :return:This Method return action of pickings.
        """
        action = {
            'domain': "[('id', 'in', " + str(self.removal_order_picking_ids.ids) + " )]",
            'name': 'Removal Order Pickings',
            'view_type': 'form',
            'view_mode': 'tree,form',
            'res_model': STOCK_PICKING,
            'type': 'ir.actions.act_window',
        }
        return action

    def removal_transfer_procurements(self):
        proc_group_obj = self.env[PROCUREMENT_GROUP]
        tracking_ref_list = list(set(self.removal_transfer_lines_ids.mapped('tracking_ref')))
        for track_ref in tracking_ref_list:
            sell_proc_group = self.create_procurement_group()
            sell_proc_group.write({'move_type': self.removal_transfer_move_type})
            unsell_proc_group = self.create_procurement_group()
            unsell_proc_group.write({'move_type': self.removal_transfer_move_type})
            config = self.instance_id.removal_order_config_ids.filtered(
                lambda l: l.removal_disposition == 'Return')
            require_stock_rule_seq = config.unsellable_route_id.rule_ids.mapped('sequence')[-1]
            unsellable_rule = config.unsellable_route_id.rule_ids.filtered(
                lambda l: l.sequence == require_stock_rule_seq)
            #sellable_rule = config.sellable_route_id.rule_ids.filtered(lambda l: l.location_src_id.usage == 'transit')
            sellable_rule_require_stock_rule_seq = config.sellable_route_id.rule_ids.mapped('sequence')[-1]
            sellable_rule = config.sellable_route_id.rule_ids.filtered(
                lambda l: l.sequence == sellable_rule_require_stock_rule_seq)
            for removal_line in self.removal_transfer_lines_ids.filtered(lambda t: t.tracking_ref == track_ref):
                amz_product = removal_line.amazon_product_id
                product_id = amz_product.product_id
                product_uom = product_id.uom_id
                removal_order_instance = self.instance_id.company_id
                sellable_quantity = 0.0 if removal_line.sellable_quantity < 0.0 else removal_line.sellable_quantity
                unsellable_quantity = 0.0 if removal_line.unsellable_quantity < 0.0 else removal_line.unsellable_quantity
                datas = {'company_id': self.instance_id.company_id,
                         'warehouse_id': self.warehouse_id, 'priority': '1', 'carrier_id': removal_line.carrier_id.id,
                         'carrier_tracking_ref': removal_line.tracking_ref}
                if unsellable_quantity > 0.0:
                    datas.update({'group_id': unsell_proc_group,
                                  'route_ids': config.unsellable_route_id})
                    qty = removal_line.unsellable_quantity
                    location_id = unsellable_rule.location_dest_id
                    self.run_procurement_group_picking(proc_group_obj, product_id, qty, product_uom,
                                                       location_id, product_id.name, self.name,
                                                       removal_order_instance, datas)
                if sellable_quantity > 0.0:
                    datas.update({'group_id': sell_proc_group,
                                  'route_ids': config.sellable_route_id})
                    qty = removal_line.sellable_quantity
                    location_id = sellable_rule.location_dest_id
                    self.run_procurement_group_picking(proc_group_obj, product_id, qty, product_uom,
                                                       location_id, product_id.name, self.name,
                                                       removal_order_instance, datas)
                pickings = self.env['stock.picking'].search([('group_id', 'in', [sell_proc_group.id, unsell_proc_group.id])])
                pickings.write(
                    {'carrier_id': removal_line.carrier_id.id, 'carrier_tracking_ref': removal_line.tracking_ref,
                     'carrier_name_in_file': removal_line.carrier_name_in_file})
        pickings = self.process_return_order_pickings_ept(sell_proc_group, unsell_proc_group)
        return pickings

    def create_procurement_group(self):
        """
        This Method create procurement group with removal order id.
        :return: This Method return create procurement group object.
        """
        proc_group_obj = self.env[PROCUREMENT_GROUP]
        return proc_group_obj.create({'removal_transfer_id': self.id, 'partner_id': self.ship_address_id.id,
                                      'name': self.name})

    def run_procurement_group_picking(self, proc_group_obj, amazon_product_id, qty, amazon_product_uom,
                                      stock_rule_location_id,
                                      amazon_product_name, removal_order_name, removal_order_instance,
                                      datas):
        """
        This Method relocates run procurement group for sellable quantity and unsellable quantity.
        :param proc_group_obj: This Arguments relocates procurement group object.
        :param amazon_product_id: This Arguments relocates amazon product id.
        :param qty: This Arguments relocates sellable quantity and unsellable quantity.
        :param amazon_product_uom: This Arguments relocates amazon product unit of measure.
        :param stock_rule_location_id: This Arguments relocates stock rule location id.
        :param amazon_product_name: This Arguments relocates amazon product name.
        :param removal_order_name: This Arguments relocates removal order name.
        :param removal_order_instance: This Arguments relocates removal order instance.
        :param datas: This Arguments relocates datas dictionary(Group_ids,route_ids).
        :return:
        """
        proc_group_obj.run([self.env[PROCUREMENT_GROUP].Procurement(amazon_product_id, qty,
                                                                    amazon_product_uom,
                                                                    stock_rule_location_id,
                                                                    amazon_product_name,
                                                                    removal_order_name,
                                                                    removal_order_instance,
                                                                    datas)])
        return proc_group_obj

    def process_return_order_pickings_ept(self, sell_proc_group, unsell_proc_group):
        """
        Define method which help to process Return Removal Order pickings.
        :param : sell_proc_group : procurement.group() object
        :param : unsell_proc_group : procurement.group() object
        :return : stock.picking() objects
        :Migration done by kishan sorani on date 01-Oct-2021
        """
        picking_obj = self.env['stock.picking']
        pickings = picking_obj.search([('group_id', 'in', [sell_proc_group.id, unsell_proc_group.id])])
        pickings_state_confirm = pickings.filtered(
            lambda p: p.state in ['confirmed', 'partially_available', 'assigned'])
        if pickings_state_confirm:
            pickings_state_confirm.write(
                {'is_fba_wh_picking': True, 'removal_picking_id': self.id, 'amazon_removal_transfer_id': self.name})
        pickings_state_waiting = pickings.filtered(lambda p: p.state in ['waiting'])
        if pickings_state_waiting:
            pickings_state_waiting.write(
                {'is_fba_wh_picking': False, 'removal_picking_id': self.id, 'amazon_removal_transfer_id': self.name})
        return pickings

    def disposal_transfer_pickings(self):
        """
        This Method relocates create disposal order pickings.
        If sellable quantity is grater then 0 then creates stock pickings.
        :return:This Method return Boolean(True/False).
        """
        log_line_obj = self.env['common.log.lines.ept']
        picking_obj = self.env['stock.picking']
        stock_move_obj = self.env['stock.move']
        config = self.instance_id.removal_order_config_ids.filtered(
            lambda l: l.removal_disposition == self.removal_disposition)
        if not config:
            message = "Removal Order configuration missing for disposition {}".format(self.removal_disposition)
            if not self._context.get('is_auto_process', False):
                raise UserError(_(message))
            model_name = self._context.get('model_name_ept', '')
            log_line_obj.create_common_log_line_ept(
                message=message, model_name=model_name, module='amazon_ept', operation_type='import', res_id=self.id,
                mismatch_details=True, amz_instance_ept=self.instance_id and self.instance_id.id or False,
                amz_seller_ept=self.instance_id.seller_id and self.instance_id.seller_id.id or False)
        picking_type_id = config.picking_type_id.id
        dest_location_id = config.location_id.id
        unsellable_source_location_id = self.disposition_location_id.id
        sellable_source_location_id = self.instance_id.fba_warehouse_id.lot_stock_id.id
        sellable_picking = unsellable_picking = False

        for removal_line in self.removal_transfer_lines_ids:
            amazon_product = removal_line.amazon_product_id
            sellable_quantity = 0.0 if removal_line.sellable_quantity < 0.0 else removal_line.sellable_quantity
            unsellable_quantity = 0.0 if removal_line.unsellable_quantity < 0.0 else removal_line.unsellable_quantity
            if sellable_quantity > 0.0:
                if not sellable_picking:
                    pick_vals = self.create_picking_vals(picking_type_id, sellable_source_location_id,
                                                         dest_location_id)
                    sellable_picking = picking_obj.create(pick_vals)
                    sellable_picking.write(
                        {'carrier_id': removal_line.carrier_id.id, 'carrier_tracking_ref': removal_line.tracking_ref,
                         'carrier_name_in_file': removal_line.carrier_name_in_file})
                move_vals = self.create_move_vals(sellable_source_location_id, dest_location_id,
                                                  amazon_product.product_id, sellable_quantity, sellable_picking.id)
                stock_move_obj.create(move_vals)
            if unsellable_quantity > 0.0:
                if not unsellable_picking:
                    pick_vals = self.create_picking_vals(picking_type_id, unsellable_source_location_id,
                                                         dest_location_id)
                    unsellable_picking = picking_obj.create(pick_vals)
                    unsellable_picking.write(
                        {'carrier_id': removal_line.carrier_id.id, 'carrier_tracking_ref': removal_line.tracking_ref,
                         'carrier_name_in_file': removal_line.carrier_name_in_file})
                move_vals = self.create_move_vals(unsellable_source_location_id, dest_location_id,
                                                  amazon_product.product_id, unsellable_quantity, unsellable_picking.id)
                stock_move_obj.create(move_vals)
        if sellable_picking:
            sellable_picking.action_confirm()
            sellable_picking.action_assign()
        if unsellable_picking:
            unsellable_picking.action_confirm()
            unsellable_picking.action_assign()
        return sellable_picking, unsellable_picking

    def create_picking_vals(self, picking_type_id, source_location_id, dest_location_id):
        """
        The usage of this Method will be prepare picking values for create stock picking.
        :param picking_type_id: integer
        :param source_location_id: integer
        :param dest_location_id: integer
        :return: dict()
        """
        return {
            'picking_type_id': picking_type_id,
            'partner_id': self.ship_address_id.id,
            # 'removal_order_id': self.id,
            'origin': self.name,
            'company_id': self.instance_id.company_id.id,
            'location_id': source_location_id,
            'location_dest_id': dest_location_id,
            'seller_id': self.instance_id and self.instance_id.seller_id and self.instance_id.seller_id.id or False
        }

    def create_move_vals(self, location_id, location_dest_id, product_id, qty, picking_id):
        """
        This Method relocates create stock move line values.
        :param location_id: This Arguments relocates sellable source location id.
        :param location_dest_id: This Arguments relocates location destination id.
        :param product_id: This Arguments relocates product_id of amazon.
        :param qty: This Arguments relocates sellable quantity.
        :param picking_id: This Arguments relocates sellable picking.
        :return: This Method prepare value of stock move and return.
        """
        vals = {
            'location_id': location_id,
            'location_dest_id': location_dest_id,
            'product_uom_qty': qty,
            'name': product_id.name,
            'product_id': product_id.id,
            'state': 'draft',
            'picking_id': picking_id,
            'product_uom': product_id.uom_id.id,
            'company_id': self.instance_id.company_id.id
        }
        return vals
