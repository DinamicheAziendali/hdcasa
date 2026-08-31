# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Togliere il tag a un prodotto deve mandare la sua offerta a ZERO su ManoMano.

Il difetto che questo banco presidia e' successo davvero: il 2026-08-25 su
ManoMano c'erano **4.117 offerte vive, tutte con giacenza > 0**, di prodotti che
in Odoo non avevano piu' il tag. Sono state spente a mano caricando un file di
4.114 righe dal loro pannello.

La causa sta nel disegno: `PUT /api/v1/offers` e' un **upsert** — sa creare e
aggiornare, **non sa togliere** — e `push_offers` ricostruisce l'elenco dal tag a
ogni giro. Tolto il tag, quel prodotto sparisce dal messaggio e ManoMano tiene
l'ultimo valore ricevuto per sempre.

⚠️ **Questo banco gira DENTRO Odoo**, ed e' il primo del modulo. I 36 banchi in
`tools/` non caricano i modelli: il 2026-08-28 un campo di tipo sbagliato ha
fermato quindici moduli insieme senza che nessuno di loro potesse vederlo.

Progetto: docs/superpowers/specs/2026-08-29-manomano-ritiro-offerte-design.md
"""
from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged

PERCORSO_CHIAMATA = (
    "odoo.addons.marketplace_manomano.connectors.manomano."
    "ManoManoConnector._request_rate_limited")


class RispostaFinta:
    """Una risposta che ManoMano potrebbe davvero dare: 200 e nessun rifiuto.

    ⚠️ Deliberatamente NON e' un mock generico che dice si' a tutto. Un finto che
    imita il comportamento *desiderato* invece di quello *vero* e' il modo in cui
    su Kaufland tre difetti veri sono passati sotto test verdi (2026-08-26).
    Qui la forma del corpo e' quella documentata, e i controlli guardano cosa e'
    stato SPEDITO, non cosa il finto ha risposto.
    """

    status_code = 200
    ok = True
    text = '{"offers": []}'

    @property
    def json(self):
        return {"offers": []}


@tagged("post_install", "-at_install", "centrivo_manomano")
class TestRitiroOfferte(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        azienda = cls.env.company

        cls.tag = cls.env["product.tag"].create({"name": "ManoMano Test Ritiro"})

        cls.listino = cls.env["product.pricelist"].create({
            "name": "Listino ManoMano Test",
            "currency_id": azienda.currency_id.id,
        })

        # Un prodotto vendibile e spedibile: senza peso ManoMano rifiuta la riga
        # (display_weight = 0), e il banco misurerebbe un salto invece di un invio.
        cls.prodotto = cls.env["product.product"].create({
            "name": "Prodotto Ritiro Test",
            "default_code": "TEST-RITIRO-1",
            "barcode": "8000000000017",
            "list_price": 49.90,
            "weight": 2.5,
            "type": "consu",
            "is_storable": True,
        })
        cls.prodotto.product_tmpl_id.product_tag_ids = [(4, cls.tag.id)]

        cls.canale = cls.env["centrivo.channel"].create({
            "name": "ManoMano Test Ritiro",
            "connector_code": "manomano",
            "company_id": azienda.id,
            "export_product_tag_ids": [(4, cls.tag.id)],
            "pricelist_selling_id": cls.listino.id,
            "manomano_contract_codes": "1234",
            "manomano_carrier_grid_name": "Consegna a domicilio standard",
            "manomano_transit_days_min": 2,
            "manomano_transit_days_max": 5,
            "manomano_default_weight": 1.0,
            "api_key": "chiave-finta-di-prova",
        })

    # ------------------------------------------------------------------
    def _manda_e_raccogli(self):
        """Fa girare push_offers senza uscire in rete e restituisce le righe spedite.

        Si legge il `json=` della chiamata: e' cio' che ManoMano riceverebbe.
        Guardare il valore di ritorno di push_offers direbbe solo quante righe
        il connettore *crede* di aver mandato.
        """
        spedite = []

        def finta(self_connettore, method, path, **kwargs):
            spedite.extend(kwargs.get("json") or [])
            return RispostaFinta()

        with patch(PERCORSO_CHIAMATA, finta):
            self.canale._get_connector().push_offers()
        return spedite

    @staticmethod
    def _riga(spedite, sku):
        for riga in spedite:
            if riga.get("sku") == sku:
                return riga
        return None

    @staticmethod
    def _prezzo(riga):
        """Il prezzo dentro il messaggio vero.

        ⚠️ Nel payload verso ManoMano il prezzo NON e' in cima: sta in
        `pricing.price_vat_included`. Leggerlo come se fosse `riga["price"]`
        (com'era scritto qui la prima volta) da' KeyError — ed e' lo stesso
        equivoco che nel connettore faceva salvare in memoria prezzo, peso e
        tempi a zero.
        """
        return (riga.get("pricing") or {}).get("price_vat_included")

    # ------------------------------------------------------------------
    def test_01_col_tag_l_offerta_parte(self):
        """Prima di tutto: il giro normale funziona ancora.

        ⚠️ Serve a distinguere «il ritiro non c'e'» da «il banco non manda
        niente»: senza questo controllo, il secondo test fallirebbe lo stesso e
        per il motivo sbagliato.
        """
        spedite = self._manda_e_raccogli()
        riga = self._riga(spedite, "TEST-RITIRO-1")
        self.assertIsNotNone(
            riga, "Col tag, l'offerta deve partire: se non parte il banco sta "
                  "misurando la configurazione, non il ritiro.")
        self.assertEqual(self._prezzo(riga), 49.90)

    def test_02_tolto_il_tag_l_offerta_va_a_zero(self):
        """IL CONTROLLO CHE CONTA.

        Si manda una volta col tag (cosi' ManoMano «conosce» l'offerta), poi si
        toglie il tag e si rimanda. La seconda volta l'offerta deve partire
        ancora, con **quantita' zero**: e' il modo corretto di metterla in pausa
        su ManoMano, senza cancellarla.

        ⚠️ Visto FALLIRE prima di scrivere il codice (ramo `prova/rosso-manomano`,
        2026-08-29): senza il ritiro non parte niente, e su ManoMano l'offerta
        resta comprabile. E' l'unico modo di sapere che questo banco protegge
        qualcosa.
        """
        self._manda_e_raccogli()

        self.prodotto.product_tmpl_id.product_tag_ids = [(3, self.tag.id)]

        spedite = self._manda_e_raccogli()
        riga = self._riga(spedite, "TEST-RITIRO-1")
        self.assertIsNotNone(
            riga,
            "Tolto il tag, l'offerta deve essere RITIRATA mandando una riga a "
            "quantita' zero. Oggi non si manda niente e ManoMano tiene "
            "l'ultimo valore per sempre: e' cosi' che sono nate le 4.117 "
            "offerte fantasma del 2026-08-25.")
        self.assertEqual(
            riga["stock"], 0,
            "La riga del ritiro deve portare quantita' 0.")
        self.assertEqual(
            self._prezzo(riga), 49.90,
            "Il ritiro manda l'ULTIMO prezzo gia' spedito: ManoMano pretende un "
            "prezzo, e non se ne inventa uno nuovo.")
        # ⚠️ E il peso: preso dal prodotto invece che dalla memoria sarebbe
        # zero al momento del ritiro, e ManoMano rifiuta il peso zero.
        self.assertEqual(
            (riga.get("shipping") or {}).get("display_weight"), 2.5,
            "Il ritiro conserva il peso gia' mandato.")

    def test_03_il_tag_vuoto_non_ritira_niente(self):
        """⚠️ La guardia che protegge dal caso peggiore, che e' umano.

        Un canale rimasto senza tag non deve spegnere il catalogo: `push_offers`
        si ferma prima, e nessuna riga parte. Se un giorno il calcolo dei ritiri
        finisse PRIMA di questa guardia, una configurazione sbagliata
        azzererebbe tutto.
        """
        self.canale.export_product_tag_ids = [(5, 0, 0)]
        spedite = self._manda_e_raccogli()
        self.assertEqual(
            spedite, [],
            "Senza tag non si manda NIENTE: ne' offerte ne' ritiri.")
