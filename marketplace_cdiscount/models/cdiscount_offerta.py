# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""L'OFFERTA di un prodotto su Cdiscount: prezzo, giacenza, éco-participation.

Una riga per scheda riuscita e per canale. E' **la memoria di cosa e' stato
mandato**: senza, non si sa cosa allineare al giro dopo ne' cosa ritirare
quando un prodotto esce dal perimetro (lezione ManoMano: le 4.117 offerte
fantasma spente a mano). L'offerta si aggiorna con un pacchetto `Upsert`, e
si ritira mandando quantita' ZERO — non si cancella mai da qui.

⚠️ QUESTO MODELLO E' IN SOLA LETTURA PER GLI UTENTI NORMALI (vedi
`security/ir.model.access.csv`), come la scheda e per la stessa ragione: lo
`stato` decide se l'offerta verra' rimandata, e «pulire» una riga rossa a mano
farebbe ripartire un prezzo su un marketplace vero.

⚠️ Il modulo scrive qui SEMPRE in `sudo()` (connettore e cron): chiudere i
permessi non rompe nessun giro.
"""
from odoo import fields, models

from ..connectors.cdiscount_rapporto import RIFIUTATO, RIUSCITO
from .cdiscount_scheda import IN_ATTESA, SCONOSCIUTO_SCHEDA

DA_MANDARE = "da_mandare"
RITIRATA = "ritirata"

STATI_OFFERTA = [
    (DA_MANDARE, "Da mandare"),
    (IN_ATTESA, "In attesa dell'esito"),
    (RIUSCITO, "Viva — Cdiscount l'ha integrata"),
    (RIFIUTATO, "Rifiutata — Cdiscount ha detto perche'"),
    (RITIRATA, "Ritirata — quantita' zero"),
    (SCONOSCIUTO_SCHEDA, "Non lo so — nessun verdetto"),
]


class CdiscountOfferta(models.Model):
    _name = "cdiscount.offerta"
    _description = "Cdiscount — offerta di un prodotto (prezzo e giacenza)"
    _order = "channel_id, codice"
    _rec_name = "codice"

    channel_id = fields.Many2one(
        "centrivo.channel", string="Canale", required=True,
        ondelete="cascade", index=True)
    scheda_id = fields.Many2one(
        "cdiscount.scheda", string="Scheda", ondelete="set null", index=True,
        help="La scheda riuscita da cui questa offerta nasce. Se la scheda "
             "non e' piu' riuscita, l'offerta si ritira (quantita' zero).")
    product_id = fields.Many2one(
        "product.product", string="Prodotto", ondelete="set null",
        index=True)
    codice = fields.Char(
        string="Codice venditore", required=True, index=True,
        help="Il `sellerExternalReference`: il nostro SKU, lo stesso della "
             "scheda. E' l'unica cosa che l'esito di Cdiscount nomina.")
    stato = fields.Selection(
        STATI_OFFERTA, string="Stato", default=DA_MANDARE, required=True,
        copy=False)
    pacchetto_id = fields.Many2one(
        "cdiscount.pacchetto", string="Pacchetto", ondelete="set null",
        index=True,
        help="Il pacchetto di offerte con cui questa riga e' partita "
             "l'ultima volta.")

    # ⚠️ LA MEMORIA: cio' che e' stato mandato l'ultima volta. Il giro dopo
    # manda solo cio' che e' cambiato, e il ritiro manda zero su cio' che
    # era stato mandato.
    ultimo_prezzo = fields.Float(
        string="Ultimo prezzo mandato (€)", digits=(16, 2), copy=False)
    ultima_quantita = fields.Integer(
        string="Ultima quantita' mandata", copy=False)
    ultima_ecotax = fields.Float(
        string="Ultima éco-participation mandata (€)", digits=(16, 2),
        copy=False)
    ritiro = fields.Boolean(
        string="E' un ritiro", default=False, copy=False,
        help="Vero quando l'ultimo invio era un ritiro (quantita' zero "
             "perche' la scheda non e' piu' riuscita o il prodotto e' "
             "sparito). All'esito positivo la riga passa a «Ritirata».")
    mandata_il = fields.Datetime(string="Mandata il", copy=False)
    motivo = fields.Text(
        string="Motivo", copy=False,
        help="Cosa ha detto Cdiscount, o perche' la riga e' stata saltata.")
    controllato_il = fields.Datetime(string="Controllato il", copy=False)

    company_id = fields.Many2one(
        "res.company", string="Azienda",
        related="channel_id.company_id", store=True, index=True)

    _sql_constraints = [
        ("uniq_canale_codice", "unique(channel_id, codice)",
         "Questo codice ha già un'offerta su questo canale."),
    ]
