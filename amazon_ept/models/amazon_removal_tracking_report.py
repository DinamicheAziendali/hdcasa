# -*- coding: utf-8 -*-
# See LICENSE file for full copyright and licensing details.
import base64
import csv
import time
from io import StringIO
from datetime import datetime, timedelta
from odoo.exceptions import UserError
from odoo import models, fields, api, _
from odoo.tools import float_round, float_compare
import logging

_logger = logging.getLogger(__name__)
AMZ_SELLER_EPT = 'amazon.seller.ept'
DATE_YMDHMS = "%Y-%m-%d %H:%M:%S"
IR_MODEL = 'ir.model'
AMZ_REMOVAL_TRACKING_REPORT_HISTORY = 'amazon.removal.tracking.report.history'
AMZ_REMOVAL_TRANSFER_EPT = 'amazon.removal.transfer.ept'


class AmazonRemovalTrackingReportHistory(models.Model):
    """
    Added class to import and process removal order report.
    """
    _name = "amazon.removal.tracking.report.history"
    _description = "Removal Tracking Report"
    _inherit = ['mail.thread', 'amazon.reports']
    _order = 'id desc'

    @api.depends('seller_id')
    def _compute_removal_company(self):
        """
        This will set the company in removal order report.
        """
        for record in self:
            company_id = record.seller_id.company_id.id if record.seller_id else False
            if not company_id:
                company_id = self.env.company.id
            record.company_id = company_id

    name = fields.Char(size=256, help="This Field relocates removal tracking report name.")
    state = fields.Selection([('draft', 'Draft'), ('SUBMITTED', 'SUBMITTED'),
                              ('_SUBMITTED_', 'SUBMITTED'), ('IN_QUEUE', 'IN_QUEUE'),
                              ('IN_PROGRESS', 'IN_PROGRESS'), ('_IN_PROGRESS_', 'IN_PROGRESS'),
                              ('DONE', 'DONE'), ('_DONE_', 'DONE'), ('_DONE_NO_DATA_', 'DONE_NO_DATA'),
                              ('FATAL', 'FATAL'), ('partially_processed', 'Partially Processed'),
                              ('processed', 'PROCESSED'), ('CANCELLED', 'CANCELLED'),
                              ('_CANCELLED_', 'CANCELLED')], string='Report Status', default='draft',
                             help="This Field relocates state of removal Tracking report process.")
    seller_id = fields.Many2one(AMZ_SELLER_EPT, string='Seller', copy=False,
                                help="Select Seller id from you wanted to get Shipping report")
    attachment_id = fields.Many2one('ir.attachment', string="Attachment",
                                    help="This Field relocates attachment id.")
    instance_id = fields.Many2one("amazon.instance.ept", string="Marketplace",
                                  help="This Field relocates instance")
    removal_picking_ids = fields.One2many("stock.picking", 'removal_transfer_report_id',
                                          string="Pickings",
                                          help="This Field relocates removal picking ids.")
    removal_count = fields.Integer(compute="_compute_removal_pickings",
                                   help="This Field relocates removal count.")
    report_id = fields.Char(size=256, string='Report ID', help="This Field relocates report id.")
    report_type = fields.Char(size=256, help='This Field relocates report type.')
    report_request_id = fields.Char(string='Report Request ID', readonly=True,
                                    help="This Field relocates report request id of amazon.")
    report_document_id = fields.Char(string='Report Document ID',
                                     help="Report Document id to recognise unique request document reference")
    start_date = fields.Datetime(help="Report Start Date")
    end_date = fields.Datetime(help="Report End Date")
    tracking_requested_date = fields.Datetime(default=time.strftime(DATE_YMDHMS),
                                              help="Report Requested Date")
    user_id = fields.Many2one('res.users', string="Requested User",
                              help="Track which odoo user has requested report")
    company_id = fields.Many2one('res.company', string="Company", copy=False,
                                 compute="_compute_removal_company",
                                 store=True, help="This Field relocates company")
    log_count = fields.Integer(compute="_compute_logs_record")
    mismatch_details = fields.Boolean(compute="_compute_logs_record")
    removal_move_type = fields.Selection([
        ('direct', 'As soon as possible'), ('one', 'When all products are ready')], 'Shipping Policy', default='one',
        required=True)

    def unlink(self):
        """
        This Method if report is processed then raise UserError.
        """
        for report in self:
            if report.state == 'processed':
                raise UserError(_('You cannot delete processed report.'))
        return super(AmazonRemovalTrackingReportHistory, self).unlink()

    @api.model
    def default_get(self, fields):
        """
        Inherited to update the report type.
        :return: list []
        """
        res = super(AmazonRemovalTrackingReportHistory, self).default_get(fields)
        if not fields:
            return res
        res.update({'report_type': 'GET_FBA_FULFILLMENT_REMOVAL_SHIPMENT_DETAIL_DATA', })
        return res

    def _compute_removal_pickings(self):
        """
        This method will count the number of removal pickings.
        """
        for record in self:
            record.removal_count = len(record.removal_picking_ids.ids)

    def _compute_logs_record(self):
        """
        This method will count the number log removal order report logs.
        """
        log_line_obj = self.env['common.log.lines.ept']
        model_id = self.env[IR_MODEL]._get(AMZ_REMOVAL_TRACKING_REPORT_HISTORY).id
        log_ids = log_line_obj.search([('res_id', '=', self.id), ('model_id', '=', model_id)]).ids
        self.log_count = log_ids.__len__()

        # Set the boolean field mismatch_details as True if found any mismatch details in log lines
        if log_line_obj.search_count([('res_id', '=', self.id), ('model_id', '=', model_id),
                                      ('mismatch_details', '=', True)]):
            self.mismatch_details = True
        else:
            self.mismatch_details = False

    @api.model_create_multi
    def create(self, vals_list):
        """
        The below method sets name of a particular record as per the sequence.
        :param: vals_list: list of values []
        :return: amazon.removal.order.report.history() object
        """
        for vals in vals_list:
            sequence = self.env.ref('amazon_ept.seq_removal_tracking_report_job', raise_if_not_found=False)
            report_name = sequence.next_by_id() if sequence else '/'
            vals.update({'name': report_name})
        return super(AmazonRemovalTrackingReportHistory, self).create(vals_list)

    def create_amazon_report_attachment(self, result):
        """
        Get Removal Orders Report as an attachment in Removal Orders Reports form view.
        """
        seller = self.seller_id
        result = result.get('document', '')
        result = result.encode()
        result = base64.b64encode(result)
        file_name = "Removal_Tracking_Report_" + time.strftime("%Y_%m_%d_%H%M%S") + '.csv'
        attachment = self.env['ir.attachment'].create({
            'name': file_name,
            'datas': result,
            'res_model': 'mail.compose.message',
            'type': 'binary'
        })
        self.message_post(body=_("Removal Order Report Downloaded"), attachment_ids=attachment.ids)
        self.write({'attachment_id': attachment.id})
        seller.write({'removal_order_report_last_sync_on': datetime.now()})

    def process_removal_tracking_report(self):
        """
        This Method relocates process removal order report.
         - read csv file and process removal order report.
         - create order if order not found in odoo then create.
         - Check amazon removal order exist order in not in ERP.
         - If disposal line dict or return line dict found then process removal lines.
         - System processed the Pending Order from Removal Order Report file.
        :return: boolean.
        """
        log_line_obj = self.env['common.log.lines.ept']
        model_id = self.env[IR_MODEL]._get(AMZ_REMOVAL_TRACKING_REPORT_HISTORY).id
        self.ensure_one()
        self.check_removal_order_configuration()
        if not self.seller_id.instance_ids.filtered(lambda l: l.is_allow_to_create_removal_order):
            if not self._context.get('is_auto_process', False):
                raise UserError(_('Please Enable Removal order configuration'))
            log_line_obj.create_common_log_line_ept(
                message='Please Enable Removal order configuration', model_name=AMZ_REMOVAL_TRACKING_REPORT_HISTORY,
                module='amazon_ept', operation_type='import', res_id=self.id, mismatch_details=True,
                amz_seller_ept=self.seller_id and self.seller_id.id or False,
                amz_instance_ept=self.instance_id and self.instance_id.id or False)
            return False
        if log_line_obj.amz_find_mismatch_details_log_lines(self.id, AMZ_REMOVAL_TRACKING_REPORT_HISTORY):
            log_line_obj.amz_find_mismatch_details_log_lines(self.id, AMZ_REMOVAL_TRACKING_REPORT_HISTORY).unlink()
        imp_file = StringIO(base64.b64decode(self.attachment_id.datas).decode())
        reader = csv.DictReader(imp_file, delimiter='\t')
        disposal_line_dict, return_line_dict, order_dict, liquidations_line_dict = {}, {}, {}, {}
        # check report headers if header is not in english the raise waring
        if self.amz_check_report_required_headers('order-id', reader.fieldnames,
                                                  self._context.get('is_auto_process', False)):
            return True
        for row in reader:
            if not row.get('removal-order-type', '') or row.get('removal-order-type', '') == 'removal-order-type':
                continue
            # added the datelogic
            if self.seller_id.removal_tracking_report_process_after_date:
                report_shipment_date = row.get('shipment-date', '')
                shipment_date = datetime.fromisoformat(report_shipment_date).date()
                removal_report_process_after_date = self.seller_id.removal_tracking_report_process_after_date
                if shipment_date < removal_report_process_after_date:
                    continue
            # added the datelogic
            order_id = row.get('order-id', '')
            tracking_number = row.get('tracking-number', '')
            order_key = order_id + "___" + tracking_number
            existing_picking, skip_line = self.check_amazon_tracking_picking_exist_order_not(row)
            if not existing_picking:
                order_dict.get(order_key).append(
                    row) if order_key in order_dict else order_dict.update({order_key: [row]})
        if order_dict:
            existing_order, disposal_line_dict, return_line_dict, liquidations_line_dict = self.create_picking_if_not_found_in_odoo(
                order_dict, disposal_line_dict, return_line_dict, liquidations_line_dict)
        if disposal_line_dict or return_line_dict or liquidations_line_dict:
            self.process_removal_lines(disposal_line_dict, return_line_dict, liquidations_line_dict)
        is_partially_processed_report = log_line_obj.search_count([
            ('res_id', '=', self.id), ('model_id', '=', model_id), ('mismatch_details', '=', True)])
        state = 'partially_processed' if is_partially_processed_report else 'processed'
        self.write({'state': state})
        return True

    def create_picking_if_not_found_in_odoo(self, order_dict, disposal_line_dict, return_line_dict,
                                            liquidations_line_dict):
        log_line_obj = self.env['common.log.lines.ept']
        removal_transfer = []
        for order_id, rows in list(order_dict.items()):
            order_id = order_id.split("___")[0]
            instance = self.seller_id.instance_ids.filtered(lambda l: l.is_allow_to_create_removal_order)
            if not instance:
                instance = self.seller_id.instance_ids[0]
            lines = []
            skip_lines = 0
            order_type = rows[0].get('removal-order-type', '')
            is_amazon_product = True
            for row in rows:
                amazon_product = self.get_amazon_product(row.get('sku', ''), instance)
                if not amazon_product:
                    is_amazon_product = False
                    # message = "Line is skipped due to product not found in ERP || Order ref {} ||" \
                    #           "Seller sku {} ".format(order_id, row.get('sku', ''))
                    message = 'Skipped Tracking Number %s because Product %s was not found in Odoo' % (
                    row.get('tracking-number', ''), row.get('sku', ''))
                    log_line_obj.create_common_log_line_ept(
                        message=message, model_name=AMZ_REMOVAL_TRACKING_REPORT_HISTORY, module='amazon_ept',
                        operation_type='import', res_id=self.id, mismatch_details=True,
                        amz_seller_ept=self.seller_id and self.seller_id.id or False,
                        amz_instance_ept=instance and instance.id or False)
                    continue
            if is_amazon_product:
                for row in rows:
                    amazon_product = self.get_amazon_product(row.get('sku', ''), instance)
                    lines, skip_lines = self.prepare_removal_picking_lines_vals_ept(lines, skip_lines, row,
                                                                                    amazon_product)
            if len(rows) == skip_lines:
                continue
            if lines:
                removal_transfer = self.create_removal_pickings_and_process(order_id, order_type, instance, lines)
                disposal_line_dict, return_line_dict, liquidations_line_dict = self.amz_prepare_disposal_and_removal_line_dict(
                    removal_transfer, rows, disposal_line_dict, return_line_dict, liquidations_line_dict)
        return removal_transfer, disposal_line_dict, return_line_dict, liquidations_line_dict

    def list_of_logs(self):
        """
        This method will return the removal order mismatch logs.
        """
        model_id = self.env[IR_MODEL]._get(AMZ_REMOVAL_TRACKING_REPORT_HISTORY).id
        action = {
            'domain': "[('res_id', '=', " + str(self.id) + " ), ('model_id', '=', " + str(model_id) + ")]",
            'name': 'Removal Orders Logs',
            'view_type': 'form',
            'view_mode': 'list,form',
            'res_model': 'common.log.lines.ept',
            'type': 'ir.actions.act_window',
        }
        return action

    def check_amazon_tracking_picking_exist_order_not(self, row):
        log_line_obj = self.env['common.log.lines.ept']
        stock_picking_obj = self.env['stock.picking']
        order_id = row.get('order-id', '')
        tracking_ref = row.get('tracking-number', '')
        skip_line = False
        #        _logger.info(tracking_ref)
        #        _logger.info(order_id)
        #        _logger.info(row.get('sku'))
        existing_picking = stock_picking_obj.search(
            [('origin', '=', order_id), ('carrier_tracking_ref', '=', tracking_ref)])
        if existing_picking:
            sku = row.get('sku')
            amazon_product = self.env['amazon.product.ept'].search(
                [('seller_sku', '=', sku),
                 ('fulfillment_by', '=', 'FBA')], limit=1)
            if not amazon_product:
                amazon_product = self.env['multi.sku.odoo.product.ept'].search([('seller_sku', '=', sku)])
                product_id = amazon_product.product_id.id if amazon_product else False
            else:
                product_id = amazon_product.product_id.id
            if product_id:
                moves = self.env['stock.move'].search(
                    [('product_id', '=', product_id), ('picking_id', 'in', existing_picking.ids)])
                #               _logger.info(moves)
                if moves:
                    skip_line = True
                else:
                    existing_picking = False
            else:
                existing_picking = False
        #      _logger.info(existing_picking)
        #      _logger.info(skip_line)
        return existing_picking, skip_line

    def get_amazon_product(self, sku, instance):
        """
        This Method relocates get amazon product using product sku and instance of amazon.
        :param sku: sku of removal order product
        :param instance: amazon.instance.ept()
        :return: This Method return amazon product.
        """
        amazon_product = self.env['amazon.product.ept'].search(
            [('seller_sku', '=', sku), ('instance_id', '=', instance.id),
             ('fulfillment_by', '=', 'FBA')], limit=1)
        return amazon_product

    def prepare_removal_picking_lines_vals_ept(self, lines, skip_lines, row, amazon_product):
        """
        Define method for prepare removal order line values.
        :param : lines : list()
        :param : skip_lines : removal order lines skip count
        :param : row : list(dict())
        :param : amazon_product : amazon.product.ept()
        :return : prepare list [], skip_lines count
        """
        carrier_obj = self.env['delivery.carrier']
        if row.get("removal-order-type", "") in ["Disposal", "Return", "Liquidations"] and float(
                row.get("shipped-quantity", 0.0)) <= 0.0:
            skip_lines += 1

        vals = {
            "amazon_product_id": amazon_product.id,
            "removal_disposition": row.get("removal-order-type", ""),
        }

        if row.get("disposition", "") == "Unsellable":
            vals.update({"unsellable_quantity": float(row.get("shipped-quantity", 0.0))})
        else:
            vals.update({"sellable_quantity": float(row.get("shipped-quantity", 0.0))})

        if row.get("carrier", ""):
            carrier = carrier_obj.search([('name', '=', row.get("carrier"))], limit=1)
            if carrier:
                vals.update({"carrier_id": carrier.id, 'tracking_ref': row.get("tracking-number", "")})

        vals.update({'tracking_ref': row.get("tracking-number", ""), 'carrier_name_in_file': row.get("carrier", "")})

        lines.append((0, 0, vals))

        return lines, skip_lines

    def create_removal_pickings_and_process(self, order_id, order_type, instance, lines):
        ctx = self._context.copy()
        ctx.update({'model_name_ept': AMZ_REMOVAL_TRACKING_REPORT_HISTORY})
        amazon_removal_transfer_obj = self.env[AMZ_REMOVAL_TRANSFER_EPT]
        removal_transfer_vals = self.prepare_amz_removal_transfer_vals_ept(order_id, order_type, instance, lines)
        removal_transfer = amazon_removal_transfer_obj.create(removal_transfer_vals)
        removal_transfer.write({'state': 'plan_approved'})
        if order_type == 'Return':
            pickings = removal_transfer.removal_transfer_procurements()
            pickings.write({'removal_transfer_report_id': self.id})
        if order_type == 'Disposal':
            sell_pick, unsell_pick = removal_transfer.disposal_transfer_pickings()
            if sell_pick:
                sell_pick.write({'removal_transfer_report_id': self.id})
            if unsell_pick:
                unsell_pick.write({'removal_transfer_report_id': self.id})
        if order_type == 'Liquidations':
            sell_pick, unsell_pick = removal_transfer.disposal_transfer_pickings()
            if sell_pick:
                sell_pick.write({'removal_transfer_report_id': self.id})
            if unsell_pick:
                unsell_pick.write({'removal_transfer_report_id': self.id})
        return removal_transfer

    def prepare_amz_removal_transfer_vals_ept(self, order_id, order_type, instance, lines):
        log_line_obj = self.env['common.log.lines.ept']
        try:
            ship_add_id = self.seller_id.amz_fba_liquidation_partner.id if order_type == 'Liquidations' else self.company_id.partner_id.id
        except Exception:
            message = "FBA Liquidation Partner is Missing! " \
                      "Please Configure FBA Liquidation Partner in Amazon Seller Configuration."
            if self._context.get('is_auto_process'):
                log_line_obj.create_common_log_line_ept(
                    message=message, model_name=AMZ_REMOVAL_TRACKING_REPORT_HISTORY, module='amazon_ept',
                    operation_type='import', res_id=self.id, mismatch_details=True,
                    amz_seller_ept=self.seller_id and self.seller_id.id or False,
                    amz_instance_ept=instance and instance.id or False)
            else:
                raise UserError(_(message))
        return {
            'name': order_id or '',
            'removal_disposition': order_type or '',
            'warehouse_id': instance.removal_warehouse_id.id if instance else False,
            'ship_address_id': ship_add_id,
            'company_id': self.seller_id.company_id.id,
            'instance_id': instance.id if instance else False,
            'removal_transfer_move_type': self.removal_move_type,
            'removal_transfer_lines_ids': lines or []

        }

    def amz_prepare_disposal_and_removal_line_dict(self, existing_order, rows, disposal_line_dict,
                                                   return_line_dict, liquidations_line_dict):
        """
        Prepare disposal and removal lines dictionary from file data
        :param existing_order: amazon.removal.order.ept()
        :param rows: list(dict())
        :param disposal_line_dict: dict{key: [row]}
        :param return_line_dict: dict{key: [row]}
        :return: dict{key: [row]}, dict{key: [row]}
        """
        log_line_obj = self.env['common.log.lines.ept']
        rows = [rows] if not isinstance(rows, list) else rows
        for row in rows:
            amazon_removal_order_config = existing_order.instance_id.removal_order_config_ids.filtered(
                lambda l, row=row: l.removal_disposition == row.get('removal-order-type', ''))
            if not amazon_removal_order_config:
                message = "Configuration not found for order-type {} || order-id {} ".format(
                    row.get('removal-order-type', ''), row.get('order-id', ''))
                log_line_obj.create_common_log_line_ept(
                    message=message, model_name=AMZ_REMOVAL_TRACKING_REPORT_HISTORY, module='amazon_ept',
                    operation_type='import', res_id=self.id, mismatch_details=True,
                    amz_instance_ept=self.instance_id and self.instance_id.id or False,
                    amz_seller_ept=self.seller_id and self.seller_id.id or False)
            else:
                key = (existing_order.id, amazon_removal_order_config.id)
                disposal_line_dict, return_line_dict, liquidations_line_dict = self.update_return_or_removal_line_dict_ept(
                    key, row, disposal_line_dict, return_line_dict, liquidations_line_dict)
        return disposal_line_dict, return_line_dict, liquidations_line_dict

    def update_return_or_removal_line_dict_ept(self, key, row, disposal_line_dict, return_line_dict,
                                               liquidations_line_dict):
        """
        Define method for update Removal or Return order lines dictionary.
        :param : key : removal order id
        :param : row : list(dict())
        :param : disposal_line_dict : dict{key: [row]}
        :param : return_line_dict : dict{key: [row]}
        :return : dict {}
        """
        log_line_obj = self.env['common.log.lines.ept']
        if row.get('removal-order-type', '') == 'Disposal':
            if key in disposal_line_dict:
                disposal_line_dict.get(key).append(row)
            else:
                disposal_line_dict.update({key: [row]})
        elif row.get('removal-order-type', '') == 'Return':
            if key in return_line_dict:
                return_line_dict.get(key).append(row)
            else:
                return_line_dict.update({key: [row]})
        elif row.get('removal-order-type', '') == 'Liquidations':
            if key in liquidations_line_dict:
                liquidations_line_dict.get(key).append(row)
            else:
                liquidations_line_dict.update({key: [row]})
        else:
            message = "Order type {} || skipped of {} ".format(row.get('removal-order-type', ''),
                                                               row.get('order-id', ''))
            log_line_obj.create_common_log_line_ept(
                message=message, model_name=AMZ_REMOVAL_TRACKING_REPORT_HISTORY, module='amazon_ept',
                operation_type='import', res_id=self.id, amz_seller_ept=self.seller_id and self.seller_id.id or False,
                amz_instance_ept=self.instance_id and self.instance_id.id or False)
        return disposal_line_dict, return_line_dict, liquidations_line_dict

    def process_removal_lines(self, disposal_line_dict, return_line_dict, liquidations_line_dict):
        """
        This Method relocates process removal order lines.
        :param liquidations_line_dict: dict()
        :param disposal_line_dict: dict()
        :param return_line_dict: dict()
        :return: boolean
        """
        if disposal_line_dict:
            self.process_disposal_lines(disposal_line_dict)
        if return_line_dict:
            self.process_return_lines(return_line_dict)
        if liquidations_line_dict:
            self.process_disposal_lines(liquidations_line_dict)
        return True

    def process_disposal_lines(self, disposal_line_dict):
        """
        This Method relocates process disposal line.
        If dispose quantity found grater 0 then check move processed or not.
        If dispose quantity found less or equal 0 then search stock move.
        :param disposal_line_dict: list(dict{key: [row]})
        :return: list()
        """
        amz_removal_order_config_obj = self.env['removal.order.config.ept']
        amz_removal_order_obj = self.env[AMZ_REMOVAL_TRANSFER_EPT]
        pickings = []
        for order_key, rows in list(disposal_line_dict.items()):
            order = amz_removal_order_obj.browse(order_key[0])
            config = amz_removal_order_config_obj.browse(order_key[1])
            picking_vals = self.amz_removal_pickings_dict(order, config)
            unsellable_source_location_id = order.disposition_location_id.id
            sellable_source_location_id = order.instance_id.fba_warehouse_id.lot_stock_id.id
            prd_wise_qty_dict = self.amz_prepare_removal_lines_prd_wise_qty_ept(rows)
            for key, row in prd_wise_qty_dict.items():
                shipped_qty = float(row.get('shipped-quantity', 0.0) or 0.0)
                product = self.find_amazon_product_for_process_removal_line(row, order.instance_id.id)
                if product:
                    source_location_id = unsellable_source_location_id if (
                            key[1] == 'Unsellable') else sellable_source_location_id
                    picking_vals.update({'source_location_id': source_location_id, 'product_id': product,
                                         'order': order})
                    if shipped_qty > 0.0:
                        mv_pickings, skip_line = self.amz_removal_procesed_qty_ept(row, picking_vals, shipped_qty)
                        if skip_line:
                            continue
                        if mv_pickings:
                            pickings += mv_pickings
        if pickings:
            pickings = list(set(pickings))
            self.process_picking(pickings)
        return pickings

    def process_return_lines(self, return_line_dict):
        """
        This Method relocates processed return removal order lines.
        This Method find amazon product for process removal line.
        This Method check move processed or not.
        :param return_line_dict: This Arguments relocates dictionary of return line.
        :return: This Method return pickings.
        """
        procurement_rule_obj = self.env['stock.rule']
        amz_removal_order_config_obj = self.env['removal.order.config.ept']
        amz_removal_order_obj = self.env[AMZ_REMOVAL_TRANSFER_EPT]
        pickings = []
        for order_key, rows in list(return_line_dict.items()):
            order = amz_removal_order_obj.browse(order_key[0])
            config = amz_removal_order_config_obj.browse(order_key[1])
            picking_vals = self.amz_removal_pickings_dict(order, config)
            procurement_rule = procurement_rule_obj.search(
                [('route_id', '=', config.unsellable_route_id.id),
                 ('location_src_id', '=', order.disposition_location_id.id)])
            unsellable_source_location_id = procurement_rule.location_src_id.id
            unsellable_dest_location_id = procurement_rule.location_dest_id.id
            procurement_rule = procurement_rule_obj.search([
                ('route_id', '=', config.sellable_route_id.id),
                ('location_src_id', '=', order.instance_id.fba_warehouse_id.lot_stock_id.id)])
            sellable_source_location_id = procurement_rule.location_src_id.id
            sellable_dest_location_id = procurement_rule.location_dest_id.id
            prd_wise_qty_dict = self.amz_prepare_removal_lines_prd_wise_qty_ept(rows)
            for key, row in prd_wise_qty_dict.items():
                product = self.find_amazon_product_for_process_removal_line(row, order.instance_id.id)
                if not product:
                    continue
                shipped_qty = float(row.get('shipped-quantity', 0.0))
                source_location_id = unsellable_source_location_id if (
                        key[1] == 'Unsellable') else sellable_source_location_id
                location_dest_id = unsellable_dest_location_id if (
                        key[1] == 'Unsellable') else sellable_dest_location_id
                picking_vals.update({'location_dest_id': location_dest_id or False,
                                     'source_location_id': source_location_id or False,
                                     'product_id': product, 'order': order})
                if shipped_qty > 0.0:
                    move_pickings, skip_lines = self.amz_removal_procesed_qty_ept(row, picking_vals, shipped_qty)
                    if skip_lines:
                        continue
                    if move_pickings:
                        pickings += move_pickings
        if pickings:
            pickings = list(set(pickings))
            self.process_picking(pickings)
        return pickings

    @staticmethod
    def amz_removal_pickings_dict(order, config):
        """
        Prepare Removal order pickings filtered values.
        :param order: amazon.removal.order.ept()
        :param config: removal.order.config.ept()
        :return: dict{}
        """
        return {
            'remaining_pickings': order.removal_order_picking_ids.filtered(
                lambda l: l.state not in ['done', 'cancel']),
            'processed_pickings': order.removal_order_picking_ids.filtered(lambda l: l.state == 'done'),
            'canceled_pickings': order.removal_order_picking_ids.filtered(lambda l: l.state == 'cancel'),
            'location_dest_id': config.location_id.id or False,
        }

    @staticmethod
    def amz_prepare_removal_lines_prd_wise_qty_ept(removal_lines):
        """
        Define this method for prepare seller sku wise removal shipped, cancel, disposed qty.
        :param: file data - dict {}
        :return: dict {}
        """
        prd_wise_qty = {}
        for line in removal_lines:
            key = (line.get('sku', ''), line.get('disposition', ''))
            fn_sku = line.get('fnsku', '')
            shipped_qty = float(line.get('shipped-quantity', 0.0) or 0.0)
            order_id = line.get('order-id', '')
            if key in prd_wise_qty:
                shipped_qty += prd_wise_qty.get(key, {}).get('shipped-quantity', 0.0)
                prd_wise_qty.get(key, {}).update({'shipped-quantity': shipped_qty})
            else:
                prd_wise_qty.update({key: {'shipped-quantity': shipped_qty, 'fnsku': fn_sku,
                                           'order-id': order_id, 'sku': line.get('sku', '')}})
        return prd_wise_qty

    def find_amazon_product_for_process_removal_line(self, line, instance):
        """
        This Method relocates find amazon product for processed removal order line.
        :param line: This Arguments relocates Line of return line dictionary.
        :param instance: This Arguments instance of amazon.
        :return: This Method return process removal order product.
        """
        amazon_product_obj = self.env['amazon.product.ept']
        log_line_obj = self.env['common.log.lines.ept']
        sku = line.get('sku', '')
        asin = line.get('fnsku', '')
        amazon_product = amazon_product_obj.search([('seller_sku', '=', sku),
                                                    ('fulfillment_by', '=', 'FBA'),
                                                    ('instance_id', '=', instance)], limit=1)
        if not amazon_product:
            amazon_product = amazon_product_obj.search([('product_asin', '=', asin),
                                                        ('fulfillment_by', '=', 'FBA'),
                                                        ('instance_id', '=', instance)], limit=1)
        product = amazon_product.product_id.id if amazon_product else False
        if not amazon_product:
            amazon_product = self.env['multi.sku.odoo.product.ept'].search([('seller_sku', '=', sku)])
            product = amazon_product.product_id.id if amazon_product else False
        if not amazon_product:
            log_line_obj.create_common_log_line_ept(
                message='Product  not found for SKU {} & ASIN {}'.format(sku, asin),
                model_name=AMZ_REMOVAL_TRACKING_REPORT_HISTORY, module='amazon_ept', operation_type='import',
                res_id=self.id, mismatch_details=True, amz_seller_ept=self.seller_id and self.seller_id.id or False,
                amz_instance_ept=instance or False)
        return product

    def amz_removal_procesed_qty_ept(self, row, picking_vals, quantity):
        """
        Processed Pending Return and Disposal orders and process create back orders
        for partially done quantity
        :param row: dict()
        :param picking_vals: dict()
        :param quantity: float
        :return: list()
        """
        log_line_obj = self.env['common.log.lines.ept']
        order_ref = row.get('order-id', '')
        sku = row.get('sku', '')
        qty = quantity
        move_pickings = []
        skip_line = False
        if picking_vals.get('processed_pickings', False):
            existing_move = self.amz_get_stock_move_from_picking_ept(picking_vals.get('product_id', False),
                                                                     picking_vals.get('processed_pickings', False),
                                                                     picking_vals.get('source_location_id', False),
                                                                     picking_vals.get('location_dest_id', False), 'done'
                                                                     )
            if existing_move:
                qty = self.check_move_processed_or_not(picking_vals.get('product_id', False), existing_move,
                                                       sku, quantity, order_ref)
        if qty > 0.0:
            moves = self.amz_get_stock_move_from_picking_ept(picking_vals.get('product_id', False),
                                                             picking_vals.get('remaining_pickings', False),
                                                             picking_vals.get('source_location_id', False),
                                                             picking_vals.get('location_dest_id', False),
                                                             ['done', 'cancel'])
            if moves:
                move_pickings = self.create_pack_operations_ept(moves, qty)
            else:
                message = 'Move not found for processing sku {} order ref {}'.format(
                    sku, picking_vals.get('order', '').name)
                log_line_obj.create_common_log_line_ept(
                    message=message, model_name=AMZ_REMOVAL_TRACKING_REPORT_HISTORY, module='amazon_ept',
                    operation_type='import', res_id=self.id, mismatch_details=True,
                    amz_seller_ept=self.seller_id and self.seller_id.id or False,
                    amz_instance_ept=self.instance_id and self.instance_id.id or False)
                skip_line = True
        return move_pickings, skip_line

    @staticmethod
    def amz_get_stock_move_from_picking_ept(product_id, pickings_ids, source_location_id,
                                            location_dest_id, state):
        """
        Filter stock move based on product, source, state location and destination location from picking
        object.
        :param product_id: integer
        :param pickings_ids: stock.picking()
        :param source_location_id: integer
        :param location_dest_id: integer
        :param state : stock move state
        :return: stock.move()
        """
        if state == 'done':
            return pickings_ids.move_ids.filtered(
                lambda l: l.product_id.id == product_id and l.location_id.id == source_location_id and
                          l.location_dest_id.id == location_dest_id and l.state == state)
        return pickings_ids.move_ids.filtered(
            lambda l: l.product_id.id == product_id and l.location_id.id == source_location_id and
                      l.location_dest_id.id == location_dest_id and l.state not in state)

    def create_pack_operations_ept(self, moves, quantity):
        """
        This Method relocates create pack operation.
        This Method create stock move line for existing move and if any quantity left then create
        stock move line.
        :param moves: stock.move()
        :param quantity: float
        :return: list()
        """
        pick_ids = []
        stock_move_line_obj = self.env['stock.move.line']
        for move in moves:
            qty_left = quantity
            if qty_left <= 0.0:
                break
            mv_done_qty = sum(line.quantity for line in move.move_line_ids.filtered(lambda l: l.picked))
            move_line_remaning_qty = move.product_uom_qty - mv_done_qty
            operations = move.move_line_ids.filtered(
                lambda o: (o.quantity <= 0 or not o.picked) and not o.result_package_id)
            for operation in operations:
                op_qty = operation.quantity if operation.quantity <= qty_left else qty_left
                operation.write({'quantity': op_qty, 'picked': True})
                # commented this method for prevent to create back order stock move line from the connector
                # self._put_in_pack(operation)
                qty_left = float_round(qty_left - op_qty,
                                       precision_rounding=operation.product_uom_id.rounding,
                                       rounding_method='UP')
                move_line_remaning_qty = move_line_remaning_qty - op_qty
                if qty_left <= 0.0:
                    break
            picking = move.picking_id
            if qty_left > 0.0 and move_line_remaning_qty > 0.0:
                op_qty = move_line_remaning_qty if move_line_remaning_qty <= qty_left else qty_left
                sml_vals = self.amz_create_removal_stock_move_line_vals(move, picking, op_qty)
                stock_move_line_obj.create(sml_vals)
                pick_ids.append(move.picking_id.id)
                qty_left = float_round(qty_left - op_qty,
                                       precision_rounding=move.product_id.uom_id.rounding,
                                       rounding_method='UP')
                if qty_left <= 0.0:
                    break
            if qty_left > 0.0:
                sml_vals = self.amz_create_removal_stock_move_line_vals(move, picking, qty_left)
                stock_move_line_obj.create(sml_vals)
            pick_ids.append(move.picking_id.id)
        return pick_ids

    @staticmethod
    def amz_create_removal_stock_move_line_vals(move, picking, op_qty):
        """
        Prepare stock move line values for removal orders stock move.
        :param move: stock.move()
        :param picking: stock.picking()
        :param op_qty: float
        :return: dict()
        """
        # here we set quantity instead of qty_done
        return {
            'product_id': move.product_id.id,
            'product_uom_id': move.product_id.uom_id.id,
            'picking_id': move.picking_id.id,
            'quantity': float(op_qty) or 0.0,
            'location_id': picking.location_id.id,
            'location_dest_id': picking.location_dest_id.id,
            'move_id': move.id,
            'picked': True
        }

    def process_picking(self, pickings):
        """
        This Method relocates process picking and change state.
        :param pickings: list().
        :return: Boolean(True/False).
        """
        log_line_obj = self.env['common.log.lines.ept']
        stock_picking_obj = self.env['stock.picking']
        for picking in pickings:
            picking = stock_picking_obj.browse(picking)
            try:
                if picking.state == 'waiting':
                    picking.action_assign()
                if picking.state == 'assigned':
                    picking.with_context(auto_processed_orders_ept=True)._action_done()
                    picking.write({'removal_transfer_report_id': self.id})
                else:
                    message = 'Stock picking could not be done due to unavailability of stock. Picking Ref %s' % (
                        picking.name)
                    log_line_obj.create_common_log_line_ept(
                        message=message, model_name=AMZ_REMOVAL_TRACKING_REPORT_HISTORY, module='amazon_ept',
                        operation_type='import', res_id=self.id, mismatch_details=False,
                        amz_seller_ept=self.seller_id and self.seller_id.id or False,
                        amz_instance_ept=self.instance_id and self.instance_id.id or False)
            except:
                continue
            # removal_order_picking_ids = picking.removal_order_id.removal_order_picking_ids.filtered(
            # lambda l: l.is_fba_wh_picking and l.state != 'done')
            # if not removal_order_picking_ids:
            # picking.removal_order_id.write({'state': 'Completed'})
        return True

    def check_removal_order_configuration(self):
        """
        Define method for check removal order configuration.
        :return:
        """
        if not self.attachment_id:
            raise UserError(_("There is no any report are attached with this record."))
        if not self.seller_id:
            raise UserError(_("Seller is not defined for processing report"))

    def action_view_removal_picking(self):
        action = self.env['ir.actions.actions']._for_xml_id(
            'amazon_ept.action_picking_tree_removal_transfer')
        if self.removal_picking_ids:
            action['domain'] = [('id', 'in', self.removal_picking_ids.ids)]
        return action

    def auto_import_removal_tracking_report(self, args={}):
        seller_id = args.get('seller_id', False)
        if seller_id:
            seller = self.env['amazon.seller.ept'].search([('id', '=', seller_id)])
            if seller.removal_tracking_report_last_sync_on:
                start_date = seller.removal_tracking_report_last_sync_on
                start_date = datetime.strftime(start_date, DATE_YMDHMS)
                start_date = datetime.strptime(str(start_date), DATE_YMDHMS)
                start_date = start_date - timedelta(days=90)
            else:
                today = datetime.now()
                earlier = today - timedelta(days=90)
                start_date = earlier.strftime(DATE_YMDHMS)
            date_end = datetime.now()
            date_end = date_end.strftime(DATE_YMDHMS)
            rem_tracking_report = self.create({
                'report_type': 'GET_FBA_FULFILLMENT_REMOVAL_SHIPMENT_DETAIL_DATA',
                'seller_id': seller_id,
                'start_date': start_date,
                'end_date': date_end,
                'state': 'draft',
            })
            rem_tracking_report.with_context(is_auto_process=True).request_report()
            seller.write({'removal_tracking_report_last_sync_on': datetime.now()})
        return True

    @api.model
    def auto_process_removal_tracking_report(self, args={}):
        seller_id = args.get('seller_id', False)
        if seller_id:
            seller = self.env[AMZ_SELLER_EPT].search([('id', '=', seller_id)])
            rem_reports = self.search([('seller_id', '=', seller.id),
                                       ('state', 'in', ['_SUBMITTED_', '_IN_PROGRESS_',
                                                        'SUBMITTED', 'IN_PROGRESS', 'IN_QUEUE'])])
            for report in rem_reports:
                report.with_context(is_auto_process=True).get_report_request_list()

            rem_reports = self.search([('seller_id', '=', seller.id),
                                       ('state', 'in', ['_DONE_', '_SUBMITTED_', '_IN_PROGRESS_',
                                                        'DONE', 'SUBMITTED', 'IN_PROGRESS']),
                                       ('report_document_id', '!=', False)])
            for report in rem_reports:
                if not report.attachment_id:
                    report.with_context(is_auto_process=True).get_report()
                if report.state in ['_DONE_', 'DONE'] and report.attachment_id:
                    report.with_context(is_auto_process=True).process_removal_tracking_report()
                self._cr.commit()
        return True
