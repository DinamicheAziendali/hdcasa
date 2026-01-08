# See LICENSE file for full copyright and licensing details.

import logging

import requests

from odoo import models


_logger = logging.getLogger(__name__)

PRODUCT_IMAGE_CODE_PREFIX = 'ProductImage'


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    def to_images_export_format(self, integration: 'models.Model'):
        if integration.is_integration_shopify:
            if not self._perform_shopify_images_migration(integration):
                # If something happened - drop all maggings and perform export from the scratch
                external_template = self.to_external_record(integration)
                external_template.all_image_external_ids.unlink()

        return super().to_images_export_format(integration)

    def _perform_shopify_images_migration(self, integration: 'models.Model'):
        """Migration of the Shopify images gids from <ProductImage> to <MediaImage>"""
        external_template = self.to_external_record(integration)
        external_images = external_template.all_image_external_ids.filtered('code')

        if any((PRODUCT_IMAGE_CODE_PREFIX in x.code) for x in external_images):
            adapter = integration.adapter

            # Get credentials
            headers = adapter._graphql.headers
            url = adapter._graphql._site.rsplit('/', 1)[0] + f'/2025-10/products/{external_template.code}/images.json'

            # Make a request to get the images
            try:
                response = requests.get(url, params={'fields': 'id,admin_graphql_api_id'}, headers=headers)
            except Exception as ex:
                _logger.error(ex)
                return False

            if not response.ok:
                _logger.error(response.text)
                return False

            try:
                mappings = {
                    str(x['id']): x['admin_graphql_api_id'] for x in response.json()['images']
                }
            except Exception as ex:
                _logger.error(ex)
                return False

            # Update codes
            for record in external_images.filtered(lambda x: PRODUCT_IMAGE_CODE_PREFIX in x.code):
                code = record.code.rsplit('/', 1)[-1]
                if code in mappings:
                    record.code = mappings[code]

        return True
