# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""
Added class and method to import product from removal order wizard.
"""

import base64
import csv
from datetime import datetime
from io import StringIO

from odoo import models, fields, _
from odoo.exceptions import UserError
AMZ_PRODUCT_EPT = 'amazon.product.ept'


class ImportProductRemovalOrder(models.TransientModel):
    """
    Added class to import products from the removal orders.
    """
    _name = 'import.product.removal.order.wizard'
    _description = 'Import product through csv file for removal order'

    choose_file = fields.Binary(help="This Field relocates Choose file.")
    file_name = fields.Char(string='Name', help="This Field relocates file name.")
    removal_order_id = fields.Many2one('amazon.removal.order.ept', string='Shipment Reference',
                                       help="This Field relocates removal order id.")
    update_existing = fields.Boolean(string='Do you want to update already exist record ?',
                                     help="This field update existing record.")
    delimiter = fields.Selection([('tab', 'Tab'), ('semicolon', 'Semicolon'), ('colon', 'Colon')],
                                 string="Separator", default="colon",
                                 help="This Field relocates delimiter of csv.")
    replace_product_qty = fields.Boolean('Do you want to replace product quantity?', help="""
                        If you select this option then it will replace product quantity by csv 
                        quantity field data,it will not perform addition like 2 quantity is there 
                        in line and csv contain 3, then it will replace 2 by 3, it won't be updated 
                        by 5.If you have not selected this option then it will increase (addition) 
                        line quantity with csv quantity field data like 2 quantity in line,
                        and csv have 3 quantity then it will update line with 5 quantity.""")

    def default_get(self, fields):
        """
        Inherited this method for update context in fields.
        :param: fields: list of fields
        :return: list []
        """
        res = super(ImportProductRemovalOrder, self).default_get(fields)
        res['removal_order_id'] = self._context.get('removal_order_id', False)
        return res

    def download_sample_attachment(self):
        """
        This Method relocates download sample file of amazon.
        :return: This Method return download file.
        """
        attachment = self.env['ir.attachment'].search([('name', '=', 'import_removal_order.csv')])
        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content/%s?download=true' % (attachment.id),
            'target': 'new',
            'nodestroy': False,
        }

    def wizard_view(self):
        """
        This Method relocates open wizard using removal order view id.
        :return: This Method return wizard.
                 If removal view exist then return removal order wizard If now then raise UserError.
        """
        removal_order_view = self.env.ref('amazon_ept.view_import_product_removal_wizard')
        if removal_order_view:
            return {
                'name': 'Import Removal Order',
                'type': 'ir.actions.act_window',
                'view_type': 'form',
                'view_mode': 'form',
                'res_model': 'import.product.removal.order.wizard',
                'views': [(removal_order_view.id, 'form')],
                'view_id': removal_order_view.id,
                'target': 'new',
                'res_id': self.ids[0],
                'context': self.env.context,
            }
        raise UserError(_("Removal order view not created"))

    def read_file(self):
        """
        This Method relocates read selected file.
        :return:This Method return read selected file with delimiter and data return
        """
        imp_file = StringIO(base64.decodebytes(self.choose_file).decode())
        content = imp_file.read()
        delimiter = ('\t', csv.Sniffer().sniff(content.splitlines()[0]).delimiter)[bool(content)]
        reader = csv.DictReader(content.splitlines(), delimiter=delimiter)
        return reader

    def validate_process(self):
        """
        This Method check validation.
        If not order state draft raise UserError,Check order,removal plan and removal order file.
        Validate process by checking all the conditions and return back with inbound shipment object
        :return: This Method removal plan.
        """
        removal_order_obj = self.env['amazon.removal.order.ept']
        orders = removal_order_obj.browse(self._context.get('active_ids', []))
        if not orders:
            raise UserError(_('Unable to process..! Shipment Plan is not found!!!.'))
        if len(orders) > 1:
            raise UserError(_('Unable to process..! You can process only one shipment plan at a time!.'))
        if orders.state != "draft":
            raise UserError(_('Unable to process..! You can process with only draft shipment plan!.'))
        if not self.choose_file:
            raise UserError(_('Unable to process..! Please select file to process...'))
        return orders

    @staticmethod
    def validate_fields(fieldname):
        """
        This Method relocates validate field.
        This import pattern requires few fields default, so check it first whether it's there or not.
        :param fieldname: This Arguments relocates field name of removal order csv.
        :return: This Method return validate all fields of removal order file.
                 If all field proper then return True If not field proper in this cases raise UserError.
        """
        require_fields = ['default_code', 'unsellable_quantity', 'sellable_quantity']
        missing = []
        for field in require_fields:
            if field not in fieldname:
                missing.append(field)
        if len(missing) > 0:
            raise UserError(_(
                'Incorrect format found..!' +
                ' Please provide all the required fields in file, missing fields => %s.' % (
                    missing)))
        return True

    @staticmethod
    def fill_dictionary_from_file(reader):
        """
        This Method relocates prepare dictionary from removal order file reader.
        :param reader: This Arguments relocates reader of removal order file.
        :return: This Method return prepare dictionary from removal order file reader and return product data.
        """
        order_data = []
        for row in reader:
            vals = {
                'default_code': row.get('default_code', False),
                'unsellable_quantity': row.get('unsellable_quantity', 0.0),
                'sellable_quantity': row.get('sellable_quantity', 0.0)
            }
            order_data.append(vals)
        return order_data

    def find_invalid_sku_list(self, product_data, removal_pan):
        """
        This Method relocates find invalid data using sku list and return invalid product data.
        :param product_data: This Arguments relocates product data.
        :param removal_pan: This Arguments relocates removal order plan
        :return: This Method invalid data find using sku and return invalid data List.
        """
        product_obj = self.env['product.product']
        amazon_product_obj = self.env[AMZ_PRODUCT_EPT]
        invalid_data = []
        for data in product_data:
            line_data = {}
            line_data.update({
                'default_code': data.get('default_code', ''),
                'unsellable_quantity': data.get('unsellable_quantity', 0.0),
                'sellable_quantity': data.get('sellable_quantity', 0.0)
            })
            default_code = data.get('default_code', '')
            if not default_code:
                message = 'Product code not found!!!'
                line_data.update({'Error': str(message)})
                invalid_data.append(line_data)
                continue

            default_code = default_code.strip()
            amazon_product_id = False
            product = product_obj.search([('default_code', '=', default_code)])
            if not product:
                amazon_product_id = amazon_product_obj.search(
                    [('instance_id', '=', removal_pan.instance_id.id),
                     ('seller_sku', '=', data.get('default_code', ''))])
            if not amazon_product_id and not product:
                message = 'Product not found for %s amazon instance!!!' %(removal_pan.instance_id.name)
                line_data.update({'Error': str(message)})
                invalid_data.append(line_data)
        return invalid_data

    def _get_amazon_product_ept(self, product, instance):
        """
        This Method relocates get amazon product using product and and instance id.
        :param product: This Arguments relocates product.
        :param instance: This Method relocates instance of amazon.
        :return: This Method return amazon product browse object.
        """
        product_domain = [('product_id', '=', product.id), ('instance_id', '=', instance.id)]
        amazon_product = self.env[AMZ_PRODUCT_EPT].search(product_domain, limit=1)
        return amazon_product

    def import_removal_line(self):
        """
        This Method relocates import removal order line.
        First read removal order file after import removal line.
        """
        product_obj = self.env['product.product']
        line_obj = self.env['removal.orders.lines.ept']
        amazon_product_obj = self.env[AMZ_PRODUCT_EPT]
        removal_plan = self.validate_process()[0]
        reader = self.read_file()
        field_name = reader.fieldnames
        if field_name and self.validate_fields(field_name):
            product_data = self.fill_dictionary_from_file(reader)
            invalid_data = self.find_invalid_sku_list(product_data, removal_plan)
            if len(invalid_data) > 0:
                msg = ""
                for err in invalid_data:
                    msg += str(list(err.values())) + "\n"
                raise UserError(_('Invalid data found..! Please correct data first and then import file. \n%s.' % msg))
            for data in product_data:
                default_code = data.get('default_code', '').strip()
                unsellable_quantity = float(data.get('unsellable_quantity', 0.0)) if \
                    data.get('unsellable_quantity', False) else 0.0
                sellable_quantity = float(data.get('sellable_quantity', 0.0)) if \
                    data.get('sellable_quantity', False) else 0.0
                product = product_obj.search([('default_code', '=', default_code)], limit=1)
                if not product:
                    amazon_product = amazon_product_obj.search(
                        [('instance_id', '=', removal_plan.instance_id.id),
                         ('seller_sku', '=', default_code)], limit=1)
                else:
                    amazon_product = self._get_amazon_product_ept(product, removal_plan.instance_id)
                domain = [('amazon_product_id', '=', amazon_product.id),
                          ('removal_order_id', '=', removal_plan.id)]
                removal_plan_line = line_obj.search(domain)
                try:
                    if not removal_plan_line:
                        removal_plan_line.create({'removal_order_id': removal_plan.id,
                                                  'amazon_product_id': amazon_product.id,
                                                  'unsellable_quantity': unsellable_quantity,
                                                  'sellable_quantity': sellable_quantity})
                    else:
                        if not self.update_existing:
                            continue
                        if self.replace_product_qty:
                            un_sell_qty = unsellable_quantity
                            sell_qty = sellable_quantity
                        else:
                            un_sell_qty = removal_plan_line.unsellable_quantity + unsellable_quantity
                            sell_qty = removal_plan_line.sellable_quantity + sellable_quantity
                        removal_plan_line.write({'unsellable_quantity': un_sell_qty, 'sellable_quantity': sell_qty})
                except Exception as exception:
                    raise UserError(_('Unable to process ..!' +
                                      'Error found while importing products => %s.' % (str(exception))))
        return {'type': 'ir.actions.act_window_close'}
