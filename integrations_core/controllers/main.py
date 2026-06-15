# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Controller HTTP per i feed di EXPORT verso i marketplace.

Espone URL PUBBLICI (auth='public') protetti da un token per canale: BricoBravo
(e altri marketplace) leggono periodicamente questi URL per aggiornare prezzi e
giacenze. Il controller serve l'ULTIMO CSV pre-generato e salvato sul canale (il
cron/bottone lo genera): qui non si rigenera nulla.

Sicurezza: il token NON viene mai loggato; in caso di token assente/errato o
canale inesistente si risponde 403 in modo UNIFORME, senza rivelare se il canale
esista. Il confronto del token usa hmac.compare_digest (tempo costante).

Generico e predisposto per più feed: `_serve_feed` è parametrico sul campo di
storage, così aggiungere il feed CATALOGO (TASK_24) è banale — basta un nuovo
campo `catalog_feed_content` sul canale e una seconda rotta che lo passa.

Serve anche le IMMAGINI dei prodotti (TASK_26) da una rotta pubblica protetta
dallo stesso token: su Odoo Community puro l'utente pubblico riceve dal /web/image
nativo il placeholder, non la foto. Qui leggiamo l'immagine in sudo e ne
restituiamo i byte, ma SOLO per i prodotti esportabili su quel canale (criterio
tag + company), così il token non diventa un modo per leggere immagini arbitrarie.
"""
import base64
import hmac
import logging

from odoo import http
from odoo.http import request
from odoo.tools.mimetypes import guess_mimetype

_logger = logging.getLogger(__name__)


class IntegrationsFeedController(http.Controller):

    # ------------------------------------------------------------------
    # Helpers comuni
    # ------------------------------------------------------------------
    def _channel_if_token_ok(self, channel_id, token):
        """Ritorna il canale se il token combacia, altrimenti None (→ 403 a cura del chiamante).

        Stessa logica delle rotte feed: lettura del canale in sudo (l'utente
        pubblico non ha ACL), confronto token in tempo costante. Non distingue
        canale assente da token errato.
        """
        channel = request.env["centrivo.channel"].sudo().browse(channel_id).exists()
        expected = channel.export_token if channel else None
        if not expected or not token or not hmac.compare_digest(str(token), str(expected)):
            return None
        return channel

    @staticmethod
    def _forbidden():
        return request.make_response(
            "Forbidden", status=403,
            headers=[("Content-Type", "text/plain; charset=utf-8")])

    @staticmethod
    def _not_found(message="Not found"):
        return request.make_response(
            message, status=404,
            headers=[("Content-Type", "text/plain; charset=utf-8")])

    def _serve_feed(self, channel_id, token, content_field, label):
        """Serve un CSV pre-generato salvato sul canale, previa verifica token.

        - 403 (uniforme) se canale inesistente, token assente o non combaciante.
        - 404 se il canale/token è valido ma il feed non è ancora stato generato.
        - 200 text/csv con il contenuto salvato altrimenti.
        """
        channel = self._channel_if_token_ok(channel_id, token)
        if not channel:
            return self._forbidden()

        content = getattr(channel, content_field) or ""
        if not content:
            _logger.warning(
                "Feed '%s' richiesto per il canale %s ma non ancora generato.",
                label, channel_id)
            return self._not_found("Feed non ancora generato")

        filename = "%s_%s.csv" % (label, channel_id)
        return request.make_response(
            content,
            headers=[
                ("Content-Type", "text/csv; charset=utf-8"),
                ("Content-Disposition", "attachment; filename=%s" % filename),
            ])

    @http.route("/integrations/feed/stock/<int:channel_id>",
                type="http", auth="public", csrf=False, methods=["GET"])
    def feed_stock(self, channel_id, token=None, **kw):
        """Feed prezzi/giacenze (6 colonne) di un canale. Param obbligatorio: token."""
        return self._serve_feed(channel_id, token, "stock_feed_content", "stock_feed")

    @http.route("/integrations/feed/catalog/<int:channel_id>",
                type="http", auth="public", csrf=False, methods=["GET"])
    def feed_catalog(self, channel_id, token=None, **kw):
        """Feed catalogo completo (23 colonne) di un canale. Param obbligatorio: token.

        Stessa identica logica di sicurezza del feed prezzi/giacenze (token via
        hmac.compare_digest, 403 uniforme, 404 se non generato, token mai loggato).
        """
        return self._serve_feed(channel_id, token, "catalog_feed_content", "catalog_feed")

    # ------------------------------------------------------------------
    # IMMAGINI prodotto (TASK_26) — rotta pubblica, byte serviti in sudo
    # ------------------------------------------------------------------
    def _is_exportable(self, channel, template):
        """True se il template prodotto è esportabile su QUESTO canale.

        Controllo di appartenenza per non trasformare la rotta in una lettura di
        immagini arbitrarie: il template deve avere uno dei tag di export del
        canale (stesso criterio del feed) e, in multi-company, appartenere alla
        company del canale (o essere condiviso, company_id vuoto).
        """
        if not template:
            return False
        tags = channel.export_product_tag_ids
        if not tags or not (template.product_tag_ids & tags):
            return False
        company = template.company_id
        if company and company != channel.company_id:
            return False
        return True

    def _serve_image(self, channel_id, token, source, res_id):
        """Serve i BYTE dell'immagine di un prodotto esportabile (no /web/image nativo).

        Letta in sudo (così il pubblico ottiene la foto reale, non il placeholder
        del Community puro), ma SOLO per prodotti esportabili sul canale. Immagine
        assente/non esportabile → 404 (mai il placeholder). Token errato → 403.
        """
        channel = self._channel_if_token_ok(channel_id, token)
        if not channel:
            return self._forbidden()

        env = request.env
        if source == "product":
            record = env["product.product"].sudo().browse(res_id).exists()
            template = record.product_tmpl_id if record else None
        elif source == "gallery":
            if "product.image" not in env:
                return self._not_found()
            record = env["product.image"].sudo().browse(res_id).exists()
            template = record.product_tmpl_id if record else None
        else:
            return self._not_found()

        if not record or not self._is_exportable(channel, template):
            return self._not_found()

        image_data = record.image_1920
        if not image_data:
            return self._not_found()

        raw = base64.b64decode(image_data)
        mimetype = guess_mimetype(raw) or "image/jpeg"
        return request.make_response(
            raw,
            headers=[
                ("Content-Type", mimetype),
                ("Content-Length", str(len(raw))),
            ])

    @http.route("/integrations/feed/image/<int:channel_id>/<string:source>/<int:res_id>",
                type="http", auth="public", csrf=False, methods=["GET"])
    def feed_image(self, channel_id, source, res_id, token=None, **kw):
        """Immagine prodotto per il feed catalogo. Param obbligatorio: token.

        source = 'product' (immagine principale, product.product) oppure 'gallery'
        (immagine aggiuntiva, product.image). Stessa sicurezza token delle altre
        rotte; in più il controllo di appartenenza (_is_exportable). Token mai loggato.
        """
        return self._serve_image(channel_id, token, source, res_id)
