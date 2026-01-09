# See LICENSE file for full copyright and licensing details.

from odoo import _
from odoo.exceptions import UserError

from .product_abstract import ProductAbstractSend
from ...exceptions import NotMappedToExternal


class ProductTemplateSendMixin(ProductAbstractSend):
    """Specific behavior only for `product.template` Odoo class during sending to external."""

    def variant_converter(self):
        if not self._sub_converter:
            self._sub_converter = self.env['product.product'] \
                .init_variant_export_converter(self.integration)

        return self._sub_converter

    def ensure_template_mapped(self):
        template_ok = self.ensure_mapped()

        variants_ok = []
        converter = self.variant_converter()

        for variant in self.get_variants():
            converter.replace_record(variant)
            variants_ok.append(converter.ensure_mapped())

        return template_ok and all(variants_ok)

    def convert_to_external(self):
        variant_ids = self.get_variants()
        converter = self.variant_converter()

        products = []
        for variant in variant_ids:
            converter.replace_record(variant)
            products.append(converter.convert_to_external())

        result = {
            'id': self.odoo_obj.id,
            'external_id': self.external_id,
            'type': self.odoo_obj.type,
            'kits': self._get_kits(),
            'products': products,
            'variant_count': len(variant_ids),
            'fields': self.calculate_send_fields(self.external_id),
        }

        result_upd = self.odoo_obj._template_converter_update(
            result,
            self.integration,
            self.external_id,
        )
        return result_upd

    def convert_pricelists(self, pricelist_ids=None, item_ids=None, raise_error=False):
        force_sync_pricelist = self.odoo_obj.to_force_sync_pricelist
        if force_sync_pricelist:
            pricelist_ids = item_ids = None

        def _format_result(converter, prices):
            return (
                converter.odoo_obj.id,
                converter.odoo_obj._name,
                converter.external_id,
                prices,
                force_sync_pricelist,
            )

        t_prices_list = self._collect_specific_prices(
            pricelist_ids=pricelist_ids,
            item_ids=item_ids,
            raise_error=raise_error,
        )
        variant_ids = self.get_variants()
        converter = self.variant_converter()

        variant_data_list = list()
        for variant in variant_ids:
            converter.replace_record(variant)
            converter.ensure_external_code()

            v_prices_list = converter._collect_specific_prices(
                pricelist_ids=pricelist_ids,
                item_ids=item_ids,
                raise_error=raise_error,
            )
            if force_sync_pricelist or v_prices_list:
                variant_data = _format_result(converter, v_prices_list)
                variant_data_list.append(variant_data)

        if force_sync_pricelist or t_prices_list or variant_data_list:
            tmpl_data = _format_result(self, t_prices_list)
            return tmpl_data, variant_data_list

        return tuple()

    def get_variants(self):
        """
            Returns a sorted recordset of product variants filtered by integration.

            The method filters the product variant records based on their integration_ids and
                sorts them based on their
            attribute values. The sorting is done in the following order:
                1. The attribute ID of the attribute value
                2. The sequence number of the attribute value.

            Returns:
                recordset: A sorted recordset of product variants filtered by integration.
        """
        variants = self.odoo_obj.product_variant_ids.filtered(
            lambda x: self.integration in x.integration_ids).sorted(
            key=lambda v: [
                (attr.attribute_id.id, attr.sequence)
                for attr in
                v.product_template_attribute_value_ids.mapped('product_attribute_value_id')
            ])

        return variants

    def _get_kits(self):
        # If the integration is configured to ignore BOMs, return an empty list
        if self.integration.ignore_boms_for_product_export:
            return []

        kit = self.odoo_obj.with_context(integration_id=self.integration.id).get_integration_kits()

        result = []

        for line in kit.bom_line_ids:
            try:
                external_record = line.product_id.to_external_record(self.integration)
            except NotMappedToExternal as ex:
                raise UserError(
                    _(
                        'The product "%s" cannot be exported because one or more of its components have '
                        'not been exported yet.\n'
                        'Please review the following:\n'
                        '1. Ensure that the component products have been exported by triggering '
                        'their export if necessary.\n'
                        '2. If the component products are still pending in the export queue, please wait for '
                        'the export process to complete.\n'
                        '3. If there are failed export jobs for the component products, review '
                        'the errors, fix them, and restart the failed jobs.\n\n'
                        'Details: %s'
                    ) % (line.product_id.display_name, ex.args[0])
                )

            result.append({
                'qty': line.product_qty,
                'name': line.display_name,
                'product_id': external_record.code,
                'external_reference': external_record.external_reference,
            })

        return result

    def send_price(self, field_name):
        if self.integration.integration_pricelist_id:
            price = self.integration.integration_pricelist_id._get_product_price(self.odoo_obj, 0)
        else:
            price = self.odoo_obj.list_price
        return {
            field_name: str(self.get_price_by_send_tax_incl(price)),
        }

    def send_pricelist_sale_price(self, field_name):
        if self.integration.integration_sale_pricelist_id:
            price, pricelist_rule_id = self.integration.integration_sale_pricelist_id._get_product_price_rule(
                self.odoo_obj, 0)

            if not pricelist_rule_id:
                # No specific rule for this product, no sale price
                return {
                    field_name: None,
                }
        else:
            raise UserError(_(
                'Missing Pricelist for Product Export The "Sale Pricelist for Product Export" is required '
                'for the "%s" integration. Please either: Set the pricelist in the settings '
                '(E-Commerce Integrations → Stores → %s → Products → Sale Pricelist for Product Export) OR '
                'Deactivate the field mapping for "Product Template Pricelist Sale Price".'
            ) % (self.integration.name, self.integration.name))

        return {
            field_name: self.get_price_by_send_tax_incl(price),
        }

    def send_integration_name(self, field_name):
        return {
            field_name: self.odoo_obj.get_integration_name(self.integration),
        }
