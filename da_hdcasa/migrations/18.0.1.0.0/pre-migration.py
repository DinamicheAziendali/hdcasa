#  Copyright 2024 Gianmarco Conte
#  License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging

from odoo.upgrade import util

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    _logger.info("START upgrade MITES")
    _deleted_xml_records = [
        'l10n_it_ricevute_bancarie.view_partner_form_riba',
        'l10n_it_account_stamp.report_invoice_document_custom_fields_ext',
        'l10n_it_withholding_tax.print_withholding_tax',
        'account_invoice_report_due_list.report_invoice_document',
        'da_mitesys_sale.sale_order_search_inherit_view',
        'l10n_it_vat_statement_split_payment.view_account_config_settings_inherit',
        'transport_carrier_brt.stock_picking_button_brt_form_view',
        'transport_carrier_brt.stock_picking_brt_tree_view_inherit',
        'l10n_it_fatturapa_out.view_invoice_form_fatturapa',
    ]

    for view in _deleted_xml_records:
        util.records.remove_view(cr, xml_id=view, silent=True)
    # util.force_install_module(cr, "l10n_it_riba_oca",
    #                           if_installed=["l10n_it_ricevute_bancarie"])
    _logger.info("CONTINUE upgrade HDCASA")
    # cr.execute("""INSERT INTO date_range_type (name, active) VALUES ('{"en_US": "Anno fiscale New"}', true);""")
    # cr.execute("UPDATE date_range SET type_id = (SELECT id FROM date_range_type WHERE name ->> 'en_US' = 'Anno fiscale New' AND active = true) WHERE type_id = 1;")
    util.force_install_module(cr, "l10n_it_central_journal_reportlab",
                              if_installed=["l10n_it_central_journal"])
    util.force_install_module(cr, "product_customerinfo",
                              if_installed=["product_supplierinfo_for_customer"])
    modules_to_uninstall = [
        'l10n_it_central_journal',
        'amazon_settlement_ext_ept',
        'da_fatturapa_round',
        'manomano',
        'manomano_firstname',
        'manomano_datafeed',
        'da_account_move_show_posted_before',
        'om_mass_confirm_cancel',
        'da_prestashop_payment_term',
        'integration_prestashop',
        'printnode_base',
        'product_multiple_barcodes',
        'product_supplierinfo_for_customer',
        'l10n_it_fatturapa_out_fiscalcode_child', #effettivamente non serve più?
    ]
    for module in modules_to_uninstall:
        _logger.info(f"{module} UNISTALLING")
        util.uninstall_module(cr, module)
