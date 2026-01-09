# See LICENSE file for full copyright and licensing details.

import json
import logging

from odoo import models, fields
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class IntegrationOrderFieldMappingEditorWizard(models.TransientModel):
    _name = 'integration.order.field.mapping.editor.wizard'
    _description = 'Integration Order Field Mapping Editor Wizard'

    integration_id = fields.Many2one(
        string='E-Commerce Store',
        related='mapping_field_id.integration_id',
    )

    mapping_field_id = fields.Many2one(
        comodel_name='external.order.field.mapping',
        string='Order Field Mapping',
        help='Reference to order field mapping.'
    )

    external_order_field = fields.Char(
        related='mapping_field_id.external_order_field',
        string='External Order Field',
        readonly=False,
    )

    test_input_file_id = fields.Many2one(
        comodel_name='sale.integration.input.file',
        string='External Data File',
        domain="[('si_id', '=', integration_id)]",
        help='File to test the Python code against. Can be a JSON file from field Raw Data.'
    )

    integration_code = fields.Text(
        related='mapping_field_id.preprocess_script',
        string="Code",
        readonly=False,
        help="Python code to execute.",
    )

    result = fields.Char(
        string="Result",
        readonly=True,
        help="Result of the executed Python code."
    )

    def action_test(self):
        """Test the Python script with external JSON input and field path."""
        self.ensure_one()

        if not self.test_input_file_id or not self.external_order_field:
            raise UserError('Please provide both an External Data File and an External Order Field.')

        # Try to resolve the value via external_order_field path
        order_data = json.loads(self.test_input_file_id.raw_data)
        value = self.mapping_field_id.calculate_value(order_data, show_error=True)
        self.result = str(value)

        return {
            'type': 'ir.actions.act_window',
            'name': 'Write Pre-processing Script',
            'res_model': self._name,
            'view_mode': 'form',
            'res_id': self.id,
            'target': 'new',
            'context': self.env.context,
        }

    def action_save_code(self):
        """Save the current code and external field to the mapping record."""
        self.ensure_one()

        if not self.mapping_field_id:
            raise UserError("No mapping record associated.")

        self.mapping_field_id.write({
            'preprocess_script': self.integration_code,
            'external_order_field': self.external_order_field,
        })

        return {'type': 'ir.actions.act_window_close'}
