import base64
import logging
import time
import requests
from odoo import fields, models, api, _
from odoo.exceptions import UserError
from odoo.addons.iap.tools import iap_tools
from ..endpoint import DEFAULT_ENDPOINT
try:
    from _collections import defaultdict
except ImportError:
    pass
_logger = logging.getLogger(__name__)


class InboundShipmentNewEpt(models.Model):
    _name = 'inbound.shipment.new.ept'
    _description = 'Inbound Shipment New Ept'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    state = fields.Selection([('draft', 'Draft'), ('ABANDONED', 'ABANDONED'),
                              ('CANCELLED', 'CANCELLED'), ('CHECKED_IN', 'CHECKED_IN'),
                              ('DELETED', 'DELETED'), ('DELIVERED', 'DELIVERED'),
                              ('IN_TRANSIT', 'IN_TRANSIT'), ('MIXED', 'MIXED'),
                              ('RECEIVING', 'RECEIVING'), ('UNCONFIRMED', 'UNCONFIRMED'),
                              ('WORKING', 'WORKING'), ('READY_TO_SHIP', 'READY_TO_SHIP'),
                              ('SHIPPED', 'SHIPPED'), ('CLOSED', 'CLOSED')],
                             string='Shipment Status', default='WORKING')
    name = fields.Char(size=120, readonly=True, required=False, index=True)
    company_id = fields.Many2one('res.company', string='Inbound Shipment Company',
                                 compute="_compute_shipment_company", store=True)
    shipment_id = fields.Char(string='Provisional Shipment ID')
    shipment_plan_id = fields.Many2one('inbound.shipment.plan.new.ept', string='Shipment Plan')
    amazon_reference_id = fields.Char(size=50, help="A unique identifier created by Amazon that "
                                                    "identifies this Amazon-partnered, Less Than "
                                                    "Truckload/Full Truckload (LTL/FTL) shipment.")
    destination_address_id = fields.Many2one('res.partner', string='Destination Address')
    placement_option_id = fields.Char(string='Placement Option Id')
    shipment_confirmation_id = fields.Char(size=120, string='Shipment ID', index=True)
    ship_from_address_id = fields.Many2one('res.partner', string='Ship To Address')
    amz_inbound_create_date = fields.Date(string='Create Date', readonly=True)
    instance_id_ept = fields.Many2one("amazon.instance.ept", string="Instance")
    shipment_line_ids = fields.One2many('inbound.shipment.line.new.ept', 'shipment_new_id',
                                        string='Shipment Lines')
    active = fields.Boolean(string="Active", default=True)
    count_pickings = fields.Integer(string='Count Picking', compute='_compute_picking_count')
    picking_ids = fields.One2many('stock.picking', 'new_odoo_shipment_id', string="Pickings", readonly=True)
    is_confirm_placement_option = fields.Boolean(string="Confirm Placement Option", default=False)
    fulfill_center_id = fields.Char(size=120, string='Fulfillment Center', readonly=True,
                                    help="DestinationFulfillmentCenterId provided by Amazon, "
                                         "when we send shipment Plan to Amazon")
    from_warehouse_id = fields.Many2one("stock.warehouse", string="From Warehouse")
    is_manually_created = fields.Boolean(default=False, copy=False)
    closed_date = fields.Date(readonly=True, copy=False)
    log_ids = fields.One2many('common.log.lines.ept', compute="_compute_error_logs")
    transport_content_exported = fields.Boolean(compute="_compute_transport_content_exported")
    is_package_label_downloaded = fields.Boolean(default=False, copy=False)
    shipment_check_status = fields.Selection([('by_user', 'By User'), ('by_scheduler', 'By Scheduler')],
                                             string='Shipment Check Status', tracking=True)
    is_ltl_shipment = fields.Boolean(string="Is LTL InboundShipment?", default=False,
                                     help="LTL(Less Than Truckload/FullTruckload (LTL/FTL))")
    partnered_ltl_id = fields.Many2one('res.partner', string='contact Information',
                                       help="Contact information for the person in your organization who is responsible for the shipment.Used by the carrier if they have questions about the shipment.")
    new_partnered_ltl_ids = fields.One2many('stock.quant.package', 'partnered_ltl_new_shipment_id',
                                            string='Partnered LTL')
    seller_freight_class = fields.Selection(
        [('NONE', 'NONE'), ('FC_50', 'FC_50'), ('FC_55', 'FC_55'), ('FC_60', 'FC_60'), ('FC_65', 'FC_65'),
         ('FC_70', 'FC_70'), ('FC_77_5', 'FC_77_5'), ('FC_85', 'FC_85'), ('FC_92_5', 'FC_92_5'), ('FC_100', 'FC_100'),
         ('FC_110', 'FC_110'), ('FC_125', 'FC_125'), ('FC_150', 'FC_150'), ('FC_175', 'FC_175'), ('FC_200', 'FC_200'),
         ('FC_250', 'FC_250'), ('FC_300', 'FC_300'), ('FC_400', 'FC_400'), ('FC_500', 'FC_500')],
        string="Seller Freight Class")
    seller_declared_value = fields.Float(digits=(16, 2), string="Seller Declare Value ")
    declared_value_currency_id = fields.Many2one('res.currency', string="Declare Value Currency")
    is_pallet_label_downloaded = fields.Boolean(default=False, copy=False)

    @api.depends('instance_id_ept')
    def _compute_shipment_company(self):
        """
        Compute the company_id for the inbound shipment
        """
        for record in self:
            company_id = record.shipment_plan_id.company_id.id if record.shipment_plan_id else (
                record.instance_id_ept.company_id.id) if record.instance_id_ept else self.env.company.id
            record.company_id = company_id

    def _compute_transport_content_exported(self):
        """
        Define this method for compute transport_content_exported or not.
        :return:
        """
        for record in self:
            if record.shipment_plan_id and record.shipment_plan_id.is_confirm_transportation_options and record.shipment_plan_id.is_confirm_delivery_window_options:
                record.transport_content_exported = True
            else:
                record.transport_content_exported = False

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
            'name': 'Inbound Pickings',
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

    def unlink(self):
        """
        Inherited unlink method to do not allow to delete shipment of which shipment plan is
        approved and which shipment is not in cancelled and deleted.
        """
        for shipment in self:
            if shipment.shipment_plan_id and shipment.shipment_plan_id.state == 'plan_approved':
                raise UserError(_('You cannot delete Inbound Shipment.'))
            if shipment.instance_id_ept and shipment.state not in ['CANCELLED', 'DELETED']:
                raise UserError(_('You cannot delete Inbound Shipment.'))
        return super(InboundShipmentNewEpt, self).unlink()

    def _compute_error_logs(self):
        """
        Define method to get shipment error logs.
        """
        log_line_obj = self.env['common.log.lines.ept']
        log_lines = log_line_obj.amz_find_mismatch_details_log_lines(self.id, 'inbound.shipment.new.ept')
        self.log_ids = log_lines.ids if log_lines else False

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
        proc_group = proc_group_obj.create({'new_odoo_shipment_id': self.id, 'name': self.name,
                                            'partner_id': self.ship_from_address_id.id})
        instance = self.instance_id_ept
        warehouse = self.amz_inbound_get_warehouse_ept(instance, self.fulfill_center_id)
        if warehouse:
            location_routes = self.amz_find_location_routes_ept(warehouse)
            group_wh_dict.update({proc_group: warehouse})
            for line in self.shipment_line_ids:
                qty = line.quantity
                product_id = line.amazon_product_id.product_id
                datas = self.amz_inbound_prepare_procure_datas(location_routes, proc_group,
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
        awd_out_pickings = self.auto_validate_awd_out_picking()
        pickings = self.mapped('picking_ids').filtered(lambda pick: not pick.is_fba_wh_picking and
                                                       pick.state not in ['done', 'cancel'])
        if awd_out_pickings and pickings:
            pickings = pickings - awd_out_pickings
        for picking in pickings:
            picking.action_assign()
        return True

    def amz_inbound_get_warehouse_ept(self, instance, fulfill_center_id):
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
        warehouse = fulfillment_center and fulfillment_center.warehouse_id or instance.fba_warehouse_id or instance.warehouse_id or False
        if not warehouse:
            error_value = ('Warehouse not found for Fulfillment Center: %s during inbound shipment %s.\n'
                           'Action items:\n'
                           '-Configure the Fulfillment Center under: Amazon → Configuration → Fulfillment Centers.\n'
                           '-Set the warehouse and re-import the shipment using the operation wizard.') % (
                              fulfill_center_id, self.name)
            log_line_obj.create_common_log_line_ept(
                message=error_value, model_name='inbound.shipment.new.ept',
                module='amazon_ept', operation_type='export',
                res_id=self.id, amz_instance_ept=instance and instance.id or False,
                amz_seller_ept=instance.seller_id and instance.seller_id.id or False)
        return warehouse

    def amz_find_location_routes_ept(self, warehouse):
        """
        Find Location routes from warehouse.
        :param warehouse: stock.warehouse()
        :return: stock.location.route()
        """
        log_line_obj = self.env['common.log.lines.ept']
        location_route_obj = self.env['stock.route']
        # handle supplier_wh_id for shipment plan and import shipment without shipment plan
        supplier_wh_id = self.shipment_plan_id.warehouse_id.id if self.shipment_plan_id.warehouse_id else self.from_warehouse_id.id
        location_routes = location_route_obj.search([('supplied_wh_id', '=', warehouse.id),
                                                     ('supplier_wh_id', '=', supplier_wh_id)], limit=1)
        if not location_routes:
            error_value = ('Cannot generate transfer, routes not found for warehouse: %s (Shipment: %s).\n'
                           'Action items:\n'
                           '-Check warehouse routes.\n'
                           '-Update routing configuration and re-import the shipment.') % (warehouse.name, self.name)
            log_line_obj.create_common_log_line_ept(message=error_value, model_name='inbound.shipment.new.ept',
                                                    module='amazon_ept', operation_type='export', res_id=self.id)
        return location_routes

    @staticmethod
    def amz_inbound_prepare_procure_datas(location_routes, proc_group, instance, warehouse):
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
            'company_id': instance.company_id.id,
            'warehouse_id': warehouse,
            'priority': '1'
        }

    def confirmation_placement_option_sp_api_v2024(self):
        """
        Define this method for confirm placement option in the Amazon.
        :return: True
        """
        kwargs = self.shipment_plan_id.prepare_kwargs_for_create_inbound_plan_sp_api_v2024(
            self.instance_id_ept, 'confirmation_placement_option_sp_api_v2024')
        kwargs.update({'inboundPlanId': self.shipment_plan_id.inbound_plan_id,
                       'placementOptionId':self.placement_option_id})
        response = iap_tools.iap_jsonrpc(DEFAULT_ENDPOINT, params=kwargs, timeout=1000)
        if response.get('error', {}):
            raise UserError(_(response.get('error', {})))
        self.shipment_plan_id.amz_create_inbound_shipment_check_status(
            self.shipment_plan_id.id, 'confirm_placement_options',
            response.get('result', {}).get('operationId', ''))
        self.write({'is_confirm_placement_option': True})
        self.amz_get_shipment_for_shipment_confirmation_id()
        return True

    def amz_get_shipment_for_shipment_confirmation_id(self):
        """
        Define this method for get shipment details to update shipment confirmation id in the
        shipment to perform check status process.
        :return:
        """
        kwargs = self.shipment_plan_id.prepare_kwargs_for_create_inbound_plan_sp_api_v2024(
            self.instance_id_ept, 'get_shipment_data_sp_api_v2024')
        kwargs.update({'inboundPlanId': self.shipment_plan_id.inbound_plan_id, 'shipmentId': self.shipment_id})
        response = iap_tools.iap_jsonrpc(DEFAULT_ENDPOINT, params=kwargs, timeout=1000)
        if response.get('error', {}):
            raise UserError(_(response.get('error', {})))
        shipment_confirm_id = response.get('result', {}).get('shipmentConfirmationId', '')
        if not shipment_confirm_id:
            raise UserError(_("Shipment was not confirmed in the Amazon seller central, "
                              "please check the shipment status in the seller central."))
        self.write({'shipment_confirmation_id': shipment_confirm_id})
        self.create_procurements()
        return True

    def check_status(self):
        """
        Check status of Shipment from amazon and update in Odoo as per response of Amazon.
        :return:True
        """
        instance_shipment_ids = defaultdict(list)
        for shipment in self:
            pickings = shipment.picking_ids.filtered(lambda x:x.picking_type_id.code == 'outgoing')
            for picking in pickings:
                if picking.state != 'done':
                    raise UserError(_("Shipment Status is not update due to outgoing picking not in done state for shipment %s") % (shipment.name))
            if not shipment.shipment_confirmation_id:
                continue
            instance = shipment.get_instance()
            instance_shipment_ids[instance].append(str(shipment.shipment_confirmation_id))
        for instance, shipment_ids in instance_shipment_ids.items():
            kwargs = self.amz_prepare_inbound_shipment_kwargs_vals(instance)
            kwargs.update({'emipro_api': 'check_status_by_shipment_ids_sp_api_v2024',
                           'shipment_ids': shipment_ids})
            response = iap_tools.iap_jsonrpc(DEFAULT_ENDPOINT, params=kwargs, timeout=1000)
            if response.get('error', {}):
                raise UserError(_(response.get('error', {})))
            _logger.info("Shipment Response : %s" % (response))
            amazon_shipments = response.get('amazon_shipments', [])
            self.amz_check_status_process_shipments(amazon_shipments, instance)
        return True

    def amz_check_status_process_shipments(self, amazon_shipments, instance):
        """
        Define this method for get shipment items using shipment id.
        :Note: need to update as per old flow
        :param amazon_shipments: shipment response
        :param instance: instance: amazon.instance.ept()
        :return:
        """
        stock_picking_obj = self.env['stock.picking']
        for ship_member in amazon_shipments:
            flag = False
            shipment_id = ship_member.get('ShipmentId', '')
            shipment_status = ship_member.get('ShipmentStatus', '')
            odoo_shipment_rec = self.search([('shipment_confirmation_id', '=', shipment_id)])
            already_returned = False
            if shipment_status in ['RECEIVING', 'CLOSED']:
                kwargs = self.amz_prepare_inbound_shipment_kwargs_vals(instance)
                kwargs.update({'emipro_api': 'get_shipment_items_by_shipment_sp_api_v2024',
                               'amazon_shipment_id': shipment_id})
                response = iap_tools.iap_jsonrpc(DEFAULT_ENDPOINT, params=kwargs, timeout=1000)
                if response.get('error', False):
                    raise UserError(_(response.get('error', {})))
                pickings = odoo_shipment_rec.mapped('picking_ids').filtered(
                    lambda r: r.state in ['assigned'] and r.is_fba_wh_picking)
                if pickings:
                    pickings.check_amazon_shipment_status_v2024(response)
                    backorders = odoo_shipment_rec.picking_ids.filtered(
                        lambda picking: picking.state in ('waiting', 'confirmed') and picking.is_fba_wh_picking)
                    if backorders:
                        backorders.action_assign()
                        backorders.write({'shipment_pick_done_by': 'by_user'})
                    odoo_shipment_rec.write({'state': shipment_status})
                    stock_picking_obj.check_qty_difference_and_create_return_picking_v2024(
                        response, shipment_id, odoo_shipment_rec.id, instance)
                    already_returned = True
                else:
                    if odoo_shipment_rec:
                        pickings = odoo_shipment_rec.mapped('picking_ids').filtered(
                            lambda r: r.state in ['draft', 'waiting', 'confirmed'] and r.is_fba_wh_picking)
                        if pickings:
                            pickings = self.amz_cancel_waiting_state_pickings(pickings, odoo_shipment_rec)
                    if not pickings:
                        flag = False
                        self.get_remaining_qty(response, instance, shipment_id, odoo_shipment_rec)
                        odoo_shipment_rec.write({'state': shipment_status})
                    else:
                        raise UserError(_("""Shipment Status is not update due to picking not found
                                             for processing  ||| Amazon status : %s ERP status : %s
                                             """ % (shipment_status, odoo_shipment_rec.state)))
                if shipment_status == 'CLOSED':
                    if not flag:
                        self.get_remaining_qty(response, instance, shipment_id, odoo_shipment_rec)
                    if not odoo_shipment_rec.closed_date:
                        odoo_shipment_rec.write({'closed_date': time.strftime("%Y-%m-%d")})
                    if odoo_shipment_rec:
                        pickings = odoo_shipment_rec.mapped('picking_ids').filtered(
                            lambda r: r.state not in ['done', 'cancel'] and r.is_fba_wh_picking)
                    if pickings:
                        pickings.action_cancel()
                        pickings.write({'shipment_pick_done_by': 'by_user'})
                    if not already_returned:
                        stock_picking_obj.check_qty_difference_and_create_return_picking_v2024(
                            response, shipment_id, odoo_shipment_rec.id, instance)
            else:
                odoo_shipment_rec.write({'state': shipment_status})
            odoo_shipment_rec.write({'shipment_check_status': 'by_user'})
        return True

    @staticmethod
    def amz_cancel_waiting_state_pickings(pickings, odoo_shipment_rec):
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
        for item in response.get('result', {}).get('ItemData', {}):
            received_qty = float(item.get('QuantityReceived', 0.0))
            if received_qty <= 0.0:
                continue
            amazon_product = picking_obj.amz_get_inbound_amazon_products_ept(instance, picking, item)
            if not amazon_product:
                continue
            picking_obj.amz_inbound_shipment_plan_line_ept_v2024(odoo_shipment_rec, amazon_product, item)
            odoo_product = amazon_product.product_id if amazon_product else False
            received_qty = picking_obj.amz_find_received_qty_from_done_moves(odoo_shipment_rec, odoo_product,
                                                                             received_qty, amazon_shipment_id)
            if received_qty <= 0.0:
                continue
            if not new_picking:
                picking_vals = self.amz_prepare_picking_vals_ept(picking)
                new_picking = picking.copy(picking_vals)
                picking_obj.amz_create_attachment_for_picking_datas_v2024(
                    response.get('result', {}).get('datas', {}), new_picking)
            move = picking.move_ids[0]
            move_vals = self.amz_prepare_inbound_move_vals_ept(new_picking, odoo_product, received_qty)
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
            'location_dest_id': picking.location_dest_id.id,
            'shipment_pick_done_by': 'by_user'
        }

    @staticmethod
    def amz_prepare_inbound_move_vals_ept(new_picking, odoo_product, received_qty):
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
        if self.instance_id_ept.seller_id.allow_negative_stock or new_move.state == 'assigned':
            new_move._set_quantity_done(abs(received_qty))
            new_move.picked = True
            new_move._action_done()
        return True

    def get_instance(self):
        """
        The method will return the instance of inbound shipment.
        :param shipment: amazon.inbound.shipment.ept()
        :return: amazon.instance.ept()
        """
        if self.instance_id_ept:
            return self.instance_id_ept
        return self.shipment_plan_id.instance_id

    def amz_prepare_inbound_shipment_kwargs_vals(self, instance):
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

    def get_package_labels(self):
        """
        Define this method for get package labels for SPD parcel.
        :return: ir.actions.act_window()
        """
        view = self.env.ref('amazon_ept.amazon_new_inbound_shipment_print_unique_label_wizard_form_view',
                            raise_if_not_found=False)
        return {
            'name': _('Labels'),
            'type': 'ir.actions.act_window',
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'amazon.new.shipment.label.wizard',
            'view_id': view.id,
            'nodestroy': True,
            'target': 'new'
        }

    def get_bill_of_lading(self):
        """
        Define this method to get bill of lading and process to create attachment of response.
        :return: True
        """
        self.ensure_one()
        instance = self.get_instance()
        kwargs = self.amz_prepare_inbound_shipment_kwargs_vals(instance)
        kwargs.update({'emipro_api': 'get_bill_of_lading_sp_api_v2024', 'shipment_id': self.shipment_confirmation_id})
        response = iap_tools.iap_jsonrpc(DEFAULT_ENDPOINT, params=kwargs, timeout=1000)
        if response.get('error', False):
            raise UserError(_(response.get('error', {})))
        result = response.get('result', {})
        document_url = result.get('DownloadURL', '')
        if not document_url:
            raise UserError(_('The Bill of Lading for your Amazon Shipment is currently unavailable'
                              ' as it has not been generated yet. We suggest trying again after a few hours.'))
        document = requests.get(document_url)
        if document.status_code != 200:
            return True
        datas = base64.b64encode(document.content)
        name = document.request.path_url.split('/')[-1].split('?')[0]
        bill_of_lading = self.env['ir.attachment'].create({
            'name': name,
            'datas': datas,
            'res_model': self._name,
            'res_id': self.id,
            'type': 'binary'
        })
        self.message_post(body=_("Bill Of Lading Downloaded"), attachment_ids=bill_of_lading.ids)
        self.write({'is_package_label_downloaded': True})
        return True

    def check_status_ept_v2024(self, amazon_shipments, seller):
        """
        method is used to check amazon shipment status.
        """
        log_line_obj = self.env['common.log.lines.ept']
        instance = seller.instance_ids
        if amazon_shipments:
            for key, amazon_shipment in amazon_shipments.items():
                shipmentid = key
                shipment = self.search([('shipment_confirmation_id', '=', shipmentid),
                                        ('instance_id_ept', 'in', instance.ids)])
                if shipment:
                    pickings = shipment.picking_ids.filtered(
                        lambda picking: picking.state in ('partially_available', 'assigned') and picking.is_fba_wh_picking)
                    if pickings:
                        pickings.check_amazon_shipment_status_ept_v2024(amazon_shipment)
                        self.amz_create_back_orders_and_check_return_picking_v2024(
                            shipment, shipmentid, amazon_shipment)
                    else:
                        pickings = shipment.picking_ids.filtered(lambda picking: picking.state in (
                            'draft', 'waiting', 'confirmed') and picking.is_fba_wh_picking)
                        self.amz_process_remaining_qty_v2024(pickings, shipment, shipmentid, amazon_shipment)
                    shipment.write({'shipment_check_status': 'by_scheduler'})
                else:
                    message = "Shipment %s is not found in ERP" % (shipmentid)
                    log_line_obj.create_common_log_line_ept(
                        message=message, model_name='inbound.shipment.new.ept', module='amazon_ept',
                        operation_type='import', amz_seller_ept=seller and seller.id or False)
        return True

    def amz_create_back_orders_and_check_return_picking_v2024(self, shipment, shipment_id, amazon_shipment):
        """
        Define this method for process and create return pickings.
        :param: shipment: inbound.shipment.new.ept()
        :param: shipment_id: str
        :param: amazon_shipment: list of shipment items
        :return:
        """
        stock_picking_obj = self.env['stock.picking']
        backorders = shipment.picking_ids.filtered(
            lambda picking: picking.state in ('waiting', 'confirmed') and picking.is_fba_wh_picking)
        if backorders:
            backorders.action_assign()
            backorders.write({'shipment_pick_done_by': 'by_scheduler'})
        stock_picking_obj.check_qty_difference_and_create_return_picking_ept_v2024(
            shipment_id, shipment, shipment.instance_id_ept, amazon_shipment)
        return True

    def amz_process_remaining_qty_v2024(self, pickings, shipment, shipment_id, amazon_shipment):
        """
        :param: pickings:
        :param: shipment:
        :param: shipment_id:
        :param: amazon_shipment:
        :return:
        """
        instance = self.get_instance()
        log_line_obj = self.env['common.log.lines.ept']
        if pickings:
            pickings = self.amz_cancel_waiting_state_pickings(pickings, shipment)
        if not pickings:
            self.get_remaining_qty_ept_v2024(shipment.instance_id_ept, shipment_id, shipment, amazon_shipment)
        else:
            message = "Shipment Status %s is not update due to picking not found for processing " \
                      "||| ERP status  : %s " % (shipment_id, shipment.state)
            log_line_obj.create_common_log_line_ept(
                message=message, model_name='inbound.shipment.new.ept', module='amazon_ept', operation_type='import',
                res_id=shipment.id, amz_instance_ept=instance and instance.id or False,
                amz_seller_ept=instance.seller_id and instance.seller_id.id or False)
        return True

    def get_remaining_qty_ept_v2024(self, instance, amazon_shipment_id, odoo_shipment_rec, items):
        """
        This method is used to get remaining qty for the shipment and process it in the odoo using scheduler.
        :param: instance : instance record
        :param: amazon_shipment_id: amazon shipment
        :param: odoo_shipment_rec : odoo shipment
        param :items: items
        """
        new_picking = False
        pickings = odoo_shipment_rec.picking_ids.filtered(
            lambda picking: picking.state == 'done' and picking.is_fba_wh_picking).sorted(key=lambda x: x.id)
        picking = pickings and pickings[0]
        if not picking:
            pickings = odoo_shipment_rec.picking_ids.filtered(
                lambda picking: picking.state == 'cancel' and picking.is_fba_wh_picking)
            picking = pickings and pickings[0]
        if picking:
            for item in items:
                received_qty = float(item.get('QuantityReceived', 0.0))
                if received_qty <= 0.0:
                    continue
                amazon_product = picking.amz_get_inbound_amazon_products_ept(instance, picking, item)
                if not amazon_product:
                    continue
                picking.amz_inbound_shipment_plan_line_ept_v2024(odoo_shipment_rec, amazon_product, item)
                odoo_product = amazon_product.product_id if amazon_product else False
                received_qty = picking.amz_find_received_qty_from_done_moves(
                    odoo_shipment_rec, odoo_product, received_qty, amazon_shipment_id)
                if received_qty <= 0.0:
                    continue
                if not new_picking:
                    new_picking = picking.copy({'is_fba_wh_picking': True, 'move_ids': [], 'group_id': False,
                                                'location_id': picking.location_id.id,
                                                'location_dest_id': picking.location_dest_id.id,
                                                'shipment_pick_done_by': 'by_scheduler'})
                move = picking.move_ids[0]
                amz_new_move = move.copy({'picking_id': new_picking.id,
                                          'product_id': odoo_product.id,
                                          'product_uom_qty': received_qty,
                                          'product_uom': odoo_product.uom_id.id,
                                          'procure_method': 'make_to_stock',
                                          'group_id': False})
                self.amz_assign_and_process_new_received_move(amz_new_move, received_qty)
        return True

    def get_shipment_report_data(self):
        """
        This method retrieves the inbound shipment data for the corresponding inbound shipment and
        invoke the other method to create or update inbound shipment report data.
        """
        inbound_ship_report_data_ept_obj = self.env['inbound.shipment.report.data.ept']
        inbound_ship_report_data = []
        instance = self.get_instance()
        kwargs = self.amz_prepare_inbound_shipment_kwargs_vals(instance)
        kwargs.update({'emipro_api': 'check_status_by_shipment_ids_sp_api_v2024',
                       'shipment_ids': [str(self.shipment_confirmation_id)]})
        response = iap_tools.iap_jsonrpc(DEFAULT_ENDPOINT, params=kwargs, timeout=1000)
        if response.get('error', {}):
            raise UserError(_(response.get('error', {})))
        shipment_detail = response.get('amazon_shipments', [])
        for ship in shipment_detail:
            shipment_id = ship.get('ShipmentId', '')
            kwargs = self.amz_prepare_inbound_shipment_kwargs_vals(instance)
            kwargs.update({'emipro_api': 'get_shipment_items_by_shipment_sp_api_v2024',
                           'amazon_shipment_id': shipment_id})
            response = iap_tools.iap_jsonrpc(DEFAULT_ENDPOINT, params=kwargs, timeout=1000)
            if response.get('error', False):
                raise UserError(f"An error Occurred during the Get Shipment Report Data: "
                                f"{str(response.get('error', {}))}")
            items_data = []
            items = response.get('result', {}).get('ItemData', [])
            for item in items:
                seller_sku = item.get('SellerSKU', '')
                qty_received = item.get('QuantityReceived', 0)
                qty_shipped = item.get('QuantityShipped', 0)
                items_data.append(
                    {'seller_sku': seller_sku, 'qty_received': qty_received, 'qty_shipped': qty_shipped})
            inbound_ship_report_data.append({'shipment_id': shipment_id, 'items': items_data,
                                             'status': ship.get('ShipmentStatus')})
            if inbound_ship_report_data:
                inbound_ship_report_data_ept_obj.create_shipment_report_data(inbound_ship_report_data,
                                                                                 instance.seller_id.id, 'new')
        return True

    def auto_validate_awd_out_picking(self):
        """
        Define this method for auto validate AWD out pickings which fully available in the odoo in the
        respective location.
        :return: stock.picking()
        """
        awd_out_pickings = self.mapped('picking_ids').filtered(
            lambda pick: not pick.is_fba_wh_picking and pick.state not in [
                'done',
                'cancel'] and pick.picking_type_id.warehouse_id and pick.picking_type_id.warehouse_id.is_awd_warehouse
                         and pick.picking_type_id.warehouse_id.is_auto_confirm_awd_picking)
        for pick in awd_out_pickings:
            if self.check_awd_out_picking_fully_available(pick):
                pick.action_assign()
                pick.with_context(skip_sms=True).button_validate()
            else:
                pick.message_post(body=_("The AWD picking was not validated automatically because the "
                                         "required quantity was not fully available in stock."))
        return awd_out_pickings

    @staticmethod
    def check_awd_out_picking_fully_available(pick):
        """
        Define this method for check that out picking is fully available.
        :param: pick: stock.picking()
        :return: True/False
        """
        is_valid_warehouse = True
        location_id = pick.location_id.id
        for move in pick.move_ids:
            available_qty = move.product_id.with_context(location=location_id).free_qty
            if available_qty < move.product_uom_qty:
                is_valid_warehouse = False
        return is_valid_warehouse
