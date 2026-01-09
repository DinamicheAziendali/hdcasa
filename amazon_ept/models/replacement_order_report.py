import time
import base64
import csv
from io import StringIO
from datetime import datetime, timedelta
from odoo import models, fields, api, _
from odoo.exceptions import UserError
from ..reportTypes import ReportType
from odoo.fields import Datetime


class ReplacementOrderReportRequestHistory(models.Model):
    _name = "replacement.order.report.request.history"
    _description = "Replacement Order Report Request History"
    _inherit = ['mail.thread', 'amazon.reports', 'mail.activity.mixin']
    _order = 'id desc'

    @api.depends('seller_id')
    def _compute_company(self):
        """
        This method will update company in the created records.
        :return:
        """
        for record in self:
            company_id = record.seller_id.company_id.id if record.seller_id else False
            if not company_id:
                company_id = self.env.company.id
            record.company_id = company_id

    def _compute_total_logs(self):
        """
        This method will find all mismatch logs associated with this report
        :return:
        """
        log_line_obj = self.env['common.log.lines.ept']
        model_id = self.env['ir.model']._get('replacement.order.report.request.history').id
        log_ids = log_line_obj.search([('res_id', '=', self.id), ('model_id', '=', model_id)]).ids
        self.log_count = log_ids.__len__()

        # Set the boolean field mismatch_details as True if found any mismatch details in log lines
        if log_line_obj.search_count([('res_id', '=', self.id), ('mismatch_details', '=', True),
                                      ('model_id', '=', model_id)]):
            self.mismatch_details = True
        else:
            self.mismatch_details = False

    name = fields.Char(size=256)
    state = fields.Selection([('draft', 'Draft'), ('SUBMITTED', 'SUBMITTED'), ('_SUBMITTED_', 'SUBMITTED'),
                              ('IN_QUEUE', 'IN_QUEUE'), ('IN_PROGRESS', 'IN_PROGRESS'),
                              ('_IN_PROGRESS_', 'IN_PROGRESS'), ('IN_FATAL', 'IN_FATAL'),
                              ('DONE', 'DONE'), ('_DONE_', 'DONE'), ('_DONE_NO_DATA_', 'DONE_NO_DATA'),
                              ('FATAL', 'FATAL'), ('partially_processed', 'Partially Processed'),
                              ('processed', 'PROCESSED'), ('CANCELLED', 'CANCELLED'),
                              ('_CANCELLED_', 'CANCELLED')], string='Report Status', default='draft',
                             help="Report Processing States")
    attachment_id = fields.Many2one('ir.attachment', string="Attachment",
                                    help="Find replace order report from odoo Attachment")
    seller_id = fields.Many2one('amazon.seller.ept', string='Seller', copy=False,
                                help="Select Seller id from you wanted to get replace order report")
    report_request_id = fields.Char(size=256, string='Report Request ID',
                                    help="Report request id to recognise unique request")
    report_document_id = fields.Char(string='Report Document ID',
                                     help="Report document id to recognise unique request")
    report_id = fields.Char(size=256, string='Report ID', help="Unique Report id for recognise report in Odoo")
    report_type = fields.Char(size=256, help="Amazon Report Type")
    start_date = fields.Datetime(help="Report Start Date")
    end_date = fields.Datetime(help="Report End Date")
    requested_date = fields.Datetime(default=time.strftime("%Y-%m-%d %H:%M:%S"), help="Report Requested Date")
    company_id = fields.Many2one('res.company', string="Company", copy=False,
                                 compute="_compute_company", store=True)
    user_id = fields.Many2one('res.users', string="Requested User",
                              help="Track which odoo user has requested report")
    log_count = fields.Integer(compute="_compute_total_logs", store=False,
                               help="Count number of created Stock Move")
    mismatch_details = fields.Boolean(compute="_compute_total_logs", help="true if mismatch details found")
    amz_instance_id = fields.Many2one('amazon.instance.ept', string="Marketplace",
                                      help="This Field relocates amazon instance.")

    def unlink(self):
        """
        Inherited this method for raise user warning id report state is belongs to
        processed or partially processed.
        :return :
        """
        for report in self:
            if report.state == 'processed' or report.state == 'partially_processed':
                raise UserError(_('You cannot delete processed report.'))
        return super(ReplacementOrderReportRequestHistory, self).unlink()

    @api.constrains('start_date', 'end_date')
    def _check_duration(self):
        """
        This method will compare Start date and End date, If End date is before start date rate warning.
        :return:
        """
        if self.start_date and self.end_date < self.start_date:
            raise UserError(_('Error!\nThe start date must be precede its end date.'))
        return True

    @api.model
    def default_get(self, fields):
        """
        Inherited this method for update replacement report type in the respective order
        for request report data in the Amazon.
        :param fields: list of fields
        :return: dict {}
        """
        res = super(ReplacementOrderReportRequestHistory, self).default_get(fields)
        if not fields:
            return res
        res.update({'report_type': ReportType.GET_FBA_FULFILLMENT_CUSTOMER_SHIPMENT_REPLACEMENT_DATA.value})
        return res

    @api.model_create_multi
    def create(self, vals_list):
        """
        Inherited this method for update report name as next sequence of the replacement report records.
        :param vals_list: {[]}
        :return: replacement.order.report.request.history()
        """
        for vals in vals_list:
            sequence = self.env.ref('amazon_ept.seq_import_replacement_order_report_job', raise_if_not_found=False)
            report_name = sequence.next_by_id() if sequence else '/'
            vals.update({'name': report_name})
        return super(ReplacementOrderReportRequestHistory, self).create(vals_list)

    @api.onchange('seller_id')
    def on_change_seller_id(self):
        """
        This method will Set Start and End date of report as per seller configurations default is 3 days.
        :return:
        """
        if self.seller_id:
            self.start_date = datetime.now() - timedelta(self.seller_id.replacement_order_report_days)
            self.end_date = datetime.now()

    def list_of_process_logs(self):
        """
        This method will list out mismatch logs for selected report.
        :return: ir.actions.act_window()
        """
        model_id = self.env['ir.model']._get('replacement.order.report.request.history').id
        action = {
            'domain': "[('res_id', '=', " + str(self.id) + " ), ('model_id','='," + str(
                model_id) + ")]",
            'name': 'Replacement Report Logs',
            'view_mode': 'list,form',
            'res_model': 'common.log.lines.ept',
            'type': 'ir.actions.act_window',
        }
        return action

    def create_amazon_report_attachment(self, result):
        """
        This method will help to create attachment record for the replacement order data in
        .csv file formate.
        :return: True
        """
        file_name = "Replacement_order_report_" + time.strftime("%Y_%m_%d_%H%M%S") + '.csv'
        result = result.get('document', '')
        result = result.encode()
        result = base64.b64encode(result)
        attachment = self.env['ir.attachment'].create({
            'name': file_name,
            'datas': result,
            'res_model': 'mail.compose.message',
            'type': 'binary'
        })
        self.message_post(body=_("Replacement Order Report Downloaded"), attachment_ids=attachment.ids)
        self.write({'attachment_id': attachment.id})
        return True

    def process_replacement_order_report(self):
        """
        Define this method process replacement orders report and link replaced orders with its
        original order.
        :return: True
        """
        self.ensure_one()
        ir_cron_obj = self.env['ir.cron']
        log_lines_obj = self.env['common.log.lines.ept']
        is_auto_process = self._context.get('is_auto_process', False)
        if not is_auto_process:
            ir_cron_obj.with_context({'raise_warning': True}).find_running_schedulers(
                'ir_cron_create_fba_replacement_orders_report_seller_', self.seller_id.id)
        if not self.attachment_id:
            message = "There is no any report are attached with this record."
            if is_auto_process:
                log_lines_obj.create_common_log_line_ept(
                    message=message, model_name='replacement.order.report.request.history',
                    fulfillment_by='FBA', module='amazon_ept',
                    operation_type='import', res_id=self.id, mismatch_details=True,
                    amz_seller_ept=self.seller_id and self.seller_id.id or False)
                return True
            else:
                raise UserError(_(message))
        mismatch_lines = log_lines_obj.amz_find_mismatch_details_log_lines(self.id, 'replacement.order.report.request.history')
        mismatch_lines and mismatch_lines.unlink()
        imp_file = StringIO(base64.b64decode(self.attachment_id.datas).decode())
        content = imp_file.read()
        delimiter = ('\t', csv.Sniffer().sniff(content.splitlines()[0]).delimiter)[bool(content)]
        settlement_reader = csv.DictReader(content.splitlines(), delimiter=delimiter)
        for row in settlement_reader:
            amz_replaced_order_id = row.get('replacement-amazon-order-id', '')
            amz_original_order_id = row.get('original-amazon-order-id', '')
            replaced_order = self.amz_find_order_based_order_reference(amz_replaced_order_id)
            original_order = self.amz_find_order_based_order_reference(amz_original_order_id)
            if not replaced_order or not original_order:
                if not replaced_order:
                    message = "FBA Replaced Order not found in odoo for order reference %s." % (amz_replaced_order_id)
                    log_lines_obj.create_common_log_line_ept(
                        message=message, model_name='replacement.order.report.request.history',
                        fulfillment_by='FBA', module='amazon_ept',
                        operation_type='import', res_id=self.id, mismatch_details=True,
                        order_ref=amz_replaced_order_id, amz_seller_ept=self.seller_id and self.seller_id.id or False,
                        amz_instance_ept=self.amz_instance_id and self.amz_instance_id.id or False)
                if not original_order:
                    message = "FBA Original Order not found in odoo for order reference %s." % (amz_original_order_id)
                    log_lines_obj.create_common_log_line_ept(
                        message=message, model_name='replacement.order.report.request.history',
                        fulfillment_by='FBA', module='amazon_ept',
                        operation_type='import', res_id=self.id, mismatch_details=True,
                        order_ref=amz_original_order_id, amz_seller_ept=self.seller_id and self.seller_id.id or False,
                        amz_instance_ept=self.amz_instance_id and self.amz_instance_id.id or False)
                continue
            replaced_order_code = self.amz_get_order_replaced_reason(row.get('replacement-reason-code', ''))
            replaced_order.write({'amz_fba_original_order_id': original_order.id,
                                  'amz_fba_replaced_order_reason': replaced_order_code.replace_order_reason})
            original_order.write({'amz_fba_replaced_order_id': replaced_order.id,
                                  'amz_fba_replaced_order_reason': replaced_order_code.replace_order_reason})
        if not log_lines_obj.amz_find_mismatch_details_log_lines(self.id, 'replacement.order.report.request.history', True):
            self.write({'state': 'processed'})
        else:
            self.write({'state': 'partially_processed'})
        return True

    def amz_get_order_replaced_reason(self, replaced_order_code):
        """
        Define this method for find or create order replaced reason based
        on replaced order code.
        :param: replaced_order_code: str
        :return: amazon.replacement.order.reason.code()
        """
        repl_order_reason_code_obj = self.env['amazon.replacement.order.reason.code']
        order_reason_code = repl_order_reason_code_obj.search([('replace_order_code', '=', replaced_order_code)], limit=1)
        if not order_reason_code:
            order_reason_code = repl_order_reason_code_obj.create({'replace_order_code': replaced_order_code})
        return order_reason_code

    def amz_find_order_based_order_reference(self, amz_order_ref):
        """
        Define this method for find imported FBA order based on amazon order reference.
        :param: amz_order_ref: amazon order reference
        :return: sale.order()
        """
        sale_order_obj = self.env['sale.order']
        return sale_order_obj.search([('amz_order_reference', '=', amz_order_ref),
                                      ('amz_seller_id', '=', self.seller_id.id),
                                      ('amz_fulfillment_by', '=', 'FBA'),
                                      ('amz_instance_id', '=', self.amz_instance_id.id)], limit=1)

    def amz_create_replacement_order_report(self, amz_seller, start_date, end_date, amz_instances=False):
        """
        This method will help to create replacement orders report as per selected seller
        instances.
        :param: amz_seller : amazon.seller.ept()
        :param: start_date : report start date
        :param: end_date : report end date
        :param: amz_instances : amazon.instance.ept()
        :return: replacement.order.report.request.history()
        """
        report_vals = self.amz_prepare_replacement_report_vals(amz_seller, start_date, end_date)
        if not amz_instances:
            amz_instances = amz_seller.instance_ids
        import_reports = []
        for instance in amz_instances:
            report_vals.update({'amz_instance_id': instance.id})
            if self._context.get('is_auto_process', False):
                report_vals.update({'state': 'draft', 'requested_date': time.strftime('%Y-%m-%d %H:%M:%S')})
            replacement_report = self.create(report_vals)
            replacement_report.request_report()
            import_reports.append(replacement_report.id)
        return import_reports

    @staticmethod
    def amz_prepare_replacement_report_vals(amz_seller, start_date, end_date):
        """
        This method will prepare replacement report values.
        :param: amz_seller : amazon.seller.ept()
        :param: start_date : report request start date
        :param: end_date : report end date
        :return: dict {}
        """
        return {
            'seller_id': amz_seller.id,
            'start_date': start_date,
            'end_date': end_date
        }

    def prepare_amazon_request_report_kwargs(self, seller):
        """
        Inherited this method for update amazon marketplace id for request replacement report in the amazon.
        :param: seller : amazon.seller.ept()
        :return: dict {}
        """
        kwargs = super(ReplacementOrderReportRequestHistory, self).prepare_amazon_request_report_kwargs(seller)
        kwargs.update({'marketplaceids': self.amz_instance_id.mapped('market_place_id')})
        return kwargs

    @api.model
    def auto_import_replacement_orders_report(self, args={}):
        """
        Define this method for auto import replacement orders report.
        :param args: dict {}
        :return: True
        """
        seller_id = args.get('seller_id', False)
        if seller_id:
            seller = self.env['amazon.seller.ept'].browse(seller_id)
            if seller.replacement_orders_report_last_sync_on:
                start_date = seller.replacement_orders_report_last_sync_on
                start_date = datetime.strftime(start_date, '%Y-%m-%d %H:%M:%S')
                start_date = datetime.strptime(str(start_date), '%Y-%m-%d %H:%M:%S')
                start_date = start_date - timedelta(hours=10)
            else:
                start_date = datetime.now() - timedelta(days=30)
            start_date = start_date + timedelta(days=seller.replacement_order_report_days * -1 or -3)
            start_date = start_date.strftime('%Y-%m-%d %H:%M:%S')
            date_end = datetime.now()
            date_end = date_end.strftime('%Y-%m-%d %H:%M:%S')
            self.with_context(is_auto_process=True).amz_create_replacement_order_report(seller, start_date, date_end)
            seller.write({'replacement_orders_report_last_sync_on': date_end})
        return True

    @api.model
    def auto_process_replacement_orders_report(self, args={}):
        """
        Define this method for auto process replacement orders report
        :param args: {}
        :return: True
        """
        seller_id = args.get('seller_id', False)
        if seller_id:
            seller = self.env['amazon.seller.ept'].browse(seller_id)
            three_days_ago = Datetime.now() - timedelta(days=3)
            replacement_reports = self.search([('seller_id', '=', seller.id),
                                               ('state', 'in', ['_SUBMITTED_', '_IN_PROGRESS_',
                                                                'SUBMITTED', 'IN_PROGRESS', 'IN_QUEUE']),
                                               ('create_date', '>=', three_days_ago)])
            for report in replacement_reports:
                report.with_context(is_auto_process=True).get_report_request_list()
            replacement_reports = self.search([('seller_id', '=', seller.id),
                                               ('state', 'in', ['_DONE_', '_SUBMITTED_', '_IN_PROGRESS_',
                                                                'DONE', 'SUBMITTED', 'IN_PROGRESS']),
                                               ('report_document_id', '!=', False)])
            for report in replacement_reports:
                if report.report_id and report.state in ['_DONE_', 'DONE'] and not report.attachment_id:
                    report.with_context(is_auto_process=True).get_report()
                if report.state in ['_DONE_', 'DONE'] and report.attachment_id:
                    report.with_context(is_auto_process=True).process_replacement_order_report()
                self._cr.commit()
        return True
