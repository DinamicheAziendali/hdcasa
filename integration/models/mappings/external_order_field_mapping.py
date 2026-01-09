# See LICENSE file for full copyright and licensing details.

import json
import re
import logging

from odoo import api, models, fields
from odoo.tools.safe_eval import safe_eval, wrap_module
from odoo.exceptions import ValidationError

from odoo.addons.base.models.ir_actions import LoggerProxy

wrapped_json = wrap_module(json, ['loads', 'dumps'])
wrapped_re = wrap_module(re, ['match', 'fullmatch', 'search', 'sub'])

_logger = logging.getLogger(__name__)

DEFAULT_CODE = """# On tab Help you can find the available variables.
# Here write your Python code.


"""


class ExternalOrderFieldMapping(models.Model):
    """
    Defines how JSON fields from external orders map to Odoo's sales orders or stock pickings.
    Optionally, a custom Python script can preprocess each field before importing data.

    External Fields:
        - Each mapped 'external_order_field' references a path in the external JSON (e.g. "payment.code").
        - Use 'order' as the root variable to access the entire external order JSON. For example:
          'order.store_name' retrieves the "store_name" attribute from the order JSON.
        - If a field doesn't exist or cannot be located, the mapped Odoo field remains empty
          and a warning is logged instead of raising an exception.
        - Complex data (e.g. {"a": 123}) is automatically serialized into the corresponding
          Odoo field.

    Pre-processing (Optional):
        - A custom Python script can transform the raw 'value' before storing it in Odoo.
        - The following variables are available in the script:
            * integration: the integration record
            * value: the external field's raw value (or None if not found)
            * order: the complete external order JSON
            * logger: Odoo's logger
            * env: the Odoo environment
            * re: the re module
            * json: the json module
        - If no script is provided, the field is imported as-is.

    This model streamlines how external JSON attributes map to Odoo, ensuring greater flexibility
    and making it easier to handle nested or complex data structures in external orders.
    """

    _name = 'external.order.field.mapping'
    _description = 'External Order Field Mapping'

    active = fields.Boolean(
        string='Active',
        default=True,
        help="Indicates if the mapping is active.",
    )

    integration_id = fields.Many2one(
        comodel_name='sale.integration',
        string='E-Commerce Store',
        required=True,
    )

    external_order_field = fields.Char(
        string='External Order Field',
        required=True,
        default='order',
    )

    odoo_order_field_id = fields.Many2one(
        string='Odoo Sales Order Field',
        comodel_name='ir.model.fields',
        domain="[('model_id.model', '=', 'sale.order')]",
    )

    odoo_picking_field_id = fields.Many2one(
        string='Odoo Transfer Field',
        comodel_name='ir.model.fields',
        domain="[('model_id.model', '=', 'stock.picking')]",
    )

    preprocess_script = fields.Text(
        string='Preprocess Script',
        readonly=False,
        default=lambda self: self._default_preprocess_script(),
        help="Python script to preprocess the value before storing it in Odoo.",
    )

    @api.constrains('odoo_order_field_id')
    def _check_unique_order_field(self):
        for rec in self:
            if rec.odoo_order_field_id:
                domain = [
                    ('integration_id', '=', rec.integration_id.id),
                    ('odoo_order_field_id', '=', rec.odoo_order_field_id.id),
                ]
                if self.search_count(domain) > 1:
                    raise ValidationError('Sales Order Field must be unique within the same integration.')

    @api.constrains('odoo_picking_field_id')
    def _check_unique_picking_field(self):
        for rec in self:
            if rec.odoo_picking_field_id:
                domain = [
                    ('integration_id', '=', rec.integration_id.id),
                    ('odoo_picking_field_id', '=', rec.odoo_picking_field_id.id),
                ]
                if self.search_count(domain) > 1:
                    raise ValidationError('Transfer Field must be unique within the same integration.')

    @api.constrains('external_order_field')
    def _check_external_order_field_format(self):
        # Support only letters, digits, underscores, dots
        for record in self:
            if record.external_order_field:
                if not re.fullmatch(r'order(\.[a-zA-Z0-9_]+)*', record.external_order_field):
                    raise ValidationError(
                        "External Field can only contain letters, numbers, underscores, and dots.\n\n"
                        "Examples:\n- order.attribute_id\n- order.product_id\n- order\n- order.id"
                    )

    @api.model
    def _get_eval_context(self):
        """Builds the execution context for safe_eval."""
        return {
            'integration': self.integration_id,
            'env': self.env,
            'json': wrapped_json,
            're': wrapped_re,
            'logger': LoggerProxy,
        }

    @staticmethod
    def parse_field_path(data: dict, path: str, show_error: bool = False) -> str:
        """Safely extracts a nested value from a dict using a dot-notation path."""
        def _warn(title: str, message: str):
            if show_error:
                return f'[{title}] {message}'
            _logger.warning(f'[{title}] {message}')
            return ''

        if not path:
            return data

        parts = path.split('.')
        if parts[0] != 'order':
            return _warn(
                'Invalid Path',
                f'Path must start with "order", got "{parts[0]}"'
            )

        current = data
        for part in parts[1:]:
            if isinstance(current, dict):
                if part not in current:
                    return _warn(
                        'Path Error',
                        f'Failed to extract "{part}" from path "{path}":\n'
                        f'key not found in dict — current: {str(current)}'
                    )
                current = current.get(part)
            else:
                return _warn(
                    'Path Error',
                    f'Failed to extract "{part}" from path "{path}":\n'
                    f'expected dict, got {type(current).__name__} — value: {repr(current)}'
                )

        return current

    def _default_preprocess_script(self) -> str:
        return DEFAULT_CODE

    @staticmethod
    def _run_preprocessing_script(preprocess_script: str, context: dict, raise_error: bool = False) -> str:
        """
        Executes the preprocessing script in a controlled environment.
        """
        try:
            safe_eval(preprocess_script.strip(), context, mode='exec', nocopy=True)
        except Exception as e:
            if raise_error:
                raise ValidationError(f'Preprocess script execution failed: {e}')
            _logger.warning('Preprocess script execution failed: %s', e)
            return ''

        if 'value' not in context:
            if raise_error:
                raise ValidationError(
                    'Preprocess script did not assign a variable named "value".'
                )
            _logger.warning("Preprocess script did not assign a variable named 'value'.")
            return ''

        try:
            return context['value']
        except (TypeError, ValueError) as e:
            if raise_error:
                raise ValidationError(f'Failed to serialize value in preprocess script: {e}')
            _logger.warning('Failed to serialize value in preprocess script: %s', e)
            return ''

    def calculate_value(self, order_data: dict, show_error: bool = False) -> str:
        """
        Preprocess the value before storing it in Odoo.
        """
        value = self.parse_field_path(order_data, self.external_order_field, show_error=show_error)
        if not self.preprocess_script:
            return value

        # Create a local context for the script execution
        local_context = self._get_eval_context()
        local_context['value'] = value
        local_context['order'] = order_data

        return self._run_preprocessing_script(self.preprocess_script.strip(), local_context, raise_error=show_error)

    def action_edit_preprocessing_script(self):
        """
        Open a new window to edit the preprocess script.
        """
        self.ensure_one()
        wizard = self.env['integration.order.field.mapping.editor.wizard'].create({'mapping_field_id': self.id})

        return {
            'type': 'ir.actions.act_window',
            'name': 'Write Pre-processing Script',
            'res_model': 'integration.order.field.mapping.editor.wizard',
            'res_id': wizard.id,
            'target': 'new',
            'view_mode': 'form',
        }
