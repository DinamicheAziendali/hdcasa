# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""La comunicazione della spedizione a Kaufland — Consegna 2, Compito 5.

⚠️ **Queste prove non dimostrano che Kaufland accetti le nostre chiamate.**
Dimostrano che ci comportiamo come la sua **guida** dice, e la guida l'abbiamo
letta, non misurata: `docs/kaufland-spedizione-guida.md`. La prova vera è una
spedizione vera, guardando il portale (Compito 9).

Le tre cose che la guida impone, e che qui si presidiano una per una:

1. ⚠️ **si comunica PER RIGA** (`/order-units/{id}/send`), non per ordine: un
   ordine da due articoli sono **due chiamate**;
2. ⚠️ **`tracking_numbers` è una STRINGA**, malgrado il plurale — più numeri si
   separano con la virgola;
3. **il codice corriere è un nome per esteso** (`BRT Bartolini`), e si risolve
   con l'impianto del tronco, non scritto a mano qui dentro.

E la cosa che nessuna guida dice ma che costa cara: **un esito incerto non è un
successo.** Se la risposta è un 502 o non arriva, la riga NON si segna
comunicata — ma nemmeno si ritenta da sola. Un numero di tracciamento riusato è
un rifiuto, già misurato su Temu.
"""
import json

from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged

from .test_import_ordini import TRASPORTO, unita


@tagged("post_install", "-at_install", "centrivo_kaufland")
class TestSpedizioneKaufland(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        azienda = cls.env.company
        cls.prodotto = cls.env["product.product"].create({
            "name": "Box doccia (prova spedizione)",
            "default_code": "TEST-KFL-SPED-1",
            "barcode": "9990000000110", "list_price": 199.0})
        cls.prodotto2 = cls.env["product.product"].create({
            "name": "Piatto doccia (prova spedizione)",
            "default_code": "TEST-KFL-SPED-2",
            "barcode": "9990000000127", "list_price": 89.0})
        cls.canale = cls.env["centrivo.channel"].create({
            "name": "Kaufland (prova spedizione)",
            "connector_code": "kaufland",
            "company_id": azienda.id,
            "kaufland_client_key": "chiave-finta",
            "kaufland_secret_key": "segreto-finto",
        })
        cls.mercato = cls.env["centrivo.kaufland.market"].create({
            "channel_id": cls.canale.id,
            "storefront": "de",
            "shipping_group_id": "195841",
        })
        # --- L'impianto corrieri del tronco, montato per davvero ----------
        # ⚠️ Non si finge la risoluzione del corriere: e' proprio il pezzo che,
        # sbagliato, manda al cliente il tracking di un altro corriere.
        cls.marca_brt = cls.env.ref("integrations_core.carrier_brand_brt")
        cls.vettore = cls.env["delivery.carrier"].create({
            "name": "BRT (prova spedizione)",
            "product_id": cls.env["product.product"].create({
                "name": "Trasporto BRT (prova)", "type": "service"}).id,
        })
        cls.env["centrivo.carrier.source"].create({
            "source_model": "delivery.carrier",
            "source_res_id": cls.vettore.id,
            "source_display": cls.vettore.display_name,
            "brand_id": cls.marca_brt.id,
            "company_id": azienda.id,
        })

    # ------------------------------------------------------------------
    # Attrezzatura
    # ------------------------------------------------------------------
    def _importa(self, righe):
        """Fa entrare l'ordine, con lo stesso finto Kaufland dell'import."""
        viste = {"n": 0}

        def finto(self_trasporto, metodo, uri, teste, corpo):
            viste["n"] += 1
            dati = righe if viste["n"] == 1 else []
            return 200, json.dumps({
                "data": dati,
                "pagination": {"offset": 0, "limit": 100, "total": len(righe)},
            }), {}

        with patch(TRASPORTO, finto):
            self.canale._get_connector().per_mercato("pull_orders")
        return self.env["centrivo.order.map"].sudo().search([
            ("channel_id", "=", self.canale.id)], limit=1)

    def _spedisci_in_odoo(self, mappa, tracking="BRT-0001", vettore=None):
        """Porta a termine il trasferimento in Odoo, col tracking nativo.

        ⚠️ Si LEGGE `carrier_tracking_ref`, il campo nativo: chi l'abbia
        scritto (ShipTracker, una persona, un altro modulo) non ci riguarda.
        """
        picking = mappa.sale_order_id.picking_ids[:1]
        if not picking:
            return picking
        picking.write({"carrier_id": (vettore or self.vettore).id,
                       "carrier_tracking_ref": tracking})
        for move in picking.move_ids:
            move.quantity = move.product_uom_qty
        picking.picking_type_id.create_backorder = "never"
        picking.button_validate()
        return picking

    def _comunica(self, mappa, risponde=None):
        """Preme il pulsante, raccogliendo le chiamate viste dal finto Kaufland."""
        chiamate = []

        def finto(self_trasporto, metodo, uri, teste, corpo):
            chiamate.append({
                "metodo": metodo, "uri": uri,
                "corpo": json.loads(corpo.decode("utf-8")) if corpo else None,
            })
            if risponde is not None:
                return risponde(len(chiamate))
            return 200, json.dumps({"data": {}}), {}

        with patch(TRASPORTO, finto):
            mappa.action_push_shipment()
        return chiamate

    def _righe(self, mappa):
        return self.env["centrivo.kaufland.order.unit"].sudo().search(
            [("order_map_id", "=", mappa.id)], order="id_order_unit")

    def _ordine_pronto(self, righe_kaufland=None):
        """Un ordine importato, spedito in Odoo, pronto da comunicare."""
        mappa = self._importa(righe_kaufland or [
            unita("314568015275035", "M845EA5", "TEST-KFL-SPED-1")])
        self._spedisci_in_odoo(mappa)
        return mappa

    # ==================================================================
    # 1) La forma della chiamata
    # ==================================================================
    def test_01_la_chiamata_e_un_PATCH_sulla_RIGA(self):
        """⚠️ `/order-units/{id_order_unit}/send`, non `/orders/{id_order}`.

        È la differenza che il piano ha chiamato «la scelta che decide la
        forma»: Kaufland ragiona per riga.
        """
        mappa = self._ordine_pronto()
        chiamate = self._comunica(mappa)
        self.assertEqual(len(chiamate), 1, "Una riga, una chiamata.")
        self.assertEqual(chiamate[0]["metodo"], "PATCH")
        self.assertTrue(
            chiamate[0]["uri"].endswith("/order-units/314568015275035/send"),
            "L'indirizzo deve portare l'id della RIGA: %s" % chiamate[0]["uri"])

    def test_02_il_corpo_porta_il_corriere_e_il_tracking(self):
        mappa = self._ordine_pronto()
        corpo = self._comunica(mappa)[0]["corpo"]
        self.assertEqual(sorted(corpo), ["carrier_code", "tracking_numbers"],
                         "Nel corpo vanno esattamente questi due campi.")
        self.assertEqual(corpo["tracking_numbers"], "BRT-0001")

    def test_03_tracking_numbers_e_una_STRINGA_non_un_elenco(self):
        """⚠️ Malgrado il plurale. Un elenco JSON verrebbe rifiutato."""
        mappa = self._ordine_pronto()
        corpo = self._comunica(mappa)[0]["corpo"]
        self.assertIsInstance(
            corpo["tracking_numbers"], str,
            "La guida dice stringa separata da virgole, non elenco.")

    def test_04_il_codice_corriere_viene_dall_impianto_del_tronco(self):
        """Il nome per esteso che Kaufland si aspetta, risolto vettore→corriere."""
        mappa = self._ordine_pronto()
        corpo = self._comunica(mappa)[0]["corpo"]
        self.assertEqual(corpo["carrier_code"], "BRT Bartolini")

    def test_05_l_eccezione_di_canale_SCAVALCA_la_tabella_del_connettore(self):
        """⚠️ È la via di fuga, e va provata proprio perché i nomi letti sulla
        guida potrebbero essere sbagliati: il giorno che Kaufland rifiuta
        «BRT Bartolini», si corregge dalla maschera, non con un rilascio."""
        self.env["centrivo.carrier.override"].create({
            "channel_id": self.canale.id,
            "brand_id": self.marca_brt.id,
            # ⚠️ Un nome PRESO DALLA LISTA di Kaufland: l'eccezione e' una
            # lista chiusa, non testo libero. E' giusto cosi' — un nome
            # inventato a mano verrebbe rifiutato da loro, e tanto vale
            # scoprirlo qui invece che davanti a un ordine vero.
            "external_code": "DPD",
            "company_id": self.env.company.id,
        })
        mappa = self._ordine_pronto()
        corpo = self._comunica(mappa)[0]["corpo"]
        self.assertEqual(corpo["carrier_code"], "DPD",
                         "L'eccezione deve vincere su «BRT Bartolini».")

    # ==================================================================
    # 2) Per riga davvero
    # ==================================================================
    def test_06_due_righe_fanno_DUE_chiamate(self):
        """⚠️ Un solo ordine in Odoo, ma due comunicazioni a Kaufland."""
        mappa = self._ordine_pronto([
            unita("314568015275035", "M845EA5", "TEST-KFL-SPED-1"),
            unita("314568015272636", "M845EA5", "TEST-KFL-SPED-2", 8900),
        ])
        chiamate = self._comunica(mappa)
        self.assertEqual(len(chiamate), 2)
        indirizzi = sorted(c["uri"].rsplit("/order-units/", 1)[-1]
                           for c in chiamate)
        self.assertEqual(indirizzi, ["314568015272636/send",
                                     "314568015275035/send"])

    # ==================================================================
    # 3) Il registro anti-doppione
    # ==================================================================
    def test_07_dopo_il_successo_la_riga_risulta_comunicata(self):
        mappa = self._ordine_pronto()
        self._comunica(mappa)
        self.assertTrue(self._righe(mappa).spedizione_comunicata)
        self.assertTrue(mappa.shipment_pushed,
                        "Comunicate tutte le righe, l'ordine è a posto.")

    def test_08_premere_due_volte_NON_richiama_Kaufland(self):
        """⚠️ È la difesa che conta: la guida non promette idempotenza, e un
        numero di tracciamento rimandato è un rifiuto (misurato su Temu)."""
        mappa = self._ordine_pronto()
        self._comunica(mappa)
        seconde = self._comunica(mappa)
        self.assertEqual(seconde, [], "Nessuna seconda chiamata.")

    def test_09_una_riga_gia_comunicata_non_si_rimanda_con_le_altre(self):
        """Il caso misto: una riga era passata, l'altra no."""
        mappa = self._ordine_pronto([
            unita("314568015275035", "M845EA5", "TEST-KFL-SPED-1"),
            unita("314568015272636", "M845EA5", "TEST-KFL-SPED-2", 8900),
        ])
        self._righe(mappa)[0].sudo().spedizione_comunicata = True
        chiamate = self._comunica(mappa)
        self.assertEqual(len(chiamate), 1, "Solo la riga che mancava.")

    # ==================================================================
    # 4) ⚠️ L'ESITO INCERTO — la parte che costa
    # ==================================================================
    def test_10_un_502_NON_segna_la_riga_come_comunicata(self):
        """⚠️ 502 vuol dire «non so»: la richiesta può essere arrivata.

        Segnarla comunicata perderebbe la spedizione; segnarla fallita e
        ritentare da soli la duplicherebbe. Si lascia da fare, e lo decide una
        persona.
        """
        mappa = self._ordine_pronto()
        self._comunica(mappa, risponde=lambda n: (502, "Bad Gateway", {}))
        self.assertFalse(self._righe(mappa).spedizione_comunicata)
        self.assertFalse(mappa.shipment_pushed)

    def test_11_una_risposta_persa_NON_segna_la_riga_come_comunicata(self):
        """Stato 0: il trasporto non ha ricevuto risposta. Stessa cosa."""
        mappa = self._ordine_pronto()
        self._comunica(mappa, risponde=lambda n: (0, "", {}))
        self.assertFalse(self._righe(mappa).spedizione_comunicata)

    def test_12_un_rifiuto_certo_lascia_la_riga_da_fare_e_lo_scrive(self):
        """400: colpa del dato. Verdetto certo, ma la riga resta da comunicare."""
        mappa = self._ordine_pronto()
        self._comunica(mappa, risponde=lambda n: (
            400, json.dumps({"message": "carrier_code non valido"}), {}))
        self.assertFalse(self._righe(mappa).spedizione_comunicata)
        registro = self.env["centrivo.job.log"].sudo().search(
            [("channel_id", "=", self.canale.id),
             ("operation", "=", "push_shipment")])
        self.assertTrue(registro, "Il rifiuto dev'essere scritto nel registro.")
        self.assertTrue(
            any("400" in (r.message or "") for r in registro),
            "E il registro deve dire lo stato, o non si capisce di che si tratta.")

    def test_13_se_una_riga_fallisce_le_altre_RESTANO_comunicate(self):
        """⚠️ La prima è passata davvero: perderne la traccia la farebbe
        rimandare al giro dopo, e su Kaufland sarebbe un doppione."""
        mappa = self._ordine_pronto([
            unita("314568015272636", "M845EA5", "TEST-KFL-SPED-2", 8900),
            unita("314568015275035", "M845EA5", "TEST-KFL-SPED-1"),
        ])
        self._comunica(mappa, risponde=lambda n: (
            (200, json.dumps({"data": {}}), {}) if n == 1
            else (500, "Errore interno", {})))
        comunicate = self._righe(mappa).filtered("spedizione_comunicata")
        self.assertEqual(len(comunicate), 1,
                         "Una comunicata, una no: non tutto o niente.")
        self.assertFalse(mappa.shipment_pushed,
                         "E l'ordine non è finito finché manca una riga.")

    def test_14_uno_schianto_su_una_riga_non_porta_via_le_precedenti(self):
        """⚠️ Non è teoria: senza la rete di sicurezza per riga, un'eccezione
        a metà annulla l'intera richiesta — comprese le righe che Kaufland ha
        già accettato. Al giro dopo verrebbero rimandate."""
        mappa = self._ordine_pronto([
            unita("314568015272636", "M845EA5", "TEST-KFL-SPED-2", 8900),
            unita("314568015275035", "M845EA5", "TEST-KFL-SPED-1"),
        ])

        def esplode(n):
            if n == 1:
                return 200, json.dumps({"data": {}}), {}
            raise RuntimeError("il trasporto è saltato per aria")

        self._comunica(mappa, risponde=esplode)
        self.assertEqual(
            len(self._righe(mappa).filtered("spedizione_comunicata")), 1,
            "La riga già accettata resta segnata.")

    # ==================================================================
    # 5) Le precondizioni: niente chiamate al buio
    # ==================================================================
    def test_15_senza_trasferimento_concluso_non_si_chiama_nessuno(self):
        mappa = self._importa([
            unita("314568015275035", "M845EA5", "TEST-KFL-SPED-1")])
        chiamate = self._comunica(mappa)
        self.assertEqual(chiamate, [], "Nessuna chiamata senza spedizione.")
        self.assertFalse(mappa.shipment_pushed)

    def test_16_senza_numero_di_tracciamento_non_si_chiama_nessuno(self):
        """⚠️ Il numero è obbligatorio salvo corriere «Other»: mandarne uno
        vuoto sarebbe un rifiuto, o peggio una spedizione senza tracciamento."""
        mappa = self._importa([
            unita("314568015275035", "M845EA5", "TEST-KFL-SPED-1")])
        self._spedisci_in_odoo(mappa, tracking="")
        self.assertEqual(self._comunica(mappa), [])

    def test_17_vettore_non_collegato_dice_DOVE_andare(self):
        """Due guasti diversi vanno detti in modo diverso, o si cerca nel
        posto sbagliato."""
        altro = self.env["delivery.carrier"].create({
            "name": "Vettore scollegato (prova)",
            "product_id": self.env["product.product"].create({
                "name": "Trasporto scollegato (prova)", "type": "service"}).id,
        })
        mappa = self._importa([
            unita("314568015275035", "M845EA5", "TEST-KFL-SPED-1")])
        self._spedisci_in_odoo(mappa, vettore=altro)
        self.assertEqual(self._comunica(mappa), [])
        registro = self.env["centrivo.job.log"].sudo().search(
            [("channel_id", "=", self.canale.id),
             ("operation", "=", "push_shipment")], limit=1)
        self.assertIn("Vettori", registro.message or "",
                      "Il messaggio deve mandare alla tabella dei vettori.")

    def test_18_corriere_senza_traduzione_dice_l_ALTRA_cosa(self):
        marca = self.env["centrivo.carrier.brand"].create({
            "name": "Corriere di fantasia", "code": "fantasia",
            "tracking_url_template": "https://esempio/{tracking}"})
        altro = self.env["delivery.carrier"].create({
            "name": "Vettore di fantasia (prova)",
            "product_id": self.env["product.product"].create({
                "name": "Trasporto fantasia (prova)", "type": "service"}).id,
        })
        self.env["centrivo.carrier.source"].create({
            "source_model": "delivery.carrier", "source_res_id": altro.id,
            "source_display": altro.display_name, "brand_id": marca.id,
            "company_id": self.env.company.id})
        mappa = self._importa([
            unita("314568015275035", "M845EA5", "TEST-KFL-SPED-1")])
        self._spedisci_in_odoo(mappa, vettore=altro)
        self.assertEqual(self._comunica(mappa), [])
        registro = self.env["centrivo.job.log"].sudo().search(
            [("channel_id", "=", self.canale.id),
             ("operation", "=", "push_shipment")], limit=1)
        self.assertIn("Copertura corrieri", registro.message or "")

    def test_19_un_ordine_in_errore_non_si_comunica(self):
        mappa = self._ordine_pronto()
        mappa.sudo().write({"state": "error"})
        self.assertEqual(self._comunica(mappa), [])
