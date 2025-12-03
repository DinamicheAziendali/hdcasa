# See LICENSE file for full copyright and licensing details.

from odoo.addons.integration.models.fields import ReceiveFields


class ReceiveFieldsPresta(ReceiveFields):
    """
    Convert Prestashop format

        {
            'language': [
                {
                    'attrs': {'id': '1'},
                    'value': 'Wood Panel'
                },
                {
                    'attrs': {'id': '2'},
                    'value': 'Wood france'
                },
            ]
        }

    to Odoo format:

        {
            'language': {
                odoo_language_id_1: 'Wood Panel',
                odoo_language_id_2: 'Wood france',
            }
        }
    """

    def _get_value(self, field_name):
        result = self.external_obj.get(field_name)

        if isinstance(result, dict) and 'value' in result:
            result = result['value']

        return result
