from odoo import api, models, fields, tools, _
from datetime import datetime
from odoo.tools import DEFAULT_SERVER_DATE_FORMAT, float_repr
DEFAULT_FACTURX_DATE_FORMAT = '%Y%m%d'
import base64
class AccountEdiFormat(models.Model):
    _inherit = 'account.edi.format'

    def _export_facturx(self, invoice):

        def format_date(dt):
            # Format the date in the Factur-x standard.
            dt = dt or datetime.now()
            return dt.strftime(DEFAULT_FACTURX_DATE_FORMAT)

        def format_monetary(number, currency):
            # Format the monetary values to avoid trailing decimals (e.g. 90.85000000000001).
            if currency.is_zero(number):  # Ensure that we never return -0.0
                number = 0.0
            return float_repr(number, currency.decimal_places)

        self.ensure_one()
        # Create file content.
        seller_siret = 'siret' in invoice.company_id._fields and invoice.company_id.siret or invoice.company_id.company_registry
        buyer_siret = 'siret' in invoice.commercial_partner_id._fields and invoice.commercial_partner_id.siret
        template_values = {
            'record': invoice,
            'format_date': format_date,
            'format_monetary': format_monetary,
            'invoice_line_values': [],
            'seller_specified_legal_organization': seller_siret,
            'buyer_specified_legal_organization': buyer_siret,
        }
        # Tax lines.
        # The old system was making one total "line" per tax in the xml, by using the tax_line_id.
        # The new one is making one total "line" for each tax category and rate group.
        aggregated_taxes_details = invoice._prepare_edi_tax_details(
            grouping_key_generator=lambda tax_values: {
                'unece_tax_category_code': tax_values['tax_id']._get_unece_category_code(invoice.commercial_partner_id, invoice.company_id),
                'amount': tax_values['tax_id'].amount
            }
        )['tax_details']

        balance_multiplicator = -1 if invoice.is_inbound() else 1
        # Map the new keys from the backported _prepare_edi_tax_details into the old ones for compatibility with the old
        # template. Also apply the multiplication here for consistency between the old and new template.
        for tax_detail in aggregated_taxes_details.values():
            tax_detail['tax_base_amount'] = balance_multiplicator * tax_detail['base_amount_currency']
            tax_detail['tax_amount'] = balance_multiplicator * tax_detail['tax_amount_currency']

            # The old template would get the amount from a tax line given to it, while
            # the new one would get the amount from the dictionary returned by _prepare_edi_tax_details directly.
            # As the line was only used to get the tax amount, giving it any line with the same amount will give a correct
            # result even if the tax line doesn't make much sense as this is a total that is not linked to a specific tax.
            # I don't have a solution for 0% taxes, we will give an empty line that will allow to render the xml, but it
            # won't be completely correct. (The RateApplicablePercent will be missing for that line)
            tax_detail['line'] = invoice.line_ids.filtered(lambda l: l.tax_line_id and l.tax_line_id.amount == tax_detail['amount'])[:1]

        # Invoice lines.
        for i, line in enumerate(invoice.invoice_line_ids.filtered(lambda l: not l.display_type)):
            price_unit_with_discount = line.price_unit * (1 - (line.discount / 100.0))
            taxes_res = line.tax_ids.with_context({'tax_computation_context':{'line_tax_amount_percent':line.line_tax_amount_percent}}).compute_all(
                price_unit_with_discount,
                currency=line.currency_id,
                quantity=line.quantity,
                product=line.product_id,
                partner=invoice.partner_id,
                is_refund=line.move_id.move_type in ('in_refund', 'out_refund'),
            )

            if line.discount == 100.0:
                gross_price_subtotal = line.currency_id.round(line.price_unit * line.quantity)
            else:
                gross_price_subtotal = line.currency_id.round(line.price_subtotal / (1 - (line.discount / 100.0)))
            line_template_values = {
                'line': line,
                'index': i + 1,
                'tax_details': [],
                'net_price_subtotal': taxes_res['total_excluded'],
                'price_discount_unit': (gross_price_subtotal - line.price_subtotal) / line.quantity if line.quantity else 0.0,
                'unece_uom_code': line.product_id.product_tmpl_id.uom_id._get_unece_code(),
            }

            for tax_res in taxes_res['taxes']:
                tax = self.env['account.tax'].browse(tax_res['id'])
                tax_category_code = tax._get_unece_category_code(invoice.commercial_partner_id, invoice.company_id)
                line_template_values['tax_details'].append({
                    'tax': tax,
                    'tax_amount': tax_res['amount'],
                    'tax_base_amount': tax_res['base'],
                    'unece_tax_category_code': tax_category_code,
                })

            template_values['invoice_line_values'].append(line_template_values)

        template_values['tax_details'] = list(aggregated_taxes_details.values())

        xml_content = b"<?xml version='1.0' encoding='UTF-8'?>"
        xml_content += self.env.ref('account_edi_facturx.account_invoice_facturx_export')._render(template_values)
        return self.env['ir.attachment'].create({
            'name': 'factur-x.xml',
            'datas': base64.encodebytes(xml_content),
            'mimetype': 'application/xml'
        })
