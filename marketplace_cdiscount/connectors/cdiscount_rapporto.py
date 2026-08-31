# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""L'esito di un pacchetto di schede, e la riconciliazione con quel che era
partito.

`POST /products-integration` accetta fino a 10.000 schede e risponde con un
numero di pacchetto: l'esito si va a ripescare dopo, e **scade in tre giorni**.
Qui si legge quel rapporto.

⚠️ **E' il punto in cui questo modulo puo' mentire.** Un rapporto che nomina
meno prodotti di quanti ne sono partiti NON e' un successo: le righe non
nominate restano senza verdetto, e se le si conta come riuscite quella scheda
non verra' mai piu' rimandata — sparisce dal catalogo senza che nessuno lo
sappia, e con un esito che scade in tre giorni non c'e' modo di accorgersene
dopo. Da qui le due regole di tutto il file:

1. `riconcilia` lavora sull'INSIEME dei codici, non sul conteggio delle voci.
   Contare le voci lascerebbe che una voce estranea o ripetuta pareggi il
   conto di una mancante, e un pacchetto dimezzato tornerebbe verde. E' la
   stessa forma gia' revisionata in `kaufland._leggi_esiti`, e lo stesso
   difetto vi era gia' stato trovato due volte.
2. «Non lo so» non e' «vuoto». Uno stato che non conosciamo, un corpo
   illeggibile e un rapporto ancora in lavorazione sono TRE cose diverse da un
   rapporto pronto e vuoto, e chi chiama non deve poterle confondere: per
   questo lo stato viaggia sempre accanto agli esiti.

⚠️ I nomi delle chiavi (`status`, `results`, `sellerProductRef`, `errors`)
vengono dalla DOCUMENTAZIONE e vanno riconfermati alla prima lettura vera: su
questo modulo c'e' gia' il precedente della ricognizione del 2026-08-25, dove
un elenco di campi che sembrava JSON era lo schema XML. Ovunque si e' scelta
la forma che sbaglia dalla parte prudente — un nome che non torna produce
«mancante», cioe' un rinvio, mai un falso successo.

Questo file NON importa Odoo: si prova con tools/test_cdiscount_rapporto.py.
"""

# ⚠️ Doppia forma di import, e serve DAVVERO: dentro Odoo questi file sono un
# pacchetto (import relativo), mentre i test di tools/ mettono la sola cartella
# `connectors/` sul percorso (import assoluto). E' la stessa convenzione di
# `kaufland_client`. Si riusano due funzioni gia' revisionate invece di
# riscriverle: `_dettaglio` tronca CHIAVE PER CHIAVE (un rifiuto di Cdiscount
# ha la stessa forma di un corpo d'errore RFC 9457, e tagliare la stringa gia'
# composta fa sparire il motivo dietro un identificativo di tracciamento),
# `_testo` conosce la trappola del `False` di Odoo.
try:
    from .cdiscount_client import SEGNO_TAGLIO, _dettaglio
    from .cdiscount_schede import _testo
except ImportError:  # eseguito fuori da Odoo, dai test di tools/
    from cdiscount_client import SEGNO_TAGLIO, _dettaglio
    from cdiscount_schede import _testo

# Lo stato del RAPPORTO. Tre valori distinti e tutti veri in senso booleano:
# chi chiama deve poter distinguere «riprova fra un po'» da «guarda cosa e'
# successo», e `if stato:` non deve far sparire nessuno dei tre.
PRONTO = "pronto"
IN_LAVORAZIONE = "in_lavorazione"
SCONOSCIUTO = "sconosciuto"

# L'esito di un SINGOLO prodotto dentro un rapporto pronto.
RIUSCITO = "riuscito"
RIFIUTATO = "rifiutato"

# Gli stati del pacchetto, in minuscolo. ⚠️ L'insieme dei PRONTI e' quello che
# apre il cancello, ed e' l'unico che non va allargato a cuor leggero: un nome
# in piu' qui vuol dire dichiarare finito un pacchetto che non lo e'. Gli altri
# due insiemi sbagliano entrambi dalla parte buona (nessun verdetto), quindi
# tutto cio' che non e' riconosciuto finisce fra gli SCONOSCIUTI.
STATI_PRONTO = ("completed",)
STATI_IN_LAVORAZIONE = ("pending", "processing", "inprogress")

# Le chiavi del corpo, tutte da riconfermare (vedi l'avvertenza in testa).
CHIAVE_RISULTATI = "results"
# ⚠️ DUE nomi per il codice, e si provano tutti e due. Noi spediamo
# `sellerProductReference` (cdiscount_schede.corpo_scheda), la documentazione
# del rapporto dice `sellerProductRef`. Se se ne accettasse UNO SOLO e
# Cdiscount usasse l'altro, ogni voce sarebbe anonima e ogni prodotto
# risulterebbe mancante: il pacchetto verrebbe rimandato all'infinito. E' la
# stessa doppia lettura di `kaufland._leggi_esiti` (id_unit / unit_id).
CHIAVI_CODICE = ("sellerProductRef", "sellerProductReference")
CHIAVE_STATO_VOCE = "status"
# L'unico valore che vale come successo su una singola riga. ⚠️ Il confronto e'
# di UGUAGLIANZA, non di inizio-stringa: un ipotetico «SuccessWithWarnings» non
# e' un successo, ed e' proprio il genere di riga che finirebbe in vetrina
# sbagliata senza che nessuno la rimandi.
ESITI_RIUSCITI = ("success",)
# Riconosciuti come rifiuti espliciti: cambia solo il MESSAGGIO, non il
# verdetto — tutto cio' che non e' un successo e' comunque rifiutato.
ESITI_RIFIUTATI = ("error", "failed", "failure", "ko")

# ⚠️ IL TETTO DEL MOTIVO COMPOSTO, e perche' non basta quello di `_dettaglio`.
# `_dettaglio` tiene a bada il CORPO della riga, chiave per chiave, ma non
# copre quel che ci mettiamo intorno: la premessa sullo stato e l'incarto della
# contraddizione. Un `status` che porta dentro una pagina d'errore HTML — cosa
# che capita davvero — su un pacchetto da 10.000 righe farebbe centinaia di MB
# nel dizionario reso e in qualunque cosa lo scriva nel registro. E' la lezione
# del Compito 3 (`_dettaglio`), applicata a TUTTO il motivo e non al solo
# corpo.
TETTO_MOTIVO = 4000
CODA_MOTIVO = " %s (motivo troncato)" % SEGNO_TAGLIO
# Quanto di uno stato di riga incomprensibile finisce nel motivo. Sessanta
# caratteri bastano a riconoscerlo; di piu' scaccerebbe il dettaglio, che e'
# il dato utile.
MAX_STATO_NEL_MOTIVO = 60
# L'incarto di una contraddizione. E' una COSTANTE perche' si riconosce: un
# prodotto nominato dieci volte con esiti alterni si incarta UNA sola volta —
# vedi `_piu_prudente`.
PREFISSO_CONTRADDIZIONE = (
    "il rapporto nomina questo prodotto piu' volte con esiti opposti")


def _tetto(testo):
    """Il motivo, mai piu' lungo di `TETTO_MOTIVO`, e se e' tagliato lo dice.

    ⚠️ Un taglio DICHIARATO e' informazione; un taglio silenzioso e' il
    difetto che tutta questa consegna esiste per evitare.
    """
    if len(testo) <= TETTO_MOTIVO:
        return testo
    return testo[:TETTO_MOTIVO - len(CODA_MOTIVO)] + CODA_MOTIVO


def _e_scalare(grezzo):
    """Se un valore puo' essere un codice prodotto.

    ⚠️ Un dizionario, una lista o un oggetto qualunque diventerebbero un
    codice INVENTATO passando da `str()` — misurato: `{"a": 1}` diventa il
    codice `"{'a': 1}"`, che non e' mai stato mandato a nessuno e fa quadrare
    conti che non quadrano. Uno SKU e' testo (o un numero); tutto il resto e'
    un elenco composto male.
    """
    return grezzo is None or isinstance(grezzo, (str, int, float, bool))


def _codice(grezzo):
    """Il codice come lo si confronta: testo pulito, senza spazi ai bordi.

    ⚠️ Si normalizzano ENTRAMBI i lati — quel che abbiamo mandato e quel che
    il rapporto nomina — perche' uno spazio in coda farebbe leggere il
    prodotto come estraneo E come mancante insieme: due bugie al prezzo di
    una.

    ⚠️ E si tolgono SOLO gli spazi: le maiuscole NON si toccano, ed e' una
    scelta. Abbassarle farebbe combaciare due SKU che in Odoo sono due
    prodotti diversi (`AB-100` e `ab-100` possono convivere), cioe' darebbe
    per confermata una scheda sulla parola di un'altra. Uno spazio in coda e'
    un difetto di trascrizione; una lettera maiuscola e' un dato. Chi passasse
    di qui per «sistemare» la normalizzazione con un `.lower()` trova un
    controllo che lo ferma.
    """
    return _testo(grezzo)


def _codice_voce(voce):
    """Il codice che una voce del rapporto nomina, o "" se e' anonima.

    ⚠️ Le due chiavi si provano IN SEQUENZA, ciascuna col suo controllo. Con
    un solo `.get(chiave, altro)` una chiave presente ma NULLA coprirebbe
    l'altra che porta il codice buono: e' l'errore che `kaufland._leggi_esiti`
    documenta per esperienza.

    ⚠️ E se le due chiavi portano DUE CODICI DIVERSI, la riga e' ambigua e si
    tratta come anonima. Era l'unico punto del file che non sbagliava dalla
    parte prudente: fermarsi al primo nome vuol dire, se il verdetto
    apparteneva all'altro, dichiarare confermata una scheda che nessuno ha
    confermato — e con `estranee` vuoto non resterebbe nemmeno il segnale che
    qualcosa non torna. Cosi' invece restano mancanti tutti e due, cioe' si
    rimandano: costa un invio, non una scheda persa.

    ⚠️ E vale ANCHE per un valore composto male. Una lista sotto l'altro nome
    — `{"sellerProductRef": ["HDC8"], "sellerProductReference": "HDC7"}` — E'
    un secondo nome, solo scritto male: la riga nomina due cose e non sappiamo
    a chi appartenga il verdetto, esattamente come con due codici in
    disaccordo. Senza questa regola HDC7 risulterebbe confermato mentre il
    successo poteva essere di HDC8, ed e' la stessa bugia da cui la regola
    nasce. La differenza fra le due strade era un'asimmetria, non una scelta.

    ⚠️ Il VUOTO e' un'altra cosa: `[]`, `{}`, `""`, `None`, `False` sotto una
    chiave dicono «questa chiave non c'e'» — in Odoo un campo non valorizzato
    si legge cosi' — e non «c'e' ed e' incomprensibile». Solo un valore
    composto male CON CONTENUTO rende ambigua la riga.
    """
    trovati = []
    for chiave in CHIAVI_CODICE:
        grezzo = voce.get(chiave)
        if not _e_scalare(grezzo):
            # Un valore che non e' un codice non diventa un codice passando da
            # `str()`, ma non si fa nemmeno finta che la chiave non ci sia:
            # se porta qualcosa, la riga e' ambigua (vedi sopra).
            if grezzo:
                return ""
            continue
        codice = _codice(grezzo)
        if codice and codice not in trovati:
            trovati.append(codice)
    if len(trovati) == 1:
        return trovati[0]
    # Nessun nome, oppure due nomi in disaccordo: anonima in tutti e due i
    # casi.
    return ""


def _motivo(voce, stato_grezzo, stato):
    """Il perche' di un rifiuto: cosa non andava, e su quale campo.

    Il dettaglio e' TUTTO quel che la voce porta oltre l'identita' e lo stato,
    non la sola chiave `errors`: cosi' se Cdiscount la rinomina il motivo
    continua ad arrivare. E' la stessa scelta di `RispostaCdiscount.messaggio`,
    che infatti presta qui il suo `_dettaglio`.

    ⚠️ Un rifiuto senza motivo NON resta muto: dire «rifiutato» e basta manda
    a cercare un guasto senza dare da nessuna parte da cui cominciare, mentre
    dire «rifiutato e il rapporto non ha detto perche'» e' un'informazione —
    vuol dire che il motivo va cercato dalla parte di Cdiscount, non nostra.

    ⚠️ TUTTO quel che entra qui dentro ha un tetto, non il solo corpo. Lo
    stato grezzo e' un valore che arriva da fuori come tutti gli altri: uno
    `status` che porta dentro una pagina d'errore da 100.000 caratteri, su un
    pacchetto da 10.000 righe, farebbe centinaia di MB. Il taglio dello stato
    e' STRETTO apposta — sessanta caratteri — perche' il dato utile e' il
    dettaglio, e una premessa lunghissima lo scaccerebbe.
    """
    resto = {c: v for c, v in voce.items()
             if c not in CHIAVI_CODICE and c != CHIAVE_STATO_VOCE}
    dettaglio = _dettaglio(resto)
    if not stato:
        premessa = ("il rapporto non dice lo stato di questa riga: si conta "
                    "come rifiutata")
    elif stato not in ESITI_RIFIUTATI:
        detto = stato_grezzo
        if len(detto) > MAX_STATO_NEL_MOTIVO:
            detto = detto[:MAX_STATO_NEL_MOTIVO] + SEGNO_TAGLIO
        premessa = ("stato «%s» non riconosciuto: si conta come rifiutato, "
                    "perche' contarlo riuscito toglierebbe la scheda dai "
                    "rinvii per sempre" % detto)
    else:
        premessa = ""
    pezzi = [uno for uno in (premessa, dettaglio) if uno]
    if pezzi:
        return _tetto(" — ".join(pezzi))
    return ("rifiutato senza motivo: il rapporto non dice quale campo, e da "
            "qui non si puo' sapere")


def _esito_voce(voce):
    """Il verdetto su un singolo prodotto: `{"esito": …, "motivo": …}`.

    ⚠️ Solo il valore documentato vale come successo; qualunque altro — uno
    stato assente, uno stato che non conosciamo, un nome che Cdiscount ha
    cambiato — si conta come RIFIUTATO. La direzione in cui si sbaglia e'
    tutto: un rifiuto sbagliato costa un rinvio, un successo sbagliato costa
    una scheda che sparisce dal catalogo e non torna piu'.
    """
    grezzo = _testo(voce.get(CHIAVE_STATO_VOCE))
    stato = grezzo.lower()
    if stato in ESITI_RIUSCITI:
        # Un successo non si porta dietro nessun motivo: non c'e' niente da
        # spiegare, e un motivo su una riga riuscita si legge come un guaio.
        return {"esito": RIUSCITO, "motivo": ""}
    return {"esito": RIFIUTATO, "motivo": _motivo(voce, grezzo, stato)}


def _piu_prudente(prima, nuovo):
    """Fra due verdetti sullo stesso prodotto, quello da tenere.

    ⚠️ SCELTA PRESA QUI, perche' la documentazione non dice cosa succeda se
    un rapporto nomina lo stesso prodotto due volte con esiti OPPOSTI. Vince
    il rifiuto, in qualunque ordine arrivino le due righe: tenere il successo
    toglierebbe la scheda dai rinvii per sempre sulla parola di meta'
    rapporto, tenere il rifiuto costa un invio ripetuto. E la scelta non
    dipende dall'ordine: se dipendesse, lo stesso rapporto letto con le righe
    scambiate darebbe due verdetti diversi.

    ⚠️ E ci si incarta UNA VOLTA SOLA. Rinfilando ogni contraddizione dentro
    la precedente il motivo cresce a ogni riga — misurato: 200 righe alterne
    sullo stesso codice facevano 10.224 caratteri, con una concatenazione che
    cresce col quadrato — e su un pacchetto da 10.000 righe sarebbe mezzo
    megabyte per UN prodotto. La notizia da dare e' una sola: «i verdetti si
    contraddicono, vince il rifiuto», e ripeterla non aggiunge niente.
    """
    if prima["esito"] == nuovo["esito"]:
        # Stesso verdetto due volte: non e' una notizia, si tiene il primo.
        return prima
    rifiuto = prima if prima["esito"] == RIFIUTATO else nuovo
    if rifiuto["motivo"].startswith(PREFISSO_CONTRADDIZIONE):
        # La contraddizione era gia' dichiarata: il verdetto e' gia' il piu'
        # prudente possibile e il motivo lo dice gia'.
        return rifiuto
    return {"esito": RIFIUTATO,
            "motivo": _tetto("%s: vince il rifiuto. Motivo del rifiuto: %s"
                             % (PREFISSO_CONTRADDIZIONE, rifiuto["motivo"]))}


def _verdetto(voce):
    """Una voce degli esiti ridotta alla forma canonica.

    ⚠️ Una voce MALFORMATA non e' una conferma. `riconcilia` puo' ricevere un
    dizionario composto a mano — o venuto da un giro precedente — e un valore
    che non si sa leggere si conta dal lato che costa un rinvio, non da quello
    che toglie la scheda dai rinvii per sempre. Ridurre tutto alla forma
    canonica qui permette a `_piu_prudente` di lavorare anche li'.
    """
    if isinstance(voce, dict) and voce.get("esito") in (RIUSCITO, RIFIUTATO):
        return {"esito": voce["esito"], "motivo": _testo(voce.get("motivo"))}
    return {"esito": RIFIUTATO,
            "motivo": _tetto("esito malformato (%r): non si sa leggere, e un "
                             "esito che non si sa leggere non e' una "
                             "conferma" % (voce,))}


def leggi_rapporto(corpo):
    """Lo stato del rapporto di un pacchetto e, se e' pronto, i suoi verdetti.

    Rende `(stato, {codice: {"esito": …, "motivo": …}})`, dove lo stato e' uno
    fra `PRONTO`, `IN_LAVORAZIONE` e `SCONOSCIUTO`.

    ⚠️ Gli esiti sono vuoti tutte le volte che lo stato non e' `PRONTO`, e
    guardare i soli esiti NON basta: un rapporto pronto e senza verdetti (un
    pacchetto che Cdiscount non ha riconosciuto) e un rapporto che non
    sappiamo leggere danno lo stesso dizionario vuoto e vogliono due decisioni
    opposte. Lo stato va guardato SEMPRE.

    Niente qui solleva: un rapporto malformato e' una notizia da leggere, non
    un giro da far morire.
    """
    if not isinstance(corpo, dict):
        # Un corpo illeggibile: il client rende gia' `corpo=None` quando la
        # risposta non e' JSON (la pagina d'errore di un proxy), ed e' proprio
        # il caso in cui NON si sa niente.
        return SCONOSCIUTO, {}
    stato = _testo(corpo.get("status")).lower()
    if stato in STATI_IN_LAVORAZIONE:
        # Il pacchetto e' ancora in coda da loro: non c'e' niente da leggere,
        # e non e' un guasto. Si ripassa. ⚠️ Ma non all'infinito: l'esito
        # scade in tre giorni, e il preavviso e' del Compito 11.
        return IN_LAVORAZIONE, {}
    if stato not in STATI_PRONTO:
        # Tutto il resto — uno stato assente, vuoto, o un nome che non
        # conosciamo (compreso un pacchetto rifiutato in blocco) — e' «non lo
        # so»: nessun verdetto, e qualcuno deve guardarlo.
        return SCONOSCIUTO, {}
    righe = corpo.get(CHIAVE_RISULTATI)
    if righe is None or righe is False:
        # ⚠️ Pronto ma SENZA la chiave dei risultati non e' «un pacchetto
        # senza prodotti»: e' un rapporto che non sappiamo leggere, e la causa
        # piu' probabile e' proprio quella dichiarata in testa al file — la
        # chiave si chiama diversamente. Un elenco VUOTO invece e' una
        # risposta vera, e infatti passa di sotto.
        return SCONOSCIUTO, {}
    if not isinstance(righe, (list, tuple)):
        # Un dizionario indicizzato per codice si leggerebbe «vuoto» ed e'
        # indistinguibile da un pacchetto senza prodotti. Non lo sappiamo.
        return SCONOSCIUTO, {}

    esiti = {}
    for voce in righe:
        if not isinstance(voce, dict):
            continue
        codice = _codice_voce(voce)
        if not codice:
            # ⚠️ Una voce ANONIMA non si attribuisce a nessuno, e non si
            # indovina a chi appartenga. Non contarla e' il lato prudente: il
            # prodotto che nominava resta senza verdetto, quindi «mancante»,
            # quindi lo si rimanda. Contarla — anche solo per far tornare i
            # numeri — coprirebbe una mancante vera.
            continue
        nuovo = _esito_voce(voce)
        prima = esiti.get(codice)
        esiti[codice] = nuovo if prima is None else _piu_prudente(prima, nuovo)
    return PRONTO, esiti


def riconcilia(mandati, esiti):
    """Quel che e' partito, confrontato con quel che il rapporto nomina.

    Rende `{"confermati": n, "rifiutati": n, "mancanti": [codici],
    "estranee": [codici]}`.

    ⚠️ IL CUORE, e il punto in cui si e' gia' sbagliato due volte in questa
    famiglia di lavori. Si lavora sull'INSIEME dei codici mandati, non sul
    numero delle voci lette: si guarda uno per uno se ogni prodotto partito e'
    tornato, e tutto cio' che non e' tornato finisce fra le MANCANTI. Contare
    le voci lascerebbe che una voce estranea, o lo stesso prodotto nominato
    due volte, pareggino il conto di una mancante — e un pacchetto dimezzato
    tornerebbe verde.

    ⚠️ «Mancante» non e' «rifiutato» e non e' «riuscito»: e' «Cdiscount non ha
    detto cosa ne ha fatto». Chi chiama non deve scrivere NIENTE su quelle
    righe, cosi' il giro dopo le rimanda — e vale sempre l'invariante

        confermati + rifiutati + len(mancanti)
            == len({_codice(uno) per uno in mandati})

    Il conto e' sui codici NORMALIZZATI, non su `set(mandati)`: `"AB "` e
    `"AB"` sono una scheda sola, perche' `corpo_scheda` normalizza prima di
    spedire e a Cdiscount ne e' arrivata una. Contarli due sarebbe la formula
    a mentire, non il codice — ma in nessuno dei due modi i confermati si
    gonfiano. Se l'invariante non torna, qualcuno e' sparito dai conti.

    Le `estranee` sono i codici che il rapporto nomina e che non erano nel
    pacchetto. Non entrano in nessun altro conto, ma si dicono: se ce ne sono,
    o si sta leggendo il rapporto di un ALTRO pacchetto, o i codici non
    corrispondono piu' — e in tutti e due i casi i numeri qui sopra non
    vogliono dire quello che sembrano.
    """
    if isinstance(mandati, (str, bytes, bytearray)):
        # ⚠️ Una stringa e' iterabile: senza questa guardia si riconcilierebbe
        # carattere per carattere e i conti sarebbero pura invenzione. Stessa
        # trappola gia' chiusa in `cdiscount_schede._immagini_valide`.
        # ⚠️ E i `bytes` sono peggio: iterandoli si ottengono NUMERI, cioe'
        # `b"AB"` diventa due codici mandati, «65» e «66» — conti inventati
        # senza nemmeno l'aria di esserlo.
        raise ValueError(
            "riconcilia(): i codici mandati sono %r, cioe' un solo codice "
            "invece di un elenco. Serve una lista." % (mandati,))
    try:
        # ⚠️ Un ValueError, non il TypeError che verrebbe da solo: e' il tipo
        # che tutto il connettore rispetta ed e' quello che chi chiama sa
        # catturare.
        grezzi = list(mandati or [])
    except TypeError:
        raise ValueError(
            "riconcilia(): i codici mandati sono %r, e non sono un elenco."
            % (mandati,))

    # I codici mandati, normalizzati e senza doppioni, NELL'ORDINE DI INVIO.
    # ⚠️ Lo stesso codice due volte e' UN prodotto: contarlo due volte
    # gonfierebbe i confermati. E l'ordine si conserva perche' chi legge il
    # rapporto ritrovi le righe dove le ha messe.
    codici = []
    attesi = set()
    for grezzo in grezzi:
        if not _e_scalare(grezzo):
            # ⚠️ Ci si ferma invece di inventare: `str({"a": 1})` darebbe il
            # codice `"{'a': 1}"`, che nessuno ha mai mandato, e i conti
            # tornerebbero su un prodotto che non esiste.
            raise ValueError(
                "riconcilia(): fra i codici mandati c'e' %r, che non e' un "
                "codice. L'elenco e' composto male." % (grezzo,))
        codice = _codice(grezzo)
        if codice in attesi:
            continue
        attesi.add(codice)
        codici.append(codice)

    # Gli esiti, con le chiavi normalizzate come i mandati. Un `esiti` che non
    # e' un dizionario vale come rapporto vuoto: nessun verdetto, tutti
    # mancanti — che e' il lato prudente.
    letti = {}
    if isinstance(esiti, dict):
        for chiave, voce in esiti.items():
            codice = _codice(chiave)
            verdetto = _verdetto(voce)
            prima = letti.get(codice)
            # ⚠️ Due chiavi che si normalizzano uguali (`"AB"` e `"AB "`) non
            # si sovrascrivono: vincerebbe l'ultima letta, cioe' il verdetto
            # dipenderebbe dall'ordine — l'esatto contrario della regola che
            # `_piu_prudente` fa rispettare dentro `leggi_rapporto`. Qui e'
            # fuori portata finche' gli esiti arrivano da li', ma questa
            # funzione e' pubblica e i conti li fa lei.
            letti[codice] = (verdetto if prima is None
                             else _piu_prudente(prima, verdetto))

    confermati = 0
    rifiutati = 0
    mancanti = []
    for codice in codici:
        if codice not in letti:
            # ⚠️ Partito e mai tornato. NON e' riuscito: e' senza verdetto.
            mancanti.append(codice)
            continue
        # I verdetti sono gia' in forma canonica (vedi `_verdetto`): solo un
        # RIUSCITO ben formato conta come conferma, e tutto il resto — una
        # voce malformata compresa — si conta dal lato che costa un rinvio
        # invece di una scheda persa.
        if letti[codice]["esito"] == RIUSCITO:
            confermati += 1
        else:
            rifiutati += 1

    # ⚠️ Le estranee si contano a parte e NON entrano nei conti di sopra: una
    # voce che nomina un prodotto mai mandato non pareggia niente.
    estranee = [codice for codice in letti if codice not in attesi]
    return {"confermati": confermati, "rifiutati": rifiutati,
            "mancanti": mancanti, "estranee": estranee}
