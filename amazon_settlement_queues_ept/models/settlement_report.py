# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

import base64
import logging
import csv
from datetime import datetime
from io import StringIO
from odoo import models, fields, _

_logger = logging.getLogger("Amazon")


class AmazonInstance(models.Model):
    _inherit = "settlement.report.ept"

    """
    @author: Twinkalc added on 19/Mar/2021.
    Inherited class to add new state "ready to import"
    """
    state = fields.Selection(selection_add=[('ready_to_import', "Ready To Import")])
    settlement_queue_id = fields.Many2one('settlement.process.queue.ept', string='Settlement Queue',
                                          help="Settlement Queue Reference")
    queue_count = fields.Integer(compute='_compute_queues', readonly=True)
    last_processed_line = fields.Integer(string="Last processed settlement line",
                                         help="Used to identify the last process line")

    def settlement_queue(self):
        """
        @author: Twinkalc added on 19/Mar/2021.
        This function is used to show generated settlement queues
        """

        self.ensure_one()
        action = self.env.ref('amazon_settlement_queues_ept.amazon_settlement_queue_action', False)
        form_view = self.env.ref('amazon_settlement_queues_ept.amazon_settlement_process_queue_ept_form_ept', False)
        result = action.read()[0] if action else {}
        result['views'] = [(form_view and form_view.id or False, 'form')]
        result['res_id'] = self.settlement_queue_id.id if self.settlement_queue_id else False
        return result

    def _compute_queues(self):
        """
        @author: Twinkalc added on 19/Mar/2021.
        This method will count the number of settlement queues.
        """
        self.queue_count = len(self.settlement_queue_id.ids)

    def process_settlement_report_file(self):
        """
        Ovveride by twinkalc
        Process work for fetch data from settlement report,create bank statement and
        process amazon settlement via queues
        """
        self.ensure_one()
        global_cron_obj = self.env['global.cron.configuration']
        self.check_instance_configuration_and_attachment_file()
        self.write({'state': 'ready_to_import'})
        self._cr.commit()
        if not self._context.get('is_auto_process') and self.seller_id:
            seller = self.seller_id
            settlement_ids = self.search([('state', '=', 'ready_to_import'), ('seller_id', '=', seller.id)])
            if settlement_ids:
                cron_id = self.env.ref(
                    'amazon_settlement_queues_ept.ir_cron_to_create_and_process_settlement_reports_seller_%d' % (
                        seller.id), raise_if_not_found=False)
                if not cron_id:
                    global_cron_obj.process_amz_settlement_queues_cron(seller)
                elif cron_id and not cron_id.sudo().active:
                    cron_id.sudo().write({'active': True, 'nextcall': datetime.now()})
                elif cron_id:
                    try:
                        cron_id.sudo().write({'nextcall': datetime.now()})
                    except Exception as e:
                        _logger.debug("Method %s will be called after commit", e)
                return {
                    'effect': {
                        'fadeout': 'slow',
                        'message': "Bank statement lines will create via scheduler and statement needs to reconcile "
                                   "either manually or via scheduler!",
                        'img_url': '/web/static/img/smile.svg',
                        'type': 'rainbow_man',
                    }
                }
        return True

    def get_amazon_settlement_queue_data(self):
        """
        Added by twinkalc on 22 March 2021
        Code to prepare an amazon transaction data
        """
        partner_obj = self.env['res.partner']
        amazon_product_obj = self.env['amazon.product.ept']
        imp_file = StringIO(base64.b64decode(self.attachment_id.datas).decode())
        content = imp_file.read()
        delimiter = ('\t', csv.Sniffer().sniff(content.splitlines()[0]).delimiter)[bool(content)]
        settlement_reader = csv.DictReader(content.splitlines(), delimiter=delimiter)
        order_list_item_price = {}
        create_or_update_refund_dict = {}
        order_list_item_fees = {}
        refund_list_item_price = {}
        amazon_other_transaction_list = {}
        product_dict = {}
        order_dict = {}

        settlement_orders = set()
        verify_next_line = False
        last_order_reference = ''
        last_processed_line = 0

        _logger.info("Preparing settlement queue data : %s"%(str(datetime.now())))
        for row in settlement_reader:
            if settlement_reader.line_num <= self.last_processed_line:
                continue

            settlement_id = row.get('settlement-id')
            if not row.get('transaction-type'):
                continue

            order_ref = row.get('order-id')
            if verify_next_line and last_order_reference != order_ref:
                break
            settlement_orders.add(order_ref)
            last_processed_line = settlement_reader.line_num

            shipment_id = row.get('shipment-id')
            order_item_code = row.get('order-item-code').lstrip('0')
            posted_date = row.get('posted-date')
            fulfillment_by = row.get('fulfillment-id')
            adjustment_id = row.get('adjustment-id')
            _logger.info("LINE NUM: %s and order ref %s" % (last_processed_line, row.get('order-id')))

            try:
                posted_date = datetime.strptime(posted_date, '%d.%m.%Y')
            except Exception:
                posted_date = datetime.strptime(posted_date, '%Y-%m-%d')

            amount = float(row.get('amount').replace(',', '.'))
            if row.get('transaction-type', '') in ['Order', 'Refund', 'Liquidations']:
                if (row.get('amount-description', '').__contains__('MarketplaceFacilitator') or \
                        row.get('amount-description', '').__contains__('LowValueGoods') or \
                        row.get('amount-type', '') == 'ItemFees' or
                        row.get('amount-description', '') == 'RegulatoryFee'):
                    order_list_item_fees = self.prepare_order_list_item_fees_ept(
                        row, settlement_id, amount, posted_date, order_list_item_fees)
                    continue

                if row.get('transaction-type', '') == 'Liquidations':
                    amazon_other_transaction_list = self.amz_prepare_order_liquidations_values(
                        row, amount, posted_date, settlement_id, amazon_other_transaction_list)
                    continue
                amz_order = self.get_settlement_report_amazon_order_ept(row)
                order_ids = order_dict.get((order_ref, shipment_id, order_item_code))
                if not order_ids:
                    order_ids = tuple(amz_order.ids)
                    order_dict.update({(order_ref, shipment_id, order_item_code): order_ids})

                partner = partner_obj.with_context(is_amazon_partner=True)._find_accounting_partner(
                    amz_order.mapped('partner_id'))
                if row.get('transaction-type', '') == 'Order':
                    key = (order_ref, order_ids, posted_date, fulfillment_by, partner.id, shipment_id)
                    order_list_item_price = self.get_amazon_order_list_item_price(key, amount, order_list_item_price)

                elif row.get('transaction-type', '') == 'Refund':
                    product_id = self.amz_get_shipment_prd_for_refund_invoice(row, self.instance_id, order_ids)
                    if not product_id:
                        product_id = product_dict.get(row.get('sku', ''))
                    if not product_id:
                        amazon_product = amazon_product_obj.search([('seller_sku', '=', row.get('sku', '')),
                                                                    ('instance_id', '=', self.instance_id.id)], limit=1)
                        product_id = amazon_product.product_id.id
                        product_dict.update({row.get('sku', ''): amazon_product.product_id.id})
                    key = (order_ref, order_ids, posted_date, fulfillment_by, partner.id, adjustment_id)
                    if not refund_list_item_price.get(key, 0.0):
                        refund_list_item_price.update({key: amount})
                    else:
                        existing_amount = refund_list_item_price.get(key, 0.0)
                        refund_list_item_price.update({key: existing_amount + amount})

                    create_or_update_refund_dict = self.get_settlement_refund_dict_ept(
                        row, key, product_id, create_or_update_refund_dict)
            else:
                if row.get('amount-type') in ['other-transaction', 'FBA Inventory Reimbursement']:
                    key = (row.get('amount-type', ''), posted_date, row.get('amount-description', ''), settlement_id)
                elif row.get('transaction-type') in ['Order_Retrocharge']:
                    key = (row.get('transaction-type'), posted_date, order_ref, settlement_id)
                else:
                    key = (row.get('amount-type', ''), posted_date, '', settlement_id)
                existing_amount = amazon_other_transaction_list.get(key, 0.0)
                amazon_other_transaction_list.update({key: existing_amount + amount})

            if len(settlement_orders) >= 200:
                last_order_reference = order_ref
                verify_next_line = True

        _logger.info("Prepared settlement queue data : %s" % (str(datetime.now())))
        return {'fees': order_list_item_fees, 'order': order_list_item_price,
                'refund': refund_list_item_price, 'other': amazon_other_transaction_list,
                'refund_invoces': create_or_update_refund_dict}, last_processed_line

    def auto_process_settlement_report(self, args={}):
        """
        Ovveride method to  Mark that as Ready to Import and create the bank statement which settlement
        reports which are in Done State.
        """
        seller_id = args.get('seller_id', False)
        if seller_id:
            seller = self.env['amazon.seller.ept'].search([('id', '=', seller_id)])
            if seller:
                settlement_reports = self.search([('seller_id', '=', seller.id), ('state', 'in', ['_DONE_', 'DONE']),
                                                  ('report_id', '!=', False)])
                for report in settlement_reports:
                    if report.instance_id:
                        report.with_context(is_auto_process=True).process_settlement_report_file()
                        self._cr.commit()
        return True

    def auto_reconcile_settlement_report(self, args={}):
        """
        Added by twinkalc to reconcile the statement statements which are in imported status.
        """
        seller_id = args.get('seller_id', False)
        if seller_id:
            seller = self.env['amazon.seller.ept'].search([('id', '=', seller_id)])
            if seller:
                settlement_reports = self.search([('seller_id', '=', seller.id), ('state', 'in', ['imported']),
                                                  ('report_id', '!=', False)])
                for report in settlement_reports:
                    if report.instance_id:
                        report.with_context(is_auto_process=True).reconcile_remaining_transactions()
        return True

    def get_settlement_report_bank_statement(self):
        """
        Define method for create bank statement for settlement report.
        """
        imp_file = StringIO(base64.b64decode(self.attachment_id.datas).decode())
        content = imp_file.read()
        delimiter = ('\t', csv.Sniffer().sniff(content.splitlines()[0]).delimiter)[bool(content)]
        settlement_reader = csv.DictReader(content.splitlines(), delimiter=delimiter)
        journal = self.instance_id.settlement_report_journal_id
        total_settlement_lines = len(list(settlement_reader)) + 1
        settlement_reader = csv.DictReader(content.splitlines(), delimiter=delimiter)
        bank_statement = self.statement_id or False
        for row in settlement_reader:
            settlement_id = row.get('settlement-id')
            if bank_statement:
                break
            else:
                bank_statement = self.create_settlement_report_bank_statement(row, journal,
                                                                              settlement_id)
                if not bank_statement:
                    break
        return bank_statement, total_settlement_lines

    def make_amazon_fee_entry(self, bank_statement, fees_type_dict):
        """
        Override this method for search fees line already exist with same
        reference than update line amount in that bank statement line other wise
        create new bank statement line.
        @:param bank_statement : bank statement
        @:fees_type_dict : to create amazon fees bank statement lines
        records.
        :return : boolean(True)
        """
        bank_statement_line_obj = self.env['account.bank.statement.line']
        for key, value in fees_type_dict.items():
            if value != 0:
                name = "%s/%s/%s" % (key[0], key[1], key[2])
                exist_bank_statement_line = bank_statement_line_obj.search([('statement_id', '=', bank_statement.id),
                                                                            ('payment_ref', '=', name)], limit=1)
                if exist_bank_statement_line:
                    exist_bank_statement_line.write({'amount': exist_bank_statement_line.amount + value})
                else:
                    bank_line_vals = {
                        'payment_ref': name,
                        'amount': value,
                        'statement_id': bank_statement.id,
                        'date': key[1],
                        'amazon_code': key[2]
                    }
                    bank_statement_line_obj.create(bank_line_vals)
        return True

    def make_amazon_other_transactions(self, seller, bank_statement, other_transactions):
        """
        Override this method for search other transaction line already exist with same
        reference than update line amount in that bank statement line other wise
        create new bank statement line.
        @:param seller : amazon.seller.ept() object
        @:param bank_statement : bank statement.
        @:param other_transactions : amazon other transactions list.
        This method is used to create bank statement lines of other
        transactions.
        """
        transaction_obj = self.env['amazon.transaction.line.ept']
        bank_statement_line_obj = self.env['account.bank.statement.line']
        trans_line_ids = transaction_obj.search([('seller_id', '=', seller.id)])

        fees_transaction_list = {trans_line_id.transaction_type_id.amazon_code: trans_line_id.id for trans_line_id in
                                 trans_line_ids}
        bank_line_vals = []
        for transaction, amount in other_transactions.items():
            if amount == 0.00:
                continue
            trans_type = transaction[0]
            trans_id = transaction[2]
            date_posted = transaction[1]
            settlement_ref = transaction[3]
            trans_type = trans_id if trans_type in ['other-transaction',
                                                    'FBA Inventory Reimbursement'] else trans_type
            if trans_id == 'Liquidations':
                trans_type = trans_id
            trans_type_line_id = fees_transaction_list.get(trans_type) or fees_transaction_list.get(
                    trans_id)
            trans_line = trans_type_line_id and transaction_obj.browse(trans_type_line_id)
            if trans_type:
                name = "%s/%s/%s/%s" % (settlement_ref, trans_type, trans_id, date_posted)
                if not trans_id:
                    name = "%s/%s/%s" % (settlement_ref, trans_type, date_posted)
            if (not trans_line) or (
                    trans_line and not trans_line.transaction_type_id.is_reimbursement):
                exist_bank_statement_line = bank_statement_line_obj.search([('statement_id', '=', bank_statement.id),
                                                                            ('payment_ref', '=', name)], limit=1)
                if exist_bank_statement_line:
                    exist_bank_statement_line.write({'amount': exist_bank_statement_line.amount + amount})
                else:
                    bank_line_vals.append({
                        'payment_ref': name,
                        'amount': amount,
                        'statement_id': bank_statement.id,
                        'date': date_posted,
                        'amazon_code': trans_type
                    })

            elif trans_line.transaction_type_id.is_reimbursement:
                name = "%s/%s/%s/%s" % (settlement_ref, trans_type, date_posted, 'Reimbursement')
                self.make_amazon_reimbursement_line_entry(bank_statement, date_posted, trans_type,
                                                          {name: amount})
        if bank_line_vals:
            bank_statement_line_obj.create(bank_line_vals)
        return True

    def make_amazon_reimbursement_line_entry(self, bank_statement, date_posted, trans_type, fees_type_dict):
        """
        Override this method for search reimbursement line already exist with same
        reference than update line amount in that bank statement line other wise
        create new bank statement line.
        @:param bank_statement : bank statement
        @:param date_posted : statement line date
        @:fees_type_dict : reimbursement line dict
        :return : account.bank.statement.line() object
        """
        bank_statement_line_obj = self.env['account.bank.statement.line']
        bank_line_vals = []
        for fee_type, amount in fees_type_dict.items():
            if amount != 0.00:
                exist_bank_statement_line = bank_statement_line_obj.search([('statement_id', '=', bank_statement.id),
                                                                            ('payment_ref', '=', fee_type)], limit=1)
                if exist_bank_statement_line:
                    exist_bank_statement_line.write({'amount': exist_bank_statement_line.amount + amount})
                else:
                    bank_line_vals.append({'payment_ref': fee_type, 'amount': amount,
                                           'statement_id': bank_statement.id, 'date': date_posted,
                                           'amazon_code': trans_type})
        if bank_line_vals:
            bank_statement_line_obj.create(bank_line_vals)
        return True
