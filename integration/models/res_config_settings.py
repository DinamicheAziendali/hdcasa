# See LICENSE file for full copyright and licensing details.

import binascii
import os


from odoo import fields, models, _

# API keys support
API_KEY_SIZE = 20  # in bytes
INTEGRATION_MODULES = [
    'integration',
    'integration_shopify',
    'integration_prestashop',
    'integration_magento2',
    'integration_woocommerce',
    'integration_queue_job',
    'queue_job'
]


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    integration_api_key = fields.Char(
        string='E-Commerce Integration API Key',
        compute='_compute_integration_api_key',
        help='API key for the integration.',
    )

    # Version information field
    integration_modules_version_info = fields.Text(
        string='E-Commerce Integration Modules Version Information',
        compute='_compute_integration_modules_version_info',
        readonly=True,
        help='Complete version information for all integration-related modules.',
    )

    def _compute_integration_api_key(self):
        """ Compute API key for the installed integration. """
        for record in self:
            record.integration_api_key = self.get_integration_api_key()

    def _compute_integration_modules_version_info(self):
        """ Compute comprehensive version information for all integration modules. """
        for record in self:
            version_info = self._get_formatted_version_info()
            record.integration_modules_version_info = version_info

    def _get_formatted_version_info(self):
        """
        Get formatted version information for all integration modules.
        """
        # Get all integration-related modules
        all_modules = self.env['ir.module.module'].search([
            ('name', 'in', INTEGRATION_MODULES)
        ])

        version_info = []

        # Check each module from the list
        for module in all_modules:
            status = "✓ INSTALLED" if module.state == 'installed' else "✗ NOT INSTALLED"
            version = module.latest_version or module.installed_version or 'Unknown'
            version_info.append(f"{module.name:<25} | {version:<12} | {status:<15}")

        return '\n'.join(version_info)

    def generate_integration_api_key(self):
        """ Generate API key for the installed integration. """
        api_key = binascii.hexlify(os.urandom(API_KEY_SIZE)).decode()
        self.env['ir.config_parameter'].sudo().set_param('integration.integration_api_key', api_key)
        return self._compute_integration_api_key()

    def get_values(self):
        """ Get values for the installed integration. """
        res = super(ResConfigSettings, self).get_values()

        res.update(
            integration_api_key=self.get_integration_api_key(),
            integration_modules_version_info=self._get_formatted_version_info(),
        )

        return res

    def get_integration_api_key(self):
        """ Get API key for the installed integration. """
        return self.env['ir.config_parameter'].sudo().get_param('integration.integration_api_key')

    def validate_configuration(self):
        wizard = self.env['integration.installation.wizard'].create({})

        return wizard.check_odoo_setup_for_integration()

    def open_getting_started_guide(self):
        return {
            'type': 'ir.actions.act_window',
            'name': _('Getting Started: E-Commerce Connector Made Easy'),
            'res_model': 'integration.configuration.wizard',
            'view_mode': 'form',
            'target': 'new',
        }
