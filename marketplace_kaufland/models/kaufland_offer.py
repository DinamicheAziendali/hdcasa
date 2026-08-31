# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
from odoo import fields, models

STATI_SCHEDA = [
    ("pronta", "Pronta — basta l'offerta"),
    ("guscio", "Guscio — c'e' il codice ma non la pagina"),
    ("assente", "Assente — Kaufland non lo conosce"),
    ("sconosciuto", "Non lo so — Kaufland non ha risposto"),
]


class KauflandOffer(models.Model):
    """Lo stato di un prodotto su un mercato Kaufland.

    ⚠️ QUESTO MODELLO E' IN SOLA LETTURA PER GLI UTENTI NORMALI
    (`security/ir.model.access.csv`: `base.group_user` ha 1,0,0,0), e non e'
    prudenza generica. La difesa contro i doppioni ha due meta': il cancello
    del riaggancio sul canale, e `ultimo_esito="errore"` su queste righe —
    il marchio che tiene fuori dalle candidate le contese, i gemelli e i
    codici a barre gia' di un'offerta viva. Il cancello ha due porte chiuse
    (`groups=` sul campo, `AccessError` sul bottone); il marchio era un campo
    normale su un modello aperto a chiunque. Un magazziniere che «pulisce»
    una riga rossa rimetterebbe in gioco cio' che era stato messo fuori
    gioco, e alla prima creazione sarebbe un'offerta doppia su un
    marketplace vero.

    ⚠️ Il modulo scrive qui SEMPRE in `sudo()` (vedi `connectors/kaufland.py`,
    dove ogni accesso passa da `self.env["kaufland.offer"].sudo()`): chiudere
    i permessi non rompe nessun giro. Chi aggiungesse una scrittura senza
    `sudo()` la vedrebbe fallire con un `AccessError` su un utente normale —
    che e' il verso giusto in cui accorgersene.
    """

    _name = "kaufland.offer"
    _description = "Kaufland — stato di un prodotto su un mercato"
    _order = "market_id, ean"

    # ⚠️ L'offerta appartiene al MERCATO, non al canale: lo stesso prodotto ha
    # un'offerta distinta su Kaufland.it e su Kaufland.de, e con la vecchia
    # chiave (canale, prodotto) la seconda non sarebbe potuta nemmeno nascere.
    market_id = fields.Many2one(
        "centrivo.kaufland.market", string="Mercato", required=True,
        ondelete="cascade", index=True)

    # Il canale resta, memorizzato, perche' meta' del modulo e le regole
    # multi-azienda ragionano ancora per canale — ma NON e' piu' la chiave.
    channel_id = fields.Many2one(
        "centrivo.channel", string="Canale", related="market_id.channel_id",
        store=True, index=True)

    # ⚠️ La riga sopravvive al prodotto perche' l'offerta su Kaufland
    # sopravvive: sparito il prodotto Odoo resta l'`id_unit`, l'unico appiglio
    # su un'offerta che la' fuori e' ancora in vendita. Con `cascade` la riga
    # sparirebbe e l'offerta resterebbe viva e irraggiungibile — e non
    # servirebbe nemmeno una cancellazione voluta: Odoo distrugge e ricrea le
    # varianti quando si cambiano le righe attributo di un template, e ogni
    # variante distrutta si porterebbe via la sua riga. Stessa scelta di
    # `centrivo.temu.listing` (marketplace_temu/models/temu_listing.py:23-25),
    # e per lo stesso motivo: prodotto NON obbligatorio, `set null`.
    product_id = fields.Many2one(
        "product.product", string="Prodotto", ondelete="set null",
        index=True)
    ean = fields.Char(string="Codice a barre", index=True)

    # ⚠️ Il `default` non e' cosmetico: senza, una riga appena creata vale
    # `False`, che NON e' `sconosciuto` — e `sconosciuto` esiste apposta per
    # dire «non lo so». Un `False` sarebbe un quinto stato muto: un secchio
    # senza nome raggruppando per stato, e nessuno dei filtri lo
    # intercetterebbe, rendendo invisibili proprio le righe mai controllate.
    stato_scheda = fields.Selection(
        STATI_SCHEDA, string="Scheda su Kaufland", default="sconosciuto",
        help="⚠️ «Guscio» significa che Kaufland risponde 200 ma la pagina "
             "non esiste: un'offerta agganciata li' non vende nulla e non "
             "da' alcun errore.")
    id_unit = fields.Char(
        string="Identificativo offerta", index=True,
        help="L'id_unit di Kaufland. Senza, l'offerta non e' aggiornabile.")

    ultimo_prezzo = fields.Integer(string="Ultimo prezzo mandato (centesimi)")
    ultima_quantita = fields.Integer(string="Ultima quantita' mandata")
    # ⚠️ QUANDO E' VUOTO, IL PRIMO INVIO SI FORZA. `ultimo_prezzo` e
    # `ultima_quantita` sono Integer: valgono 0 finche' qualcuno non ci
    # scrive, quindi «mai mandata» e «mandata zero» sono lo stesso valore. Su
    # un prodotto a giacenza 0 il confronto `0 != 0` non manderebbe niente, e
    # su Kaufland resterebbe l'`amount` con cui l'offerta e' nata: l'offerta
    # continuerebbe a vendere merce che non c'e', e non si auto-riparerebbe
    # mai (un prodotto stabilmente a zero non torna «cambiato»). Questa data
    # dice se un invio NOSTRO e' davvero avvenuto, ed e' l'unica cosa che
    # distingue le due situazioni. Il riaggancio NON la scrive: la' i valori
    # arrivano da Kaufland, non da noi.
    allineato_il = fields.Datetime(
        string="Allineato il", copy=False,
        help="Quando prezzo e giacenza sono stati mandati a Kaufland da qui. "
             "Vuoto significa mai: il primo allineamento manda tutti e due i "
             "valori anche se sembrano gia' a posto.")

    # ⚠️ Chi ha scritto l'errore che sta sulla riga. Serve a distinguere un
    # impedimento che una PERSONA deve risolvere (una contesa scritta dalla
    # ricognizione o dal riaggancio: la' il messaggio e' l'unica cosa
    # interrogabile per prodotto, e un giro riuscito non lo puo' cancellare)
    # da un guasto TRANSITORIO dell'allineamento, che il giro dopo deve poter
    # sostituire o chiudere. Senza, la riga direbbe «Fallito» col motivo
    # dell'altro ieri mentre si allinea benissimo.
    errore_da = fields.Selection(
        [("allineamento", "Allineamento")], string="Errore scritto da",
        copy=False,
        help="Vuoto significa un altro giro (ricognizione, riaggancio, "
             "creazione): quegli errori l'allineamento non li tocca.")

    ultimo_esito = fields.Selection(
        [("successo", "Riuscito"), ("errore", "Fallito"),
         ("saltato", "Saltato")], string="Ultimo esito")
    ultimo_messaggio = fields.Text(string="Ultimo messaggio")
    controllato_il = fields.Datetime(string="Controllato il")

    company_id = fields.Many2one(
        "res.company", string="Azienda",
        related="channel_id.company_id", store=True, index=True)

    _sql_constraints = [
        # ⚠️ Sul MERCATO, non sul canale. Con la chiave vecchia
        # `unique(channel_id, product_id)` un canale che serve piu' mercati non
        # potrebbe avere lo stesso prodotto su due mercati — cioe' il
        # multi-mercato sarebbe impossibile per costruzione.
        ("uniq_mercato_prodotto",
         "unique(market_id, product_id)",
         "Questo prodotto ha gia' una riga su questo mercato."),
        # ⚠️ Due prodotti diversi non possono puntare alla stessa offerta
        # viva: e' l'esito tipico di un riaggancio sbagliato (che abbina
        # l'`id_offer` di Kaufland al `default_code` del prodotto — NON
        # l'EAN), e senza vincolo l'allineamento manderebbe due
        # prezzi diversi alla stessa offerta, vincerebbe l'ultimo e nessuno
        # dei due giri segnalerebbe un errore. Postgres considera i NULL
        # distinti fra loro, quindi le righe non ancora agganciate (id_unit
        # vuoto) non si disturbano: verificato su postgres:16 il 2026-08-26.
        # ⚠️ Anche questo per mercato: lo stesso `id_unit` puo' esistere su
        # mercati diversi senza che sia un errore.
        ("uniq_mercato_id_unit",
         "unique(market_id, id_unit)",
         "Un'altra riga di questo mercato punta gia' a questa offerta "
         "Kaufland."),
    ]
