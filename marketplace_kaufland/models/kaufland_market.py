# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Un mercato Kaufland dentro un canale.

Le chiavi API di Kaufland sono dell'account venditore, non del mercato: il
mercato viaggia come parametro su ogni chiamata (`?storefront=it`). Cinque
mercati non devono quindi voler dire cinque canali con lo stesso segreto
copiato cinque volte — chi ne dimentica uno alla rotazione rompe quel mercato
in silenzio.

⚠️ **Ma la ragione per cui questo modello esiste non e' quella.** Il cancello
del riaggancio — cio' che autorizza a CREARE offerte su Kaufland — deve contare
**per mercato**. Tenuto sul canale, una lettura parziale su Kaufland.de
aprirebbe o chiuderebbe il cancello anche per l'Italia: un mercato si
porterebbe dietro gli altri, ed e' la protezione che vale piu' di tutte.

**Il mercato non e' un'etichetta: e' l'unita' su cui si conta.**

Piano: docs/superpowers/plans/2026-08-29-kaufland-multi-mercato.md
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError

MERCATI = [("at", "Austria"), ("de", "Germania"), ("es", "Spagna"),
           ("fr", "Francia"), ("it", "Italia"), ("nl", "Paesi Bassi")]


class CentrivoKauflandMarket(models.Model):
    """Un mercato Kaufland servito da un canale.

    ⚠️ **IN SOLA LETTURA PER GLI UTENTI NORMALI**, come `kaufland.offer` e per
    la stessa ragione: `riagganciato` e `offerte_attese` sono il cancello, e chi
    li tocca a mano decide **cosa viene creato su un marketplace vero**. Il
    connettore scrive sempre in `sudo()`, quindi chiudere i permessi non rompe
    nessun giro; una scrittura aggiunta senza `sudo()` fallirebbe con un
    `AccessError` su un utente normale — che e' il verso giusto in cui
    accorgersene.
    """

    _name = "centrivo.kaufland.market"
    _description = "Kaufland — un mercato servito da un canale"
    _order = "channel_id, storefront"
    _rec_name = "storefront"

    channel_id = fields.Many2one(
        "centrivo.channel", string="Canale", required=True,
        ondelete="cascade", index=True)

    storefront = fields.Selection(
        MERCATI, string="Mercato", required=True, index=True,
        help="Il mercato Kaufland di questa riga. Viaggia come `storefront` "
             "su ogni chiamata.")

    # ⚠️ Sta qui e non sul canale perche' e' davvero per mercato: il gruppo di
    # Kaufland.de non vale per Kaufland.it. Kaufland rifiuta le offerte che non
    # ne hanno uno.
    shipping_group_id = fields.Char(
        string="Gruppo di spedizione",
        help="⚠️ Kaufland rifiuta le offerte che non ne hanno uno, ed e' per "
             "mercato: quello di Kaufland.de non vale per Kaufland.it.")

    warehouse_id = fields.Char(
        string="Magazzino Kaufland",
        help="Facoltativo. Vuoto significa il magazzino predefinito.")

    active = fields.Boolean(
        string="Attivo", default=True,
        help="Un mercato spento non viene ne' letto ne' scritto. Serve a "
             "sospendere un mercato senza perdere la sua fotografia.")

    # ------------------------------------------------------------------
    # IL CANCELLO — per mercato, ed e' il punto di tutto questo modello.
    # ------------------------------------------------------------------
    riagganciato = fields.Boolean(
        string="Riagganciato", copy=False, groups="base.group_system",
        help="⚠️ Non dice «ho letto le offerte vive»: dice «le ho lette TUTTE, "
             "e ognuna si sa dove e' finita». E' cio' che autorizza a creare "
             "offerte su QUESTO mercato.")
    riagganciato_il = fields.Datetime(
        string="Riagganciato il", copy=False, groups="base.group_system")
    offerte_attese = fields.Integer(
        string="Offerte attese su Kaufland", copy=False,
        groups="base.group_system",
        help="Quante offerte vive il riaggancio si aspetta di trovare su "
             "questo mercato. Un elenco piu' corto non apre il cancello: "
             "puo' essere un filtro della loro API, non offerte sparite.")

    offer_ids = fields.One2many(
        "kaufland.offer", "market_id", string="Offerte")

    company_id = fields.Many2one(
        "res.company", string="Azienda", related="channel_id.company_id",
        store=True, index=True)

    _sql_constraints = [
        # ⚠️ Un mercato una volta sola per canale: due righe «it» sullo stesso
        # canale vorrebbero dire due cancelli per lo stesso mercato, e il giro
        # ne aprirebbe uno lasciando l'altro indietro.
        ("uniq_canale_mercato", "unique(channel_id, storefront)",
         "Questo mercato Kaufland e' gia' configurato su questo canale."),
    ]

    @api.depends("storefront", "channel_id")
    def _compute_display_name(self):
        etichette = dict(MERCATI)
        for riga in self:
            riga.display_name = "%s — %s" % (
                riga.channel_id.display_name or "",
                etichette.get(riga.storefront, riga.storefront or ""))

    def _controlla_configurazione(self):
        """Quello che Kaufland pretende, detto PRIMA di chiamarlo.

        ⚠️ Senza gruppo di spedizione Kaufland rifiuta le offerte, e il
        messaggio che restituisce non dice che manca quello: si perde tempo a
        sospettare le credenziali.
        """
        self.ensure_one()
        if not (self.shipping_group_id or "").strip():
            raise UserError(_(
                "Sul mercato «%s» manca il gruppo di spedizione: Kaufland "
                "rifiuta le offerte che non ne hanno uno.") % self.display_name)
        return True
