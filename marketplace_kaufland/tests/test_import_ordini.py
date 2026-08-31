# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""L'import degli ordini Kaufland — Consegna 2, perimetro «solo import».

Il finto Kaufland risponde con la **forma misurata sul vero** il 2026-08-31
(`docs/kaufland-ordini-misure.md`), non con una forma inventata: due chiamate,
`/order-units/` che porta tutto, `id_offer` uguale al nostro codice articolo,
importi in **centesimi**, indirizzo con `street` e `house_number` separati.

Le decisioni che queste prove presidiano sono di Angelo (2026-08-31):

- l'ordine nasce **già confermato**;
- **prodotto NON mappato** → l'ordine va in **errore, e si vede**;
- **prodotto senza GIACENZA** → l'ordine **entra lo stesso**, la mancanza si
  guarda in magazzino. ⚠️ Sono due casi diversi: uno è un dato che manca,
  l'altro è merce che manca;
- la **posizione fiscale** viene dalla riga del **mercato** (l'IVA è per Paese).

⚠️ La spedizione NON è in questo perimetro: si proverà su una spedizione vera.
"""
import json

from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged

TRASPORTO = ("odoo.addons.marketplace_kaufland.connectors.kaufland_client."
             "TrasportoRequests.chiama")

INDIRIZZO = {
    "first_name": "Jessica", "last_name": "Salihu", "company_name": None,
    "street": "Bergstr", "house_number": "43", "postcode": "53424",
    "city": "Remagen", "country": "DE", "phone": None,
    "additional_field": None,
}


def unita(id_unit, id_order, codice, prezzo_centesimi=23000):
    """Una riga d'ordine con la forma vera di Kaufland."""
    return {
        "id_order_unit": id_unit,
        "id_order": id_order,
        "id_offer": codice,
        "status": "need_to_be_sent",
        "price": prezzo_centesimi,
        "revenue_gross": 20010,
        "revenue_net": 16338,
        "currency": "EUR",
        "vat": 19,
        "shipping_rate": 0,
        "delivery_time_min": 9,
        "delivery_time_max": 12,
        "delivery_time_expires_iso": "2026-09-16T21:59:59Z",
        "unit_condition": "new",
        "storefront": "de",
        "ts_created_iso": "2026-08-30T17:46:55Z",
        "buyer": {"id_buyer": 42763170,
                  "email": "cakxq1hq-b5b27934ea0e@kaufland-marktplatz.de"},
        "shipping_address": dict(INDIRIZZO),
        "billing_address": dict(INDIRIZZO),
        "product": {"title": "Duschkabine", "eans": ["9990000000011"],
                    "id_product": 575807986, "manufacturer": "Rollplast Pinto"},
    }


@tagged("post_install", "-at_install", "centrivo_kaufland")
class TestImportOrdini(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        azienda = cls.env.company
        # ⚠️ Codici INVENTATI, non quelli veri: sullo stage i prodotti veri
        # esistono gia' e il loro codice a barre e' unico — creare un gemello
        # farebbe fallire il banco nel `setUpClass`, cioe' prima di misurare
        # qualunque cosa. La FORMA della risposta resta quella misurata; i
        # codici no, e non devono esserlo.
        cls.venduto = cls.env["product.product"].create({
            "name": "Box doccia (prova import)",
            "default_code": "TEST-KFL-VENDUTO",
            "barcode": "9990000000011", "list_price": 199.0})
        # ⚠️ Un prodotto che in Odoo NON ha giacenza: dev'entrare lo stesso.
        cls.senza_giacenza = cls.env["product.product"].create({
            "name": "Box doccia senza scorta (prova import)",
            "default_code": "TEST-KFL-SENZA-SCORTA",
            "barcode": "9990000000028", "list_price": 80.0,
            "is_storable": True})
        cls.team = cls.env["crm.team"].create({"name": "Kaufland (prova)"})
        cls.posizione = cls.env["account.fiscal.position"].create({
            "name": "Germania — prova", "company_id": azienda.id})
        cls.canale = cls.env["centrivo.channel"].create({
            "name": "Kaufland (prova ordini)",
            "connector_code": "kaufland",
            "company_id": azienda.id,
            "kaufland_client_key": "chiave-finta",
            "kaufland_secret_key": "segreto-finto",
            "team_id": cls.team.id,
        })
        cls.mercato = cls.env["centrivo.kaufland.market"].create({
            "channel_id": cls.canale.id,
            "storefront": "de",
            "shipping_group_id": "195841",
            "fiscal_position_id": cls.posizione.id,
        })

    # ------------------------------------------------------------------
    def _scarica(self, righe, indirizzi=None):
        """Fa girare lo scarico contro un finto Kaufland.

        `indirizzi`, se passato, raccoglie gli URI chiamati: serve a provare
        COSA abbiamo chiesto, non solo cosa abbiamo fatto della risposta.
        """
        viste = {"n": 0}

        def finto(self_trasporto, metodo, uri, teste, corpo):
            viste["n"] += 1
            if indirizzi is not None:
                indirizzi.append(uri)
            dati = righe if viste["n"] == 1 else []
            return 200, json.dumps({
                "data": dati,
                "pagination": {"offset": 0, "limit": 100, "total": len(righe)},
            }), {}

        with patch(TRASPORTO, finto):
            self.canale._get_connector().per_mercato("pull_orders")

    def _mappa(self, id_order):
        return self.env["centrivo.order.map"].sudo().search([
            ("channel_id", "=", self.canale.id),
            ("external_id", "=", id_order)])

    # ------------------------------------------------------------------
    def test_01_un_ordine_diventa_un_ordine_di_vendita(self):
        """Il caso normale, con i numeri veri del 2026-08-30."""
        self._scarica([unita("314568015275035", "M845EA5", "TEST-KFL-VENDUTO")])
        mappa = self._mappa("M845EA5")
        self.assertEqual(len(mappa), 1, "Dev'esserci UNA mappa per l'ordine.")
        self.assertEqual(mappa.state, "imported")
        ordine = mappa.sale_order_id
        self.assertTrue(ordine, "E dev'esserci l'ordine di vendita.")
        self.assertEqual(len(ordine.order_line), 1)
        self.assertEqual(ordine.order_line.product_id, self.venduto)
        # ⚠️ 23000 sono CENTESIMI: 230,00 €. Letti come euro sarebbero 23.000.
        # ⚠️ E in `price_unit` va il LORDO, il prezzo pagato dal cliente: in HD
        # casa le aliquote sono configurate «IVA inclusa», quindi e' Odoo a
        # scorporare. Precisato da Angelo il 2026-08-31, dopo che avevo
        # sbagliato conclusione: il 264,50 dello stage veniva da un'imposta di
        # prova al 15% NON inclusa, che si sommava. Il difetto era l'imposta,
        # non il prezzo.
        self.assertAlmostEqual(
            ordine.order_line.price_unit, 230.00, places=2,
            msg="In price_unit va il prezzo pagato dal cliente, IVA inclusa.")

    def test_02_l_ordine_nasce_CONFERMATO(self):
        """Decisione di Angelo: confermato, non bozza."""
        self._scarica([unita("314568015275035", "M845EA5", "TEST-KFL-VENDUTO")])
        self.assertEqual(
            self._mappa("M845EA5").sale_order_id.state, "sale",
            "L'ordine dev'essere confermato, non restare in bozza.")

    def test_03_due_righe_dello_stesso_ordine_fanno_UN_ordine(self):
        """⚠️ Kaufland ragiona per RIGA: due `order_unit` con lo stesso
        `id_order` sono UN ordine con due righe, non due ordini."""
        self._scarica([
            unita("314568015275035", "M845EA5", "TEST-KFL-VENDUTO"),
            unita("314568015272636", "M845EA5", "TEST-KFL-SENZA-SCORTA", 8000),
        ])
        mappa = self._mappa("M845EA5")
        self.assertEqual(len(mappa), 1, "Un solo ordine, non due.")
        self.assertEqual(
            len(mappa.sale_order_id.order_line), 2, "Con due righe.")

    def test_04_prodotto_NON_MAPPATO_manda_l_ordine_in_errore(self):
        """⚠️ Decisione di Angelo: errore, e dev'essere VISIBILE.

        Il prodotto non si crea mai da un ordine: è la regola di casa. Ma un
        ordine perso in silenzio è peggio di un ordine rifiutato — la riga di
        mappa in errore è il posto dove si va a guardare.
        """
        self._scarica([unita("999", "SCONOSCIUTO", "CODICE-CHE-NON-ESISTE")])
        mappa = self._mappa("SCONOSCIUTO")
        self.assertEqual(len(mappa), 1, "L'ordine dev'essere REGISTRATO.")
        self.assertEqual(mappa.state, "error")
        self.assertFalse(mappa.sale_order_id, "E nessun ordine creato.")
        self.assertTrue(
            mappa.error_message,
            "Con un messaggio che dica cosa manca: senza, la riga rossa non "
            "aiuta nessuno.")

    def test_05_prodotto_SENZA_GIACENZA_entra_lo_stesso(self):
        """⚠️ L'altro caso, e non va confuso col precedente.

        «Il prodotto non c'è in Odoo» e «la merce non c'è in magazzino» sono
        due cose diverse: la prima è un dato che manca, la seconda è merce che
        manca. La seconda non deve fermare l'ordine — il cliente ha comprato, e
        la mancanza si guarda in magazzino.
        """
        self._scarica([unita("314568015272636", "M999", "TEST-KFL-SENZA-SCORTA", 8000)])
        mappa = self._mappa("M999")
        self.assertEqual(
            mappa.state, "imported",
            "Senza giacenza l'ordine entra LO STESSO.")
        self.assertTrue(mappa.sale_order_id)

    def test_06_due_giri_non_creano_due_ordini(self):
        """L'idempotenza: lo scarico si ripete, l'ordine no."""
        righe = [unita("314568015275035", "M845EA5", "TEST-KFL-VENDUTO")]
        self._scarica(righe)
        self._scarica(righe)
        self.assertEqual(
            len(self._mappa("M845EA5")), 1,
            "Due giri, un ordine solo.")
        self.assertEqual(
            self.env["sale.order"].search_count(
                [("id", "=", self._mappa("M845EA5").sale_order_id.id)]), 1)

    def test_07_l_indirizzo_ricompone_via_e_numero_civico(self):
        """⚠️ `street` e `house_number` arrivano SEPARATI.

        Tenuti separati, il numero civico si perde e il pacco non arriva.
        """
        self._scarica([unita("314568015275035", "M845EA5", "TEST-KFL-VENDUTO")])
        cliente = self._mappa("M845EA5").sale_order_id.partner_shipping_id
        self.assertIn("Bergstr", cliente.street or "")
        self.assertIn(
            "43", cliente.street or "",
            "Il numero civico dev'essere nell'indirizzo, non perso.")
        self.assertEqual((cliente.zip or ""), "53424")
        self.assertEqual((cliente.city or ""), "Remagen")

    def test_08_la_posizione_fiscale_viene_dal_MERCATO(self):
        """⚠️ L'IVA è per Paese: 19% in Germania, 22% in Italia.

        Se la posizione fiscale stesse sul canale, al secondo mercato sarebbe
        già sbagliata.
        """
        self._scarica([unita("314568015275035", "M845EA5", "TEST-KFL-VENDUTO")])
        ordine = self._mappa("M845EA5").sale_order_id
        self.assertEqual(
            ordine.fiscal_position_id, self.posizione,
            "L'ordine tedesco deve nascere con la posizione fiscale del "
            "mercato tedesco.")

    def test_09_il_team_di_vendita_viene_dal_canale(self):
        """Decisione di Angelo: configurabile, e sul canale."""
        self._scarica([unita("314568015275035", "M845EA5", "TEST-KFL-VENDUTO")])
        self.assertEqual(
            self._mappa("M845EA5").sale_order_id.team_id, self.team)

    def test_10_un_ordine_che_ROMPE_non_porta_via_gli_altri(self):
        """⚠️ L'isolamento per ordine, qui come su BricoBravo.

        Il secondo ordine è irrimediabile (prodotto sconosciuto), il terzo no:
        il terzo dev'essere importato lo stesso.
        """
        self._scarica([
            unita("1", "M-A", "TEST-KFL-VENDUTO"),
            unita("2", "M-ROTTO", "CODICE-CHE-NON-ESISTE"),
            unita("3", "M-C", "TEST-KFL-SENZA-SCORTA", 8000),
        ])
        self.assertEqual(self._mappa("M-A").state, "imported")
        self.assertEqual(self._mappa("M-ROTTO").state, "error")
        self.assertEqual(
            self._mappa("M-C").state, "imported",
            "L'ordine DOPO quello guasto dev'essere importato lo stesso.")

    def test_11_il_totale_che_non_torna_finisce_nel_REGISTRO(self):
        """⚠️ La guardia: un ordine che in Odoo non vale quanto il cliente ha pagato.

        Scrivere il prezzo giusto non basta: se l'imposta applicata non è
        configurata «IVA inclusa», si somma a un prezzo che ce l'ha già dentro
        e il totale sale. Se l'aliquota è diversa da quella di Kaufland, il
        totale è un altro ancora. In entrambi i casi **non lo dice nessuno**, e
        si scopre in contabilità mesi dopo.

        Qui la posizione fiscale di prova non applica l'imposta di Kaufland,
        quindi il totale non torna: la differenza DEVE finire nel registro.
        """
        self._scarica([unita("314568015275035", "M845EA5", "TEST-KFL-VENDUTO")])
        mappa = self._mappa("M845EA5")
        self.assertEqual(
            mappa.state, "imported",
            "L'ordine resta importato: esiste, il cliente ha comprato.")
        righe = self.env["centrivo.job.log"].sudo().search([
            ("channel_id", "=", self.canale.id),
            ("operation", "=", "pull_orders"),
            ("result", "=", "error"),
        ])
        self.assertTrue(
            righe, "La differenza fra il totale Odoo e quello Kaufland deve "
                   "lasciare una riga nel registro.")
        self.assertIn("totale", (righe[0].message or "").lower())

    def test_12_il_pulsante_scarica_e_dice_com_e_andata(self):
        """Il bottone che Angelo preme, non solo il metodo che chiama.

        ⚠️ E' quello del TRONCO, in cima alla scheda del canale: su Kaufland
        passa dall'override che gira sui mercati e restituisce la notifica.

        ⚠️ Un metodo che funziona e un bottone che funziona non sono la stessa
        cosa: fra i due c'e' la notifica, ed e' l'unica parte che l'utente
        vede. Se quella esplode, il giro e' andato bene e sembra andato male.
        """
        viste = {"n": 0}

        def finto(self_trasporto, metodo, uri, teste, corpo):
            viste["n"] += 1
            dati = ([unita("314568015275035", "M845EA5", "TEST-KFL-VENDUTO")]
                    if viste["n"] == 1 else [])
            return 200, json.dumps({
                "data": dati,
                "pagination": {"offset": 0, "limit": 100, "total": 1},
            }), {}

        with patch(TRASPORTO, finto):
            # ⚠️ `action_pull_orders`, cioe' il pulsante «Scarica ordini» che
            # sta gia' in cima alla scheda del canale. Un secondo pulsante
            # tutto mio era un doppione — segnalato da Angelo il 2026-08-31.
            azione = self.canale.action_pull_orders()

        parametri = azione.get("params") or {}
        self.assertIn("importati", (parametri.get("message") or "").lower())
        self.assertEqual(self._mappa("M845EA5").state, "imported")

    def test_13_premuto_due_volte_non_duplica(self):
        """Il bottone è ripetibile: è il primo dubbio di chi lo preme."""
        righe = [unita("314568015275035", "M845EA5", "TEST-KFL-VENDUTO")]
        self._scarica(righe)
        self._scarica(righe)
        self.assertEqual(len(self._mappa("M845EA5")), 1)

    # ==================================================================
    # ⚠️ LA TRAPPOLA DEI 15 MINUTI
    # Letta sulla guida il 2026-08-31, non misurata: una riga d'ordine nasce
    # in stato `open` e per 15 minuti Kaufland NASCONDE gli indirizzi, apposta,
    # per impedire di spedire troppo presto.
    # ==================================================================
    def test_20_lo_scarico_chiede_SOLO_le_righe_da_spedire(self):
        """⚠️ Senza il filtro entrerebbero anche le righe in stato `open`, cioè
        senza indirizzo. A mano non capita quasi mai; col cron ogni 15 minuti
        capita — ed è il momento in cui nessuno sta guardando."""
        indirizzi = []
        self._scarica([unita("314568015275035", "M845EA5", "TEST-KFL-VENDUTO")],
                      indirizzi=indirizzi)
        self.assertTrue(indirizzi, "Almeno una chiamata dev'esserci stata.")
        self.assertTrue(
            all("status=need_to_be_sent" in uri for uri in indirizzi),
            "Ogni chiamata deve chiedere lo stato: %s" % indirizzi)

    def test_21_un_cliente_gia_noto_NON_perde_l_indirizzo(self):
        """⚠️ La seconda cintura, e difende una cosa che non si recupera.

        Se una riga arrivasse senza indirizzo (per la finestra dei 15 minuti,
        o per qualunque altro motivo), scriverne i vuoti sul cliente
        CANCELLEREBBE il suo indirizzo buono. Il filtro di stato dovrebbe già
        impedirlo: questa prova esiste perché il prezzo di sbagliarsi è un
        pacco che non arriva, e le cinture si mettono in due.
        """
        posta = "cliente-gia-noto@kaufland-marktplatz.de"
        cliente = self.env["res.partner"].create({
            "name": "Cliente già noto", "email": posta,
            "street": "Via Buona 10", "city": "Salerno", "zip": "84100"})
        riga = unita("314568015275099", "M845EA9", "TEST-KFL-VENDUTO")
        riga["buyer"] = {"id_buyer": 1, "email": posta}
        # La riga arriva SENZA indirizzo, come farebbe una `open`.
        riga["shipping_address"] = {"first_name": "Cliente", "last_name": "già noto"}
        self._scarica([riga])
        self.assertEqual(cliente.street, "Via Buona 10",
                         "L'indirizzo buono non si tocca.")
        self.assertEqual(cliente.city, "Salerno")
        self.assertEqual(cliente.zip, "84100")
