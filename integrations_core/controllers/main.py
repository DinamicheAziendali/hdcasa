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
import hashlib
import hmac
import logging
import time

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

    # --- Percorso CALDO: configurazione di canale con cache a TTL breve ------
    #
    # Misurato sui log di Odoo.sh il 2026-07-28: ManoMano fa una HEAD **e** una
    # GET per OGNI foto, e ciascuna richiesta costava 14 interrogazioni al
    # database. Su un'importazione dell'intero catalogo (23.105 immagini) sono
    # 46.210 richieste e circa 650.000 interrogazioni — ed è questo, non il peso
    # delle foto, ad aver fatto arrendere il loro scaricatore.
    #
    # Buona parte di quelle interrogazioni rileggeva ogni volta le STESSE cose:
    # token del canale, tag di esportazione, company, risoluzione. Dati che
    # cambiano una volta al mese. Qui se ne tiene una copia per pochi secondi.
    #
    # TTL breve e voluto: dopo un cambio di token (o dei tag di export) la
    # modifica ha effetto entro _CHANNEL_CACHE_TTL secondi, non oltre. La cache
    # è per-processo: ogni worker ha la sua, e si svuota al riavvio.
    _CHANNEL_CACHE = {}
    _CHANNEL_CACHE_TTL = 60

    @classmethod
    def _invalidate_channel_cache(cls):
        """Svuota la cache (utile nei test e in un eventuale hook di scrittura)."""
        cls._CHANNEL_CACHE.clear()

    def _channel_image_config(self, channel_id):
        """Copia dei dati di canale che servono alla rotta immagini.

        Ritorna sempre un dizionario (mai un recordset): così il percorso caldo
        non tocca l'ORM del canale. `token` a None significa canale inesistente
        → il chiamante risponde 403 come prima, senza distinguere i due casi.
        """
        key = (request.env.cr.dbname, channel_id)
        now = time.time()
        cached = self._CHANNEL_CACHE.get(key)
        if cached and cached["expires"] > now:
            return cached

        channel = request.env["centrivo.channel"].sudo().browse(channel_id).exists()
        config = {
            "expires": now + self._CHANNEL_CACHE_TTL,
            "token": channel.export_token if channel else None,
            "tag_ids": frozenset(channel.export_product_tag_ids.ids) if channel
                       else frozenset(),
            "company_id": channel.company_id.id if channel else False,
            "resolution": channel.feed_image_resolution if channel else "1920",
        }
        self._CHANNEL_CACHE[key] = config
        return config

    @staticmethod
    def _is_exportable_config(config, template):
        """Come _is_exportable, ma sulla copia in cache invece che sul recordset.

        Stesso identico criterio (tag di export + company): la sicurezza della
        rotta non cambia, cambia solo da dove arrivano i dati del canale.
        """
        if not template:
            return False
        tags = config["tag_ids"]
        if not tags or not (set(template.product_tag_ids.ids) & tags):
            return False
        company = template.company_id
        if company and company.id != config["company_id"]:
            return False
        return True

    def _serve_image(self, channel_id, token, source, res_id):
        """Serve i BYTE dell'immagine di un prodotto esportabile (no /web/image nativo).

        Letta in sudo (così il pubblico ottiene la foto reale, non il placeholder
        del Community puro), ma SOLO per prodotti esportabili sul canale. Immagine
        assente/non esportabile → 404 (mai il placeholder). Token errato → 403.
        """
        config = self._channel_image_config(channel_id)
        expected = config["token"]
        if not expected or not token or not hmac.compare_digest(str(token),
                                                                str(expected)):
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

        if not record or not self._is_exportable_config(config, template):
            return self._not_found()

        resolution = config["resolution"]

        # --- Cache: prima di leggere il blob, si prova a rispondere 304 -------
        # Gli scaricatori dei marketplace ripassano sulle STESSE immagini a ogni
        # invio del feed. Con un validatore possiamo dire "non è cambiata" senza
        # leggere né trasferire i byte: è il risparmio più grande, e si paga solo
        # una lettura di write_date.
        # (Misurato: ManoMano NON manda If-None-Match — riscarica tutto. Restiamo
        # corretti comunque, per gli scaricatori che invece la usano.)
        etag = self._image_etag(record, template, resolution)
        if etag and request.httprequest.headers.get("If-None-Match") == etag:
            return request.make_response("", status=304, headers=[("ETag", etag)])

        # --- HEAD: si risponde senza LEGGERE l'immagine ------------------------
        # Metà delle richieste sono HEAD ("c'è?"), e finora costavano quanto una
        # GET: il gestore leggeva e decodificava la foto, e poi il corpo veniva
        # buttato via perché a una HEAD non spetta. Qui si risponde con le sole
        # intestazioni, prese dai METADATI dell'allegato (una lettura leggera,
        # mai i byte).
        if request.httprequest.method == "HEAD":
            return self._head_response(record, resolution, etag)

        image_data = self._image_blob(record, resolution)
        if not image_data:
            return self._not_found()

        raw = base64.b64decode(image_data)
        mimetype = guess_mimetype(raw) or "image/jpeg"
        headers = [
            ("Content-Type", mimetype),
            ("Content-Length", str(len(raw))),
            # Immagine di prodotto: cambia di rado. Un giorno di validità evita
            # gli scaricamenti ripetuti senza congelare un eventuale aggiornamento.
            ("Cache-Control", "public, max-age=86400"),
        ]
        if etag:
            headers.append(("ETag", etag))
        return request.make_response(raw, headers=headers)

    def _head_response(self, record, resolution, etag):
        """Risposta a una HEAD: sole intestazioni, senza leggere i byte.

        Tipo e dimensione arrivano dai metadati dell'allegato che Odoo usa per
        conservare le immagini (`ir.attachment.mimetype` / `file_size`): una
        riga leggera, non il blob. Se l'allegato non si trova — per esempio
        quando la variante eredita la foto dal template — si ripiega leggendo il
        campo, ma **senza decodificarlo**: la dimensione si ricava dalla
        lunghezza del base64.

        Se non si riesce a determinare la dimensione si omette Content-Length:
        su una HEAD è legittimo, ed è meglio di un valore sbagliato.
        """
        mimetype, size = self._image_meta(record, resolution)
        if mimetype is None:
            image_data = self._image_blob(record, resolution)
            if not image_data:
                return self._not_found()
            mimetype, size = "image/jpeg", self._b64_decoded_size(image_data)

        headers = [
            ("Content-Type", mimetype or "image/jpeg"),
            ("Cache-Control", "public, max-age=86400"),
        ]
        if etag:
            headers.append(("ETag", etag))
        response = request.make_response("", headers=headers)
        if size:
            # Werkzeug ricalcolerebbe Content-Length sul corpo vuoto (0), che su
            # una HEAD farebbe credere l'immagine vuota: qui si disattiva quel
            # ricalcolo e si dichiara la dimensione che avrebbe la GET.
            response.automatically_set_content_length = False
            response.headers["Content-Length"] = str(size)
        return response

    @staticmethod
    def _image_meta(record, resolution):
        """(mimetype, dimensione) dai metadati dell'allegato, SENZA leggere i byte.

        Le immagini di image.mixin sono conservate come allegati: interrogando
        ir.attachment per (modello, campo, id) si ottengono tipo e dimensione
        senza toccare il contenuto. (None, None) se l'allegato non c'è.
        """
        field = "image_%s" % (resolution or "1920")
        try:
            attachment = record.env["ir.attachment"].sudo().search_read(
                [("res_model", "=", record._name),
                 ("res_field", "=", field),
                 ("res_id", "=", record.id)],
                ["mimetype", "file_size"], limit=1)
        except Exception:  # noqa: BLE001 - la HEAD non deve mai fallire per questo
            return None, None
        if not attachment:
            return None, None
        return (attachment[0].get("mimetype") or "image/jpeg",
                attachment[0].get("file_size") or None)

    @staticmethod
    def _b64_decoded_size(data):
        """Dimensione dei byte decodificati, ricavata dalla lunghezza del base64.

        Aritmetica pura: evita di decodificare l'immagine solo per sapere quanto
        pesa. 4 caratteri base64 = 3 byte, meno il riempimento finale ('=').
        """
        if not data:
            return 0
        if isinstance(data, str):
            data = data.encode("ascii", "ignore")
        data = data.strip()
        padding = data.count(b"=")
        return max(0, (len(data) // 4) * 3 - padding)

    @staticmethod
    def _image_blob(record, resolution):
        """Il blob della risoluzione scelta sul canale, con ripiego sull'originale.

        Odoo (image.mixin) tiene già pronte le versioni ridimensionate: leggere
        la 1024 invece della 1920 non costa CPU e alleggerisce il trasferimento.
        Se la versione richiesta mancasse (dato anomalo), si ripiega sempre
        sull'originale: meglio un'immagine pesante che nessuna immagine.
        """
        field = "image_%s" % (resolution or "1920")
        return getattr(record, field, False) or record.image_1920

    @staticmethod
    def _image_etag(record, template, resolution):
        """Validatore di cache dell'immagine, senza leggerne i byte.

        Composto da modello, id, risoluzione e data di ultima modifica. Si include
        ANCHE la write_date del template perché su product.product l'immagine può
        arrivare dal template: guardando solo la variante, una foto sostituita sul
        template non cambierebbe il validatore e i marketplace continuerebbero a
        servirsi la vecchia. Se le date mancano si ritorna None: niente ETag,
        nessuna cache — mai una cache sbagliata.
        """
        stamps = [record.write_date, template.write_date if template else None]
        if not any(stamps):
            return None
        material = "|".join([
            record._name, str(record.id), str(resolution or "1920"),
            *[s.isoformat() if s else "" for s in stamps],
        ])
        return '"%s"' % hashlib.sha1(material.encode("utf-8")).hexdigest()

    @http.route("/integrations/feed/image/<int:channel_id>/<string:source>/<int:res_id>",
                type="http", auth="public", csrf=False, methods=["GET"])
    def feed_image(self, channel_id, source, res_id, token=None, **kw):
        """Immagine prodotto per il feed catalogo. Param obbligatorio: token.

        source = 'product' (immagine principale, product.product) oppure 'gallery'
        (immagine aggiuntiva, product.image). Stessa sicurezza token delle altre
        rotte; in più il controllo di appartenenza (_is_exportable). Token mai loggato.
        """
        return self._serve_image(channel_id, token, source, res_id)
