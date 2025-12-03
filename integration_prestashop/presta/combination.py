#  See LICENSE file for full copyright and licensing details.

from .base_model import BaseModel


class Combination(BaseModel):
    """Prestashop product-combination (product.product)"""

    _product_id = None

    def _save(self, vals):
        is_update = bool(self.id)
        result = super()._save(vals)
        if is_update:
            self._thirtybees_forcibly_update_price_per_shop(vals)
        return result

    @property
    def product_id(self):
        if self._product_id:
            return self._product_id

        assert self.id, 'we can get product_id on for already created combination'

        return self._client.get('combinations', self.id)['combination']['id_product']

    @product_id.setter
    def product_id(self, value):
        self._product_id = value

    def assign_image(self, externat_image_id):
        data = {
            'images': {
                'attrs': {
                    'nodeType': 'image',
                    'api': 'images/products',
                },
                'image': {
                    'id': str(externat_image_id),
                }
            }
        }
        self.associations = data
        return self.save()

    def _prepare_save_vals(self):
        product_option_values_to_update = self._to_update.pop('product_option_values', None)

        combination_schema = super()._prepare_save_vals()

        if product_option_values_to_update is not None:
            option_values = \
                combination_schema['combination']['associations']['product_option_values']
            option_values['product_option_value'] = \
                [{'id': str(x)} for x in product_option_values_to_update._ids]

            # otherwise error Undefined index # TODO: common logic
            if 'value' in option_values:
                del option_values['value']

        return combination_schema

    def _thirtybees_forcibly_update_price_per_shop(self, combination):
        # Some bug on Thirtybees doesn't allow us update price when id_group_shop
        # is set. It just doesn't save price at all. But it works ok with PrestaShop.
        # So we update price per shop as workaround
        for shop_id in self._shop_ids:
            self._client.edit('combinations', combination, options={'id_shop': shop_id})

    def delete(self):
        delete_url = self._client._api_url + 'combinations/' + str(self.id)
        if self._id_group_shop:
            delete_url += '?id_group_shop=%s' % self._id_group_shop

        self._client.delete_with_url(delete_url)

    def __bool__(self):
        return bool(self.id)
