# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Lo stato di UNA scheda prodotto su Cdiscount.

Una riga per prodotto e per canale: cosa e' partito, in quale pacchetto, e
cosa Cdiscount ne ha detto.

⚠️ QUESTO MODELLO E' IN SOLA LETTURA PER GLI UTENTI NORMALI
(`security/ir.model.access.csv`: `base.group_user` ha 1,0,0,0), e non e'
prudenza generica. Lo `stato` di una riga e' cio' che decide se la scheda
verra' rimandata: `in_attesa` la fa ripescare dal raccoglitore, `riuscito` la
toglie dai giri per sempre. Una persona che «pulisce» una riga rossa
portandola a `riuscito` fa sparire dal catalogo una scheda che non e' mai
nata, in silenzio e senza lasciare traccia.

⚠️ **CHI SCRIVE QUI, E COME.** Tutto cio' che gira senza nessuno davanti — il
connettore, il raccoglitore, la procedura dei contenuti — scrive in `sudo()`:
il cron gira come root, ma un bottone no, e chiudere i permessi non deve
rompere nessun giro automatico. **C'e' UNA eccezione, ed e' voluta**:
`action_cdiscount_ripesca` scrive sulle righe che gli arrivano dalla
selezione, con l'ambiente di chi ha cliccato (un `ir.actions.server` passa i
`records` cosi' come sono). Li' la rete non e' `sudo()` ma la GUARDIA
`base.group_system` in testa al metodo — l'unico gruppo a cui il CSV concede
la scrittura. Le due cose non sono intercambiabili, e questa riga esiste
perche' non lo sembrino: chi aggiungesse una scrittura in quel metodo
credendo di stare in `sudo()` la vedrebbe fallire su un utente normale, che e'
il verso giusto in cui accorgersene — ma solo perche' quel metodo si e'
gia' fermato prima.
"""
import logging

from odoo import _, fields, models
from odoo.exceptions import AccessError

# ⚠️ DUE verdetti si prendono da dove nascono, e SOLO due. `RIUSCITO` e
# `RIFIUTATO` sono cio' che il raccoglitore (Compito 10) scrivera' su questo
# campo leggendolo da `cdiscount_rapporto.riconcilia`: due copie della stessa
# stringa che divergono per una lettera farebbero fallire la scrittura sulla
# Selection — oppure, peggio, passerebbero e lascerebbero un valore che
# nessun filtro intercetta.
from ..connectors.cdiscount_rapporto import RIFIUTATO, RIUSCITO

# ⚠️ Il separatore con cui gli indirizzi delle immagini stanno in UN SOLO
# campo si prende da dove nascono — la colonna «immagini» del file dei
# contenuti — e non si riscrive qui. Il campo `immagini` conserva ESATTAMENTE
# la cella del file: due copie di questa virgola che divergessero
# spezzerebbero gli indirizzi in punti diversi in scrittura e in lettura, e la
# scheda partirebbe con mezzo indirizzo come copertina.
from ..connectors.cdiscount_contenuti import SEPARATORE_IMMAGINI

# ⚠️ Lo stato «aperto» di un PACCHETTO si prende da dove vive. Serve a
# `ARENATE`, che distingue «il pacchetto sta ancora lavorando» da «il
# pacchetto e' chiuso e non dira' piu' niente»: due copie della parola che
# divergessero non farebbero rumore da nessuna parte — il dominio
# semplicemente non troverebbe piu' niente, e il gesto di recupero sparirebbe
# in silenzio proprio come le righe che deve recuperare.
from .cdiscount_pacchetto import APERTO

# ⚠️ GLI ALTRI DUE STATI SONO NOSTRI E SI SCRIVONO QUI. Non si importano da
# `cdiscount_rapporto` NEMMENO quando la parola coincide — ed e' il punto in
# cui il difetto n.2 del brief rientrava dalla finestra.
#
# In `cdiscount_rapporto` convivono DUE vocabolari distinti, dichiarati a
# commento: lo stato del RAPPORTO (righe 56-58, «sconosciuto» compreso) e
# l'esito di un SINGOLO prodotto (righe 60-62, `RIUSCITO`/`RIFIUTATO`). Il
# suo «sconosciuto» appartiene al PRIMO vocabolario: compare solo come primo
# elemento della tupla resa da `leggi_rapporto` (righe 330, 341, 349, 353),
# mai come verdetto di un prodotto. Che valga la stessa parola del nostro e'
# una coincidenza, non un legame — e infatti su una scheda quel valore non lo
# scrive il raccoglitore ma la scadenza (Compito 11), da tutt'altro punto.
#
# ⚠️ Il danno se lo si importasse: il giorno in cui qualcuno vuole
# distinguere «rapporto illeggibile» da «scheda senza verdetto» e rinomina
# quella costante, cambierebbe in silenzio LA CHIAVE DELLA SELECTION E IL
# DEFAULT di questo campo. E' una costante Python interna — nessuna colonna,
# nessuna migrazione, niente che assomigli a un cambio di schema — e tutte le
# righe gia' scritte a «sconosciuto» diventerebbero un valore fuori
# Selection: si mostrerebbero vuote e sparirebbero dal filtro «Senza
# verdetto». Cioe' esattamente il quinto stato muto contro cui il default
# esiste.
#
# «In attesa» non ha nemmeno un gemello: e' una cosa che diciamo NOI di una
# scheda partita, e nei rapporti di Cdiscount non compare mai.
IN_ATTESA = "in_attesa"
SCONOSCIUTO_SCHEDA = "sconosciuto"

_logger = logging.getLogger(__name__)

STATI_SCHEDA = [
    (IN_ATTESA, "In attesa dell'esito"),
    (RIUSCITO, "Riuscita — la scheda c'e'"),
    (RIFIUTATO, "Rifiutata — Cdiscount ha detto perche'"),
    (SCONOSCIUTO_SCHEDA, "Non lo so — nessun verdetto"),
]


# ⚠️ «SENZA CONTENUTI» ESISTE IN TRE COPIE, E VANNO CONTATE ONESTAMENTE.
# Questa costante ne e' UNA: la usano la procedura del file dei contenuti
# (Compito 8), che con questo dominio conta quante schede aspettano ancora una
# traduzione — e' IL numero che dice quanto lavoro manca — e il filtro della
# pagina (Compito 12), che mostra le stesse righe. Le altre due sono
# `CON_CONTENUTI` qui sotto, che e' la sua negazione RISCRITTA A MANO e non
# derivata, e la copia testuale del dominio dentro
# `views/cdiscount_scheda_views.xml`, perche' una vista non puo' importare una
# costante Python.
#
# ⚠️ Percio' cio' che tiene insieme le tre non e' «c'e' una definizione sola»
# — non e' vero — ma DUE CONTROLLI, e vanno tenuti vivi:
#   - `tools/test_cdiscount_manifest.py` (controllo 7) confronta il dominio
#     dell'XML con questa costante, foglia per foglia;
#   - `tools/test_cdiscount_invio.py` (sezione 9e) prova che `CON_CONTENUTI` e
#     `SENZA_CONTENUTI` sono l'uno il contrario esatto dell'altro su tutte e
#     otto le combinazioni dei tre campi.
# Al primo campo aggiunto a una sola delle tre, uno dei due cade.
#
# ⚠️ Serve TUTTI E TRE i campi, in OR: una scheda con il titolo francese ma
# senza descrizione non e' componibile — `cdiscount_schede.corpo_scheda`
# pretende titolo, descrizione E almeno un'immagine, e li rifiuta uno per uno.
# Contare solo il titolo direbbe «pronte» delle righe che l'invio scartera'.
#
# ⚠️ `= False` intercetta anche la stringa vuota: Odoo scrive NULL al posto di
# `""` sui campi Char e Text, quindi non serve (e non funzionerebbe) un
# confronto con `""`.
#
# ⚠️ In XML il dominio va riscritto a mano — una vista non puo' importare una
# costante Python. Quello del filtro «Senza contenuti» del Compito 12 e',
# testualmente:
#     ['|', '|', ('titolo', '=', False), ('descrizione', '=', False),
#      ('immagini', '=', False)]
SENZA_CONTENUTI = [
    "|", "|",
    ("titolo", "=", False),
    ("descrizione", "=", False),
    ("immagini", "=", False),
]
CON_CONTENUTI = [
    ("titolo", "!=", False),
    ("descrizione", "!=", False),
    ("immagini", "!=", False),
]

# ⚠️ LE ORFANE, e sono la cosa piu' facile da perdere di tutta la consegna.
#
# Una riga «in attesa» SENZA pacchetto e' una riga che e' partita (o che
# potrebbe essere partita) e per cui non esiste nessun appiglio: non ha un
# numero di pacchetto, quindi **nessun raccoglitore la guardera' mai** — il
# raccoglitore parte dai pacchetti aperti, e qui non ce n'e' uno. E non e'
# nemmeno candidata a ripartire: `_candidate()` vuole `sconosciuto` senza
# pacchetto, oppure `rifiutato`, e «in attesa» non e' ne' l'uno ne' l'altro.
# Senza questo dominio e il filtro che lo mostra, quelle righe non
# comparirebbero da nessuna parte e resterebbero ferme per sempre, in
# silenzio.
#
# ⚠️ Ci si arriva per CINQUE strade, tutte dentro `manda_schede`
# (`connectors/cdiscount.py`), e tutte passano da `_in_volo_senza_esito`:
#   1. l'invio si ferma su un esito IGNOTO (rete caduta a meta' chiamata);
#   2. Cdiscount accetta ma la risposta non porta nessun numero;
#   3. il numero arriva ma non si riesce a scriverlo in Odoo;
#   4. un errore Python interrompe il giro;
#   5. il pacchetto e' scritto ma la scrittura delle righe fallisce.
# Nei casi 3 e 5 il pacchetto puo' esserci davvero e il suo numero sta nel
# registro di sistema: e' li' che si guarda prima di ripescare.
#
# ⚠️ E il ripescaggio NON e' automatico, e non deve diventarlo: in tutti e
# cinque i casi la scheda **potrebbe esistere gia' su Cdiscount**, e
# rimandarla creerebbe un doppione su un catalogo pubblico. E' la stessa
# ragione per cui `_candidate()` le esclude di proposito. Le rimette in gioco
# `action_cdiscount_ripesca`, cioe' una persona che ha guardato.
ORFANE = [
    ("stato", "=", IN_ATTESA),
    ("pacchetto_id", "=", False),
]

# ⚠️⚠️ LE ARENATE, e sono il vicolo cieco piu' capiente del modulo: ci
# finiscono fino a 10.000 schede per volta.
#
# Una riga «senza verdetto» CON un pacchetto ormai chiuso e' lo stato in cui
# la scadenza (`_scade_uno`) mette TUTTE le righe di ogni pacchetto scaduto. E
# da li' non esce da nessuna parte:
#
#   - `_candidate()` vuole `sconosciuto` SENZA pacchetto: non riparte mai;
#   - il raccoglitore parte da `_dominio_aperti()`, che filtra i pacchetti
#     `aperto`, e questo e' `scaduto`: non lo guardera' mai piu';
#   - `ORFANE` pretende `in_attesa`: il ripescaggio le rifiutava.
#
# ⚠️ E non si isolavano nemmeno a vista: il filtro «Senza verdetto» le tiene
# insieme alle schede mai partite, che all'inizio sono la maggioranza — dei
# 604 prodotti del perimetro, 347 non hanno ancora il nome francese.
#
# ⚠️ Lo scenario che ci porta e' il rischio numero uno dichiarato in tre file:
# i nomi delle chiavi del rapporto vengono dalla documentazione e non sono
# mai stati visti sul vero. Se sono sbagliati, ogni pacchetto resta
# illeggibile, scade, e ci finisce dentro tutto il catalogo.
#
# ⚠️ «Non piu' aperto» e non «scaduto»: un pacchetto `raccolto` e' chiuso
# anche lui, e se una sua riga e' rimasta senza verdetto (il rapporto non la
# nominava e qualcosa e' andato storto altrove) e' esattamente nella stessa
# condizione. La condizione guarda cosa impedisce il recupero, non come ci si
# e' arrivati.
#
# ⚠️ Il pacchetto DEVE esserci: una riga `sconosciuto` senza pacchetto e' la
# riga appena nata, quella che aspetta la traduzione francese, e non c'e'
# niente da ripescare. Senza questa condizione il bottone toccherebbe tutto
# il catalogo.
#
# ⚠️⚠️ E GLI STATI SENZA VERDETTO SONO DUE, non uno. «In attesa» con un
# pacchetto CHIUSO e' lo stesso identico vicolo cieco spostato di uno stato:
# il raccoglitore parte dai pacchetti `aperto` e non lo guardera' mai piu',
# `_candidate()` non prende le righe «in attesa», e `ORFANE` vuole il
# pacchetto ASSENTE. Ci si arriva quando `_in_volo_senza_esito` non riesce a
# staccare il pacchetto vecchio (il suo secondo tentativo, quello stretto), e
# in teoria da un processo ucciso in mezzo alla scadenza. Senza questa
# seconda parola quella riga finirebbe in mezzo alle «In attesa dell'esito»
# vere, dove nessun filtro la distingue e il ripescaggio la rifiuta.
#
# ⚠️ Non si sovrappone mai a `ORFANE`: quella vuole il pacchetto assente,
# questa lo pretende presente.
ARENATE = [
    # ⚠️ Una LISTA e non una tupla: il banco del manifesto confronta questo
    # dominio con la sua copia a mano in XML, che una lista la scrive per
    # forza. Per Odoo sono equivalenti; per il confronto no.
    ("stato", "in", [SCONOSCIUTO_SCHEDA, IN_ATTESA]),
    ("pacchetto_id", "!=", False),
    ("pacchetto_id.stato", "!=", APERTO),
]

# Quanti codici si nominano per esteso nella riga di registro del
# ripescaggio. Stessa ragione (e stesso numero) di
# `MAX_CODICI_NEL_MESSAGGIO` nel connettore: un elenco di 10.000 codici in un
# campo Text non lo legge nessuno, e il conto esatto viaggia sempre accanto
# all'elenco corto.
MAX_CODICI_RIPESCATI = 20


class CdiscountScheda(models.Model):
    _name = "cdiscount.scheda"
    _description = "Cdiscount — stato di una scheda prodotto"
    _order = "channel_id, codice"
    # Non c'e' un campo `name`: la riga si chiama col codice venditore, che e'
    # anche l'unica cosa che il rapporto di Cdiscount nomina.
    _rec_name = "codice"

    channel_id = fields.Many2one(
        "centrivo.channel", string="Canale", required=True,
        ondelete="cascade", index=True)

    # ⚠️ NON OBBLIGATORIO E `set null`: LA SCHEDA SU CDISCOUNT SOPRAVVIVE AL
    # PRODOTTO ODOO. Sparito il prodotto resta il `codice`, l'unico appiglio
    # su una scheda che la' fuori esiste ancora, in un catalogo pubblico. Con
    # `cascade` la riga sparirebbe e la scheda resterebbe viva e
    # irraggiungibile — e non servirebbe nemmeno una cancellazione voluta:
    # Odoo distrugge e ricrea le varianti quando si cambiano le righe
    # attributo di un template, e ogni variante distrutta si porterebbe via
    # la sua riga. Stessa scelta di `centrivo.temu.listing`
    # (marketplace_temu/models/temu_listing.py) e di `kaufland.offer`, e per
    # lo stesso motivo.
    product_id = fields.Many2one(
        "product.product", string="Prodotto", ondelete="set null",
        index=True)

    # Il `sellerProductReference` che spediamo e che il rapporto ci rimanda
    # indietro. E' la chiave della riconciliazione: senza, un esito non si
    # attribuisce a nessuno.
    # ⚠️ OBBLIGATORIO, e non e' simmetrico con `product_id` qui sopra. Il
    # prodotto puo' mancare per scelta (difetto n.4: la scheda sopravvive al
    # prodotto Odoo), il codice no: e' il `_rec_name`, ed e' l'UNICA chiave
    # con cui il rapporto di Cdiscount nomina questa riga. Una riga senza
    # codice e senza prodotto non e' nominabile, non e' riconciliabile e —
    # coi permessi `1,0,0,0` — un utente normale non puo' nemmeno
    # cancellarla: resterebbe in mezzo all'elenco per sempre.
    #
    # ⚠️ Il confronto con `kaufland.offer.ean`, che NON e' obbligatorio, non
    # regge: la' la chiave vera e' `product_id` e l'EAN e' un attributo in
    # piu'. Qui e' esattamente il contrario.
    codice = fields.Char(
        string="Codice venditore", required=True, index=True,
        help="Il riferimento con cui la scheda e' partita. E' l'unica cosa "
             "che il rapporto di Cdiscount nomina.")

    # ------------------------------------------------------------------
    # I CONTENUTI, che in Odoo non ci sono.
    #
    # ⚠️ Titolo, descrizione e immagini NON vengono dal prodotto Odoo: vengono
    # da Shopify, che li ha piu' completi e le cui immagini sono gia'
    # pubbliche — cioe' scaricabili da Cdiscount. Entrano da un file CSV
    # caricato a mano (`models/cdiscount_contenuti_wizard.py`), e stanno qui
    # perche' e' qui che l'invio (Compito 9) va a prenderli.
    #
    # ⚠️ E vanno scritti IN FRANCESE. E' il vero collo di bottiglia della
    # consegna: dei 604 prodotti del perimetro solo 257 hanno oggi un nome
    # francese, e per gli altri 347 questi tre campi restano vuoti finche'
    # qualcuno non li traduce fuori di qui. Il conto delle righe vuote e'
    # quello che dice quanto lavoro manca: vedi `SENZA_CONTENUTI` in testa al
    # file.
    #
    # ⚠️ Non sono `required`: una riga senza contenuti e' uno stato legittimo
    # e ATTESO — e' precisamente la scheda che aspetta la traduzione. Renderli
    # obbligatori impedirebbe di creare la riga, e con essa sparirebbe la sola
    # traccia del fatto che quel prodotto manca all'appello.
    # ------------------------------------------------------------------
    titolo = fields.Char(
        string="Titolo (francese)", copy=False,
        help="Il nome che legge il cliente su Cdiscount. Massimo 132 "
             "caratteri: piu' lungo, l'invio si ferma e non lo accorcia.")

    descrizione = fields.Text(
        string="Descrizione (francese)", copy=False,
        help="La descrizione della scheda. Massimo 2000 caratteri: piu' "
             "lunga, l'invio si ferma e non la taglia.")

    # ⚠️ UN SOLO CAMPO E NON UN `One2many`, ed e' una scelta. L'ordine degli
    # indirizzi conta — il primo e' la copertina — e su un elenco di righe
    # figlie l'ordine si difende con un campo sequenza che qualcuno prima o
    # poi riordina per sbaglio, mandando in vetrina la foto sbagliata senza
    # lasciare traccia. Qui l'ordine e' quello del testo, che e' anche quello
    # della cella del file: se e' sbagliato, si vede.
    #
    # ⚠️ Il separatore e' quello del file (`SEPARATORE_IMMAGINI`, la virgola),
    # e non un a capo: cosi' il campo conserva esattamente la cella scritta
    # nel foglio di calcolo, e il confronto fra cio' che si vede qui e cio'
    # che sta nel file e' immediato. Un a capo, per giunta, non
    # sopravviverebbe intatto a un giro di andata e ritorno: una casella di
    # testo del browser scrive «\r\n», e un indirizzo vuoto in fondo
    # sparirebbe in silenzio.
    immagini = fields.Text(
        string="Immagini (indirizzi)", copy=False,
        help="Gli indirizzi HTTPS delle immagini, separati dalla virgola, "
             "nell'ordine: il primo e' la copertina. Devono essere pubblici "
             "e almeno 500x500 — Cdiscount se li scarica da solo.")

    # ⚠️ NON e' un doppione dei tre campi qui sopra, e non serve a dire «ci
    # sono i contenuti» (per quello c'e' `SENZA_CONTENUTI`, che guarda i campi
    # veri). Serve a dire QUANDO sono arrivati: davanti a una scheda rifiutata
    # per il titolo, la prima domanda e' se il file caricato ieri l'avesse gia'
    # corretta o no, e senza questa data la risposta non c'e' da nessuna parte.
    contenuti_il = fields.Datetime(
        string="Contenuti caricati il", copy=False,
        help="Quando questa scheda ha ricevuto i testi dall'ultimo file dei "
             "contenuti.")

    # ⚠️ `set null` anche qui, e per un motivo diverso: il pacchetto e' un
    # documento di trasporto, la scheda e' la merce. Cancellato il pacchetto
    # (per esempio ripulendo i vecchi), le schede devono restare col loro
    # verdetto — con `cascade` si porterebbe via lo storico di 10.000 righe.
    pacchetto_id = fields.Many2one(
        "cdiscount.pacchetto", string="Pacchetto", ondelete="set null",
        index=True,
        help="Il pacchetto con cui questa scheda e' partita. Vuoto significa "
             "che non e' mai partita.")

    # ⚠️ IL `default` NON E' UN DETTAGLIO, ed e' un difetto gia' pagato su
    # Kaufland. Senza, una riga appena creata vale `False`, che NON e' nessuno
    # dei quattro stati: sarebbe un quinto stato muto, un secchio senza nome
    # raggruppando per stato, e nessuno dei filtri della schermata lo
    # intercetterebbe. Sparirebbero dalla vista proprio le righe mai mandate —
    # cioe' quelle che aspettano qualcosa da noi.
    #
    # ⚠️ E il default e' `sconosciuto`, non `in_attesa`: «in attesa» dice «e'
    # partita, aspetto l'esito», e una riga appena nata non e' partita. Dirlo
    # la manderebbe nel giro del raccoglitore, che andrebbe a chiedere l'esito
    # di un pacchetto che non esiste.
    # ⚠️ `required=True` NON e' un doppione del `default`: e' la seconda meta'
    # della stessa difesa. Il default copre la CREAZIONE, non una
    # `write({"stato": False})` — che e' precisamente il quinto stato muto
    # contro cui il difetto n.2 mette in guardia, e che nessun default
    # fermerebbe. Il default appoggia la porta, `required` la chiude. Lo
    # stesso `required` ce l'ha gia' `cdiscount.pacchetto.stato`.
    stato = fields.Selection(
        STATI_SCHEDA, string="Stato", default=SCONOSCIUTO_SCHEDA,
        required=True, copy=False,
        help="«Non lo so» e' lo stato di partenza e vale anche dopo una "
             "scadenza: non sappiamo se Cdiscount l'abbia accettata.")

    motivo = fields.Text(
        string="Motivo", copy=False,
        help="Cosa ha detto Cdiscount, o perche' non l'ha detto.")

    controllato_il = fields.Datetime(
        string="Controllato il", copy=False,
        help="Quando l'esito di questa scheda e' stato letto l'ultima volta.")

    company_id = fields.Many2one(
        "res.company", string="Azienda",
        related="channel_id.company_id", store=True, index=True)

    _sql_constraints = [
        # ⚠️ IL DOPPIONE NON E' UN'IPOTESI DI SCUOLA, ED E' MUTO. La procedura
        # del file dei contenuti (Compito 8) cerca la riga e, se non la trova,
        # la crea: due caricamenti dello stesso file a pochi secondi l'uno
        # dall'altro — o UNO SOLO, ripetuto da chi ha visto il browser andare
        # in timeout dopo che la transazione era gia' passata — non trovano
        # nulla e creano entrambi.
        #
        # Da li' in poi nessuno se ne accorge: l'invio (Compito 9) manda la
        # scheda due volte nello stesso pacchetto, il raccoglitore (Compito
        # 10) scrive il verdetto sulla riga che la ricerca restituisce per
        # prima, e L'ALTRA RESTA «sconosciuto» PER SEMPRE — in cima al filtro
        # «Senza verdetto», identica a un problema vero. E' lo stesso vincolo
        # che il gemello di casa ha gia' (`kaufland.offer`,
        # marketplace_kaufland/models/kaufland_offer.py).
        #
        # ⚠️ Per CANALE, non in assoluto: lo stesso codice puo' avere una
        # scheda su piu' canali Cdiscount, e sono schede diverse.
        ("uniq_canale_codice", "unique(channel_id, codice)",
         "Questo codice ha già una scheda su questo canale."),
    ]

    # ------------------------------------------------------------------
    # IL RIPESCAGGIO DELLE ORFANE
    #
    # ⚠️ E' l'unico modo che una persona ha di rimettere in gioco una riga
    # rimasta «in attesa» senza pacchetto (vedi `ORFANE` in testa al file).
    # Senza, quelle righe sarebbero perdute per sempre e in silenzio: nessun
    # raccoglitore le tocca, nessun invio le riprende.
    #
    # ⚠️ E' UN GESTO, NON UN AUTOMATISMO, e la differenza e' tutto il punto:
    # la scheda potrebbe gia' esistere su Cdiscount, e rimandarla alla cieca
    # farebbe un doppione su un catalogo pubblico. Percio' non c'e' nessun
    # cron che lo faccia, non c'e' nessun bottone «ripesca tutte», e le righe
    # vanno SELEZIONATE una per una da chi ha letto il motivo.
    # ------------------------------------------------------------------
    def action_cdiscount_ripesca(self):
        """Rimette fra i candidati le righe selezionate che sono ferme.

        Due specie, e sono ferme per due motivi diversi:

        - le ORFANE, «in attesa» SENZA pacchetto: si riporta lo stato a «non
          lo so», e tanto basta perche' rientrino in `_candidate()`;
        - le ARENATE, «senza verdetto» con un pacchetto ormai CHIUSO: lo
          stato e' gia' quello giusto, ma il pacchetto attaccato le tiene
          fuori dai candidati. Li' si stacca il pacchetto.

        Non manda niente ne' l'una ne' l'altra: da quel momento la riga
        ripartira' col prossimo invio, insieme alle altre.

        ⚠️ **Perche' staccare il pacchetto invece di allargare
        `_candidate()`.** Far entrare fra i candidati tutte le righe senza
        verdetto con un pacchetto chiuso vorrebbe dire rimandare
        AUTOMATICAMENTE ogni scheda di ogni pacchetto scaduto, fino a 10.000
        per volta, senza che nessuno abbia guardato: e' precisamente il
        doppione su un catalogo pubblico che questo modulo esiste per
        impedire. Il numero del pacchetto non si perde — resta nel motivo, e
        la riga di registro lo nomina.

        ⚠️ Tocca SOLO quelle due specie, e le altre righe selezionate le
        lascia stare NOMINANDOLE. Una selezione fatta col «seleziona tutto»
        prende anche le righe in attesa CON pacchetto — quelle stanno
        aspettando un esito che arrivera' — e riportarle indietro le farebbe
        ripartire mentre Cdiscount sta ancora lavorando il loro pacchetto:
        doppioni, di nuovo. Il filtro sta qui dentro e non nella schermata,
        perche' una schermata si puo' aggirare con una chiamata RPC.

        ⚠️ Il MOTIVO NON SI CANCELLA: gli si mette una riga davanti. E' cio'
        che dice perche' la scheda si era fermata, ed e' l'unica cosa che
        resta da guardare se il ripescaggio si rivela sbagliato. Lo
        cancellera' l'invio, e solo quando la scheda sara' ripartita davvero.
        """
        # ⚠️ Il permesso e' lo stesso dei due bottoni del canale, e per lo
        # stesso motivo: questo gesto rimette schede in coda per un catalogo
        # pubblico. `ir.model.access.csv` gia' nega la scrittura a
        # `base.group_user` (1,0,0,0) e quindi la `write` piu' sotto
        # fallirebbe comunque — ma con un `AccessError` generico dell'ORM,
        # che non spiega niente. Questo lo spiega.
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_(
                "«Rimetti in gioco» è riservato agli amministratori: "
                "rimette una scheda in coda per un catalogo pubblico, e la "
                "scheda potrebbe già esistere là fuori."))

        # ⚠️ Il criterio si legge da `ORFANE`, la stessa costante che il
        # filtro della schermata ricopia a mano: `filtered_domain` valuta un
        # dominio Odoo sui record gia' in memoria. Riscrivere qui la
        # condizione con un `lambda` significherebbe due definizioni di
        # «orfana» — quella che si VEDE nel filtro e quella che si TOCCA
        # ripescando — e il giorno in cui divergono il bottone agirebbe su
        # righe diverse da quelle mostrate, che e' il modo peggiore possibile
        # di sbagliare su un catalogo pubblico.
        orfane = self.filtered_domain(ORFANE)
        arenate = self.filtered_domain(ARENATE)
        # ⚠️ Le due specie non si sovrappongono mai (una vuole `in_attesa`
        # senza pacchetto, l'altra `sconosciuto` con un pacchetto), quindi
        # togliere prima l'una e poi l'altra non puo' contare niente due
        # volte.
        lasciate = self - orfane - arenate

        if not (orfane or arenate):
            # ⚠️ IL MESSAGGIO DICEVA IL FALSO, ed era la seconda meta' del
            # vicolo cieco: mandava «le risolve il raccoglitore» anche alle
            # righe di un pacchetto SCADUTO, che il raccoglitore non guardera'
            # mai piu'. L'unico gesto di recupero le rifiutava e le rimandava
            # ad aspettare una cosa che non sarebbe arrivata. Ora le due
            # specie recuperabili si nominano, e il raccoglitore si nomina
            # solo per i pacchetti che sono davvero ancora aperti.
            return self._cdiscount_avviso(_(
                "Nessuna delle %s righe selezionate si può rimettere in "
                "gioco. Si rimettono in gioco due specie di righe: quelle "
                "«in attesa dell'esito» che NON hanno un pacchetto, e quelle "
                "senza verdetto — «Non lo so» o «In attesa» — il cui "
                "pacchetto è ormai CHIUSO (scaduto o già raccolto). Le altre "
                "o non sono mai partite, e ripartono da sole al prossimo "
                "invio se hanno i contenuti; o sono già riuscite; o stanno "
                "aspettando l'esito di un pacchetto ANCORA APERTO, e solo "
                "quelle le risolve il raccoglitore.") % len(self),
                "warning")

        # ⚠️ L'ORA VA CONVERTITA, e non e' pignoleria. `fields.Datetime.now()`
        # rende UTC naive: scritta cosi' com'e' dentro un testo che una
        # persona legge, d'estate direbbe DUE ORE PRIMA dell'ora vera, e chi
        # confronta questa nota con l'orario di un invio nel registro
        # concluderebbe che il ripescaggio e' avvenuto prima di un fatto che
        # invece l'ha preceduto. E' la stessa classe di errore che la
        # consegna sorveglia sui filtri della scadenza.
        # ⚠️ `%Z` in coda perche' anche l'ora giusta, senza il nome del fuso,
        # non e' verificabile da chi la legge sei mesi dopo. Se l'utente non
        # ha un fuso impostato, `context_timestamp` rende UTC e `%Z` scrive
        # «UTC»: dice il vero in tutti e due i casi.
        quando = fields.Datetime.context_timestamp(
            orfane[:1], fields.Datetime.now()).strftime("%Y-%m-%d %H:%M:%S %Z")
        chi = self.env.user.display_name
        for riga in orfane:
            nota = _(
                "⚠️ Rimessa in gioco a mano il %(quando)s da %(chi)s: era "
                "«in attesa» senza pacchetto, e ripartirà col prossimo "
                "invio. Perché si era fermata:"
            ) % {"quando": quando, "chi": chi}
            riga.write({
                "stato": SCONOSCIUTO_SCHEDA,
                "motivo": "%s\n%s" % (nota, riga.motivo or _("(non detto)")),
            })

        # ⚠️ I numeri dei pacchetti si raccolgono STRADA FACENDO, perche'
        # dopo il distacco non sono piu' leggibili da nessuna parte: sono
        # l'unico appiglio per andare a guardare su Cdiscount cosa c'e'
        # davvero, ed e' esattamente la cosa da fare prima di rimandare.
        numeri_arenate = {}
        for riga in arenate:
            # ⚠️ Il numero si legge PRIMA di staccare il pacchetto, ed e'
            # l'unica cosa che sopravvive al distacco: la riga di registro lo
            # nomina, e il motivo se lo porta dietro finche' l'invio non lo
            # cancella.
            numero = riga.pacchetto_id.numero or _("(senza numero)")
            numeri_arenate[riga.id] = numero
            nota = _(
                "⚠️ Rimessa in gioco a mano il %(quando)s da %(chi)s: era "
                "senza verdetto nel pacchetto %(numero)s, che è ormai chiuso "
                "e non dirà più niente. ⚠️ NON sappiamo se Cdiscount l'abbia "
                "presa: se la scheda esiste già là fuori, il prossimo invio "
                "ne farà un doppione su un catalogo pubblico. Perché si era "
                "fermata:"
            ) % {"quando": quando, "chi": chi, "numero": numero}
            riga.write({
                "stato": SCONOSCIUTO_SCHEDA,
                # ⚠️ E' QUESTO che la rimette in gioco: lo stato era gia'
                # «non lo so», a tenerla fuori dai candidati era il pacchetto.
                "pacchetto_id": False,
                "motivo": "%s\n%s" % (nota, riga.motivo or _("(non detto)")),
            })

        if orfane:
            self._cdiscount_registra_ripescaggio(orfane, chi, _(
                "erano «in attesa» senza pacchetto, cioè in un limbo che "
                "nessun giro automatico risolve"))
        if arenate:
            self._cdiscount_registra_ripescaggio(
                arenate, chi,
                _("erano senza verdetto in un pacchetto ormai chiuso, che "
                  "nessun raccoglitore guarderà più"),
                numeri=numeri_arenate)

        messaggio = _(
            "Rimesse in gioco %(quante)s schede (%(orfane)s senza pacchetto, "
            "%(arenate)s da pacchetti chiusi): ripartiranno col prossimo "
            "invio, se hanno titolo, descrizione e immagini."
        ) % {"quante": len(orfane) + len(arenate), "orfane": len(orfane),
             "arenate": len(arenate)}
        if arenate:
            messaggio += _(
                " ⚠️ Delle %s da pacchetti chiusi non sappiamo se Cdiscount "
                "le abbia prese: se esistono già là fuori, il prossimo invio "
                "ne farà dei doppioni."
            ) % len(arenate)
        if lasciate:
            messaggio += _(
                " ⚠️ %s righe selezionate NON sono state toccate: non erano "
                "né orfane né ferme in un pacchetto chiuso."
            ) % len(lasciate)
        # ⚠️ Niente verde quando ci sono delle arenate: quelle POTREBBERO
        # esistere gia' su Cdiscount, e una notifica verde che sparisce da
        # sola e' il modo di non far leggere proprio quella riga.
        return self._cdiscount_avviso(
            messaggio, "warning" if (lasciate or arenate) else "success")

    def _cdiscount_registra_ripescaggio(self, ripescate, chi, spiegazione,
                                        numeri=None):
        """Una riga nel registro delle operazioni, per canale.

        ⚠️ Serve perche' il ripescaggio e' l'unico gesto di questa consegna
        che RIMETTE IN CODA schede per un catalogo pubblico senza passare da
        un bottone del canale. Se domani nascono doppioni su Cdiscount, la
        domanda sara' «chi ha rimesso in gioco cosa, e quando»: senza questa
        riga la risposta non esiste da nessuna parte — il campo `motivo`
        della scheda viene cancellato dal primo invio riuscito.

        ⚠️ Nel suo savepoint e con l'eccezione catturata: scrivere il
        registro non deve poter annullare il ripescaggio, che a quel punto e'
        gia' scritto. Stessa forma (e stessa ragione) di
        `centrivo.channel._cdiscount_registra`.
        """
        for canale in ripescate.mapped("channel_id"):
            righe = ripescate.filtered(lambda r, c=canale: r.channel_id == c)
            codici = [riga.codice or "?" for riga in righe]
            elenco = ", ".join(codici[:MAX_CODICI_RIPESCATI])
            if len(codici) > MAX_CODICI_RIPESCATI:
                elenco += _(" … e altre %s") % (len(codici)
                                                - MAX_CODICI_RIPESCATI)
            # ⚠️ I numeri dei pacchetti chiusi si scrivono qui e SOLO qui: il
            # campo `pacchetto_id` e' appena stato staccato, e il motivo sulla
            # riga lo cancella il primo invio riuscito. Senza questa riga,
            # «da quale pacchetto veniva questa scheda» non e' piu' una
            # domanda a cui qualcuno possa rispondere.
            coda = ""
            if numeri:
                distinti = sorted({numeri.get(riga.id, "?")
                                   for riga in righe})
                coda = _(" Pacchetti chiusi da cui venivano: %s.") % ", ".join(
                    distinti[:MAX_CODICI_RIPESCATI])
            try:
                with self.env.cr.savepoint():
                    self.env["centrivo.job.log"].sudo().create({
                        "channel_id": canale.id,
                        "operation": "cdiscount_ripesca",
                        # ⚠️ `skip` e non `success`: qui non e' riuscito
                        # niente, e' stato DECISO qualcosa. Il verde lo
                        # scrivera' l'invio, e solo se la scheda nascera'
                        # davvero.
                        "result": "skip",
                        "message": _(
                            "%(quante)s schede rimesse in gioco a mano da "
                            "%(chi)s: %(spiegazione)s.%(coda)s ⚠️ Se una di "
                            "queste esisteva già su Cdiscount, il prossimo "
                            "invio ne farà un doppione. Codici: %(codici)s"
                        ) % {"quante": len(righe), "chi": chi,
                             "spiegazione": spiegazione, "coda": coda,
                             "codici": elenco},
                        "company_id": canale.company_id.id,
                    })
            except Exception:  # noqa: BLE001
                _logger.exception(
                    "Cdiscount: non si e' potuta scrivere la riga di "
                    "registro del ripescaggio di %s schede sul canale %s",
                    len(righe), canale.id)

    @staticmethod
    def _cdiscount_avviso(messaggio, tipo):
        """La notifica del ripescaggio.

        ⚠️ `sticky` quando NON e' tutto verde, come nelle notifiche dei due
        bottoni del canale: una notifica che sparisce da sola e' il modo di
        non far leggere proprio la riga che andava letta.
        """
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Cdiscount — rimetti in gioco"),
                "message": messaggio,
                "type": tipo,
                "sticky": tipo != "success",
            },
        }

    # ------------------------------------------------------------------
    def elenco_immagini(self):
        """Gli indirizzi delle immagini, nell'ordine. Il primo e' la copertina.

        ⚠️ Esiste perche' il modo in cui gli indirizzi stanno in un campo solo
        e' UN DETTAGLIO DI QUESTO MODELLO, e chi li legge non deve conoscerlo.
        L'invio (Compito 9) passa questa lista a
        `cdiscount_schede.corpo_scheda`, che pretende un elenco e rifiuta una
        stringa nominando il prodotto: se ognuno si spezzasse il campo per
        conto suo, il giorno in cui il separatore cambia si romperebbe in un
        posto solo dei due, e in silenzio.

        ⚠️ Un indirizzo VUOTO non si butta via. `_immagini_valide` lo rifiuta
        nominando la posizione, e lo fa apposta: togliere un buco fa scivolare
        avanti tutte le immagini seguenti e manda il prodotto in vetrina con
        la copertina sbagliata, senza che resti traccia da nessuna parte.
        Toglierlo qui sarebbe lo stesso danno, un passo prima e senza nemmeno
        un errore. La colonna INTERAMENTE vuota e' un'altra cosa — «questa
        scheda non ha immagini» — e rende `[]`, cosi' l'invio la rifiuta con
        «nessuna immagine», che e' la notizia vera.
        """
        self.ensure_one()
        # ⚠️ `or ""`: in Odoo un Text non valorizzato si legge `False`, e
        # `False.split` non esiste.
        testo = (self.immagini or "").strip()
        if not testo:
            return []
        return [pezzo.strip() for pezzo in testo.split(SEPARATORE_IMMAGINI)]
