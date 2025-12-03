# -*- coding: utf-8 -*-
# See LICENSE file for full copyright and licensing details.
import base64
import csv
from io import StringIO
from odoo import models, fields, api, _


class AmazonProcessImportExport(models.TransientModel):
    _inherit = 'amazon.process.import.export'

    both_operations = fields.Selection(
        selection_add=[('create_settlement_report', 'Manually Create Settlement Report')])

    def import_export_processes(self):
        """
        Use: create settlement report record and attached file in the record.
        Params: Amazon Process Import Export => Self
        ----------------------------------------------
        Added by: Harsh Parekh @Emipro Technologies
        Added on: 16-02-2021
        """
        if self.both_operations == "create_settlement_report":
            self.create_settlement_report_records()
        else:
            return super(AmazonProcessImportExport, self).import_export_processes()
        return True

    def create_settlement_report_records(self):
        """
        Use: create settlement report record and attached file in the record.
        Params: Amazon Process Import Export => Self
        ----------------------------------------------
        Added by: Harsh Parekh @Emipro Technologies
        Added on: 16-02-2021
        """
        settlement_report_obj = self.env['settlement.report.ept']
        if self.file_name[-4:] != '.csv':
            return True
        data = StringIO(base64.b64decode(self.choose_file).decode())
        content = data.read()
        data.seek(0)
        delimiter = ('\t', csv.Sniffer().sniff(content.splitlines()[0]).delimiter)[bool(content)]
        settlement_reader = csv.DictReader(data, delimiter=delimiter)
        start_date = False
        end_date = False
        for row in settlement_reader:
            start_date = row.get('settlement-start-date', False)
            end_date = row.get('settlement-end-date', False)
            if start_date and end_date:
                start_date = settlement_report_obj.format_amz_settlement_report_date(start_date)
                end_date = settlement_report_obj.format_amz_settlement_report_date(end_date)
                break
        sequence = self.env.ref('amazon_ept.seq_import_settlement_report_job')
        if sequence:
            report_name = sequence.next_by_id()
        else:
            report_name = '/'
        settlement_report_obj = self.env['settlement.report.ept']
        settlement_report_record = settlement_report_obj.create(
            {'seller_id': self.seller_id.id or False,
             'name': report_name,
             'instance_id': self.instance_id.id or False,
             'currency_id': self.instance_id.country_id.currency_id.id or False,
             'user_id': self.env.user.id,
             'start_date': start_date and start_date.strftime('%Y-%m-%d'),
             'end_date': end_date and end_date.strftime('%Y-%m-%d'),
             'state': '_DONE_'})

        attachment_id = self.create_attachment_for_settlement_report(settlement_report_record)

        settlement_report_record.message_post(body=_("<b>Settlement Record has been created manually.</b>"),
                                              attachment_ids=attachment_id.ids)

        settlement_report_record.write({'attachment_id': attachment_id.id})
        return True

    def create_attachment_for_settlement_report(self, settlement_report_record):
        """
        Use: Attach file in the settlement report record when it's create manually from the wizard.
        Params:  settlement_report_record
        Return : Attachment record.
        ----------------------------------------------
        Added by: Harsh Parekh @Emipro Technologies
        Added on: 16-02-2021
        """

        vals = {
            'name': self.file_name,
            'datas': self.choose_file,
            'type': 'binary',
            'res_model': 'settlement.report.ept',
            'res_id': settlement_report_record.id
        }
        return self.env['ir.attachment'].create(vals)
