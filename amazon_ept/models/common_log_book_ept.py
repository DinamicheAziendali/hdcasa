# -*- coding: utf-8 -*-
# See LICENSE file for full copyright and licensing details.

"""
Inherited class to add common method to create amazon transaction logs
"""
import time
import os
import base64
import logging
import ast
from datetime import datetime, timedelta
from io import BytesIO
from dateutil.relativedelta import relativedelta
from odoo import models, fields
from odoo.tools.misc import xlsxwriter

_logger = logging.getLogger(__name__)


class CommonLogBookEpt(models.Model):
    """
    Inherited class to store define the common method to create log
    """
    _inherit = 'common.log.book.ept'

    active_product_attachment_id = fields.Many2one('ir.attachment', string="Attachment Id",
                                                   help="Id of attachment record", readonly=True)
    is_active_product_list = fields.Boolean(string="Active Product List", readonly=True,
                                            help="Boolean set if Active Product List Model",
                                            compute="_compute_is_active_product_list", default=False)

    def unlink(self):
        """
        Delete ir.attachment record when user delete active
        product log record by using active_product_attachment_id field

        @author : Kishan Sorani
        :return:
        """
        if self.active_product_attachment_id:
            self.active_product_attachment_id.unlink()
        return super(CommonLogBookEpt, self).unlink()

    @staticmethod
    def get_cleanup_rules():
        states = ["processed", "CANCELLED", "_CANCELLED_"]
        return [
            {
                "model": "amazon.fba.live.stock.report.ept",
                "states": states,
            },
            {
                "model": "active.product.listing.report.ept",
                "states": states,
            },
            {
                "model": "amazon.removal.order.report.history",
                "states": states,
            },
            {
                "model": "amazon.removal.tracking.report.history",
                "states": states,
            },
            {
                "model": "rating.report.history",
                "states": states,
            },
            {
                "model": "replacement.order.report.request.history",
                "states": states,
            },
            {
                "model": "sale.order.return.report",
                "states": states,
            },
            {
                "model": "settlement.report.ept",
                "states": states + ["confirm"],
            },
            {
                "model": "shipping.report.request.history",
                "states": states,
            },
            {
                "model": "amazon.stock.adjustment.report.history",
                "states": states,
            },
            {
                "model": "amazon.vcs.tax.report.ept",
                "states": states,
            },
            {
                "model": "fbm.sale.order.report.ept",
                "states": states,
            },
        ]

    def cron_cleanup_old_report_attachments(self, limit_per_model=500, args={}):
        if args.get('seller_id', False):
            seller_id = self.env['amazon.seller.ept'].browse(args.get('seller_id'))
        else:
            return False
        cutoff_date = fields.Datetime.now() - relativedelta(day=seller_id.amz_auto_cleanup_old_attachments_after_days)
        for rule in self.get_cleanup_rules():
            records = self.env[rule["model"]].search([
                ("attachment_id", "!=", False),
                ("seller_id", "=", seller_id.id),
                ("state", "in", rule["states"]),
                ("create_date", "<", cutoff_date),
            ], limit=limit_per_model)
            attachments = records.mapped("attachment_id")
            _logger.info(f"Deleting attachments for model: {rule['model']}, total attachments: {len(attachments)}")
            if records:
                records.write({"attachment_id": False})
            if attachments:
                attachments.unlink()
        return True

    def _compute_is_active_product_list(self):
        """
        This method set is_active_prodcut_list boolean field
        1) if active record model is active.product.listing.report.ept
           and created log lines records with product title and seller sku
           set true

        2) else set false
        :return:
        @author : Kishan Sorani
        """

        if self.model_id.model == 'active.product.listing.report.ept':
            if self.log_lines.filtered(lambda line: line.default_code and line.product_title):
                self.is_active_product_list = True
            else:
                self.is_active_product_list = False
        else:
            self.is_active_product_list = False

    def get_mismatch_report(self):
        """
        Will download excel report of mismatch details.
        @updtae by : Kishan Sorani
        :return: An action containing URL of excel attachment or bool.
        """
        self.ensure_one()
        # Get filestore path of an attachment, model_id and log.
        filestore = self.env["ir.attachment"]._filestore()
        model_id = self.env['ir.model']._get('active.product.listing.report.ept').id
        log = self.env["common.log.book.ept"].search([('res_id', '=', self.res_id),
                                                      ('model_id', '=', model_id)])
        active_product_record = self.env["active.product.listing.report.ept"].search([('id', '=', self.res_id)])

        # Create an excel file at filestore location of mismatched records.
        _file = filestore + "/Mismatch_Details_{0}.xlsx".format(time.strftime("%d_%m_%Y|%H_%M_%S"))
        workbook = xlsxwriter.Workbook(_file)
        header_style = workbook.add_format({'bold': True})
        header_fields = ["Title", "Internal Reference",
                         "Seller SKU",
                         "Marketplace", "Fulfillment"]

        # Write data to that excel file.
        worksheet = workbook.add_worksheet()
        worksheet.set_column(0, 0, 60)
        worksheet.set_column(1, 4, 30)
        for column_number, cell_value in enumerate(header_fields):
            worksheet.write(0, column_number, cell_value, header_style)
        for row, log_line in enumerate(log.log_lines, start=1):
            if log_line.product_title and log_line.default_code:
                log_line = [log_line.product_title,
                            ' ',
                            log_line.default_code,
                            active_product_record.instance_id.marketplace_id.name,
                            log_line.fulfillment_by]

                for column, cell_value in enumerate(log_line):
                    worksheet.write(row, column, cell_value)
        workbook.close()
        # Open that excel file for reading purpose and create
        # File pointer for same.
        excel_file = open(_file, "rb")
        file_pointer = BytesIO(excel_file.read())
        file_pointer.seek(0)
        if not self.active_product_attachment_id:
            # Bind that created file to attachment and then close
            # File and file pointers and then delete file from filestore.
            new_attachment = self.env["ir.attachment"].create({
                "name": "Mismatch_Details_{0}.xlsx".format(time.strftime("%d_%m_%Y|%H_%M_%S")),
                "datas": base64.b64encode(file_pointer.read()),
                "type": "binary"
            })
            file_pointer.close()
            excel_file.close()
            os.remove(_file)
            self.write({'active_product_attachment_id': new_attachment.id})
        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content/%s?download=true' % self.active_product_attachment_id.id,
            'target': 'self'
        }

    def amz_create_schedule_activity_for_missing_products(self, missing_products, amz_seller, model_name, res_id,
                                                          fulfillment_by):
        """
        Define this method for create schedule activity for the missing amazon products while importing
        amazon orders in odoo.
        :param: missing_products: list of amazon products seller sku
        :param: amz_seller: amazon.seller.ept()
        :param: model_name: model name - str
        :param: model_id: resource id
        :param: fulfillment_by: amazon fulfillment by either FBA or FBM
        :return: boolean (TRUE/FALSE).
        """
        model_id = self.env['ir.model']._get(model_name).id
        mail_activity_obj = self.env['mail.activity']
        activity_type_id = amz_seller.amz_activity_type_id.id
        date_deadline = datetime.strftime(
            datetime.now() + timedelta(days=int(amz_seller.amz_activity_date_deadline)), "%Y-%m-%d")
        for product_sku in missing_products:
            activity_note = 'Amazon %s Product not found for %s seller sku.' % (fulfillment_by, product_sku)
            for user_id in amz_seller.amz_activity_user_ids:
                mail_activity = mail_activity_obj.search([('user_id', '=', user_id.id),
                                                          ('activity_type_id', '=', activity_type_id),
                                                          ('note', '=', activity_note)])
                if not mail_activity:
                    vals = self.amz_prepare_vals_for_schedule_activity_for_missing_products(
                        activity_type_id, activity_note, user_id, date_deadline, model_id, res_id)
                    try:
                        mail_activity_obj.create(vals)
                    except Exception as error:
                        _logger.info("Unable to create schedule activity, Please give proper "
                                     "access right of this user :%s  ", user_id.name)
                        _logger.info(error)
        return True

    def amz_prepare_vals_for_schedule_activity_for_missing_products(self, activity_type_id, note, user_id,
                                                                    date_deadline, model_id, res_id):
        """
        Define this method for prepare values for missing amazon products schedule activity.
        :param: activity_type_id: mail.activity.type()
        :param: note: str
        :param: user_id: res.users()
        :param: date_deadline: activity deadline date
        :param: model_id: resource id
        :return: dict {}.
        """
        values = {'activity_type_id': activity_type_id,
                  'note': note,
                  'res_id': res_id,
                  'user_id': user_id.id or self._uid,
                  'res_model_id': model_id,
                  'date_deadline': date_deadline
                  }
        return values

    def amz_create_schedule_activity_for_failed_outbound_order(self, amz_seller, model_name, res_id,
                                                               error_response, order_ref):
        """
        Define this method for create schedule activity for the failed outbound order.
        :param: amz_seller: amazon.seller.ept()
        :param: model_name: model name - str
        :param: model_id: resource id
        :param: error_response: order response
        :param: order_ref: str
        :return: boolean (TRUE/FALSE).
        """
        model_id = self.env['ir.model']._get(model_name).id
        mail_activity_obj = self.env['mail.activity']
        activity_type_id = amz_seller.amz_outbound_activity_type_id.id
        date_deadline = datetime.strftime(
            datetime.now() + timedelta(days=int(amz_seller.amz_outbound_activity_date_deadline)), "%Y-%m-%d")
        error_response = ast.literal_eval(error_response)
        error_message = error_response and error_response[0].get('message', '') or ''
        activity_note = ("Error occurred while create Outbound Fulfillment in Amazon: %s. "
                         "Manual actions may be needed.<br> Exception:<br> %s") % (order_ref, error_message)
        for user_id in amz_seller.amz_outbound_activity_user_ids:
            mail_activity = mail_activity_obj.search([('user_id', '=', user_id.id),
                                                      ('activity_type_id', '=', activity_type_id),
                                                      ('note', '=', activity_note)])
            if not mail_activity:
                vals = self.amz_prepare_vals_for_schedule_activity_for_missing_products(
                    activity_type_id, activity_note, user_id, date_deadline, model_id, res_id)
                try:
                    mail_activity_obj.create(vals)
                except Exception as error:
                    _logger.info("Unable to create schedule activity, Please give proper "
                                 "access right of this user :%s  ", user_id.name)
                    _logger.info(error)
        return True
