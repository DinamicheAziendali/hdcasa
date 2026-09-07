# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Il ripescaggio delle schede ferme — le voci 2.2, 2.3 e 2.4 della lista.

⚠️ **Perché queste prove nascono adesso e non allora.** La lista di controllo
della prima installazione dice, alla voce 2.4:

> «è l'unica riga di codice del modulo scritta sulla semantica documentata di
> Odoo senza poterla provare qui — su questa macchina non esiste un sorgente
> Odoo da leggere»

Un Odoo vero adesso c'è: lo stage Community. Quelle voci non sono più «da
spuntare a mano guardando lo schermo», sono misurabili.

**Cosa difendono, e perché è la cosa più seria del modulo.** Il ripescaggio è
**l'unico modo** di far ripartire una scheda ferma. Ce ne sono di due specie e
la seconda è il vicolo cieco più capiente: **tutte** le righe di ogni pacchetto
scaduto — fino a 10.000 per volta — finiscono «senza verdetto con un pacchetto
chiuso», e da lì nessun giro automatico le tocca più.

⚠️ E il rischio che ci porta non è teorico: i nomi delle chiavi del rapporto
vengono dalla documentazione e **non sono mai stati visti sul vero**. Se sono
sbagliati, ogni pacchetto resta illeggibile, scade, e ci finisce dentro tutto
il catalogo.
"""
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.marketplace_cdiscount.models.cdiscount_scheda import (
    ARENATE, IN_ATTESA, ORFANE, SCONOSCIUTO_SCHEDA)
from odoo.addons.marketplace_cdiscount.models.cdiscount_pacchetto import (
    APERTO, RACCOLTO, SCADUTO, TIPO_SCHEDE)


@tagged("post_install", "-at_install", "centrivo_cdiscount")
class TestRipescaggio(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.canale = cls.env["centrivo.channel"].create({
            "name": "Cdiscount (prova ripescaggio)",
            "connector_code": "cdiscount",
            "company_id": cls.env.company.id,
        })
        cls.Scheda = cls.env["cdiscount.scheda"]
        cls.Pacchetto = cls.env["cdiscount.pacchetto"]

    # ------------------------------------------------------------------
    def _pacchetto(self, numero, stato):
        return self.Pacchetto.create({
            "channel_id": self.canale.id, "numero": numero,
            "tipo": TIPO_SCHEDE, "nato_il": "2026-09-01 08:00:00",
            "scade_il": "2026-09-04 08:00:00", "stato": stato,
            "company_id": self.env.company.id})

    def _scheda(self, codice, stato, pacchetto=None):
        return self.Scheda.create({
            "channel_id": self.canale.id, "codice": codice, "stato": stato,
            "pacchetto_id": pacchetto.id if pacchetto else False,
            "company_id": self.env.company.id})

    # ==================================================================
    # Voce 2.3 — il filtro che attraversa una relazione
    # ==================================================================
    def test_01_il_filtro_delle_ARENATE_si_puo_cercare(self):
        """⚠️ È l'unico dominio del modulo che attraversa una relazione
        (`pacchetto_id.stato`). Se la forma fosse sbagliata, il clic darebbe un
        errore tecnico invece di una lista — e il vicolo cieco più capiente
        resterebbe invisibile."""
        chiuso = self._pacchetto("P-CHIUSO-1", SCADUTO)
        arenata = self._scheda("TEST-CDI-ARE-1", SCONOSCIUTO_SCHEDA, chiuso)
        trovate = self.Scheda.search(
            ARENATE + [("channel_id", "=", self.canale.id)])
        self.assertIn(arenata, trovate)

    def test_02_e_NON_prende_le_schede_mai_partite(self):
        """⚠️ Senza la condizione sul pacchetto, il criterio prenderebbe le
        righe appena nate — quelle che aspettano la traduzione francese, che
        all'inizio sono la maggioranza (347 su 604). Il bottone toccherebbe
        tutto il catalogo."""
        mai_partita = self._scheda("TEST-CDI-NUOVA-1", SCONOSCIUTO_SCHEDA)
        trovate = self.Scheda.search(
            ARENATE + [("channel_id", "=", self.canale.id)])
        self.assertNotIn(mai_partita, trovate)

    def test_03_un_pacchetto_ANCORA_APERTO_non_e_arenato(self):
        """Quelle righe un esito lo stanno ancora aspettando, e arriverà."""
        aperto = self._pacchetto("P-APERTO-1", APERTO)
        in_volo = self._scheda("TEST-CDI-VOLO-1", SCONOSCIUTO_SCHEDA, aperto)
        trovate = self.Scheda.search(
            ARENATE + [("channel_id", "=", self.canale.id)])
        self.assertNotIn(in_volo, trovate)

    def test_04_anche_RACCOLTO_e_chiuso_non_solo_scaduto(self):
        """⚠️ La condizione guarda cosa IMPEDISCE il recupero, non come ci si è
        arrivati: un pacchetto già raccolto le cui righe sono rimaste senza
        verdetto è nella stessa identica condizione di uno scaduto."""
        raccolto = self._pacchetto("P-RACCOLTO-1", RACCOLTO)
        arenata = self._scheda("TEST-CDI-ARE-2", SCONOSCIUTO_SCHEDA, raccolto)
        trovate = self.Scheda.search(
            ARENATE + [("channel_id", "=", self.canale.id)])
        self.assertIn(arenata, trovate)

    def test_05_e_gli_stati_senza_verdetto_sono_DUE(self):
        """⚠️ «In attesa» con un pacchetto chiuso è lo stesso vicolo cieco
        spostato di uno stato. Senza questa seconda parola quella riga
        finirebbe fra le «In attesa» vere, dove nessun filtro la distingue e il
        ripescaggio la rifiuterebbe."""
        chiuso = self._pacchetto("P-CHIUSO-2", SCADUTO)
        arenata = self._scheda("TEST-CDI-ARE-3", IN_ATTESA, chiuso)
        trovate = self.Scheda.search(
            ARENATE + [("channel_id", "=", self.canale.id)])
        self.assertIn(arenata, trovate)

    # ==================================================================
    # Voce 2.4 — ⚠️ `filtered_domain` su un percorso puntato, IN MEMORIA
    # ==================================================================
    def test_06_filtered_domain_attraversa_la_relazione_come_search(self):
        """⚠️ **La voce che la lista dichiarava non provabile.**

        Il bottone valuta lo stesso criterio del filtro, ma sui record **già in
        memoria**, con `filtered_domain`. Che quel metodo segua un percorso
        puntato è semantica documentata di Odoo, mai eseguita qui.

        Se divergesse da `search`, ciò che la schermata **mostra** e ciò che il
        bottone **tocca** sarebbero due insiemi diversi — su un catalogo
        pubblico è il modo peggiore possibile di sbagliare.
        """
        chiuso = self._pacchetto("P-CHIUSO-3", SCADUTO)
        aperto = self._pacchetto("P-APERTO-2", APERTO)
        arenata = self._scheda("TEST-CDI-CFR-1", SCONOSCIUTO_SCHEDA, chiuso)
        in_volo = self._scheda("TEST-CDI-CFR-2", SCONOSCIUTO_SCHEDA, aperto)
        mai_partita = self._scheda("TEST-CDI-CFR-3", SCONOSCIUTO_SCHEDA)
        tutte = arenata | in_volo | mai_partita

        in_memoria = tutte.filtered_domain(ARENATE)
        dal_database = self.Scheda.search(
            ARENATE + [("id", "in", tutte.ids)])
        self.assertEqual(
            in_memoria, dal_database,
            "Ciò che il bottone tocca deve essere ciò che il filtro mostra.")
        self.assertEqual(in_memoria, arenata)

    def test_07_lo_stesso_per_le_ORFANE(self):
        orfana = self._scheda("TEST-CDI-ORF-1", IN_ATTESA)
        aperto = self._pacchetto("P-APERTO-3", APERTO)
        attesa_vera = self._scheda("TEST-CDI-ORF-2", IN_ATTESA, aperto)
        tutte = orfana | attesa_vera
        self.assertEqual(tutte.filtered_domain(ORFANE),
                         self.Scheda.search(ORFANE + [("id", "in", tutte.ids)]))
        self.assertEqual(tutte.filtered_domain(ORFANE), orfana)

    # ==================================================================
    # Voce 2.2 — il conto del bottone è quello del filtro
    # ==================================================================
    def test_08_il_ripescaggio_tocca_ESATTAMENTE_le_ferme(self):
        """⚠️ Una selezione fatta col «seleziona tutto» prende anche le righe
        che stanno aspettando un esito che arriverà: rimandarle mentre
        Cdiscount lavora il loro pacchetto è un doppione."""
        chiuso = self._pacchetto("P-CHIUSO-4", SCADUTO)
        aperto = self._pacchetto("P-APERTO-4", APERTO)
        orfana = self._scheda("TEST-CDI-RIP-1", IN_ATTESA)
        arenata = self._scheda("TEST-CDI-RIP-2", SCONOSCIUTO_SCHEDA, chiuso)
        da_lasciare = self._scheda("TEST-CDI-RIP-3", IN_ATTESA, aperto)

        (orfana | arenata | da_lasciare).action_cdiscount_ripesca()

        # L'orfana torna «non lo so» e perde il vincolo che la teneva ferma.
        self.assertEqual(orfana.stato, SCONOSCIUTO_SCHEDA)
        # L'arenata era già nello stato giusto: le si stacca il pacchetto.
        self.assertFalse(arenata.pacchetto_id)
        # ⚠️ E quella che aspetta davvero NON si tocca.
        self.assertEqual(da_lasciare.stato, IN_ATTESA)
        self.assertEqual(da_lasciare.pacchetto_id, aperto)

    def test_09_su_una_ARENATA_il_bottone_non_da_errore(self):
        """Voce 2.4 della lista, nella sua forma operativa: deve rispondere con
        una notifica, non con una pagina d'errore."""
        chiuso = self._pacchetto("P-CHIUSO-5", SCADUTO)
        arenata = self._scheda("TEST-CDI-RIP-4", SCONOSCIUTO_SCHEDA, chiuso)
        esito = arenata.action_cdiscount_ripesca()
        self.assertIsInstance(esito, dict)
        self.assertEqual(esito.get("type"), "ir.actions.client")

    def test_10_su_righe_non_ripescabili_lo_dice_e_non_tocca_niente(self):
        aperto = self._pacchetto("P-APERTO-5", APERTO)
        ferma_no = self._scheda("TEST-CDI-RIP-5", IN_ATTESA, aperto)
        esito = ferma_no.action_cdiscount_ripesca()
        self.assertEqual(ferma_no.stato, IN_ATTESA)
        self.assertEqual(ferma_no.pacchetto_id, aperto)
        self.assertIn("rimettere in gioco",
                      (esito.get("params") or {}).get("message", ""))

    # ==================================================================
    # ⚠️ Il doppione del file dei contenuti — voce 3.1
    # ==================================================================
    def test_11_lo_stesso_codice_due_volte_NON_si_crea_due_volte(self):
        """⚠️ «La corsa che non è mai stata eseguita davvero», dice la lista, ed
        è il caso più probabile di tutti: basta un browser che va in timeout
        dopo che il caricamento è passato, e chi ha caricato riprova.

        Se nascessero doppioni, la scheda partirebbe due volte nello stesso
        pacchetto e **una delle due resterebbe senza verdetto per sempre**, in
        cima al filtro, identica a un problema vero.
        """
        from psycopg2 import IntegrityError

        from odoo.tools import mute_logger
        self._scheda("TEST-CDI-DUP-1", SCONOSCIUTO_SCHEDA)
        with self.assertRaises(IntegrityError), \
                mute_logger("odoo.sql_db"), self.env.cr.savepoint():
            self._scheda("TEST-CDI-DUP-1", SCONOSCIUTO_SCHEDA)
            self.env.flush_all()
