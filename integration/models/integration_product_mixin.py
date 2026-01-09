# See LICENSE file for full copyright and licensing details.

from odoo import models, _

from ..exceptions import NotMappedToExternal


class IntegrationProductMixin(models.AbstractModel):
    _name = 'integration.product.mixin'
    _description = 'Integration Product Mixin'

    _image_name = None
    _image_names = None

    def to_external_record(self, integration, raise_error=True):
        """
        Redefined method from the integration.model.mixin
        because of integration_mapping_ids field exists
        """
        self.ensure_one()

        mapping = self.integration_mapping_ids\
            .filtered(lambda x: x.integration_id.id == integration.id)[-1:]

        if not mapping and raise_error:
            raise NotMappedToExternal(
                _('Can\'t map odoo value to external code'),
                self._name,
                self.id,
                integration,
            )

        return mapping.external_record

    def to_external(self, integration):
        """Redefined method from the integration.model.mixin"""
        external = self.to_external_record(integration)
        return external.code

    def _get_extra_images(self):
        return getattr(self, self._image_names)

    def action_integration_mappings(self):
        """Open a list view with mappings for the current product"""
        mapping_ids = self.mapped('integration_mapping_ids')

        return {
            'type': 'ir.actions.act_window',
            'name': 'Product Mappings',
            'res_model': mapping_ids._name,
            'view_mode': 'list',
            'domain': [('id', 'in', mapping_ids.ids)],
            'target': 'current',
        }
