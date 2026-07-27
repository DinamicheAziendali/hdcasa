# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Mappatura configurabile colonna-feed-ManoMano → fonte Odoo (per canale).

Ogni riga dice come popolare UNA colonna del feed prodotto: da un campo Odoo, da
un valore fisso, o da un'immagine (principale/galleria). Tutto configurabile per
istanza (nessun campo cablato).
"""
from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

_PRODUCT_FIELD_DOMAIN = "[('model', 'in', ['product.template', 'product.product'])]"


class ManoManoFeedMap(models.Model):
    _name = "centrivo.manomano.feed.map"
    _description = "Mappatura feed prodotto ManoMano"

    channel_id = fields.Many2one(
        "centrivo.channel", string="Canale", required=True, ondelete="cascade")
    mm_field_id = fields.Many2one(
        "centrivo.manomano.feed.field", string="Colonna ManoMano",
        ondelete="restrict", index=True,
        domain="[('contract_type', '!=', 'mf')]",
        # Il context di un campo Python è un DIZIONARIO, non una stringa: una
        # stringa arriva così com'è al client, che prova a espanderla con **
        # e solleva "argument after ** must be a mapping, not str".
        context={"create": False, "edit": False},
        help="Campo del feed ManoMano. L'elenco si aggiorna col bottone "
             "«Aggiorna campi da ManoMano» sul canale.")
    mm_mandatory = fields.Boolean(
        string="Obbligatorio", related="mm_field_id.mandatory")
    mm_has_values = fields.Boolean(
        string="Lista chiusa", related="mm_field_id.has_values")
    source_type = fields.Selection(
        selection=[
            ("field", "Campo prodotto"),
            ("attribute", "Attributo prodotto"),
            ("fixed", "Valore fisso"),
            ("main_image", "Immagine principale"),
            ("gallery_image", "Immagine galleria (n)"),
        ],
        string="Fonte", required=True, default="field")
    source_field_id = fields.Many2one(
        "ir.model.fields", string="Campo Odoo",
        domain=_PRODUCT_FIELD_DOMAIN, ondelete="cascade")
    attribute_id = fields.Many2one(
        "product.attribute", string="Attributo prodotto", ondelete="restrict",
        help="I valori dell'attributo sul prodotto finiscono in questa colonna, "
             "uniti da '#' se sono più di uno.")
    fixed_value = fields.Char(string="Valore fisso")
    image_index = fields.Integer(
        string="Indice immagine galleria", default=1,
        help="Solo per fonte 'Immagine galleria': quale immagine (1 = prima).")
    company_id = fields.Many2one(
        "res.company", string="Azienda", required=True,
        default=lambda self: self.env.company)

    _sql_constraints = [
        ("uniq_channel_field", "unique(channel_id, mm_field_id)",
         "Ogni colonna ManoMano può essere mappata una sola volta per canale."),
    ]

    @api.constrains("mm_field_id")
    def _check_mm_field(self):
        """Colonna obbligatoria, ma NON `required` sul campo.

        Un `required=True` imporrebbe un NOT NULL alla colonna prima che la
        migrazione possa riempirla, facendo fallire l'aggiornamento del modulo
        sulle righe già configurate.
        """
        for record in self:
            if not record.mm_field_id:
                raise ValidationError(
                    "Indica la colonna ManoMano su ogni riga di mappatura.")

    @staticmethod
    def _normalize_value_name(name):
        """Normalizza un nome di valore attributo solo ai fini del confronto.

        Toglie spazi ai bordi e ignora maiuscole/minuscole, così "Rosso" e
        " rosso " inseriti a mano non generano un quasi-duplicato. Il valore
        scritto su Odoo resta sempre quello originale di ManoMano.
        """
        return (name or "").strip().casefold()

    def action_create_attribute(self):
        """Crea in Odoo l'attributo con i valori ammessi da ManoMano.

        SEMPRE `create_variant='no_variant'`: questi attributi servono solo ad
        arricchire la scheda, non a generare varianti — con liste da centinaia di
        valori il catalogo esploderebbe.

        Se l'attributo esiste già (per nome, o perché è già agganciato alla
        riga) viene controllato PRIMA di scrivere nulla: se genera varianti
        (`create_variant` diverso da `no_variant`) ci si ferma con un errore,
        perché aggiungerci i valori di ManoMano farebbe esplodere le varianti
        dei prodotti che lo usano già.

        Idempotente: se l'attributo esiste già aggiunge SOLO i valori mancanti
        (confronto senza distinguere spazi/maiuscole) e non tocca nulla di
        quanto fatto a mano. Premerlo due volte non duplica niente.
        """
        self.ensure_one()
        field = self.mm_field_id
        if not field or not field.value_ids:
            raise UserError(
                "Questo campo ManoMano non ha una lista chiusa di valori: "
                "non c'è nessun attributo da creare.")

        Attribute = self.env["product.attribute"]
        attribute = self.attribute_id or Attribute.search(
            [("name", "=", field.label or field.name)], limit=1)

        # Controllo di sicurezza PRIMA di qualunque scrittura: se l'attributo
        # esiste già ma genera varianti, riempirlo di valori ManoMano
        # creerebbe migliaia di varianti sui prodotti che lo usano.
        if attribute and attribute.create_variant != "no_variant":
            raise UserError(
                "L'attributo «%s» genera varianti di prodotto (non è "
                "«senza varianti»). Aggiungere i valori di ManoMano lo "
                "userebbe per creare una variante per ogni valore, facendo "
                "esplodere il numero di varianti nel catalogo. Usa o crea "
                "un attributo che NON genera varianti." % attribute.name)

        created = False
        if not attribute:
            attribute = Attribute.create({
                "name": field.label or field.name,
                "create_variant": "no_variant",
            })
            created = True

        existing = {self._normalize_value_name(v.name)
                    for v in attribute.value_ids}
        nuovi = [{"attribute_id": attribute.id, "name": value.name,
                  "sequence": value.sequence}
                 for value in field.value_ids
                 if self._normalize_value_name(value.name) not in existing]
        if nuovi:
            self.env["product.attribute.value"].create(nuovi)

        self.attribute_id = attribute.id
        self.source_type = "attribute"
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Attributo ManoMano",
                "message": ("Attributo «%s» %s: %s valori aggiunti, %s già "
                            "presenti. Riga impostata su fonte «Attributo "
                            "prodotto». Ora valorizzalo sulle schede prodotto."
                            % (attribute.name,
                               "creato" if created else "aggiornato",
                               len(nuovi), len(existing))),
                "type": "success",
                "sticky": False,
            },
        }
