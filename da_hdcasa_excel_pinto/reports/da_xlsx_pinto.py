# Copyright (C) 2022-Today:
# Dinamiche Aziendali srl (<http://www.dinamicheaziendali.it/>)
# @author: Gianmarco Conte (gconte@dinamicheaziendali.it)
# License GPL-3.0 or later (http://www.gnu.org/licenses/gpl.html).

from datetime import datetime

from odoo import _, models


class DaXlsxPinto(models.AbstractModel):
    _name = "report.da_hdcasa_excel_pinto.report_xlsx_pinto"
    _description = "Report XLSX Pinto"
    _inherit = "report.report_xlsx.abstract"

    def generate_xlsx_report(self, workbook, data, picking_ids):
        sheet = workbook.add_worksheet(_("Trasferimenti"))
        sheet.set_portrait()
        sheet.fit_to_pages(1, 0)
        sheet.set_zoom(100)
        sheet.set_column(0, 0, 12)
        sheet.set_column(1, 1, 21)
        sheet.set_column(2, 2, 25)
        sheet.set_column(3, 3, 20)
        sheet.set_column(4, 4, 25)
        sheet.set_column(5, 5, 15)
        sheet.set_column(6, 6, 35)
        sheet.set_column(7, 7, 10)
        sheet.set_column(8, 8, 10)
        sheet.set_column(9, 9, 15)
        sheet.set_column(10, 10, 12)
        text_style = workbook.add_format(
            {"bold": False, "font_size": 9, "font_color": "#000000", "align": "left"}
        )
        title_style = workbook.add_format({"bold": True, "bottom": 1})
        number_style = workbook.add_format(
            {"bold": False, "font_size": 9, "font_color": "#000000", "align": "right"}
        )
        monetary_style = workbook.add_format(
            {
                "bold": False,
                "font_size": 9,
                "font_color": "#000000",
                "align": "right",
                "num_format": "€ #,##0.00",
            }
        )
        sheet_title = [
            _("Riferimento"),
            _("Documento di Origine"),
            _("Cliente di destinazione"),
            _("Paese di destinazione"),
            _("Codice Prodotto"),
            _("Codice a Barre"),
            _("Prodotto"),
            _("Costo"),
            _("Quantità"),
            _("Unità di misura"),
            _("Data"),
        ]
        i = 0
        sheet.write_row(i, 0, sheet_title, title_style)
        sheet.freeze_panes(1, 0)
        i = 1
        for picking in picking_ids:
            writed = False
            for move in picking.move_ids:
                for move_line in move.move_line_ids:
                    if not writed:
                        writed = True
                    qty = self.get_qty_move_line(move_line)
                    partner_id = self.get_partner(picking)
                    product_supplier_code = self.get_supplier_code_product(
                        move_line.product_id, picking.partner_id
                    )
                    product_supplier_price = self.get_supplier_cost_product(
                        move_line.product_id, picking.partner_id
                    )
                    sheet.write(i, 0, picking.name, text_style)
                    sheet.write(i, 1, picking.origin or "", text_style)
                    sheet.write(i, 2, partner_id.name, text_style)
                    sheet.write(i, 3, partner_id.country_id.name, text_style)
                    sheet.write(i, 4, product_supplier_code, text_style)
                    sheet.write(i, 5, move_line.product_id.barcode, number_style)
                    sheet.write(i, 6, move_line.product_id.display_name, text_style)
                    sheet.write(i, 7, product_supplier_price, monetary_style)
                    sheet.write(i, 8, qty, number_style)
                    sheet.write(i, 9, move_line.product_uom_id.name, text_style)
                    sheet.write(
                        i, 10, datetime.now().date().strftime("%d/%m/%Y"), text_style
                    )
                    i += 1
            if writed:
                picking.exported_pinto = True
        return i

    def get_qty_move_line(self, move_line):
        if move_line.qty_done:
            qty = move_line.qty_done
        else:
            qty = move_line.reserved_uom_qty
        return qty

    def get_supplier_code_product(self, product, supplier):
        if product and supplier:
            # product_supplier_ids = product.seller_ids.mapped('name')
            product_supplier_ids = product.seller_ids.mapped("partner_id")
            if supplier in product_supplier_ids:
                product_supplier = product.seller_ids.filtered(
                    lambda p: p.partner_id == supplier
                )
                if product_supplier and product_supplier[0].product_code:
                    return product_supplier[0].product_code
            return product.default_code

    def get_supplier_cost_product(self, product, supplier):
        if product and supplier:
            product_supplier_ids = product.seller_ids.mapped("partner_id")
            if supplier in product_supplier_ids:
                product_supplier = product.seller_ids.filtered(
                    lambda p: p.partner_id == supplier
                )
                if product_supplier and product_supplier[0].price:
                    return product_supplier[0].price
            return product.standard_price

    def get_partner(self, picking):
        return picking.get_shipping_dest()
