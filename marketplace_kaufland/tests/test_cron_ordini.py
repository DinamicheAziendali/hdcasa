# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Kaufland dentro il cron degli ordini — Consegna 2, Compito 8.

⚠️ **Non c'e' un cron di Kaufland, e non deve esserci.** Lo scarico ordini ha
UN SOLO cron per tutti i marketplace (`Integrations: scarica ordini`), che
scorre i canali attivi. Kaufland ci si innesta come Temu e BricoBravo. Un cron
in piu' sarebbe un doppione, e due passate che scaricano gli stessi ordini sono
il modo di scoprire tardi che qualcosa si e' importato due volte.

⚠️ **Ma l'innesto non era mai stato provato.** Su Kaufland lo scarico passa per
i MERCATI (`per_mercato`), e il metodo del tronco chiama `pull_orders()`
diritto: senza la deviazione sul canale, ogni passaggio del cron solleverebbe
«operazione avviata senza un mercato» — su un canale che non ha fatto niente di
male. Queste prove sono la prova che la deviazione c'e' e regge.

E poi il cancello della SPEDIZIONE, che e' una faccenda diversa: vedi sotto.
"""
import json

from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged

from .test_import_ordini import TRASPORTO, unita


@tagged("post_install", "-at_install", "centrivo_kaufland")
class TestCronOrdiniKaufland(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.prodotto = cls.env["product.product"].create({
            "name": "Box doccia (prova cron)",
            "default_code": "TEST-KFL-CRON-1",
            "barcode": "9990000000210", "list_price": 199.0})
        cls.canale = cls.env["centrivo.channel"].create({
            "name": "Kaufland (prova cron)",
            "connector_code": "kaufland",
            "company_id": cls.env.company.id,
            "kaufland_client_key": "chiave-finta",
            "kaufland_secret_key": "segreto-finto",
        })
        cls.mercato_de = cls.env["centrivo.kaufland.market"].create({
            "channel_id": cls.canale.id, "storefront": "de",
            "shipping_group_id": "195841"})

    def _solo_il_nostro(self):
        """Spegne gli altri canali per la durata della prova.

        ⚠️ Non e' pulizia: senza, il canale Kaufland VERO dello stage risponde
        per primo al finto trasporto e si prende la riga d'ordine — la mappa
        nasce sotto di lui, e la prova fallisce accusando il codice di un
        difetto che non ha. Ci sono cascato al primo giro.

        ⚠️ E l'altra meta': il cron scorre TUTTI i canali attivi, compresi
        BricoBravo e ManoMano con le credenziali vere. Odoo blocca da solo le
        chiamate non finte dentro i test — ma quel blocco e' l'ultima rete, non
        la prima: una prova che si appoggia a lui per non telefonare a un
        marketplace vero e' scritta male. Qui non ci arriva nemmeno.
        """
        altri = self.env["centrivo.channel"].search(
            [("id", "!=", self.canale.id), ("active", "=", True)])
        altri.write({"active": False})

    def _cron(self, righe):
        """Fa girare IL cron del tronco, non il pulsante di Kaufland."""
        self._solo_il_nostro()
        chiamate = []
        viste = {}

        def finto(self_trasporto, metodo, uri, teste, corpo):
            chiamate.append(uri)
            # Una sola pagina piena per mercato, poi vuoto.
            mercato = uri.split("storefront=")[-1].split("&")[0]
            viste[mercato] = viste.get(mercato, 0) + 1
            dati = righe if viste[mercato] == 1 else []
            return 200, json.dumps({
                "data": dati,
                "pagination": {"offset": 0, "limit": 100, "total": len(righe)},
            }), {}

        with patch(TRASPORTO, finto):
            self.env["centrivo.channel"].cron_pull_all_channels()
        return chiamate

    def _mappa(self, id_order):
        return self.env["centrivo.order.map"].sudo().search([
            ("channel_id", "=", self.canale.id),
            ("external_id", "=", id_order)])

    # ------------------------------------------------------------------
    def test_01_il_cron_del_tronco_importa_un_ordine_Kaufland(self):
        """⚠️ È l'unica prova che conta di questo compito: il cron condiviso
        arriva davvero fino a un ordine Kaufland dentro Odoo."""
        self._cron([unita("314568015280001", "MCRON01", "TEST-KFL-CRON-1")])
        mappa = self._mappa("MCRON01")
        self.assertEqual(len(mappa), 1, "Il cron deve aver creato la mappa.")
        self.assertEqual(mappa.state, "imported")
        self.assertTrue(mappa.sale_order_id, "E l'ordine di vendita.")

    def test_02_il_cron_passa_per_OGNI_mercato(self):
        """Due mercati, due letture: il cron non deve fermarsi al primo."""
        self.env["centrivo.kaufland.market"].create({
            "channel_id": self.canale.id, "storefront": "it",
            "shipping_group_id": "195842"})
        chiamate = self._cron([])
        mercati = {uri.split("storefront=")[-1].split("&")[0]
                   for uri in chiamate}
        self.assertEqual(mercati, {"de", "it"},
                         "Devono essere letti tutti i mercati: %s" % chiamate)

    def test_03_il_cron_chiede_solo_le_righe_da_spedire(self):
        """La trappola dei 15 minuti vale anche — anzi soprattutto — dal cron:
        è lì che nessuno sta guardando."""
        chiamate = self._cron([])
        self.assertTrue(chiamate)
        self.assertTrue(all("status=need_to_be_sent" in u for u in chiamate))

    def test_04_un_canale_Kaufland_senza_mercati_non_ferma_il_cron(self):
        """⚠️ Il cron scorre TUTTI i canali attivi: un canale mal configurato
        deve lasciar passare gli altri, non spegnere lo scarico di tutti."""
        malato = self.env["centrivo.channel"].create({
            "name": "Kaufland (senza mercati)",
            "connector_code": "kaufland",
            "company_id": self.env.company.id,
            "kaufland_client_key": "x", "kaufland_secret_key": "y"})
        self._solo_il_nostro()
        malato.active = True      # ⚠️ questo deve restare acceso: e' il guasto
        self._cron([unita("314568015280002", "MCRON02", "TEST-KFL-CRON-1")])
        self.assertEqual(len(self._mappa("MCRON02")), 1,
                         "L'ordine del canale sano dev'essere entrato lo stesso.")


@tagged("post_install", "-at_install", "centrivo_kaufland")
class TestCancelloSpedizioneKaufland(TransactionCase):
    """⚠️ Il cancello della SPEDIZIONE — e perché esiste.

    Lo scarico ordini e la comunicazione spedizioni hanno DUE cron diversi, e
    quello delle spedizioni scorre anch'esso tutti i canali attivi. Oggi è
    spento; il giorno che qualcuno lo accende per BricoBravo, **Kaufland ci
    finirebbe dentro da solo** — e comincerebbe a mandare, in automatico e senza
    che nessuno guardi, chiamate che **non sono mai state provate contro
    Kaufland vero**.

    Su Kaufland non esiste un ambiente di prova: quelle chiamate scrivono su
    ordini di clienti veri, e un numero di tracciamento riusato è un rifiuto.

    Quindi la spedizione Kaufland **nasce col cancello chiuso**, come tutto il
    resto in questo progetto: dal cron non parte, **a mano sì**. Il cancello lo
    apre una persona, dopo aver visto sul portale che il primo invio è andato.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.prodotto = cls.env["product.product"].create({
            "name": "Box doccia (prova cancello)",
            "default_code": "TEST-KFL-CANC-1",
            "barcode": "9990000000227", "list_price": 199.0})
        cls.canale = cls.env["centrivo.channel"].create({
            "name": "Kaufland (prova cancello spedizione)",
            "connector_code": "kaufland",
            "company_id": cls.env.company.id,
            "kaufland_client_key": "chiave-finta",
            "kaufland_secret_key": "segreto-finto"})
        cls.env["centrivo.kaufland.market"].create({
            "channel_id": cls.canale.id, "storefront": "de",
            "shipping_group_id": "195841"})
        cls.marca_brt = cls.env.ref("integrations_core.carrier_brand_brt")
        cls.vettore = cls.env["delivery.carrier"].create({
            "name": "BRT (prova cancello)",
            "product_id": cls.env["product.product"].create({
                "name": "Trasporto BRT (cancello)", "type": "service"}).id})
        cls.env["centrivo.carrier.source"].create({
            "source_model": "delivery.carrier",
            "source_res_id": cls.vettore.id,
            "source_display": cls.vettore.display_name,
            "brand_id": cls.marca_brt.id,
            "company_id": cls.env.company.id})

    def _ordine_pronto(self):
        viste = {"n": 0}
        righe = [unita("314568015290001", "MCANC01", "TEST-KFL-CANC-1")]

        def finto(self_trasporto, metodo, uri, teste, corpo):
            viste["n"] += 1
            dati = righe if viste["n"] == 1 else []
            return 200, json.dumps({
                "data": dati, "pagination": {"offset": 0, "limit": 100,
                                             "total": len(righe)}}), {}

        with patch(TRASPORTO, finto):
            self.canale._get_connector().per_mercato("pull_orders")
        mappa = self.env["centrivo.order.map"].sudo().search(
            [("channel_id", "=", self.canale.id)], limit=1)
        picking = mappa.sale_order_id.picking_ids[:1]
        picking.write({"carrier_id": self.vettore.id,
                       "carrier_tracking_ref": "BRT-CANC-1"})
        for move in picking.move_ids:
            move.quantity = move.product_uom_qty
        picking.picking_type_id.create_backorder = "never"
        picking.button_validate()
        return mappa

    def _conta_chiamate(self, azione):
        # ⚠️ Stessa ragione di `_solo_il_nostro` qui sopra: il cron delle
        # spedizioni scorre anche lui tutti i canali attivi.
        self.env["centrivo.channel"].search(
            [("id", "!=", self.canale.id), ("active", "=", True)]
        ).write({"active": False})
        chiamate = []

        def finto(self_trasporto, metodo, uri, teste, corpo):
            chiamate.append(uri)
            return 200, json.dumps({"data": {}}), {}

        with patch(TRASPORTO, finto):
            azione()
        return chiamate

    # ------------------------------------------------------------------
    def test_10_col_cancello_chiuso_il_CRON_non_manda_niente(self):
        """⚠️ È la prova che conta: automatico spento finché non l'ha visto
        una persona."""
        mappa = self._ordine_pronto()
        chiamate = self._conta_chiamate(
            self.env["centrivo.channel"].cron_push_shipments)
        self.assertEqual(chiamate, [],
                         "Dal cron non deve partire niente: %s" % chiamate)
        self.assertFalse(mappa.shipment_pushed)

    def test_11_col_cancello_chiuso_il_PULSANTE_manda_lo_stesso(self):
        """Il primo invio è un gesto di una persona che guarda il portale:
        dev'essere possibile, ed è il solo modo di aprire il cancello."""
        mappa = self._ordine_pronto()
        chiamate = self._conta_chiamate(mappa.action_push_shipment)
        self.assertEqual(len(chiamate), 1,
                         "A mano la spedizione deve partire.")
        self.assertTrue(mappa.shipment_pushed)

    def test_12_col_cancello_aperto_anche_il_cron_manda(self):
        """Aperto il cancello, Kaufland rientra nell'automatismo come gli altri."""
        mappa = self._ordine_pronto()
        self.canale.sudo().action_kaufland_apri_cancello_spedizione()
        chiamate = self._conta_chiamate(
            self.env["centrivo.channel"].cron_push_shipments)
        self.assertEqual(len(chiamate), 1)
        self.assertTrue(mappa.shipment_pushed)

    def test_13_il_rifiuto_dal_cron_dice_PERCHE_e_dove_si_apre(self):
        """Un cron che non fa niente in silenzio è peggio di uno che fallisce."""
        self._ordine_pronto()
        self._conta_chiamate(self.env["centrivo.channel"].cron_push_shipments)
        registro = self.env["centrivo.job.log"].sudo().search(
            [("channel_id", "=", self.canale.id),
             ("operation", "=", "push_shipment")], limit=1)
        self.assertTrue(registro, "Dev'esserci una riga nel registro.")
        # ⚠️ Senza maiuscole/minuscole: il messaggio grida «A MANO» apposta, e
        # un banco che si aggrappa a come e' scritta una parola si rompe alla
        # prima riformulazione, senza che sia cambiato niente di vero.
        testo = (registro.message or "").lower()
        self.assertIn("a mano", testo,
                      "Deve dire che il primo invio si fa a mano: %s" % testo)
        self.assertIn("portale", testo,
                      "E deve dire dove si guarda per poter aprire.")
