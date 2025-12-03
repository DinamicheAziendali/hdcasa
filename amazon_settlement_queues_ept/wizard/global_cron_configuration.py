# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""
Added class and fields to configure the GLOBAL cron and added fields to active the
common FBA and FBM settlement scheduler configurations.
"""
from datetime import datetime
from dateutil.relativedelta import relativedelta
from odoo import models, _
from odoo.exceptions import UserError


class GlobalCronConfiguration(models.TransientModel):
    """
    Inherited class to configure the FBM and FBA settlement scheduler.
    """
    _inherit = "global.cron.configuration"

    def setup_amz_settlement_report_process_cron(self, seller):
        """
        This method will active the cron to process settlement report.
        """
        res = super(GlobalCronConfiguration, self).setup_amz_settlement_report_process_cron(seller)
        if self.amz_settlement_report_auto_create:
            self.with_context(is_auto_process=True).process_amz_settlement_queues_cron(seller)
        else:
            queue_cron_exist = self.env.ref(
                'amazon_settlement_queues_ept.ir_cron_to_create_and_process_settlement_reports_seller_%d' % (seller.id),
                raise_if_not_found=False)
            if queue_cron_exist:
                queue_cron_exist.write({'active': False})
            reconcile_cron_exist = self.env.ref(
                'amazon_settlement_queues_ept.ir_cron_auto_reconcile_settlement_report_seller_%d' % (seller.id),
                raise_if_not_found=False)
            if reconcile_cron_exist:
                reconcile_cron_exist.write({'active': False})

        return res

    def process_amz_settlement_queues_cron(self, seller):
        """
        This method will active the cron to process settlement report.
        param amazon_seller : seller record.
        """
        cron_exist = self.env.ref(
            'amazon_settlement_queues_ept.ir_cron_to_create_and_process_settlement_reports_seller_%d' % (seller.id),
            raise_if_not_found=False)

        if self.amz_settlement_report_create_next_execution:
            process_next_execution = self.amz_settlement_report_create_next_execution + relativedelta(minutes=20)
        else:
            process_next_execution = datetime.now()

        vals = {'active': True,
                'nextcall': process_next_execution,
                'user_id': self.amz_settlement_report_create_user_id.id if self.amz_settlement_report_create_user_id else self.env.user.id,
                'code': "model.create_and_process_amazon_settlements({'seller_id':%d})" % (seller.id),
                'amazon_seller_cron_id': seller.id}

        if cron_exist:
            cron_exist.write(vals)
        else:
            cron_exist = self.env.ref('amazon_settlement_queues_ept.ir_cron_to_create_and_process_settlement_reports',
                                      raise_if_not_found=False)
            if not cron_exist:
                raise UserError(_('Core settings of Amazon are deleted, please upgrade Amazon module'
                                  ' to back this settings.'))

            name = 'FBA&FBM-' + seller.name + ' : Process Settlement Queues'
            vals.update({'name': name})
            new_cron = cron_exist.copy(default=vals)
            self.env['ir.model.data'].create({'module': 'amazon_settlement_queues_ept',
                                              'name': 'ir_cron_to_create_and_process_settlement_reports_seller_%d' % (
                                                  seller.id),
                                              'model': 'ir.cron',
                                              'res_id': new_cron.id,
                                              'noupdate': True
                                              })
        if self._context.get('is_auto_process'):
            self.reconcile_amz_settlement_statements_cron(seller)
        return True

    def reconcile_amz_settlement_statements_cron(self, seller):
        """
        This method will active the cron to process settlement report.
        param amazon_seller : seller record.
        """
        cron_exist = self.env.ref(
            'amazon_settlement_queues_ept.ir_cron_auto_reconcile_settlement_report_seller_%d' % (seller.id),
            raise_if_not_found=False)
        process_next_execution = self.amz_settlement_report_create_next_execution + relativedelta(minutes=40)
        vals = {'active': True,
                'nextcall': process_next_execution,
                'user_id': self.amz_settlement_report_create_user_id.id,
                'code': "model.auto_reconcile_settlement_report({'seller_id':%d})" % (seller.id),
                'amazon_seller_cron_id': seller.id}

        if cron_exist:
            cron_exist.write(vals)
        else:
            cron_exist = self.env.ref('amazon_settlement_queues_ept.ir_cron_auto_reconcile_settlement_report',
                                      raise_if_not_found=False)
            if not cron_exist:
                raise UserError(_('Core settings of Amazon are deleted, please upgrade Amazon module'
                                  ' to back this settings.'))

            name = 'FBA&FBM-' + seller.name + ' : Reconcile Settlement Report'
            vals.update({'name': name})
            new_cron = cron_exist.copy(default=vals)
            self.env['ir.model.data'].create({'module': 'amazon_settlement_queues_ept',
                                              'name': 'ir_cron_auto_reconcile_settlement_report_seller_%d' % (
                                                  seller.id),
                                              'model': 'ir.cron',
                                              'res_id': new_cron.id,
                                              'noupdate': True
                                              })
        return True
