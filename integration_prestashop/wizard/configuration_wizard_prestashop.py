#  See LICENSE file for full copyright and licensing details.

from odoo import models, fields, _
from odoo.exceptions import UserError

import re


API_NO = 'no'
API_YES = 'yes'
API_NOT_USED = 'not_used'

USED_RESOURCES = {
    'addresses': '10001',  # GET, PUT, POST, DELETE, REQUIRED
    'carriers': '10001',
    'categories': '11111',
    'combinations': '11111',
    'configurations': '10001',
    'countries': '10001',
    'customers': '10001',
    'currencies': '10001',
    'groups': '10001',
    'images': '11111',
    'languages': '10001',
    'messages': '10001',
    'orders': '10001',
    'order_carriers': '11001',
    'order_details': '10001',
    'order_states': '10001',
    'order_payments': '10001',
    'manufacturers': '10100',
    'products': '11111',
    'product_options': '11111',
    'product_option_values': '11111',
    'product_features': '11111',
    'product_feature_values': '11111',
    'states': '10001',
    'stock_availables': '11001',
    'taxes': '10001',
    'tax_rule_groups': '10001',
    'webhooks': '10110',
    'specific_prices': '10111',
}


class QuickConfigurationPrestashop(models.TransientModel):
    _name = 'configuration.wizard.prestashop'
    _inherit = 'configuration.wizard'
    _description = 'Quick Configuration for Prestashop'
    _steps = [
        ('step_url', 'Step 1. Enter Webservice Url and Key'),
        ('step_api', 'Step 2. Checking the Availability of Prestashop Resources'),
        ('step_languages', 'Step 3. Languages Mapping'),
        ('step_tax_group', 'Step 4. Configure Default Taxes for each Tax Rule'),
        ('step_order_status', 'Step 5. Sales Orders statuses management'),
        ('step_finish', 'Finish')
    ]

    state = fields.Char(
        default='step_url',
    )
    url = fields.Char(
        string='Shop Url',
    )
    admin_url = fields.Char(
        string='Admin Url',
        help='This URL us needed in order to provide quick links to products '
             'in admin console after validation of products happened'
    )
    key = fields.Char(
        string='Webservice Key',
    )
    configuration_tax_group_ids = fields.One2many(
        comodel_name='configuration.wizard.prestashop.tax.group',
        inverse_name='configuration_wizard_id',
        string='Taxes for Each Tax Rule',
    )
    order_status_ids = fields.Many2many(
        comodel_name='integration.sale.order.sub.status.external',
        string='Receive Orders in Status',
        relation='configuration_wizard_prestashop_status_rel',
        domain='[("integration_id", "=", integration_id)]',
    )
    configuration_api_ids = fields.One2many(
        comodel_name='configuration.wizard.prestashop.api',
        inverse_name='configuration_wizard_id',
        string='Api Resources',
    )
    run_action_on_cancel_so = fields.Boolean(
        string='Sync Cancelled SO Status',
        help=(
            'Enable this option to automatically update the order status in the e-commerce '
            'system when a sales order is cancelled in Odoo.'
        ),
    )
    sub_status_cancel_id = fields.Many2one(
        comodel_name='sale.order.sub.status',
        string='Sub-Status for Cancelled SO',
        domain='[("integration_id", "=", integration_id)]',
        copy=False,
        help=(
            'Specify the sub-status that will be send to the e-commerce system when '
            'a sales order is cancelled in Odoo.'
        ),
    )
    run_action_on_shipping_so = fields.Boolean(
        string='Sync Shipped SO Status',
        help=(
            'Activate this option to update the order status in the e-commerce system '
            'automatically when a sales order is marked as shipped in Odoo.'
        ),
    )
    sub_status_shipped_id = fields.Many2one(
        comodel_name='sale.order.sub.status',
        string='Sub-Status for Shipped SO',
        domain='[("integration_id", "=", integration_id)]',
        copy=False,
        help=(
            'Specify the sub-status that will be sent to the e-commerce system for '
            'orders marked as shipped in Odoo.'
        ),
    )
    run_action_on_so_invoice_status = fields.Boolean(
        string='Sync Invoiced/Paid SO Status',
        help=(
            'Enable this option to update the order status in the e-commerce system for '
            'sales orders that are invoiced or marked as paid in Odoo. Specific behaviors '
            'based on the payment method can be configured under '
            '"Auto-Workflow → Payment Methods", where you can adjust when the payment status '
            'is sent. By default, the action occurs when an order is fully invoiced, '
            'and all related invoices are "Paid" or "In Payment".'
        ),
    )
    sub_status_paid_id = fields.Many2one(
        comodel_name='sale.order.sub.status',
        string='Sub-Status for Invoiced/Paid SO',
        domain='[("integration_id", "=", integration_id)]',
        copy=False,
        help=(
            'Choose the sub-status to be applied to orders in the e-commerce system once '
            'the corresponding sales order in Odoo is fully paid.'
        ),
    )

    # Step Url
    def run_before_step_url(self):
        self.url = self.integration_id.get_settings_value('url')
        self.admin_url = self.integration_id.get_settings_value('admin_url')
        self.key = self.integration_id.get_settings_value('key')

    def run_after_step_url(self):
        self.integration_id.set_settings_value('url', self.url)
        self.integration_id.set_settings_value('admin_url', self.admin_url)
        self.integration_id.set_settings_value('key', self.key)

        self.integration_id.increment_sync_token()
        self.integration_id.action_active()

        return True

    # Step Languages
    def run_after_step_languages(self):
        res = super().run_after_step_languages()
        if res:
            self.integration_id.set_settings_value('language_id', self.language_default_id.code)
        return res

    # Step Tax Group
    def run_before_step_tax_group(self):
        self.integration_id.initial_import_taxes()
        self.configuration_tax_group_ids.unlink()

        values_list = list()
        default_vals = dict(configuration_wizard_id=self.id)

        tax_group_ids = self.env['integration.account.tax.group.external'].search([
            ('integration_id', '=', self.integration_id.id),
        ])
        for tax_group in tax_group_ids:
            values = {
                **default_vals,
                'external_tax_group_id': tax_group.id,
                'default_external_tax_id': tax_group.default_external_tax_id.id,
                'sequence': tax_group.sequence,
            }
            values_list.append(values)

        self.env['configuration.wizard.prestashop.tax.group'].create(values_list)

    def run_after_step_tax_group(self):
        for line in self.configuration_tax_group_ids:
            tax_group = line.external_tax_group_id
            tax_group.default_external_tax_id = line.default_external_tax_id.id
            tax_group.sequence = line.sequence

        return True

    # Step Order Status
    def run_before_step_order_status(self):
        self.integration_id.integrationApiImportSaleOrderStatuses()

        self.run_action_on_cancel_so = self.integration_id.run_action_on_cancel_so
        self.sub_status_cancel_id = self.integration_id.sub_status_cancel_id

        self.run_action_on_shipping_so = self.integration_id.run_action_on_shipping_so
        self.sub_status_shipped_id = self.integration_id.sub_status_shipped_id

        self.run_action_on_so_invoice_status = self.integration_id.run_action_on_so_invoice_status
        self.sub_status_paid_id = self.integration_id.sub_status_paid_id

    def run_after_step_order_status(self):
        if not self.order_status_ids:
            raise UserError(_('You should select order sub-statuses'))

        ids = [x.code for x in self.order_status_ids]

        receive_filter = self.integration_id.get_class().default_receive_orders_filter
        receive_filter = re.sub('<put state id here>', '|'.join(ids), receive_filter)

        self.integration_id.set_settings_value('receive_orders_filter', receive_filter)

        self.integration_id.run_action_on_cancel_so = self.run_action_on_cancel_so
        self.integration_id.run_action_on_shipping_so = self.run_action_on_shipping_so
        self.integration_id.run_action_on_so_invoice_status = self.run_action_on_so_invoice_status

        self.integration_id.sub_status_cancel_id = self.sub_status_cancel_id
        self.integration_id.sub_status_shipped_id = self.sub_status_shipped_id
        self.integration_id.sub_status_paid_id = self.sub_status_paid_id

        if self.sub_status_cancel_id or self.sub_status_shipped_id:
            self.integration_id.export_sale_order_status_job_enabled = True

        return True

    # Step API
    def run_before_step_api(self):
        values_list = list()
        self.configuration_api_ids = None
        resources = self.integration_id._build_adapter().get_api_resources()
        default_vals = dict(configuration_wizard_id=self.id)

        def get_setting_value(res_name, res_used, method_name):
            if method_name == 'required':
                return API_YES if int(res_used) else API_NO

            if not int(res_used):
                return API_NOT_USED

            value = resources.get('api', {}).get(res_name, {}).get('attrs', {}).get(method_name)
            return API_YES if value == 'true' else API_NO

        for resource_name, usage in USED_RESOURCES.items():
            values = {
                **default_vals,
                'resource_name': resource_name,
                'method_get': get_setting_value(resource_name, usage[0], 'get'),
                'method_put': get_setting_value(resource_name, usage[1], 'put'),
                'method_post': get_setting_value(resource_name, usage[2], 'post'),
                'method_delete': get_setting_value(resource_name, usage[3], 'delete'),
                'method_required': get_setting_value(resource_name, usage[4], 'required'),
            }
            values_list.append(values)

        self.env['configuration.wizard.prestashop.api'].create(values_list)

    def run_after_step_api(self):
        self.run_before_step_api()

        if self.configuration_api_ids.filtered(
                lambda x: (x.method_required == API_YES
                           and (API_NO in (
                                x.method_get, x.method_put, x.method_post, x.method_delete
                                )))):
            raise UserError(_('You should grant access to all required Prestashop resources'))

        return True

    def action_recheck_api(self):
        self.run_before_step_api()

        return self.get_action_view()

    @staticmethod
    def get_form_xml_id():
        return 'integration_prestashop.view_configuration_wizard'


class QuickConfigurationPrestashopTaxGroup(models.TransientModel):
    _name = 'configuration.wizard.prestashop.tax.group'
    _inherit = 'configuration.wizard.tax.group.abstract'
    _description = 'Quick Configuration Prestashop Tax Group'

    sequence = fields.Integer(
        help=(
            'When exporting from Odoo to Prestashop, take the most priority item first '
            'in Order to define which Odoo Tax correspond to which tax group.'
        ),
    )
    configuration_wizard_id = fields.Many2one(
        comodel_name='configuration.wizard.prestashop',
    )
    external_tax_group_id = fields.Many2one(
        string='Prestashop External Tax Rule',
    )


class QuickConfigurationPrestashopApi(models.TransientModel):
    _name = 'configuration.wizard.prestashop.api'
    _description = 'Quick Configuration Prestashop Api'

    def _get_api_method_usage(self):
        return [(API_YES, 'Yes'), (API_NO, 'No'), (API_NOT_USED, 'Not Used')]

    configuration_wizard_id = fields.Many2one(
        comodel_name='configuration.wizard.prestashop',
        ondelete='cascade',
    )
    resource_name = fields.Char(
        string='Resource',
    )
    method_get = fields.Selection(
        selection=_get_api_method_usage,
        string='GET',
    )
    method_put = fields.Selection(
        selection=_get_api_method_usage,
        string='PUT',
    )
    method_post = fields.Selection(
        selection=_get_api_method_usage,
        string='POST',
    )
    method_delete = fields.Selection(
        selection=_get_api_method_usage,
        string='DELETE',
    )
    method_required = fields.Selection(
        selection=_get_api_method_usage,
        string='Required',
    )
