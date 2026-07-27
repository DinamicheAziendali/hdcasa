# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Rotta pubblica per il feed prodotto ManoMano.

Estende IntegrationsFeedController per RIUSARE la logica di sicurezza
(_serve_feed, token via hmac). ManoMano scarica questo URL (importazione
automatica). Nessuna modifica a integrations_core.
"""
from odoo import http
from odoo.addons.integrations_core.controllers.main import (
    IntegrationsFeedController,
)


class ManoManoFeedController(IntegrationsFeedController):

    @http.route("/integrations/feed/manomano-products/<int:channel_id>",
                type="http", auth="public", csrf=False, methods=["GET"])
    def feed_manomano_products(self, channel_id, token=None, **kw):
        """Feed prodotto ManoMano (colonne dalla Taxonomy). Param obbligatorio: token."""
        return self._serve_feed(
            channel_id, token,
            "manomano_product_feed_content", "manomano_products")
