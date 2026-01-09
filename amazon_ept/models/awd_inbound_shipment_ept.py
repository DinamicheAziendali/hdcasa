# -*- coding: utf-8 -*-
# See LICENSE file for full copyright and licensing details.
from collections import defaultdict
import logging
import time
from datetime import datetime, timedelta, timezone
from odoo import fields, models, api, _
from odoo.exceptions import UserError
from odoo.addons.iap.tools import iap_tools
from odoo.fields import Datetime
from ..endpoint import DEFAULT_ENDPOINT

_logger = logging.getLogger(__name__)
DATE_YMDHMS = "%Y-%m-%d %H:%M:%S"
DATE_YMDTHMS = "%Y-%m-%dT%H:%M:%S"


class AWDInboundShipmentEpt(models.Model):
    _name = 'awd.inbound.shipment.ept'
    _description = 'AWD Inbound Shipment Ept'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    state = fields.Selection([('draft', 'Draft'), ('ABANDONED', 'ABANDONED'),
                              ('CANCELLED', 'CANCELLED'), ('CHECKED_IN', 'CHECKED_IN'),
                              ('DELETED', 'DELETED'), ('DELIVERED', 'DELIVERED'),
                              ('IN_TRANSIT', 'IN_TRANSIT'), ('MIXED', 'MIXED'),
                              ('RECEIVING', 'RECEIVING'), ('UNCONFIRMED', 'UNCONFIRMED'),
                              ('WORKING', 'WORKING'), ('READY_TO_SHIP', 'READY_TO_SHIP'),
                              ('SHIPPED', 'SHIPPED'), ('CLOSED', 'CLOSED')],
                             string='AWD Shipment Status', default='WORKING')
    name = fields.Char(size=120, readonly=True, required=False, index=True)
    shipment_id = fields.Char(string='Shipment ID')
    company_id = fields.Many2one('res.company', string='AWD Shipment Company',
                                 compute="_compute_awd_shipment_company", store=True)
    ship_from_address_id = fields.Many2one('res.partner', string='Ship To Address')
    from_warehouse_id = fields.Many2one("stock.warehouse", string="From Warehouse")
    closed_date = fields.Date(readonly=True, copy=False)
    log_ids = fields.One2many('common.log.lines.ept', compute="_compute_error_logs")
    count_pickings = fields.Integer(string='Count Picking', compute='_compute_picking_count')
    picking_ids = fields.One2many('stock.picking', 'new_odoo_awd_shipment_id', string="Pickings", readonly=True)
    instance_id_ept = fields.Many2one("amazon.instance.ept", string="Instance")
    amazon_reference_id = fields.Char(size=50, help="A unique identifier created by Amazon that "
                                                    "identifies this Amazon-partnered, Less Than "
                                                    "Truckload/Full Truckload (LTL/FTL) shipment.")
    destination_address_id = fields.Many2one('res.partner', string='Destination Address')
    fulfill_center_id = fields.Char(size=120, string='Fulfillment Center', readonly=True,
                                    help="DestinationFulfillmentCenterId provided by Amazon, "
                                         "when we send shipment Plan to Amazon")
    warehouse_reference_id = fields.Char(string='Warehouse Reference Id', readonly=True)
    response_destination_address = fields.Text('Destination Address')
    response_origin_address = fields.Text('Origin Address')
    awd_shipment_line_ids = fields.One2many('awd.inbound.shipment.line.ept', 'awd_shipment_new_id',
                                            string='Shipment Lines')
    active = fields.Boolean(string="Active", default=True)
    is_manually_created = fields.Boolean(default=False, copy=False)
    carrier_code_type = fields.Char(string='Carrier Code Type', readonly=True)
    carrier_code_value = fields.Char(string='Carrier Code Value', readonly=True)
    awd_to_warehouse_id = fields.Many2one("stock.warehouse", string="To Warehouse")

    @api.depends('instance_id_ept')
    def _compute_awd_shipment_company(self):
        """
        Compute the company_id for the inbound shipment
        """
        for record in self:
            company_id = record.instance_id_ept.company_id.id if record.instance_id_ept else self.env.company.id
            record.company_id = company_id

    def _compute_error_logs(self):
        """
        Define method to get shipment error logs.
        """
        log_line_obj = self.env['common.log.lines.ept']
        log_lines = log_line_obj.amz_find_mismatch_details_log_lines(self.id, 'awd.inbound.shipment.ept')
        self.log_ids = log_lines.ids if log_lines else False

    def _compute_picking_count(self):
        """
        This method is used to compute total numbers of pickings
        :return: N/A
        """
        for rec in self:
            rec.count_pickings = len(rec.picking_ids.ids)

    def action_view_pickings(self):
        """
        This method creates and return an action for opening the view of stock.picking
        :return: action
        """
        action = {
            'name': 'AWD Inbound Pickings',
            'res_model': 'stock.picking',
            'type': 'ir.actions.act_window'
        }
        if self.count_pickings != 1:
            action.update({'domain': [('id', 'in', self.picking_ids.ids)],
                           'view_mode': 'list,form'})
        else:
            action.update({'res_id': self.picking_ids.id,
                           'view_mode': 'form'})
        return action

    def amz_prepare_awd_shipment_kwargs_vals(self, instance):
        """
        Prepare General Arguments for call Amazon MWS API
        :param instance:
        :return: kwargs {}
        """
        account = self.env['iap.account'].search([('service_name', '=', 'amazon_ept')])
        dbuuid = self.env['ir.config_parameter'].sudo().get_param('database.uuid')
        amz_marketplace_code = instance.seller_id.country_id.amazon_marketplace_code or instance.seller_id.country_id.code
        if amz_marketplace_code.upper() == 'GB':
            amz_marketplace_code = 'UK'
        kwargs = {'merchant_id': instance.merchant_id and str(instance.merchant_id) or False,
                  'app_name': 'amazon_ept_spapi', 'account_token': account.account_token,
                  'dbuuid': dbuuid, 'marketplace_id': instance.market_place_id,
                  'amazon_marketplace_code': amz_marketplace_code}
        return kwargs

    def get_awd_inbound_import_shipment_data_wise(self, instance, warehouse_id, start_date, end_date,
                                                  ship_to_address=False):
        amz_awd_inbound_shipment_obj = self.env['awd.inbound.shipment.ept']
        awd_inbound_shipment_list = []
        kwargs = amz_awd_inbound_shipment_obj.amz_prepare_awd_shipment_kwargs_vals(instance)
        if start_date or end_date:
            start_date, end_date = self.get_report_start_and_end_date(start_date, end_date)
        kwargs.update(
            {'emipro_api': 'get_awd_shipment_sp_api_v2024_05_09', 'updatedAfter': start_date, 'updatedBefore': end_date,
             'import_awd_shipment_with_date_range': True, 'skuQuantities': 'SHOW', 'sortBy': 'UPDATED_AT',
             'sortOrder': 'ASCENDING', 'maxResults': '200'})
        response = iap_tools.iap_jsonrpc(DEFAULT_ENDPOINT, params=kwargs, timeout=1000)
        if response.get('error', False):
            raise UserError(_(response.get('error', {})))
        awd_inbound_shipment_list = response.get('result', {})
        return awd_inbound_shipment_list

    def get_awd_inbound_import_shipment(self, instance, warehouse_id, shipment_id=False, ship_to_address=False,
                                        to_warehouse_id=False):
        """
        Import already created Inbound Shipment from shipment id, and it will be created for given warehouse id.
        :param instance:
        :param warehouse_id:
        :param shipment_id:
        :param ship_to_address:
        :param start_date:
        :param end_date:
        :return:
        """
        amz_awd_inbound_shipment_obj = self.env['awd.inbound.shipment.ept']
        common_log_line_obj = self.env['common.log.lines.ept']
        shipment_ids = shipment_id.split(',')
        # No Need to Import Duplicate AWD Inbound Shipment
        awd_inbound_shipment = amz_awd_inbound_shipment_obj.search([('shipment_id', 'in', shipment_ids)])
        awd_inbound_shipment_list = []
        if awd_inbound_shipment:
            shipments = ", ".join(str(shipment.shipment_id) for shipment in awd_inbound_shipment)
            message = "Shipments %s already exists" % shipments
            common_log_line_obj.create_common_log_line_ept(message=message, model_name='awd.inbound.shipment.ept',
                                                           module='amazon_ept', operation_type='import',
                                                           amz_seller_ept=instance.seller_id.id)
            return False
        for shipment_id in shipment_ids:
            kwargs = amz_awd_inbound_shipment_obj.amz_prepare_awd_shipment_kwargs_vals(instance)
            kwargs.update({'emipro_api': 'get_awd_shipment_sp_api_v2024_05_09',
                           'shipmentId': shipment_id,
                           'import_awd_shipment_with_date_range': False,
                           'skuQuantities': 'SHOW'
                           })
            response = iap_tools.iap_jsonrpc(DEFAULT_ENDPOINT, params=kwargs, timeout=1000)
            if response.get('error', False):
                raise UserError(_(response.get('error', {})))
            amazon_awd_shipments = response.get('result', {})
            awd_inbound_shipment = self.create_amazon_awd_shipment(amazon_awd_shipments, instance, warehouse_id.id,
                                                                   ship_to_address, to_warehouse_id.id)
            if not awd_inbound_shipment:
                raise UserError(_("Inbound Shipment is not found in Amazon for shipment Id %s" % shipment_id))
            self.create_amazon_awd_inbound_shipment_line(response, awd_inbound_shipment, instance)
            awd_inbound_shipment.create_procurements()
            if awd_inbound_shipment:
                awd_inbound_shipment_list.append(awd_inbound_shipment.id)
        return awd_inbound_shipment_list

    def create_amazon_awd_shipment(self, results, instance_id, from_warehouse_id, ship_to_address, to_warehouse):
        """
        Method for Create AWD Inbound Shipment which is already created in Amazon.
        :param results:
        :param instance_id:
        :param from_warehouse_id:
        :param ship_to_address:
        :return:
        """
        awd_inbound_shipment = False
        amz_awd_inbound_shipment_obj = self.env['awd.inbound.shipment.ept']
        result = results
        shipment_id = result.get('shipmentId', False)
        fulfillment_center_id = ''
        amazon_reference_id = ''
        if not ship_to_address:
            warehouse = amz_awd_inbound_shipment_obj.amz_awd_inbound_get_warehouse_ept(
                instance_id, fulfillment_center_id)
            ship_to_address = warehouse.partner_id if warehouse.partner_id else False
        awd_inbound_shipment = amz_awd_inbound_shipment_obj.create({
            'name': shipment_id, 'amazon_reference_id': amazon_reference_id,
            'shipment_id': shipment_id,
            'warehouse_reference_id': result.get('warehouseReferenceId'),
            'response_destination_address': result.get('destinationAddress'),
            'response_origin_address': result.get('originAddress'),
            'carrier_code_type': result.get('carrierCode').get('carrierCodeType'),
            'carrier_code_value': result.get('carrierCode').get('carrierCodeValue'),
            'ship_from_address_id': ship_to_address.id if ship_to_address else self.env.company.partner_id.id,
            'instance_id_ept': instance_id.id, 'fulfill_center_id': fulfillment_center_id,
            'from_warehouse_id': from_warehouse_id, 'is_manually_created': True, 'awd_to_warehouse_id': to_warehouse})
        return awd_inbound_shipment

    def create_amazon_awd_inbound_shipment_line(self, response, awd_inbound_shipment, instance_id):
        """
        Define this method for create amazon inbound shipment lines.
        :param: items: dict of shipment details
        :param: inbound_shipment: inbound.shipment.new.ept()
        :param: instance_id: instance record
        :return:
        """
        result = response.get('result')
        not_exist_seller_skus = []
        amazon_awd_inbound_shipment_plan_line_obj = self.env['awd.inbound.shipment.line.ept']
        amazon_product_obj = self.env['amazon.product.ept']
        new_items = result.get('shipmentSkuQuantities')
        shipmentContainerQuantities = result.get('shipmentContainerQuantities', [])
        received_qty_by_sku = {}
        for container in shipmentContainerQuantities:
            distribution_package = container.get('distributionPackage', {})
            if distribution_package.get('type') == 'CASE':
                products = distribution_package.get('contents', {}).get('products', [])
                for product in products:
                    sku = product.get('sku')
                    qty_per_package = product.get('quantity', 0)
                    received_qty_by_sku[sku] = received_qty_by_sku.get(sku, 0.0) + qty_per_package
        for new_item in new_items:
            seller_sku = new_item.get('sku', '')
            # fn_sku = new_item.get('FulfillmentNetworkSKU', '')
            case_count = received_qty_by_sku.get(seller_sku, 0.0)
            received_qty = float(new_item.get('receivedQuantity', {}).get('quantity', 0.0))
            expectedQuantity = float(new_item.get('expectedQuantity', {}).get('quantity', 0.0))
            package_type = new_item.get('receivedQuantity', {}).get('unitOfMeasurement')
            if package_type == 'CASES':
                finalReceiveQty = received_qty * case_count
                finalExpectedQuantity = expectedQuantity * case_count
            else:
                finalReceiveQty = received_qty
                finalExpectedQuantity = expectedQuantity
            amazon_product = amazon_product_obj.search_amazon_product(instance_id.id, seller_sku, 'FBA')
            if not amazon_product:
                not_exist_seller_skus.append(seller_sku)
                continue
            amazon_awd_inbound_shipment_plan_line_obj.create({
                'amazon_product_id': amazon_product.id,
                'seller_sku': seller_sku,
                'quantity': finalExpectedQuantity,
                'fn_sku': '',
                'received_qty': finalReceiveQty,
                'awd_shipment_new_id': awd_inbound_shipment.id})
        if not_exist_seller_skus:
            user_message = ("You will be required to map products before proceeding. Please map the following "
                            "Amazon SKUs with Odoo products and try again!\n%s" % not_exist_seller_skus)
            raise UserError(_(user_message))
        return True

    @api.model
    def create_procurements(self):
        """
        This method will find warehouse and location according to routes,
        if found then Create and run Procurement, also it will assign pickings if found.
        :return: boolean
        """
        proc_group_obj = self.env['procurement.group']
        picking_obj = self.env['stock.picking']
        group_wh_dict = {}
        proc_group = proc_group_obj.create({'new_odoo_awd_shipment_id': self.id, 'name': self.name,
                                            'partner_id': self.ship_from_address_id.id})
        instance = self.instance_id_ept
        warehouse = self.amz_awd_inbound_get_warehouse_ept(instance, self.fulfill_center_id)
        # warehouse = self.awd_to_warehouse_id
        if warehouse:
            location_routes = self.amz_find_awd_location_routes_ept(warehouse)
            group_wh_dict.update({proc_group: warehouse})
            for line in self.awd_shipment_line_ids:
                qty = line.quantity
                product_id = line.amazon_product_id.product_id
                datas = self.amz_awd_inbound_prepare_procure_datas(location_routes, proc_group,
                                                                   instance, warehouse)
                proc_group_obj.run([proc_group_obj.Procurement(product_id, qty, product_id.uom_id,
                                                               warehouse.lot_stock_id,
                                                               product_id.name, self.name,
                                                               instance.company_id, datas)])
        if group_wh_dict:
            for group, warehouse in group_wh_dict.items():
                picking = picking_obj.search([('group_id', '=', group.id),
                                              ('picking_type_id.warehouse_id', '=', warehouse.id)])
                if picking:
                    picking.write({'is_fba_wh_picking': True})

        pickings = self.mapped('picking_ids').filtered(lambda pick: not pick.is_fba_wh_picking and
                                                                    pick.state not in ['done', 'cancel'])
        for picking in pickings:
            picking.action_assign()
        return True

    def amz_awd_inbound_get_warehouse_ept(self, instance, fulfill_center_id):
        """
        Get warehouse from fulfillment center, if not found then from instance.
        :param instance: amazon.instance.ept()
        :param fulfill_center_id: amazon fulfill center id
        :return: stock.warehouse()
        """
        log_line_obj = self.env['common.log.lines.ept']
        fulfillment_center_obj = self.env['amazon.fulfillment.center']
        fulfillment_center = fulfillment_center_obj.search([('center_code', '=', fulfill_center_id),
                                                            ('seller_id', '=', instance.seller_id.id)], limit=1)
        # warehouse = fulfillment_center and fulfillment_center.warehouse_id or instance.fba_warehouse_id or instance.warehouse_id or False
        warehouse = self.awd_to_warehouse_id if self.awd_to_warehouse_id else instance.seller_id.awd_warehouse_id
        if not warehouse:
            error_value = ('Warehouse not found for Fulfillment Center: %s to process AWD shipment: %s.\n'
                           'Action items:\n'
                           '-Configure the Fulfillment Center under: Amazon → Configuration → Fulfillment Centers.\n'
                           '-Set the warehouse and re-import the AWD shipment using the operation wizard.') % (
                              fulfill_center_id, self.name)
            log_line_obj.create_common_log_line_ept(
                message=error_value, model_name='inbound.shipment.new.ept',
                module='amazon_ept', operation_type='export',
                res_id=self.id, amz_instance_ept=instance and instance.id or False,
                amz_seller_ept=instance.seller_id and instance.seller_id.id or False)
        return warehouse

    def amz_find_awd_location_routes_ept(self, warehouse):
        """
        Find Location routes from warehouse.
        :param warehouse: stock.warehouse()
        :return: stock.location.route()
        """
        log_line_obj = self.env['common.log.lines.ept']
        location_route_obj = self.env['stock.route']
        # handle supplier_wh_id for shipment plan and import shipment without shipment plan
        supplier_wh_id = self.from_warehouse_id.id
        location_routes = location_route_obj.search([('supplied_wh_id', '=', warehouse.id),
                                                     ('supplier_wh_id', '=', supplier_wh_id)], limit=1)
        if not location_routes:
            error_value = ('Cannot generate transfer, routes not found for warehouse: %s (Shipment: %s).\n'
                           'Action items:\n'
                           '-Check warehouse routes.\n'
                           '-Update routing configuration and re-import the AWD shipment.') % (warehouse.name,
                                                                                           self.name)
            log_line_obj.create_common_log_line_ept(message=error_value, model_name='inbound.shipment.new.ept',
                                                    module='amazon_ept', operation_type='export', res_id=self.id)
        return location_routes

    @staticmethod
    def amz_awd_inbound_prepare_procure_datas(location_routes, proc_group, instance, warehouse):
        """
        Prepare Procurement values dictionary.
        :param location_routes: stock.location.route()
        :param proc_group: procurement.group()
        :param instance: amazon.instance.ept()
        :param warehouse: stock.warehouse()
        :return: dict{}
        """
        return {
            'route_ids': location_routes,
            'group_id': proc_group,
            'company_id': instance.company_id,
            'warehouse_id': warehouse,
            'priority': '1'
        }

    def get_instance(self):
        """
        The method will return the instance of inbound shipment.
        :param shipment: amazon.inbound.shipment.ept()
        :return: amazon.instance.ept()
        """
        if self.instance_id_ept:
            return self.instance_id_ept
        return self.shipment_plan_id.instance_id

    def check_status(self):
        """
        Check status of AWD Shipment from amazon and update in Odoo as per response of Amazon.
        :return:True
        """
        instance_awd_shipment_ids = defaultdict(list)
        for shipment in self:
            if not shipment.shipment_id:
                continue
            instance = shipment.get_instance()
            instance_awd_shipment_ids[instance].append(str(shipment.shipment_id))
        for instance, shipment_ids in instance_awd_shipment_ids.items():
            shipment_id = shipment_ids[0]
            kwargs = self.amz_prepare_awd_shipment_kwargs_vals(instance)
            kwargs.update({'emipro_api': 'get_awd_shipment_sp_api_v2024_05_09',
                           'shipmentId': shipment_id,
                           'import_awd_shipment_with_date_range': False,
                           'skuQuantities': 'SHOW'
                           })
            response = iap_tools.iap_jsonrpc(DEFAULT_ENDPOINT, params=kwargs, timeout=1000)
            if response.get('error', {}):
                raise UserError(_(response.get('error', {})))
            _logger.info("Shipment Response : %s" % (response))
            amazon_shipments = response.get('result', {})
            self.amz_check_status_process_awd_shipments(amazon_shipments, instance)
        return True

    def amz_check_status_process_awd_shipments(self, amazon_awd_shipments, instance):
        """
         Define this method for get shipment items using shipment id.
        :param amazon_shipments: shipment response
        :param instance: instance: amazon.instance.ept()
        :return:
        """
        stock_picking_obj = self.env['stock.picking']
        ship_member = amazon_awd_shipments
        shipment_id = ship_member.get('shipmentId', '')
        shipment_status = ship_member.get('shipmentStatus', '')
        odoo_shipment_rec = self.search([('shipment_id', '=', shipment_id)])
        already_returned = False
        flag = False
        if shipment_status in ['RECEIVING', 'CLOSED']:
            pickings = odoo_shipment_rec.mapped('picking_ids').filtered(
                lambda r: r.state in ['assigned'] and r.is_fba_wh_picking)
            if pickings:
                pickings.check_amazon_awd_shipment_status_v2024(ship_member)
                backorders = odoo_shipment_rec.picking_ids.filtered(
                    lambda picking: picking.state in ('waiting', 'confirmed') and picking.is_fba_wh_picking)
                if backorders:
                    backorders.action_assign()
                odoo_shipment_rec.write({'state': shipment_status})
                stock_picking_obj.awd_check_qty_difference_and_create_return_picking_v2024(
                    ship_member, shipment_id, odoo_shipment_rec.id, instance)
                already_returned = True
            else:
                if odoo_shipment_rec:
                    pickings = odoo_shipment_rec.mapped('picking_ids').filtered(
                        lambda r: r.state in ['draft', 'waiting', 'confirmed'] and r.is_fba_wh_picking)
                    if pickings:
                        pickings = self.amz_awd_cancel_waiting_state_pickings(pickings, odoo_shipment_rec)
                if not pickings:
                    flag = False
                    self.get_remaining_qty(ship_member, instance, shipment_id, odoo_shipment_rec)
                    odoo_shipment_rec.write({'state': shipment_status})
                else:
                    raise UserError(_("""Shipment Status is not update due to picking not found
                                         for processing  ||| Amazon status : %s ERP status : %s
                                         """ % (shipment_status, odoo_shipment_rec.state)))
            if shipment_status == 'CLOSED':
                if not flag:
                    self.get_remaining_qty(ship_member, instance, shipment_id, odoo_shipment_rec)
                if not odoo_shipment_rec.closed_date:
                    odoo_shipment_rec.write({'closed_date': time.strftime("%Y-%m-%d")})
                if odoo_shipment_rec:
                    pickings = odoo_shipment_rec.mapped('picking_ids').filtered(
                        lambda r: r.state not in ['done', 'cancel'] and r.is_fba_wh_picking)
                if pickings:
                    pickings.action_cancel()
                if not already_returned:
                    stock_picking_obj.awd_check_qty_difference_and_create_return_picking_v2024(
                        ship_member, shipment_id, odoo_shipment_rec.id, instance)
        else:
            odoo_shipment_rec.write({'state': shipment_status})
        return True

    @staticmethod
    def amz_awd_cancel_waiting_state_pickings(pickings, odoo_shipment_rec):
        """
        Define this method for cancel inbound shipment waiting state pickings for non tracking products.
        :param: pickings: stock.picking()
        :param: odoo_shipment_rec: amazon.inbound.shipment.ept()
        :return: stock.picking()
        """
        for picking in pickings.filtered(lambda pick: pick.state in ('waiting', 'confirmed')):
            if not picking.move_ids.filtered(lambda move: move.product_id.tracking in ('serial', 'lot')):
                picking.action_cancel()
        return odoo_shipment_rec.mapped('picking_ids').filtered(
            lambda r: r.state in ['draft', 'waiting', 'confirmed'] and r.is_fba_wh_picking)

    def get_remaining_qty(self, response, instance, amazon_shipment_id, odoo_shipment_rec):
        """
        Get remaining Quantity from done or cancelled pickings
        :param response: amazon inbound response
        :param instance: amazon.instance.ept()
        :param amazon_shipment_id:
        :param odoo_shipment_rec:
        :return: Boolean
        """
        pickings = odoo_shipment_rec.picking_ids.filtered(
            lambda picking: picking.state == 'done' and picking.is_fba_wh_picking).sorted(key=lambda x: x.id)
        if not pickings:
            pickings = odoo_shipment_rec.picking_ids.filtered(
                lambda picking: picking.state == 'cancel' and picking.is_fba_wh_picking)
        picking = pickings and pickings[0]
        self.amz_inbound_copy_new_picking(response, instance, odoo_shipment_rec,
                                          amazon_shipment_id, picking)
        return True

    def amz_inbound_copy_new_picking(self, response, instance, odoo_shipment_rec, amazon_shipment_id, picking):
        """
        create copy of picking and stock move if quantity mismatch found from done moves and amazon received quantity.
        :param response: list(dict{})
        :param instance: amazon.instance.ept()
        :param odoo_shipment_rec:
        :param amazon_shipment_id:
        :param picking:
        :return:
        """
        picking_obj = self.env['stock.picking']
        new_picking = picking_obj
        new_items = response.get('shipmentSkuQuantities')
        shipment_container_quantities = response.get('shipmentContainerQuantities', [])
        received_qty_by_sku = {}
        for container in shipment_container_quantities:
            distribution_package = container.get('distributionPackage', {})
            if distribution_package.get('type') == 'CASE':
                products = distribution_package.get('contents', {}).get('products', [])
                for product in products:
                    sku = product.get('sku')
                    qty_per_package = product.get('quantity', 0)
                    received_qty_by_sku[sku] = received_qty_by_sku.get(sku, 0.0) + qty_per_package
        for item in new_items:
            seller_sku = item.get('sku', '')
            case_count = received_qty_by_sku.get(seller_sku, 0.0)
            received_qty = float(item.get('receivedQuantity', {}).get('quantity', 0.0))
            expectedQuantity = float(item.get('expectedQuantity', {}).get('quantity', 0.0))
            package_type = item.get('receivedQuantity', {}).get('unitOfMeasurement')
            if package_type == 'CASES':
                received_qty = received_qty * case_count
            else:
                received_qty = received_qty
            if received_qty <= 0.0:
                continue
            amazon_product = picking_obj.amz_get_awd_inbound_amazon_products_ept(instance, picking, item)
            if not amazon_product:
                continue
            odoo_product = amazon_product.product_id if amazon_product else False
            received_qty = picking_obj.amz_find_received_qty_from_done_moves(odoo_shipment_rec, odoo_product,
                                                                             received_qty, amazon_shipment_id)
            if received_qty <= 0.0:
                continue
            if not new_picking:
                picking_vals = self.amz_prepare_picking_vals_ept(picking)
                new_picking = picking.copy(picking_vals)
            move = picking.move_ids[0]
            move_vals = self.amz_prepare_awd_inbound_move_vals_ept(new_picking, odoo_product, received_qty)
            amz_new_move = move.copy(move_vals)
            self.amz_assign_and_process_new_received_move(amz_new_move, received_qty)
        return new_picking

    @staticmethod
    def amz_prepare_picking_vals_ept(picking):
        """
        Prepare vals for copy fba warehouse picking.
        :param picking: stock.picking()
        :return: dict {}
        """
        return {
            'is_fba_wh_picking': True,
            'move_ids': [],
            'group_id': False,
            'location_id': picking.location_id.id,
            'location_dest_id': picking.location_dest_id.id
        }

    @staticmethod
    def amz_prepare_awd_inbound_move_vals_ept(new_picking, odoo_product, received_qty):
        """
        Prepare move vals for inbound shipment fba warehouse stock move.
        :param new_picking: stock.picking()
        :param odoo_product: produtc.product()
        :param received_qty: float
        :return: dict {}
        """
        return {
            'picking_id': new_picking.id,
            'product_id': odoo_product.id,
            'product_uom_qty': received_qty,
            'product_uom': odoo_product.uom_id.id,
            'procure_method': 'make_to_stock',
            'group_id': False
        }

    def amz_assign_and_process_new_received_move(self, new_move, received_qty):
        """
        Define this method for validate received stock moves.
        :return: True
        """
        new_move._action_assign()
        new_move._set_quantity_done(abs(received_qty))
        new_move.picked = True
        new_move._action_done()
        return True

    def get_report_start_and_end_date(self, start_date, end_date):
        if start_date:
            try:
                db_import_time = time.strptime(str(start_date), DATE_YMDHMS)
            except Exception:
                db_import_time = time.strptime(str(start_date), "%Y-%m-%d %H:%M:%S.%f")
            db_import_time = time.strftime(DATE_YMDTHMS, db_import_time)
            start_date = time.strftime(DATE_YMDTHMS, time.gmtime(
                time.mktime(time.strptime(db_import_time, DATE_YMDTHMS))))
            start_date = str(start_date) + 'Z'
        else:
            today = datetime.now()
            earlier = today - timedelta(days=30)
            earlier_str = earlier.strftime(DATE_YMDTHMS)
            start_date = earlier_str + 'Z'

        if end_date:
            try:
                db_import_time = time.strptime(str(end_date), DATE_YMDHMS)
            except Exception:
                db_import_time = time.strptime(str(end_date), "%Y-%m-%d %H:%M:%S.%f")
            db_import_time = time.strftime(DATE_YMDTHMS, db_import_time)
            end_date = time.strftime(DATE_YMDTHMS, time.gmtime(
                time.mktime(time.strptime(db_import_time, DATE_YMDTHMS))))
            end_date = str(end_date) + 'Z'
        else:
            today = datetime.now()
            earlier_str = today.strftime(DATE_YMDTHMS)
            end_date = earlier_str + 'Z'

        return start_date, end_date

    def auto_import_awd_shipment(self, args={}):
        awd_inbound_shipment_obj = self.env['awd.inbound.shipment.ept']
        common_log_line_obj = self.env['common.log.lines.ept']
        seller_id = args.get('seller_id', False)
        if seller_id:
            seller = self.env['amazon.seller.ept'].search([('id', '=', seller_id)])
            current_date = datetime.now(timezone.utc)
            last_updated_before = current_date
            if seller.amz_last_auto_import_awd_shipment_date:
                last_sync_time = seller.amz_last_auto_import_awd_shipment_date
            else:
                last_sync_time = datetime.now(timezone.utc)
            last_updated_after = (last_sync_time - timedelta(days=30))
            instance = seller.instance_ids[0]
            from_warehouse_id = instance.warehouse_id
            ship_to_address = ''
            to_warehouse_id = seller.awd_warehouse_id
            awd_inbound_shipment_result = awd_inbound_shipment_obj.get_awd_inbound_import_shipment_data_wise(
                instance, from_warehouse_id, last_updated_after, last_updated_before, ship_to_address)
            if awd_inbound_shipment_result:
                awd_shipments = awd_inbound_shipment_result.get('shipments')
                for shipment_result in awd_shipments:
                    shipment_id = shipment_result.get('shipmentId')
                    awd_inbound_shipment_obj.get_awd_inbound_import_shipment(instance, from_warehouse_id, shipment_id,
                                                                             ship_to_address, to_warehouse_id)
            seller.amz_last_auto_import_awd_shipment_date = last_updated_before
            return True

    def auto_process_awd_shipment_report(self, args={}):
        seller_id = args.get('seller_id', False)
        if seller_id:
            seller = self.env['amazon.seller.ept'].search([('id', '=', seller_id)])
            three_days_ago = Datetime.now() - timedelta(days=3)
            rem_reports = self.search([('instance_id_ept', 'in', seller.instance_ids.ids),
                                       ('create_date', '>=', three_days_ago),])
            for report in rem_reports:
                report.check_status()
            self._cr.commit()
        return True
