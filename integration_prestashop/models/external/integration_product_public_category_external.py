# See LICENSE file for full copyright and licensing details.

from odoo import models, fields


class IntegrationProductPublicCategoryExternal(models.Model):
    _inherit = 'integration.product.public.category.external'

    auto_export = fields.Boolean(
        string='Auto Synchronize if Child Selected',
    )
    is_root_category = fields.Boolean(
        string='Root Category',
    )

    def _post_import_external_one(self, adapter_external_record):
        self.is_root_category = adapter_external_record.get('is_root_category', False)

    def _get_parent_recursively(self, parents=None):
        parent_list = parents or list()

        if not parent_list or self.auto_export:
            parent_list.append(self.code)

        if not self.parent_id:
            return parent_list

        return self.parent_id._get_parent_recursively(parent_list)
