# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.


"""
Imported datetime because during convert the string of dict to dict data contain the posted date
with date object.
"""
import datetime
import logging
from odoo import models, fields, api, _
import functools

_logger = logging.getLogger("Amazon")


class AmazonSettlementQueue(models.Model):
    _name = "settlement.process.queue.ept"
    _description = "Settlement Queues"

    name = fields.Char('Reference', copy=False, readonly=True, default=lambda x: _('New'))
    settlement_id = fields.Many2one('settlement.report.ept', string="Settlement Reference",
                                    help="Settlement Reference")
    settlement_queue_line_ids = fields.One2many('settlement.process.queue.line.ept', 'settlement_queue_id',
                                                string="Settlement Queue Lines")
    settlement_remaining_lines = fields.Integer(string="Settlement Reference",
                                                help="Settlement Remaining lines to process")

    @api.model
    def create(self, vals):
        """
        Inherited by twinkalc on 22 Mar 2021 to update an sequence of queues.
        """
        if not vals.get('name') or vals['name'] == _('New'):
            vals['name'] = self.env['ir.sequence'].next_by_code('settlement_process_queue_ept_sequence') or _('New')
        return super(AmazonSettlementQueue, self).create(vals)

    def create_and_process_amazon_settlements(self, args={}):
        """
        Added by twinkalc on 22 Mar 2021
        This method will prepare an settlement queue data and process to create an settlement queues.
        Also It will process those queues to create an bank statement lines.
        """
        seller_id = args.get('seller_id', False)
        if not seller_id:
            return True
        seller = self.env['amazon.seller.ept'].search([('id', '=', seller_id)])
        if not seller:
            return True
        settlement_obj = self.env['settlement.report.ept']
        settlement_data_queue_line_obj = self.env['settlement.process.queue.line.ept']
        settlement_ids = settlement_obj.search([('state', '=', 'ready_to_import'), ('seller_id', '=', seller.id)],
                                               order="id asc")
        queue_line_counter = 0
        for settlement_id in settlement_ids:
            _logger.info("PROCESSING SETTLEMENT REPORT %s" % settlement_id)
            settlement_queue_id = settlement_id.settlement_queue_id or False
            bank_statement, total_settlement_lines = settlement_id.get_settlement_report_bank_statement()
            if not bank_statement:
                continue
            if not settlement_id.statement_id:
                settlement_id.write({'statement_id': bank_statement.id})
            settlement_ref = bank_statement.settlement_ref
            if not settlement_queue_id and self.settlement_remaining_lines == 0.0:
                settlement_queue_id = self.create({'settlement_id': settlement_id.id,
                                                   'settlement_remaining_lines': total_settlement_lines})

            if settlement_queue_id:
                if settlement_queue_id.settlement_remaining_lines != 0.0:
                    settlement_data_dict, last_processed_line = settlement_id.get_amazon_settlement_queue_data()
                    settlement_processed_lines = last_processed_line - settlement_id.last_processed_line
                    settlement_remaining_lines = settlement_queue_id.settlement_remaining_lines - settlement_processed_lines
                    settlement_queue_id.write({'settlement_remaining_lines': settlement_remaining_lines})
                    for key, value in settlement_data_dict.items():
                        if value:
                            settlement_data_queue_line_obj.create({
                                'settlement_queue_id': settlement_queue_id.id,
                                'queue_type': key,
                                'settlement_data': value})
                    settlement_id.write(
                        {'settlement_queue_id': settlement_queue_id, 'last_processed_line': last_processed_line})
                    self._cr.commit()

                if settlement_queue_id.settlement_remaining_lines == 0.0:
                    for settlement_queue_line in settlement_queue_id.settlement_queue_line_ids:
                        settlement_data = settlement_queue_line.settlement_data
                        settlement_data = eval(settlement_data)
                        if settlement_queue_line.settlement_data == '{}':
                            settlement_queue_line.unlink()
                            continue
                        queue_line_counter += 1
                        if queue_line_counter > 2:
                            break
                        for i in range(0, len(settlement_data), 5):
                            if isinstance(settlement_data, str):
                                settlement_data = eval(settlement_data)
                            splited_dict = dict(list(settlement_data.items())[0: 5])
                            rem_list = list(splited_dict.keys())
                            if settlement_queue_line.queue_type == 'fees':
                                settlement_id.make_amazon_fee_entry(bank_statement, splited_dict)
                            elif settlement_queue_line.queue_type == 'other':
                                settlement_id.make_amazon_other_transactions(seller, bank_statement, splited_dict)
                            elif settlement_queue_line.queue_type == 'order':
                                settlement_id.process_settlement_orders(bank_statement, settlement_ref, splited_dict)
                            elif settlement_queue_line.queue_type == 'refund':
                                settlement_id.process_settlement_refunds(bank_statement.id, splited_dict)
                            else:
                                settlement_id.create_refund_invoices(splited_dict, bank_statement)
                            [settlement_data.pop(key) for key in rem_list]
                            settlement_queue_line.write({'settlement_data': settlement_data})
                            self._cr.commit()

            if bank_statement.balance_end == 0.0 and not settlement_queue_id.settlement_queue_line_ids:
                settlement_id.write({'state': 'imported'})
                settlement_queue_id.unlink()
            self._cr.commit()
        return True
