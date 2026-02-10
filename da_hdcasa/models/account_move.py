# Copyright 2025 Andrea Barbato - Dinamiche Aziendali srl
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).

from odoo import models
from odoo.tools import float_is_zero


class AccountMoveInherit(models.Model):
    _inherit = "account.move"

    def _l10n_it_edi_add_base_lines_xml_values(
        self, base_lines_aggregated_values, is_downpayment
    ):
        res = super()._l10n_it_edi_add_base_lines_xml_values(
            base_lines_aggregated_values, is_downpayment
        )
        for base_line, _aggregated_values in base_lines_aggregated_values:
            vat_tax = (
                base_line["tax_ids"]
                .flatten_taxes_hierarchy()
                .filtered(lambda t: t._l10n_it_filter_kind("vat") and t.amount >= 0)[:1]
            )

            if vat_tax.oss_country_id:
                vat_tax_amount = "%.*f" % (
                    2,
                    vat_tax.amount
                    if not float_is_zero(vat_tax.amount, precision_digits=2)
                    else 0.0,
                )

                for altri_dati_gestionali in base_line["it_values"][
                    "altri_dati_gestionali_list"
                ]:
                    if altri_dati_gestionali["tipo_dato"] == "OSS":
                        riferimento_testo = " ".join(
                            [
                                "IVA OSS",
                                vat_tax_amount,
                                str(vat_tax.oss_country_id.name),
                            ]
                        )
                        altri_dati_gestionali["riferimento_testo"] = riferimento_testo
                        break
        return res
