# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""
Added class nad import to create inbound shipment line and to import product.
"""

import csv
from io import StringIO
import xlrd
import base64
import os
from odoo import models, fields, _
from odoo.exceptions import UserError, ValidationError


class ImportProductInboundShipment(models.TransientModel):
    """
    Added class to to relate with the inbound shipment and process to create shipment line and
    import product.
    """
    _name = 'import.product.inbound.shipment'
    _description = 'Import product through csv file for inbound shipment'

    choose_file = fields.Binary('Choose File')
    file_name = fields.Char("Filename", help="File Name")
    shipment_id = fields.Many2one('inbound.shipment.plan.ept', 'Shipment Reference')
    new_shipment_plan_id = fields.Many2one('inbound.shipment.plan.new.ept', 'Shipment Plan Reference')
    update_existing = fields.Boolean('Do you want to update already exist record ?')
    replace_product_qty = fields.Boolean('Do you want to replace product quantity?', help="""
            If you select this option then it will replace product quantity by csv quantity field data, 
            it will not perform addition like 2 quantity is there in line and csv contain 3,
            then it will replace 2 by 3, it won't be updated by 5.

            If you have not selected this option then it will increase (addition) line quantity with 
            csv quantity field data like 2 quantity in line, and csv have 3 quantity then 
            it will update line with 5 quantity. 
        """)
    delimiter = fields.Selection([('tab', 'Tab'), ('semicolon', 'Semicolon'), ('colon', 'Colon')],
                                 "Separator", default="colon")

    def default_get(self, fields):
        """
        inherited method to update the shipment id
        """
        res = super(ImportProductInboundShipment, self).default_get(fields)
        res['shipment_id'] = self._context.get('shipment_id', False)
        return res

    def wizard_view(self):
        """
        will return the import inbound shipment wizard view
        """
        if self._context.get('new_inbound_plan'):
            view = self.env.ref('amazon_ept.view_inbound_shipment_new_product_import_wizard')
        else:
            view = self.env.ref('amazon_ept.view_inbound_product_import_wizard')

        return {
            'name': 'Import Product',
            'type': 'ir.actions.act_window',
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'import.product.inbound.shipment',
            'views': [(view.id, 'form')],
            'view_id': view.id,
            'target': 'new',
            'res_id': self.ids[0],
            'context': self.env.context,
        }

    def download_sample_product_csv(self):
        """
        Download Sample file for Inbound Shipment Plan Products Import
        :return: Dict
        """
        if self._context.get('new_inbound_plan'):
            instance_id = self.new_shipment_plan_id.instance_id
            if instance_id.is_expiration or instance_id.is_manufacturing_lot_code:
                domain = ('name', '=', 'sample_inbound_shipment_plan_new_with_expiration_and_manufacturing_lot.xlsx')
            else:
                domain = ('name', '=', 'sample_inbound_shipment_plan_new.xlsx')
            attachment = self.env['ir.attachment'].search([domain, ('res_model', '=', 'inbound.shipment.plan.new.ept')])
        else:
            attachment = self.env['ir.attachment'].search([('name', '=', 'inbound_shipment_plan_sample.csv'),
                                                           ('res_model', '=', 'import.product.inbound.shipment')])
        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content/%s?download=true' % (attachment.id),
            'target': 'new',
            'nodestroy': False,
        }

    def read_file(self):
        """
        Read selected file to import order and return Reader to the caller
        :return : reader object of selected file.
        """
        if os.path.splitext(self.file_name)[1].lower() != '.csv':
            raise ValidationError(_("Invalid file format. You are only allowed to upload .csv file."))
        try:
            data = StringIO(base64.b64decode(self.choose_file).decode())
        except Exception:
            data = StringIO(base64.b64decode(self.choose_file).decode('ISO-8859-1'))
        content = data.read()
        delimiter = ('\t', csv.Sniffer().sniff(content.splitlines()[0]).delimiter)[bool(content)]
        reader = csv.DictReader(content.splitlines(), delimiter=delimiter)
        return reader

    def validate_fields(self, fieldnames):
        """This import pattern requires few fields default, so check it first whether it's there
            or not.
        """
        if self._context.get('new_inbound_plan'):
            require_fields = ['seller_sku', 'quantity', 'label_owner', 'prep_owner']
            instance_id = self.new_shipment_plan_id.instance_id
            if instance_id.is_expiration or instance_id.is_manufacturing_lot_code:
                require_fields.extend(['expiration', 'manufacturing_lot_code'])
        else:
            require_fields = ['seller_sku', 'quantity', 'quantity_in_case']
        missing = []
        for field in require_fields:
            if field not in fieldnames:
                missing.append(field)

        if len(missing) > 0:
            raise UserError(_('Incorrect format found..!\nPlease provide all the required fields in file, '
                              'missing fields => %s.' % missing))
        return True

    def validate_process(self):
        """
        Validate process by checking all the conditions and return back with inbound shipment object
        """
        if self._context.get('new_inbound_plan'):
            shipment_plan = self.env['inbound.shipment.plan.new.ept'].browse(
                self._context.get('new_shipment_plan_id', []))
        else:
            shipment_plan = self.env['inbound.shipment.plan.ept'].browse(self._context.get('shipment_id', []))
        if not self.choose_file:
            raise UserError(_('Unable to process..!\nPlease select file to process...'))
        return shipment_plan

    def check_product_file_validation(self, sheets):
        """
        This method is used to check the file
        """
        is_header = False
        for sheet in sheets.sheets():
            for row_no in range(sheet.nrows):
                if not is_header:
                    headers = [d.value for d in sheet.row(row_no)]
                    self.validate_fields(headers)
                    is_header = True
                if row_no !=0 and sheet.row(row_no):
                    seller_sku = sheet.row(row_no)[0].value
                    quantity = sheet.row(row_no)[1].value
                    amazon_product = self.env['amazon.product.ept'].search(
                        [('instance_id', '=', self.new_shipment_plan_id.instance_id.id),
                         ('fulfillment_by', '=', 'FBA'), ('seller_sku', '=', seller_sku)], limit=1)
                    if not quantity or quantity <= 0.0:
                        raise UserError(_('For Seller Sku %s Quantity must not zero or negative in file at row no %s' % (
                            seller_sku, row_no + 1)))
                    if not amazon_product:
                        raise UserError(_('Amazon product not found , please check product exist or not for sku '
                                          '%s,instance %s and Fulfillment by amazon in file at row no %s' % (
                                          seller_sku, self.new_shipment_plan_id.instance_id.name, row_no + 1)))

    def import_inbound_shipment_new_line(self):
        """
        This method is used to read and will create inbound shipment lines
        """
        if not self.choose_file:
            raise UserError(_('Unable to process..!\nPlease select file to process...'))
        if os.path.splitext(self.file_name)[1].lower() != '.xlsx':
            raise ValidationError(_("Invalid file format. You are only allowed to upload .xlsx file."))
        sheets = xlrd.open_workbook(file_contents=base64.b64decode(self.choose_file.decode('UTF-8')))
        if sheets.sheets()[0].nrows <=1:
            raise UserError('No Data Found in the file.')
        self.check_product_file_validation(sheets)
        row_number = 1
        header = dict()
        is_header = False
        for sheet in sheets.sheets():
            for row_no in range(sheet.nrows):
                if not is_header:
                    headers = [d.value for d in sheet.row(row_no)]
                    self.validate_fields(headers)
                    [header.update({d: headers.index(d)}) for d in headers]
                    is_header = True
                    continue
                row = dict()
                [row.update({k: sheet.row(row_no)[v].value}) for k, v in header.items() for c in
                 sheet.row(row_no)]
                row_number += 1
                for key in ['seller_sku', 'quantity', 'label_owner', 'prep_owner']:
                    if isinstance(row.get(key), float):
                        data = str(row.get(key)).split('.')
                        row[key] = data[0] if data[1] == '0' else data
                self.amz_import_product_create_inbound_shipment_line(row, row_number)
        return {'type': 'ir.actions.act_window_close'}

    def import_shipment_line(self):
        """
        will create shipment lines and import the products
        """
        amazon_product_obj = self.env['amazon.product.ept']
        shipment_plan = self.validate_process()[0]
        reader = self.read_file()
        fieldnames = reader.fieldnames
        if self.validate_fields(fieldnames):
            for row in reader:
                seller_sku = row.get('seller_sku', '')
                quantity = float(row.get('quantity', 0)) if row.get('quantity', 0) else 0.0
                quantity_in_case = float(row.get('quantity_in_case', 0)) if row.get(\
                    'quantity_in_case', 0) else 0.0
                amazon_product = amazon_product_obj.search(
                    [('instance_id', '=', shipment_plan.instance_id.id),
                     ('fulfillment_by', '=', 'FBA'),
                     ('seller_sku', '=', seller_sku)], limit=1)
                if not amazon_product:
                    raise UserError(_('Amazon product not found , please check product exist or not for '
                        'sku %s,instance %s and Fulfillment by amazon' % (seller_sku, shipment_plan.instance_id.name)))

                shipment_plan_line_obj = shipment_plan.shipment_line_ids.filtered(
                    lambda line, amazon_product=amazon_product: line.amazon_product_id.id == amazon_product.id)
                try:
                    if not shipment_plan_line_obj:
                        dict_data = {
                            'shipment_plan_id': shipment_plan.id,
                            'amazon_product_id': amazon_product.id,
                            'quantity': quantity,
                            'quantity_in_case': quantity_in_case
                        }
                        shipment_plan_line_obj.create(dict_data)
                    else:
                        if self.update_existing:
                            if self.replace_product_qty:
                                total_qty = quantity
                            else:
                                total_qty = quantity + shipment_plan_line_obj.quantity
                            shipment_plan_line_obj.write({'quantity': total_qty, 'quantity_in_case': quantity_in_case})
                except Exception as e:
                    raise UserError(_('Unable to process ..!\nError found while importing products => %s.' % (str(e))))

            return {'type': 'ir.actions.act_window_close'}

    def amz_import_product_create_inbound_shipment_line(self, row, row_number):
        """
        will create inbound shipment new lines and import the products
        """
        shipment_plan = self.validate_process()[0]
        if row:
            seller_sku = row.get('seller_sku', '')
            label_owner = row.get('label_owner')
            prep_owner = row.get('prep_owner')
            quantity = float(row.get('quantity', 0)) if row.get('quantity', 0) else 0.0
            if not seller_sku:
                message = "Seller SKU is required to add the product in file line %s" % (row_number)
                raise UserError(message)
            elif not quantity:
                message = ("Quantity is required to add the product in file line %s and Quantity must be greater "
                           "than zero!") % (row_number)
                raise UserError(message)
            amazon_product = self.env['amazon.product.ept'].search([('instance_id', '=', shipment_plan.instance_id.id),
                                                        ('fulfillment_by', '=', 'FBA'),
                                                        ('seller_sku', '=', seller_sku)], limit=1)
            if not amazon_product:
                raise UserError(_('Amazon product not found , please check product exist or not for sku '
                                  '%s,instance %s and Fulfillment by amazon' % (seller_sku, shipment_plan.instance_id.name)))

            shipment_plan_line_obj = shipment_plan.new_shipment_line_ids.filtered(
                lambda line, amazon_product=amazon_product: line.amazon_product_id.id == amazon_product.id)
            try:
                if not shipment_plan_line_obj:
                    dict_data = {'shipment_new_plan_id': shipment_plan.id, 'amazon_product_id': amazon_product.id,
                        'quantity': quantity}
                    if label_owner or prep_owner:
                        dict_data.update({'label_owner': label_owner, 'prep_owner': prep_owner})
                    shipment_plan_line_obj = shipment_plan_line_obj.create(dict_data)
                    if not label_owner or not prep_owner:
                        shipment_plan_line_obj.onchange_shipment_new_plan_id()
                else:
                    if self.update_existing and shipment_plan_line_obj:
                        shipment_plan_line_obj.write({'quantity': quantity})
            except Exception as e:
                raise UserError(_('Unable to process ..!\nError found while importing products => %s.' % (str(e))))
