# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""La procedura con cui una persona carica il file dei contenuti.

I titoli e le descrizioni delle schede Cdiscount **non vengono da Odoo**:
vengono da Shopify, che li ha piu' completi e le cui immagini sono gia'
pubbliche. Entrano da un file CSV composto fuori e caricato qui a mano.

⚠️ **GLI SCARTI SI MOSTRANO, NON SI REGISTRANO E BASTA.** E' la regola per cui
questo file esiste. Chi carica il file sta guardando lo schermo in quel
momento: e' l'unico istante in cui puo' correggerlo — ha il foglio di calcolo
aperto, sa da dove viene quella riga, e con il numero di riga davanti la
sistema in trenta secondi. Un elenco finito in una riga di registro che
nessuno apre e' indistinguibile da un silenzio, e un silenzio qui vale una
scheda che non nascera' mai senza che nessuno sappia perche'. Il registro si
scrive lo stesso, ma **in piu'**, mai al posto.

⚠️ **Il vero collo di bottiglia e' il francese**, e questa procedura non lo
risolve: non traduce niente. Dei 604 prodotti del perimetro solo 257 hanno
oggi un nome francese. Cio' che puo' fare — e che nessun altro pezzo del
modulo puo' fare — e' **dire il numero**: quante righe sono entrate, quante
sono state scartate, e quante schede di questo canale restano senza contenuti.
E' il conto che dice ad Angelo quanto lavoro di traduzione manca.

La forma e' quella del wizard di `centrivo_sync_fornitori`
(`models/sync_import_wizard.py`), che fa lo stesso mestiere: un `Binary` che
l'operatore riempie, un bottone, e la finestra che si **riapre** per mostrare
l'esito invece di chiudersi lasciandolo indietro.
"""
import base64
import logging

from odoo import _, fields, models
from odoo.exceptions import UserError

from ..connectors.cdiscount_contenuti import SEPARATORE_IMMAGINI, leggi_csv
# ⚠️ `RIUSCITO` serve a dire una cosa che l'esito non diceva: quali dei testi
# appena scritti NON ripartiranno. Si prende da dove vive lo stato di una
# scheda, non si riscrive qui.
from .cdiscount_scheda import RIUSCITO, SENZA_CONTENUTI

_logger = logging.getLogger(__name__)

# Quanti scarti finiscono davvero sotto gli occhi di chi ha caricato.
#
# ⚠️ Non e' il troncamento silenzioso che il resto del modulo vieta: quanti ne
# restano fuori **si dice**, e si dice anche che sono nel registro. Il tetto
# c'e' perche' un export sbagliato puo' produrre uno scarto per riga, e
# diecimila righe dentro un campo di testo rendono la finestra illeggibile —
# cioe' ottengono lo stesso risultato del non mostrarle. Con un perimetro di
# 604 prodotti questo tetto non si tocca mai: si tocca solo quando il file e'
# sbagliato in blocco, e in quel caso la notizia utile e' «e' sbagliato in
# blocco», non la millesima riga.
MAX_SCARTI_MOSTRATI = 500

# I codici PostgreSQL che NON si inghiottono MAI, nemmeno dentro il savepoint
# che protegge la creazione «se manca».
#
# ⚠️ Qui non si e' «gia' dentro un guasto» come in `_al_riparo`: si e' sul
# percorso normale, quello che gira 604 volte su un caricamento riuscito. Un
# errore di serializzazione (40001) o un deadlock (40P01) NON sono «la riga
# esisteva gia'»: sono la richiesta che va rifatta da capo, e Odoo la rifa'
# da solo — ma solo se l'eccezione gli arriva. Inghiottirli la trasformerebbe
# in uno scarto che accusa una riga innocente, e il caricamento andrebbe
# avanti su una transazione che verra' comunque buttata. E' la stessa regola
# scritta in `integrations_core/connectors/base.py`, docstring di
# `_prendi_il_turno`: si lascia risalire tutto cio' che non e' il guasto
# previsto.
#
# ⚠️ Tutto il RESTO si inghiotte, e non per pigrizia: un caricamento di 604
# righe non deve morire per una riga sola. Ma si inghiotte **con
# `_logger.exception`**, traceback compreso — vedi `_scheda`.
CONCORRENZA = ("40001", "40P01")


class CdiscountContenutiWizard(models.TransientModel):
    _name = "cdiscount.contenuti.wizard"
    _description = "Cdiscount — carica il file dei contenuti"

    channel_id = fields.Many2one(
        "centrivo.channel", string="Canale", required=True,
        ondelete="cascade",
        # ⚠️ Il dominio non e' cosmetico: senza, i testi francesi di Cdiscount
        # si potrebbero caricare su un canale Kaufland, che creerebbe righe
        # `cdiscount.scheda` legate a un canale che non parlera' mai con
        # Cdiscount — e nessuno se ne accorgerebbe, perche' il caricamento
        # riuscirebbe.
        domain=[("connector_code", "=", "cdiscount")],
        help="Il canale Cdiscount su cui scrivere i contenuti.")

    file_contenuti = fields.Binary(
        string="File dei contenuti (CSV)", attachment=False,
        help="Il CSV con le colonne codice, titolo, descrizione, immagini, "
             "separate dal punto e virgola. Va salvato in UTF-8.")
    nome_file = fields.Char(string="Nome file")

    # ⚠️ QUESTO CAMPO E' IL COMPITO. E' dove finiscono gli scarti, ed e' il
    # motivo per cui il bottone riapre la finestra invece di chiuderla.
    esito = fields.Text(string="Esito del caricamento", readonly=True)
    esito_mostrato = fields.Boolean(default=False)

    # ------------------------------------------------------------------
    def _righe_del_file(self):
        """`(righe, scarti)` dal file caricato, o una UserError leggibile.

        ⚠️ **Si passano i BYTE, non una stringa.** Un `fields.Binary` di Odoo
        rende base64; decodificato, resta binario. La decodifica del TESTO la
        fa `leggi_csv`, che usa `utf-8-sig` (si mangia il segno che Excel
        mette in testa) e che **su un file non-UTF-8 si ferma** invece di
        leggerlo alla meno peggio. Su un file che esiste per il francese e'
        esattamente il verso giusto: un accento letto male diventerebbe un
        punto interrogativo su un catalogo pubblico, e nessun controllo a
        valle lo intercetterebbe.

        ⚠️ Si cattura **solo `ValueError`**, e non e' una scommessa: e' il
        contratto dichiarato di `leggi_csv`, provato su 60.008 ingressi di
        spazzatura. Il suo messaggio contiene gia' il perche' e, quando c'e',
        il numero di riga: si mostra **tale e quale**, perche' e' azionabile
        cosi' com'e'. Riscriverlo lo peggiorerebbe.

        ⚠️ E c'e' un caso TUTTO-O-NIENTE: se il file ha una virgoletta aperta
        e mai chiusa, `leggi_csv` solleva e **non carica niente** — nemmeno le
        righe prima del guasto. E' voluto: i prodotti inghiottiti da quella
        virgoletta non sono nemmeno elencabili, e mezzo catalogo aggiornato
        credendo di averlo aggiornato tutto e' peggio di zero.
        """
        self.ensure_one()
        if not self.file_contenuti:
            raise UserError(_("Carica prima il file CSV dei contenuti."))
        try:
            contenuto = base64.b64decode(self.file_contenuti)
        except Exception as errore:  # noqa: BLE001
            raise UserError(
                _("Il file non si e' potuto decodificare: %s") % errore)
        try:
            return leggi_csv(contenuto)
        except ValueError as errore:
            # Il messaggio nomina gia' la causa e la riga: si mostra intero.
            raise UserError(_("Il file non si e' potuto leggere.\n\n%s")
                            % errore)

    # ------------------------------------------------------------------
    def _prodotto(self, codice):
        """`(prodotto, avvertenza)` per un codice: il riferimento interno.

        Il `codice` del file e' il nostro SKU, cioe' il `default_code` della
        variante — la stessa chiave con cui Kaufland riaggancia le sue offerte
        (`marketplace_kaufland/connectors/kaufland.py`).

        ⚠️ **LA RICERCA E' LIMITATA ALL'AZIENDA DEL CANALE**, e non e'
        pignoleria multi-azienda: si cerca in `sudo()`, e in `sudo()` si
        vedono anche i prodotti delle ALTRE aziende. Con due aziende e lo
        stesso riferimento interno su prodotti diversi — che e' normale,
        perche' il riferimento interno e' unico per azienda e non in assoluto
        — succederebbe una di due cose, tutte e due brutte: il collegamento
        finisce sul prodotto dell'azienda sbagliata e su Cdiscount parte il
        **GTIN di un altro prodotto**, oppure scatta l'avvertenza «PIU'
        prodotti» su un doppione che non e' tale. Stesso filtro, e per lo
        stesso motivo, di `kaufland.py` (`("company_id", "in", [False,
        azienda.id])`): il `False` tiene dentro i prodotti condivisi, che non
        appartengono a nessuna azienda in particolare.
        `centrivo.channel.company_id` e' `required`, quindi l'azienda c'e'
        sempre.

        ⚠️ **Un codice senza prodotto NON e' uno scarto**: i testi si scrivono
        lo stesso. La riga resta utile — e' la traccia che quel codice esiste
        nel file dei contenuti — e l'invio la rifiutera' da solo, perche' il
        GTIN e la marca li prende dal prodotto. Ma si **dice**, subito, nella
        stessa finestra: e' l'unico momento in cui chi ha in mano il file puo'
        accorgersi di aver scritto un riferimento che in Odoo non esiste.

        ⚠️ **Due prodotti con lo stesso riferimento interno non si scelgono a
        caso.** Odoo non impedisce il doppione, e prenderne uno a sorte
        significherebbe mandare su Cdiscount il GTIN dell'altro: la scheda
        nascerebbe, con il codice a barre di un prodotto diverso. Si lascia
        vuoto e si dice quale codice guardare.

        ⚠️ **UN PRODOTTO ARCHIVIATO NON E' UN PRODOTTO CHE NON ESISTE**, e i
        due casi si dicono diversi. Escluderlo dalla SCELTA e' giusto — un
        archiviato non si vende, ed e' la stessa scelta di Kaufland — ma
        chiamarlo «nessun prodotto con questo riferimento interno» manda a
        cercare un guasto inesistente: chi legge va a controllare l'export di
        Shopify, dove il codice e' scritto giusto. E non e' un'ipotesi di
        scuola: **Odoo distrugge e ricrea le varianti quando si toccano le
        righe attributo di un template** (lo stesso motivo per cui
        `cdiscount.scheda.product_id` e' `ondelete="set null"`), e il giro
        dell'archiviazione e' l'altro modo con cui una variante sparisce
        dall'elenco senza che nessuno l'abbia cancellata. Il secondo `search`
        con `active_test=False` serve SOLO a scegliere il messaggio giusto, mai
        a scegliere il prodotto — la stessa forma di
        `centrivo_sync_fornitori/models/sync_channel.py`.
        """
        Prodotto = self.env["product.product"].sudo()
        azienda = self.channel_id.company_id
        dominio = [("default_code", "=", codice),
                   ("company_id", "in", [False, azienda.id])]
        trovati = Prodotto.search(dominio, limit=2)
        if len(trovati) > 1:
            return Prodotto.browse(), _(
                "codice «%(codice)s»: nell'azienda «%(azienda)s» ci sono PIU' "
                "prodotti con questo riferimento interno. Non se ne sceglie "
                "uno a caso — la scheda partirebbe con il codice a barre di "
                "un altro prodotto. Va corretto il doppione in Odoo."
             ) % {"codice": codice, "azienda": azienda.display_name}
        if trovati:
            return trovati, ""
        # Niente fra gli attivi. Prima di dire «non esiste», si guarda se e'
        # solo archiviato: sono due notizie diverse e mandano a guardare in
        # due posti diversi.
        archiviato = Prodotto.with_context(active_test=False).search(
            dominio, limit=1)
        if archiviato:
            return Prodotto.browse(), _(
                "codice «%(codice)s»: il prodotto in Odoo c'e' ma e' "
                "ARCHIVIATO (%(nome)s). Il riferimento nel file e' giusto: e' "
                "il prodotto che va riattivato, oppure la riga va tolta dal "
                "file. I testi sono stati scritti lo stesso, ma la scheda non "
                "partira'."
             ) % {"codice": codice, "nome": archiviato.display_name}
        return Prodotto.browse(), _(
            "codice «%(codice)s»: nessun prodotto nell'azienda «%(azienda)s» "
            "ha questo riferimento interno, nemmeno fra gli archiviati. I "
            "testi sono stati scritti lo stesso, ma la scheda non potra' "
            "partire: il GTIN e la marca si prendono dal prodotto."
        ) % {"codice": codice, "azienda": azienda.display_name}

    # ------------------------------------------------------------------
    def _scheda(self, codice):
        """La riga `cdiscount.scheda` di questo codice: trovata, o creata.

        ⚠️ **`unique(channel_id, codice)` e un `create` in un ciclo sono un
        `IntegrityError` che aspetta.** Non serve la malizia: bastano due
        caricamenti dello stesso file a pochi secondi l'uno dall'altro, o
        **uno solo** ripetuto da chi ha visto il browser andare in timeout
        DOPO che la transazione era gia' passata. Il `search` non trova nulla
        in tutte e due le transazioni, e tutte e due creano.

        ⚠️ **E CATTURARE L'ERRORE IN PYTHON NON BASTA — e' la lezione piu'
        cara di questo impianto.** Un vincolo violato lascia la transazione
        PostgreSQL in stato ABORTITO: da li' in poi ogni istruzione fallisce,
        e il commit finale della richiesta diventa un **ROLLBACK silenzioso**.
        Senza savepoint, un `except` attorno al `create` non salverebbe
        niente: si vedrebbe la finestra dell'esito annunciare 257 righe
        caricate, e sul database non ce ne sarebbe nessuna. E' lo stesso
        motivo per cui `integrations_core/connectors/base.py` avvolge ogni
        scrittura di servizio in `_al_riparo`.

        ⚠️ **IL `flush_all()` PRIMA DEL SAVEPOINT E' IL PEZZO PORTANTE DI
        TUTTO IL METODO**, e la ragione e' piu' dura di «cosi' le scritture
        partono prima». Quando il savepoint fallisce, il suo `ROLLBACK TO
        SAVEPOINT` **butta via l'intero stato ORM della transazione**, non
        soltanto il `create` che e' esploso: le scritture ancora in sospeso
        nella cache spariscono tutte insieme. Senza questa riga, un doppione
        alla riga 300 di 604 si porterebbe via **le 299 scritture
        precedenti** — e la finestra direbbe «299 contenuti scritti» con
        ZERO righe in banca dati. E' la stessa bugia del ROLLBACK silenzioso,
        raggiunta per un'altra strada. Scaricando prima, quelle 299 sono gia'
        arrivate al database FUORI dal savepoint, e il rollback non le vede.

        Quello DENTRO serve al verso opposto: l'`INSERT` deve essere davvero
        emesso prima che il savepoint si chiuda, altrimenti l'errore
        esploderebbe piu' tardi, fuori dalla protezione.
        """
        self.ensure_one()
        Scheda = self.env["cdiscount.scheda"].sudo()
        dominio = [("channel_id", "=", self.channel_id.id),
                   ("codice", "=", codice)]
        esistente = Scheda.search(dominio, limit=1)
        if esistente:
            return esistente, False

        self.env.flush_all()
        try:
            with self.env.cr.savepoint():
                nuova = Scheda.create({
                    "channel_id": self.channel_id.id,
                    "codice": codice,
                })
                self.env.flush_all()
        except Exception as errore:  # noqa: BLE001
            # ⚠️ `exception` E NON `info`: IL REGISTRO E' PROMESSO A VOCE.
            # Se qui sotto la ricerca non ritrova niente, chi ha caricato il
            # file legge uno scarto che dice «guarda il registro del server:
            # e' l'unico posto dove c'e' il motivo». Se in quel registro c'e'
            # una riga sola che dice «l'ha creata un altro giro», il motivo
            # non e' da nessuna parte — e per giunta quella riga dice il
            # falso, perche' e' scritta PRIMA di sapere com'e' andata la
            # ricerca. `_logger.exception` scrive il traceback: e' quello che
            # fanno tutti e due i gemelli di casa
            # (`integrations_core/connectors/base.py`, `_al_riparo`, e
            # `marketplace_kaufland/connectors/kaufland.py`).
            _logger.exception(
                "Cdiscount: la scheda «%s» del canale %s non si e' potuta "
                "creare.", codice, self.channel_id.id)
            # ⚠️ La concorrenza NON si inghiotte: risale, e Odoo rifa' la
            # richiesta da capo. Vedi `CONCORRENZA` in testa al file.
            if getattr(errore, "pgcode", None) in CONCORRENZA:
                raise
            # ⚠️ Si svuota la cache dell'ORM prima di rileggere. Il perche'
            # esatto NON e' verificato: nella lettura piu' probabile il
            # rollback del savepoint chiama gia' `cr.clear()` e fa piu' di
            # questa riga (svuota anche le ricomputazioni pendenti, che
            # `invalidate_all(flush=False)` non tocca), e allora la riga e'
            # ridondante; nella lettura opposta il savepoint rimette in piedi
            # la transazione ma NON la memoria di Odoo, e dentro resta una
            # riga con un `id` che in banca dati non esiste piu'. Le due
            # letture portano allo stesso comportamento, e su questa macchina
            # non c'e' un sorgente Odoo per decidere quale sia giusta: la riga
            # resta perche' nel dubbio e' innocua, ma **non si copi altrove
            # come se il meccanismo fosse accertato**. `flush=False` in ogni
            # caso: scaricare la scrittura appena annullata la farebbe
            # esplodere una seconda volta, stavolta fuori dal savepoint.
            self.env.invalidate_all(flush=False)
            ritrovata = Scheda.search(dominio, limit=1)
            if ritrovata:
                # Solo ADESSO lo si puo' dire: l'ha creata un altro giro
                # mentre noi guardavamo altrove, e la si riusa.
                _logger.info(
                    "Cdiscount: la scheda «%s» del canale %s esisteva gia' "
                    "(creata da un altro giro), la si riusa.",
                    codice, self.channel_id.id)
            return ritrovata, False
        return nuova, True

    # ------------------------------------------------------------------
    def action_carica(self):
        """Legge il file, scrive i contenuti, e MOSTRA cos'e' rimasto fuori."""
        self.ensure_one()
        righe, scarti = self._righe_del_file()

        create = 0
        aggiornate = 0
        # ⚠️ QUANTE DI QUESTE RIGHE SONO GIA' PUBBLICATE SU CDISCOUNT.
        # I contenuti si scrivono su QUALUNQUE riga, anche su una `riuscito`,
        # e per la Consegna 1 il comportamento e' giusto: il testo va
        # conservato, e la Consegna che sapra' AGGIORNARE una scheda lo
        # trovera' pronto. Ma `_candidate()` esclude `riuscito`, quindi quei
        # testi non ripartiranno — e senza questo conto la finestra diceva
        # «1 aggiornata» e su Cdiscount non cambiava niente, per sempre e
        # senza una riga rossa da nessuna parte. Il comportamento resta,
        # l'esito smette di mentire per omissione.
        pubblicate = 0
        avvertenze = []
        adesso = fields.Datetime.now()
        for riga in righe:
            codice = riga["codice"]
            scheda, e_nuova = self._scheda(codice)
            if not scheda:
                # Il `create` e' fallito e nemmeno la ricerca l'ha ritrovata:
                # non e' il doppione, e' qualcos'altro. Si dice, non si
                # inghiotte.
                scarti.append(_(
                    "codice «%s»: la riga della scheda non si e' potuta "
                    "creare e non esisteva gia'. Guarda il registro del "
                    "server: e' l'unico posto dove c'e' il motivo.") % codice)
                continue

            prodotto, avvertenza = self._prodotto(codice)
            if avvertenza:
                avvertenze.append(avvertenza)

            valori = {
                "titolo": riga["titolo"],
                "descrizione": riga["descrizione"],
                # ⚠️ Si ricompone con LO STESSO separatore con cui il file
                # l'aveva spezzata, e non si toglie nessun pezzo vuoto: un
                # buco fa scivolare avanti le immagini seguenti, e il prodotto
                # va in vetrina con la copertina sbagliata.
                "immagini": SEPARATORE_IMMAGINI.join(riga["immagini"]),
                "contenuti_il": adesso,
            }
            # ⚠️ Il prodotto si scrive solo se lo abbiamo trovato: un
            # `product_id = False` cancellerebbe un collegamento buono messo
            # da un giro precedente, per colpa di un riferimento interno
            # cambiato in Odoo dopo il primo caricamento.
            if prodotto:
                valori["product_id"] = prodotto.id
            # ⚠️ Si legge PRIMA della `write`: la scrittura non tocca lo
            # stato, ma leggerlo dopo legherebbe questo conto a quel fatto.
            if scheda.stato == RIUSCITO:
                pubblicate += 1
            scheda.write(valori)
            if e_nuova:
                create += 1
            else:
                aggiornate += 1

        self.esito = self._componi_esito(righe, scarti, avvertenze, create,
                                         aggiornate, pubblicate)
        self.esito_mostrato = True
        # Il registro **in piu'**, mai al posto della finestra.
        _logger.info(
            "Cdiscount: contenuti caricati sul canale %s — %d righe lette, "
            "%d create, %d aggiornate (di cui %d gia' pubblicate), %d "
            "scarti, %d avvertenze.",
            self.channel_id.id, len(righe), create, aggiornate, pubblicate,
            len(scarti), len(avvertenze))
        # ⚠️ SCARTI **E** AVVERTENZE, tutte e due le specie e senza tetto. Il
        # messaggio del tetto rimanda al registro («e altre %d, nel registro
        # del server»), e un rimando a un registro che non le contiene e' una
        # bugia con la faccia di un aiuto: chi va a cercarle non le trova e
        # conclude che il registro e' rotto. Ed e' lo scenario piu' probabile
        # di tutti — basta un export da Shopify fatto con lo SKU Shopify
        # invece del riferimento interno Odoo per avere 604 avvertenze in un
        # colpo, 500 mostrate e 104 da cercare altrove.
        for uno in scarti:
            _logger.warning("Cdiscount, scarto nel file dei contenuti: %s",
                            uno)
        for uno in avvertenze:
            _logger.warning(
                "Cdiscount, avvertenza sul file dei contenuti: %s", uno)
        return self._riapri()

    # ------------------------------------------------------------------
    def _componi_esito(self, righe, scarti, avvertenze, create, aggiornate,
                       pubblicate=0):
        """Il testo che chi ha caricato legge, scarti compresi."""
        self.ensure_one()
        Scheda = self.env["cdiscount.scheda"].sudo()
        del_canale = [("channel_id", "=", self.channel_id.id)]
        in_tutto = Scheda.search_count(del_canale)
        senza = Scheda.search_count(del_canale + SENZA_CONTENUTI)

        pezzi = [
            _("Canale: %s") % self.channel_id.display_name,
            _("File: %s") % (self.nome_file or _("(senza nome)")),
            "",
            # ⚠️ TRE NUMERI DISTINTI, e non se ne somma nessuno in silenzio.
            # «Lette» sono le righe che `leggi_csv` ha accettato; «scritti»
            # sono quelle che hanno davvero raggiunto una scheda; «scartate»
            # tiene insieme le due specie — le righe che il file non ha
            # saputo dare e quelle che Odoo non ha saputo scrivere — perche'
            # per chi corregge il file sono la stessa cosa: righe di cui non
            # e' entrato niente, ognuna con scritto sotto il suo perche'.
            # Dire un solo totale «entrate/uscite» lo farebbe tornare a forza
            # nascondendo proprio la specie piu' rara, che e' la piu' grave.
            _("Righe lette dal file: %d") % len(righe),
            # ⚠️ Il conto delle gia' pubblicate si scrive SEMPRE, anche a
            # zero: e' il numero che QUALIFICA «aggiornate», e un
            # qualificatore che compare solo qualche volta fa leggere il
            # numero come se non ne avesse mai avuto bisogno.
            _("Contenuti scritti: %(entrate)d — %(create)d schede create, "
              "%(aggiornate)d aggiornate, di cui %(pubblicate)d già "
              "pubblicate su Cdiscount.")
            % {"entrate": create + aggiornate, "create": create,
               "aggiornate": aggiornate, "pubblicate": pubblicate},
            _("Righe scartate: %d (nessun contenuto scritto per queste)")
            % len(scarti),
        ]

        # ⚠️ E QUANDO CE NE SONO, SI DICE COSA VUOL DIRE. Il comportamento e'
        # corretto per questa Consegna — i testi si conservano —, e' l'esito
        # che mentiva per omissione: chi rilegge il francese, corregge un
        # titolo e ricarica il file leggeva «1 aggiornata» e su Cdiscount non
        # cambiava niente, per sempre, senza una riga rossa da nessuna parte.
        if pubblicate:
            pezzi.append("")
            pezzi.append(_(
                "⚠️ %(quante)d di queste schede sono GIÀ PUBBLICATE su "
                "Cdiscount (stato «Riuscita»): i testi appena scritti "
                "restano in Odoo ma NON ripartiranno. Questa consegna sa "
                "CREARE una scheda su Cdiscount, non aggiornarne una che "
                "esiste già: là fuori resta il testo vecchio. Se una "
                "correzione deve arrivare al cliente, per ora va fatta a "
                "mano sul portale Cdiscount.") % {"quante": pubblicate})

        if scarti:
            pezzi.append("")
            pezzi.append(_("SCARTI — ogni riga qui sotto NON e' entrata:"))
            for uno in scarti[:MAX_SCARTI_MOSTRATI]:
                pezzi.append("  - %s" % uno)
            rimasti = len(scarti) - MAX_SCARTI_MOSTRATI
            if rimasti > 0:
                pezzi.append(_(
                    "  ... e altri %d scarti, non mostrati per non rendere "
                    "illeggibile questa finestra. Ci sono tutti nel registro "
                    "del server. Con cosi' tanti scarti il file e' sbagliato "
                    "in blocco: conviene rifare l'export invece di correggere "
                    "riga per riga.") % rimasti)

        if avvertenze:
            pezzi.append("")
            pezzi.append(_(
                "ATTENZIONE — questi contenuti sono stati scritti, ma le loro "
                "schede non potranno partire cosi' come sono:"))
            for uno in avvertenze[:MAX_SCARTI_MOSTRATI]:
                pezzi.append("  - %s" % uno)
            rimaste = len(avvertenze) - MAX_SCARTI_MOSTRATI
            if rimaste > 0:
                pezzi.append(_("  ... e altre %d, nel registro del server.")
                             % rimaste)

        # ⚠️ IL CONTO DEL FRANCESE. `leggi_csv` non lo puo' fare — non sa
        # quanti prodotti dovessero esserci — e nessun altro pezzo lo fa.
        pezzi.append("")
        pezzi.append(_(
            "Schede di questo canale: %(tutte)d in tutto, di cui "
            "%(senza)d ancora SENZA contenuti (manca il titolo, la "
            "descrizione o le immagini). Sono le schede che aspettano una "
            "traduzione in francese: finche' i tre campi non ci sono tutti, "
            "la scheda non si compone e l'invio la rifiuta.")
            % {"tutte": in_tutto, "senza": senza})
        # ⚠️ Il conto qui sopra guarda le RIGHE CHE ESISTONO, e va detto: un
        # prodotto mai comparso in nessun file non ha nessuna riga, quindi non
        # e' fra i «senza contenuti» — e uno zero letto senza questa frase
        # sembrerebbe «finito», con meta' del perimetro ancora da tradurre.
        pezzi.append(_(
            "⚠️ Qui si contano solo le schede che esistono. Un prodotto del "
            "perimetro mai comparso in nessun file non ha ancora nessuna "
            "riga, e in questo conto non c'e': se «in tutto» e' meno dei "
            "prodotti che vuoi vendere su Cdiscount, la differenza e' "
            "lavoro che manca anche lei."))
        return "\n".join(pezzi)

    # ------------------------------------------------------------------
    def _riapri(self):
        """Riapre la finestra sullo stesso record, per far LEGGERE l'esito.

        ⚠️ Non si chiude e non si mostra una notifica: una notifica sparisce
        da sola dopo qualche secondo e non si puo' rileggere, e gli scarti
        vanno guardati uno per uno con il foglio di calcolo di fianco. E'
        la stessa forma del wizard di `centrivo_sync_fornitori`.
        """
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Carica il file dei contenuti"),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }
