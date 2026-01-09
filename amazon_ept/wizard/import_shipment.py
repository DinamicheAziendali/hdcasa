# -*- coding: utf-8 -*-
# See LICENSE file for full copyright and licensing details.
"""
Added class  and methods to request for inbound shipment and process inbound shipment response.
"""

from odoo import models, fields, _
from odoo.exceptions import UserError
from odoo.addons.iap.tools import iap_tools
from ..endpoint import DEFAULT_ENDPOINT
from dateutil import parser


class AmazonInboundImportShipmentWizard(models.TransientModel):
    """
    Added to import inbound shipment.
    """
    _name = "amazon.inbound.import.shipment.ept"
    _description = 'amazon.inbound.import.shipment.ept'

    shipment_id = fields.Char(required=True)
    instance_id = fields.Many2one('amazon.instance.ept', string='Marketplace', required=True)
    from_warehouse_id = fields.Many2one("stock.warehouse", string="From Warehouse", required=True)
    sync_product = fields.Boolean(default=True, help="Set to True to if you want before import shipment "
                                                     "automatically sync the amazon product.")

    def create_amazon_inbound_shipment_line(self, items, inbound_shipment, instance_id):
        """
        Define this method for create amazon inbound shipment lines.
        :param: items: dict of shipment details
        :param: inbound_shipment: inbound.shipment.new.ept()
        :param: instance_id: instance record
        :return:
        """
        not_exist_seller_skus = []
        amazon_inbound_shipment_plan_line_obj = self.env['inbound.shipment.line.new.ept']
        amazon_product_obj = self.env['amazon.product.ept']
        for item in items:
            seller_sku = item.get('SellerSKU', '')
            fn_sku = item.get('FulfillmentNetworkSKU', '')
            received_qty = float(item.get('QuantityShipped', 0.0))
            prep_owner = item.get('PrepDetailsList', [])[0].get('PrepOwner', '') if item.get('PrepDetailsList', []) else False
            amazon_product = amazon_product_obj.search_amazon_product(instance_id.id, seller_sku, 'FBA')
            if not amazon_product:
                amazon_product = amazon_product_obj.search([('product_asin', '=', fn_sku),
                                                            ('instance_id', '=', instance_id.id)], limit=1)
            if not amazon_product:
                not_exist_seller_skus.append(seller_sku)
                continue
            amazon_inbound_shipment_plan_line_obj.create({
                'amazon_product_id': amazon_product.id, 'seller_sku': seller_sku,
                'quantity': received_qty, 'fn_sku': fn_sku,
                'prep_owner': prep_owner, 'shipment_new_id': inbound_shipment.id})
        if not_exist_seller_skus:
            user_message = ("You will be required to map products before proceeding. Please map the following "
                            "Amazon SKUs with Odoo products and try again!\n%s" % not_exist_seller_skus)
            raise UserError(_(user_message))
        return True

    def get_list_inbound_shipment_items(self, shipment_id, instance, inbound_shipment):
        """
        Get list of shipment items from amazon to odoo
        :param: shipment_id: str
        :param: instance: amazon.instance.ept
        :param: inbound_shipment: inbound.shipment.new.ept()
        :return:
        """
        kwargs = inbound_shipment.amz_prepare_inbound_shipment_kwargs_vals(instance)
        kwargs.update({'emipro_api': 'get_shipment_items_by_shipment_sp_api_v2024',
                       'amazon_shipment_id': shipment_id})
        response = iap_tools.iap_jsonrpc(DEFAULT_ENDPOINT, params=kwargs, timeout=1000)
        if response.get('error', False):
            raise UserError(_(response.get('error', {})))
        items = response.get('result', {}).get('ItemData', {})
        return self.create_amazon_inbound_shipment_line(items, inbound_shipment, instance)

    def create_amazon_inbound_shipment(self, results, instance_id, from_warehouse_id, ship_to_address):
        """
        Method for Create Inbound Shipment which is already created in Amazon.
        :param results: [{},{}]
        :param instance_id: amazon.instance.ept() or False
        :param from_warehouse_id: from warehouse id
        :param ship_to_address: int
        :return: amazon.inbound.shipment.ept() or False
        """
        inbound_shipment = False
        # amazon_inbound_shipment_obj = self.env['amazon.inbound.shipment.ept']
        amazon_inbound_shipment_obj = self.env['inbound.shipment.new.ept']
        for result in results:
            shipment_name = result.get('ShipmentName', False)
            for date_str in shipment_name.split('('):
                shipment_date_str = date_str.split(')')[0]
                try:
                    amz_create_date = parser.parse(shipment_date_str)
                    break
                except:
                    amz_create_date = False
            shipment_id = result.get('ShipmentId', False)
            fulfillment_center_id = result.get('DestinationFulfillmentCenterId', False)
            amazon_reference_id = ''
            if not ship_to_address:
                warehouse = amazon_inbound_shipment_obj.amz_inbound_get_warehouse_ept(
                    instance_id, fulfillment_center_id)
                ship_to_address = warehouse.partner_id if warehouse.partner_id else False
            inbound_shipment = amazon_inbound_shipment_obj.create({
                'name': shipment_name, 'amazon_reference_id': amazon_reference_id,
                'shipment_confirmation_id': shipment_id,
                'ship_from_address_id': ship_to_address.id if ship_to_address else self.env.company.partner_id.id,
                'amz_inbound_create_date': amz_create_date,
                'instance_id_ept': instance_id.id, 'fulfill_center_id': fulfillment_center_id,
                'from_warehouse_id': from_warehouse_id, 'is_manually_created': True})
        return inbound_shipment

    def get_inbound_import_shipment(self, instance, warehouse_id, ship_ids, ship_to_address=False):
        """
        Import already created Inbound Shipment from shipment id and
        it will be created for given warehouse id.
        :param instance: amazon.instance.ept()
        :param warehouse_id: stock.warehouse()
        :param ship_ids: []
        :param ship_to_address: int
        :return:
        """
        amz_inbound_shipment_obj  = self.env['inbound.shipment.new.ept']
        shipment_ids = ship_ids.split(',')
        # No Need to Import Duplicate Inbound Shipment
        inbound_shipment = amz_inbound_shipment_obj.search([('shipment_confirmation_id', 'in', shipment_ids)])
        inbound_shipment_list = []
        if inbound_shipment:
            shipments = ", ".join(str(shipment.shipment_id) for shipment in inbound_shipment)
            raise UserError(_("Shipments %s already exists" % shipments))
        for shipment_id in shipment_ids:
            kwargs = amz_inbound_shipment_obj.amz_prepare_inbound_shipment_kwargs_vals(instance)
            kwargs.update({'emipro_api': 'check_status_by_shipment_ids_sp_api_v2024',
                           'shipment_ids': [shipment_id]})
            response = iap_tools.iap_jsonrpc(DEFAULT_ENDPOINT, params=kwargs, timeout=1000)
            if response.get('error', False):
                raise UserError(_(response.get('error', {})))
            amazon_shipments = response.get('amazon_shipments', [])
            inbound_shipment = self.create_amazon_inbound_shipment(amazon_shipments, instance, warehouse_id.id,
                                                                   ship_to_address)
            if not inbound_shipment:
                raise UserError(_("Inbound Shipment is not found in Amazon for shipment Id %s" % shipment_id))
            self.get_list_inbound_shipment_items(shipment_id, instance, inbound_shipment)
            inbound_shipment.create_procurements()
            if inbound_shipment:
                inbound_shipment_list.append(inbound_shipment.id)
        return inbound_shipment_list
