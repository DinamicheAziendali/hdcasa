# -*- coding: utf-8 -*-
# See LICENSE file for full copyright and licensing details.

"""
Inherited class to display the shipment amazon product's.
"""
from odoo import models, fields, api
from odoo.exceptions import UserError
from copy import deepcopy


class StockQuantPackage(models.Model):
    """
    inherited class to display the amazon product from the shipment lines.
    """
    _inherit = 'stock.quant.package'

    @api.model
    def default_get(self, fields):
        """
        Use: Used for Add domain in Amazon Product field while enter Carton Information,
        display only Amazon Products which are in Shipment Lines
        @:param: self -> stock.quant.package, fields -> {}
        @:return: {} => dict
        ----------------------------------------------
        Added by: Dhaval Sanghani @Emipro Technologies
        Added on: 30-May-2020
        """
        res = super(StockQuantPackage, self).default_get(fields)
        new_shipment = self._context.get('new_inbound_shipment', False)
        old_shipment = self._context.get('inbound_shipment', False)
        new_inbound_plan = self._context.get('inbound_shipment_new_plan', False)
        product_ids = []
        carton_info_ids = []
        shipment_model = self.env['inbound.shipment.new.ept'] if new_shipment else self.env[
            'amazon.inbound.shipment.ept']
        inbound_shipment = shipment_model.browse(new_shipment or old_shipment)
        if inbound_shipment:
            product_ids = self.get_amazon_products(inbound_shipment) or []
        elif new_inbound_plan:
            inbound_shipment_new_plan = self.env['inbound.shipment.plan.new.ept'].browse(new_inbound_plan)
            product_ids = self.get_amazon_products(inbound_shipment_new_plan) if inbound_shipment_new_plan else []
            carton_info_ids = self.get_carton_info_ids(inbound_shipment_new_plan) if inbound_shipment_new_plan else []
        res.update({'amazon_product_ids': product_ids, 'new_carton_info_ids': carton_info_ids})
        return res

    def _compute_amazon_products(self):
        # Added By: Dhaval Sanghani [30-May-2020]
        """
        Added method to compute amazon products based on shipment record.
        """
        res = {}
        for record in self:
            if record.inbound_shipment_plan_id:
                shipment = record.inbound_shipment_plan_id
            else:
                shipment = record.partnered_ltl_shipment_id \
                    if record.partnered_ltl_shipment_id else record.partnered_small_parcel_shipment_id
            if shipment:
                record.amazon_product_ids = record.get_amazon_products(shipment)
        return res

    def _compute_new_carton_info_ids(self):
        """
        Define this method for calculate carton content information.
        :return: 
        """
        res = {}
        for record in self:
            if record.inbound_shipment_plan_id:
                shipment_plan = record.inbound_shipment_plan_id
            else:
                shipment_plan = False
            if shipment_plan:
                record.new_carton_info_ids = record.get_carton_info_ids(shipment_plan)
        return res

    def get_amazon_products(self, inbound_shipment):
        """
        Use: Return Amazon Products which are in Shipment Lines
        @:param: self -> stock.quant.package, inbound_shipment -> amazon.inbound.shipment.ept record
        @:return:
        ----------------------------------------------
        Added by: Dhaval Sanghani @Emipro Technologies
        Added on: 30-May-2020
        """
        if inbound_shipment._name == self.env['inbound.shipment.plan.new.ept']._name and inbound_shipment.new_shipment_line_ids:
            amz_carton_content_info_obj = self.env['amazon.carton.content.info.ept']
            carton_info_ids = amz_carton_content_info_obj.search([('inbound_shipment_plan_id', '=', inbound_shipment.id)])
            product_ids = carton_info_ids.mapped('amazon_product_id').ids
        else:
            if inbound_shipment._name == 'inbound.shipment.new.ept':
                product_ids = inbound_shipment.shipment_line_ids.amazon_product_id.ids
            else:
                product_ids = inbound_shipment.mapped('odoo_shipment_line_ids').mapped('amazon_product_id').ids
        return product_ids

    def get_carton_info_ids(self, shipment_plan):
        """
        Use: Return Amazon Products which are in Shipment Lines
        @:param: self -> stock.quant.package, inbound_shipment -> amazon.inbound.shipment.ept record
        @:return:
        ----------------------------------------------
        Added by: Dhaval Sanghani @Emipro Technologies
        Added on: 30-May-2020
        """
        self._cr.execute('SELECT MIN(id) AS id FROM amazon_carton_content_info_ept where inbound_shipment_plan_id = %s '
                         'GROUP BY packing_group_id', (shipment_plan.id,))
        result = self._cr.fetchall()
        carton_info_ids = [data[0] for data in result] if result else []
        return carton_info_ids

    def _compute_box_total_qty(self):
        """
        Define this method for compute total box quantity.
        :return:
        """
        for record in self:
            box_total_qty = 0.0
            for carton in record.carton_info_ids:
                box_total_qty += carton.quantity
            record.total_box_qty = box_total_qty

    box_no = fields.Char()
    box_content_information_source = fields.Selection([('BOX_CONTENT_PROVIDED', 'BOX_CONTENT_PROVIDED'),
                                                       ('MANUAL_PROCESS', 'MANUAL_PROCESS'),
                                                       ('BARCODE_2D', 'BARCODE_2D')],
                                                      string='Box Content Information Source')
    carton_info_ids = fields.One2many("amazon.carton.content.info.ept", "package_id",
                                      string="Carton Info")
    amz_carton_info_id = fields.Many2one("amazon.carton.content.info.ept",
                                         string="Carton Information")
    amazon_product_ids = fields.One2many("amazon.product.ept", compute="_compute_amazon_products")
    partnered_small_parcel_shipment_id = fields.Many2one("amazon.inbound.shipment.ept",
                                                         "Small Parcel Shipment")
    is_update_inbound_carton_contents = fields.Boolean(default=False, copy=False)
    partnered_ltl_shipment_id = fields.Many2one("amazon.inbound.shipment.ept", "LTL Shipment")
    package_status = fields.Selection([('SHIPPED', 'SHIPPED'),
                                       ('IN_TRANSIT', 'IN_TRANSIT'),
                                       ('DELIVERED', 'DELIVERED'),
                                       ('CHECKED_IN', 'CHECKED_IN'),
                                       ('RECEIVING', 'RECEIVING'),
                                       ('CLOSED', 'CLOSED'),
                                       ('DELETED', 'DELETED')])
    weight_unit = fields.Selection([('pounds', 'Pounds'), ('kilograms', 'Kilograms'), ])
    weight_value = fields.Float()
    ul_id = fields.Many2one('product.ul.ept', string="Logistic Unit")
    is_stacked = fields.Boolean()
    box_expiration_date = fields.Date(copy=False)
    inbound_shipment_plan_id = fields.Many2one('inbound.shipment.plan.new.ept', "Inbound Shipment Plan")
    packing_group_id = fields.Char('Packing Group Id')
    new_carton_info_ids = fields.One2many("amazon.carton.content.info.ept", compute="_compute_new_carton_info_ids")
    total_box_qty = fields.Float(string="Total Qty", digits=(16, 2), help="Total package quantity",
                                 compute="_compute_box_total_qty")
    is_same_details_for_multiple_boxs = fields.Boolean(string="Is same details for multiple Boxes?", default=False)
    package_total_qty = fields.Float(string="Package Total Qty", digits=(16, 2),
                                     help="Please ensure you accurately enter the total quantity of all boxes for the "
                                          "selected products. Once this quantity is entered, the system will automatically "
                                          "generate the detailed information for all the respective boxes.")
    single_package_qty = fields.Float(string="Single Package Qty",
                                      help="Please enter single package quantity for respective products.")
    partnered_ltl_new_shipment_id = fields.Many2one("inbound.shipment.new.ept", "LTL New Shipment")
    stack_ability = fields.Selection([('STACKABLE', 'STACKABLE'), ('NON_STACKABLE', 'NON_STACKABLE')])


    @api.onchange('amz_carton_info_id')
    def onchange_amz_carton_info_id(self):
        """
        Define this method for update products in the package.
        :return:
        """
        for record in self.filtered(lambda l: l.amz_carton_info_id):
            carton_info_ids = record.amz_carton_info_id.search([
                ('packing_group_id', '=', record.amz_carton_info_id.packing_group_id)])
            product_ids = carton_info_ids.mapped('amazon_product_id').ids
            record.amazon_product_ids = product_ids if product_ids else record.amazon_product_ids

    @api.model_create_multi
    def create(self, vals_list):
        """
        Inherited this method for prepare multiple package details which same as the main package details,
        based on the is_same_details_for_multiple_boxs, package_total_qty and single_package_qty field data.
        :param: vals_list: [{}]
        :return: stock.quant.package()
        """
        package_details = []
        for val in vals_list:
            if val.get('is_same_details_for_multiple_boxs'):
                package_details = self.amz_prepare_package_details(val, package_details)
        if package_details:
            vals_list.extend(package_details)
        return super(StockQuantPackage, self).create(vals_list)

    @staticmethod
    def amz_prepare_package_details(val, package_details):
        """
        Define this method for prepare extra package details.
        :param: val: main package val
        :param: package_details: new package vals list
        :return: [{}, {}] or []
        """
        if not val.get('package_total_qty', 0) or not val.get('single_package_qty', 0):
            return package_details
        if val.get('package_total_qty', 0) < val.get('single_package_qty', 0):
            raise UserError('Please enter single package quantity is less than total package quantity.')
        # check whether package_total_qty if properly division by single_package_qty because
        # we are not able to prepare data for partially package
        if val.get('package_total_qty', 0) % val.get('single_package_qty', 0) != 0:
            raise UserError('Please enter properly single package quantity which calculate no of boxes.')
        # find the total no of packages
        total_no_of_packages = val.get('package_total_qty', 0) / val.get('single_package_qty', 0)
        # here not include main package data in the remaining package
        no_of_remaining_packages = int(total_no_of_packages - 1)
        for i in range(no_of_remaining_packages):
            new_package_dict = deepcopy(val)
            if 'package_total_qty' in new_package_dict.keys():
                del new_package_dict['package_total_qty']
            if 'single_package_qty' in new_package_dict.keys():
                del new_package_dict['single_package_qty']
            if 'is_same_details_for_multiple_boxs' in new_package_dict.keys():
                del new_package_dict['is_same_details_for_multiple_boxs']
            new_carton_info_data = [(0, 0, entry[2].copy()) for entry in new_package_dict.get(
                'carton_info_ids', {}) if len(entry) == 3 and isinstance(entry[2], dict)]
            new_package_dict.update({'carton_info_ids': new_carton_info_data})
            package_details.append(new_package_dict)
        return package_details
