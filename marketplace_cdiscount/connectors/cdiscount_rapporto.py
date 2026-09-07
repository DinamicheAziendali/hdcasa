# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""L'esito di un pacchetto di schede, e la riconciliazione con quel che era
partito.

`POST /products-integration` accetta fino a 10.000 schede e risponde con un
numero di pacchetto: l'esito si va a ripescare dopo, e **scade in tre giorni**.
Qui si legge quel rapporto.

⚠️ **LA FORMA E' QUELLA MISURATA IL 2026-09-02** sull'account vero
(`docs/cdiscount-misurato-2026-09-02.md`), e non e' quella della prima
versione di questo file. Il rapporto NON ha uno stato del pacchetto: e' un
elenco paginato

    {"itemsPerPage": n, "items": [
        {"gtin": "...", "sellerProductReference": "HDC00041",
         "productReference": "AUC...", "packageId": "...",
         "status": "Refused" | "Validated" | "Integrated",
         "operationType": "Creation" | "Modification" | "Identical",
         "errors": [{"code", "field", "message", "type"}, ...],
         "warnings": [...], "infos": [...], "submissionDate": "..."}]}

in cui **ogni riga porta il suo verdetto**. L'involucro e' MISURATO; i nomi
delle chiavi di riga sono LETTI sulla documentazione riletta lo stesso giorno
(la chiamata che scrive non si e' potuta provare: non c'e' un sandbox).

⚠️ **E' il punto in cui questo modulo puo' mentire.** Un rapporto che nomina
meno prodotti di quanti ne sono partiti NON e' un successo: le righe non
nominate restano senza verdetto, e se le si conta come riuscite quella scheda
non verra' mai piu' rimandata — sparisce dal catalogo senza che nessuno lo
sappia. Da qui le regole di tutto il file:

1. `riconcilia` lavora sull'INSIEME dei codici, non sul conteggio delle voci.
2. «Non lo so» non e' «vuoto». Un corpo illeggibile, un elenco vuoto (Cdiscount
   ci sta ancora lavorando: misurato, risponde 200 e `items: []`) e un elenco
   con delle righe sono TRE cose diverse, e chi chiama non deve confonderle.
3. **`Validated` non e' un verdetto.** E' «ha passato i controlli, non e'
   ancora sul sito»: la scheda si ASPETTA, senza scrivere niente, e il giro
   dopo rilegge. Contarla riuscita la toglierebbe dai giri prima che esista;
   contarla rifiutata la farebbe rimandare mentre sta nascendo. ⚠️ Il
   significato esatto va confermato alla prima spedizione vera.
4. `operationType` e' la risposta alla domanda «esisteva gia'?» — quella che su
   Kaufland dava una schermata e qui arriva solo dopo aver mandato. Si
   conserva, verdetto per verdetto.

Questo file NON importa Odoo: si prova con tools/test_cdiscount_rapporto.py.
"""

# ⚠️ Doppia forma di import, e serve DAVVERO: dentro Odoo questi file sono un
# pacchetto (import relativo), mentre i test di tools/ mettono la sola cartella
# `connectors/` sul percorso (import assoluto).
try:
    from .cdiscount_client import SEGNO_TAGLIO, _dettaglio
    from .cdiscount_schede import _testo
except ImportError:  # eseguito fuori da Odoo, dai test di tools/
    from cdiscount_client import SEGNO_TAGLIO, _dettaglio
    from cdiscount_schede import _testo

# Lo stato del RAPPORTO. Tre valori distinti e tutti veri in senso booleano.
PRONTO = "pronto"
IN_LAVORAZIONE = "in_lavorazione"
SCONOSCIUTO = "sconosciuto"

# L'esito di un SINGOLO prodotto dentro un rapporto pronto.
RIUSCITO = "riuscito"
RIFIUTATO = "rifiutato"
# ⚠️ Il terzo: «Validated». Non e' un verdetto (regola 3): chi riconcilia lo
# conta fra le MANCANTI, cosi' nessuno scrive niente, e lo elenca a parte,
# cosi' il messaggio puo' dire «ci stanno lavorando» invece di «non la nomina».
ATTESA = "attesa"

# Cosa Cdiscount ha fatto della scheda (regola 4). I tre valori vengono dalla
# documentazione; uno che non conosciamo resta "" e NON cambia il verdetto.
CREAZIONE = "creazione"
MODIFICA = "modifica"
IDENTICA = "identica"
OPERAZIONI = {"creation": CREAZIONE, "modification": MODIFICA,
              "identical": IDENTICA}

# Le chiavi del corpo.
CHIAVE_RIGHE = "items"
# ⚠️ DUE nomi per il codice, e si provano tutti e due. Il primo e' quello con
# cui spediamo e che la documentazione del rapporto dichiara; il secondo era
# nella prima versione della documentazione e resta accettato — costa niente
# e protegge dal giorno in cui lo cambiano di nuovo. Stessa disciplina di
# `kaufland._leggi_esiti` (id_unit / unit_id).
CHIAVI_CODICE = ("sellerProductReference", "sellerProductRef")
CHIAVE_STATO_VOCE = "status"
CHIAVE_OPERAZIONE = "operationType"
CHIAVE_ERRORI = "errors"
# ⚠️ I confronti sono di UGUAGLIANZA, non di inizio-stringa: un ipotetico
# «IntegratedWithWarnings» non e' un successo, ed e' proprio il genere di riga
# che finirebbe in vetrina sbagliata senza che nessuno la rimandi.
ESITI_RIUSCITI = ("integrated",)
ESITI_ATTESA = ("validated",)
# Riconosciuti come rifiuti espliciti: cambia solo il MESSAGGIO, non il
# verdetto — tutto cio' che non e' un successo ne' un'attesa e' rifiutato.
ESITI_RIFIUTATI = ("refused",)
# Le chiavi di riga che identificano la scheda e il pacchetto: sono le stesse
# su ogni riga e nel motivo sarebbero solo rumore che nasconde il campo
# colpevole. Non entrano nel motivo.
CHIAVI_IDENTITA = ("gtin", "productReference", "packageId",
                   "submissionDate", "variantGroupReference")

# ⚠️ IL TETTO DEL MOTIVO COMPOSTO. `_dettaglio` tiene a bada il corpo chiave
# per chiave, ma non copre quel che ci mettiamo intorno. Un `status` che porta
# dentro una pagina d'errore HTML — cosa che capita davvero — su un pacchetto
# da 10.000 righe farebbe centinaia di MB.
TETTO_MOTIVO = 4000
CODA_MOTIVO = " %s (motivo troncato)" % SEGNO_TAGLIO
MAX_STATO_NEL_MOTIVO = 60
MAX_ERRORE_NEL_MOTIVO = 300
PREFISSO_CONTRADDIZIONE = (
    "il rapporto nomina questo prodotto piu' volte con esiti opposti")

# ⚠️ L'ordine di prudenza fra i tre verdetti, per `_piu_prudente`: il rifiuto
# batte tutto (costa un rinvio), l'attesa batte il successo (costa una
# rilettura), il successo e' l'unico che toglie la scheda dai giri per sempre
# e quindi vince solo su se stesso.
_PRUDENZA = {RIFIUTATO: 2, ATTESA: 1, RIUSCITO: 0}


def _tetto(testo):
    """Il motivo, mai piu' lungo di `TETTO_MOTIVO`, e se e' tagliato lo dice."""
    if len(testo) <= TETTO_MOTIVO:
        return testo
    return testo[:TETTO_MOTIVO - len(CODA_MOTIVO)] + CODA_MOTIVO


def _taglia(testo, quanto):
    testo = _testo(testo)
    if len(testo) > quanto:
        return testo[:quanto] + SEGNO_TAGLIO
    return testo


def _e_scalare(grezzo):
    """Se un valore puo' essere un codice prodotto.

    ⚠️ Un dizionario, una lista o un oggetto qualunque diventerebbero un
    codice INVENTATO passando da `str()`. Uno SKU e' testo (o un numero).
    """
    return grezzo is None or isinstance(grezzo, (str, int, float, bool))


def _codice(grezzo):
    """Il codice come lo si confronta: testo pulito, senza spazi ai bordi.

    ⚠️ Si tolgono SOLO gli spazi: le maiuscole NON si toccano. Abbassarle
    farebbe combaciare due SKU che in Odoo sono due prodotti diversi, cioe'
    darebbe per confermata una scheda sulla parola di un'altra.
    """
    return _testo(grezzo)


def _codice_voce(voce):
    """Il codice che una voce del rapporto nomina, o "" se e' anonima.

    ⚠️ Le due chiavi si provano IN SEQUENZA, ciascuna col suo controllo: con
    un solo `.get(chiave, altro)` una chiave presente ma NULLA coprirebbe
    l'altra che porta il codice buono.

    ⚠️ Se le due chiavi portano DUE CODICI DIVERSI, la riga e' ambigua e si
    tratta come anonima: restano mancanti tutti e due, cioe' si rimandano.
    Vale anche per un valore composto male CON CONTENUTO (una lista sotto
    l'altro nome). Il VUOTO invece (`[]`, `{}`, `""`, `None`, `False`) dice
    «questa chiave non c'e'».
    """
    trovati = []
    for chiave in CHIAVI_CODICE:
        grezzo = voce.get(chiave)
        if not _e_scalare(grezzo):
            if grezzo:
                return ""
            continue
        codice = _codice(grezzo)
        if codice and codice not in trovati:
            trovati.append(codice)
    if len(trovati) == 1:
        return trovati[0]
    return ""


def _operazione(voce):
    """Cosa Cdiscount ha fatto della scheda, in forma nostra, o ""."""
    grezzo = voce.get(CHIAVE_OPERAZIONE)
    if not _e_scalare(grezzo):
        return ""
    return OPERAZIONI.get(_testo(grezzo).lower(), "")


def _errori_in_chiaro(errori):
    """Gli errori di una riga come li scrive Cdiscount, riga per riga.

    La forma documentata e' un elenco di `{code, field, message, type}`: si
    scrive «campo: messaggio [codice]». Tutto cio' che non ha quella forma
    passa da `_dettaglio`, che sa troncare chiave per chiave, cosi' un errore
    scritto in un altro modo si legge lo stesso invece di sparire.
    """
    if isinstance(errori, (list, tuple)):
        pezzi = []
        altri = []
        for uno in errori:
            if isinstance(uno, dict):
                campo = _taglia(uno.get("field"), MAX_STATO_NEL_MOTIVO)
                messaggio = _taglia(uno.get("message"),
                                    MAX_ERRORE_NEL_MOTIVO)
                codice = _taglia(uno.get("code"), MAX_STATO_NEL_MOTIVO)
                testo = ": ".join(p for p in (campo, messaggio) if p)
                if codice:
                    testo = ("%s [%s]" % (testo, codice)) if testo else codice
                if testo:
                    pezzi.append(testo)
                    continue
            if uno is not None and uno != "":
                altri.append(uno)
        if altri:
            pezzi.append(_dettaglio({"altro": altri}))
        return "; ".join(pezzi)
    if errori is None or errori == "" or errori is False:
        return ""
    if isinstance(errori, dict):
        return _dettaglio(errori)
    return _taglia(errori, MAX_ERRORE_NEL_MOTIVO)


def _motivo(voce, stato_grezzo, stato):
    """Il perche' di un rifiuto: cosa non andava, e su quale campo.

    Gli errori si leggono per primi e in chiaro; poi tutto quel che la riga
    porta oltre l'identita', lo stato e l'operazione (avvisi compresi:
    sono la seconda cosa da correggere). Cosi' se Cdiscount rinomina una
    chiave il motivo continua ad arrivare.

    ⚠️ Un rifiuto senza motivo NON resta muto: dire «rifiutato e il rapporto
    non ha detto perche'» e' un'informazione.
    """
    errori = _errori_in_chiaro(voce.get(CHIAVE_ERRORI))
    resto = {c: v for c, v in voce.items()
             if c not in CHIAVI_CODICE and c not in CHIAVI_IDENTITA
             and c not in (CHIAVE_STATO_VOCE, CHIAVE_OPERAZIONE,
                           CHIAVE_ERRORI)}
    dettaglio = _dettaglio(resto) if resto else ""
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
    pezzi = [uno for uno in (premessa, errori, dettaglio) if uno]
    if pezzi:
        return _tetto(" — ".join(pezzi))
    return ("rifiutato senza motivo: il rapporto non dice quale campo, e da "
            "qui non si puo' sapere")


def _esito_voce(voce):
    """Il verdetto su un singolo prodotto: `{"esito", "motivo", "operazione"}`.

    ⚠️ Solo `Integrated` vale come successo e solo `Validated` come attesa;
    qualunque altro stato — assente, sconosciuto, o un nome della vecchia
    documentazione come «Success» — si conta come RIFIUTATO. Aspettare
    all'infinito uno stato che non conosciamo sarebbe lasciare scadere
    l'esito; contarlo riuscito sarebbe peggio.
    """
    grezzo = _testo(voce.get(CHIAVE_STATO_VOCE))
    stato = grezzo.lower()
    operazione = _operazione(voce)
    if stato in ESITI_RIUSCITI:
        return {"esito": RIUSCITO, "motivo": "", "operazione": operazione}
    if stato in ESITI_ATTESA:
        return {"esito": ATTESA, "motivo": "", "operazione": operazione}
    return {"esito": RIFIUTATO, "motivo": _motivo(voce, grezzo, stato),
            "operazione": operazione}


def _piu_prudente(prima, nuovo):
    """Fra due verdetti sullo stesso prodotto, quello da tenere.

    ⚠️ SCELTA PRESA QUI: vince il piu' prudente (`_PRUDENZA`), in qualunque
    ordine arrivino le righe. Se dipendesse dall'ordine, lo stesso rapporto
    letto con le righe scambiate darebbe due verdetti diversi.

    ⚠️ E ci si incarta UNA VOLTA SOLA: rinfilando ogni contraddizione dentro
    la precedente il motivo cresce col quadrato delle righe.
    """
    if prima["esito"] == nuovo["esito"]:
        return prima
    vincitore = (prima if _PRUDENZA[prima["esito"]]
                 >= _PRUDENZA[nuovo["esito"]] else nuovo)
    if vincitore["esito"] != RIFIUTATO:
        # Attesa contro riuscito: si aspetta, e non c'e' niente da
        # spiegare — al giro dopo il rapporto dira' l'ultima parola.
        return vincitore
    if vincitore["motivo"].startswith(PREFISSO_CONTRADDIZIONE):
        return vincitore
    return {"esito": RIFIUTATO,
            "motivo": _tetto("%s: vince il rifiuto. Motivo del rifiuto: %s"
                             % (PREFISSO_CONTRADDIZIONE, vincitore["motivo"])),
            "operazione": vincitore.get("operazione", "")}


def _verdetto(voce):
    """Una voce degli esiti ridotta alla forma canonica.

    ⚠️ Una voce MALFORMATA non e' una conferma: si conta dal lato che costa
    un rinvio. Un esito senza `operazione` (un giro precedente, un dizionario
    a mano) e' comunque buono: la chiave nuova non rende malformato il
    vecchio.
    """
    if isinstance(voce, dict) and voce.get("esito") in _PRUDENZA:
        return {"esito": voce["esito"], "motivo": _testo(voce.get("motivo")),
                "operazione": _testo(voce.get("operazione"))}
    return {"esito": RIFIUTATO,
            "motivo": _tetto("esito malformato (%r): non si sa leggere, e un "
                             "esito che non si sa leggere non e' una "
                             "conferma" % (voce,)),
            "operazione": ""}


def leggi_rapporto(corpo):
    """Lo stato del rapporto di un pacchetto e, se e' pronto, i suoi verdetti.

    Rende `(stato, {codice: {"esito", "motivo", "operazione"}})`, dove lo
    stato e' uno fra `PRONTO`, `IN_LAVORAZIONE` e `SCONOSCIUTO`.

    `corpo` e' il rapporto INTERO — tutte le pagine gia' unite da chi chiama
    — nella forma `{"items": [...]}`, oppure direttamente l'elenco delle
    righe (e' quel che `RispostaCdiscount.dati` rende).

    ⚠️ Gli esiti sono vuoti tutte le volte che lo stato non e' `PRONTO`, e
    guardare i soli esiti NON basta: lo stato va guardato SEMPRE.

    Niente qui solleva: un rapporto malformato e' una notizia da leggere, non
    un giro da far morire.
    """
    if isinstance(corpo, dict):
        righe = corpo.get(CHIAVE_RIGHE)
    elif isinstance(corpo, (list, tuple)):
        righe = corpo
    else:
        # Un corpo illeggibile: il client rende gia' `corpo=None` quando la
        # risposta non e' JSON (la pagina d'errore di un proxy).
        return SCONOSCIUTO, {}
    if not isinstance(righe, (list, tuple)):
        # ⚠️ Senza `items`, o con un `items` che non e' un elenco, non e' «un
        # pacchetto senza righe»: e' un rapporto che non sappiamo leggere —
        # compresa la forma VECCHIA (`status`/`results`) della prima
        # documentazione. Nessun verdetto.
        return SCONOSCIUTO, {}
    if not righe:
        # ⚠️ MISURATO: per un numero valido Cdiscount risponde 200 e `items`
        # vuoto finche' ci lavora, ed e' indistinguibile da «non conosco
        # questo pacchetto». Si aspetta: e' la scadenza a tre giorni a dire
        # quando smettere. Non e' un guasto, e non si scrive niente.
        return IN_LAVORAZIONE, {}

    esiti = {}
    for voce in righe:
        if not isinstance(voce, dict):
            continue
        codice = _codice_voce(voce)
        if not codice:
            # ⚠️ Una voce ANONIMA non si attribuisce a nessuno: il prodotto
            # che nominava resta senza verdetto, quindi mancante.
            continue
        nuovo = _esito_voce(voce)
        prima = esiti.get(codice)
        esiti[codice] = nuovo if prima is None else _piu_prudente(prima, nuovo)
    return PRONTO, esiti


def riconcilia(mandati, esiti):
    """Quel che e' partito, confrontato con quel che il rapporto nomina.

    Rende `{"confermati": n, "rifiutati": n, "mancanti": [codici],
    "estranee": [codici], "attese": [codici]}`.

    ⚠️ IL CUORE. Si lavora sull'INSIEME dei codici mandati, non sul numero
    delle voci lette: si guarda uno per uno se ogni prodotto partito e'
    tornato, e tutto cio' che non e' tornato finisce fra le MANCANTI.

    ⚠️ «Mancante» non e' «rifiutato» e non e' «riuscito»: chi chiama non deve
    scrivere NIENTE su quelle righe. Vale sempre l'invariante

        confermati + rifiutati + len(mancanti)
            == len({_codice(uno) per uno in mandati})

    ⚠️ Le `attese` (regola 3 in testa al file) stanno DENTRO le mancanti —
    cosi' non si scrive niente e il pacchetto resta aperto — e si elencano a
    parte perche' il messaggio possa distinguere «Cdiscount ci sta lavorando»
    da «Cdiscount non l'ha nominata». Un'estranea in attesa non e' un'attesa
    nostra.

    Le `estranee` sono i codici che il rapporto nomina e che non erano nel
    pacchetto: non entrano in nessun altro conto, ma si dicono.
    """
    if isinstance(mandati, (str, bytes, bytearray)):
        # ⚠️ Una stringa e' iterabile: senza questa guardia si riconcilierebbe
        # carattere per carattere. I `bytes` sono peggio: danno NUMERI.
        raise ValueError(
            "riconcilia(): i codici mandati sono %r, cioe' un solo codice "
            "invece di un elenco. Serve una lista." % (mandati,))
    try:
        grezzi = list(mandati or [])
    except TypeError:
        raise ValueError(
            "riconcilia(): i codici mandati sono %r, e non sono un elenco."
            % (mandati,))

    # I codici mandati, normalizzati e senza doppioni, NELL'ORDINE DI INVIO.
    codici = []
    attesi = set()
    for grezzo in grezzi:
        if not _e_scalare(grezzo):
            raise ValueError(
                "riconcilia(): fra i codici mandati c'e' %r, che non e' un "
                "codice. L'elenco e' composto male." % (grezzo,))
        codice = _codice(grezzo)
        if codice in attesi:
            continue
        attesi.add(codice)
        codici.append(codice)

    # Gli esiti, con le chiavi normalizzate come i mandati. Un `esiti` che non
    # e' un dizionario vale come rapporto vuoto: tutti mancanti.
    letti = {}
    if isinstance(esiti, dict):
        for chiave, voce in esiti.items():
            codice = _codice(chiave)
            verdetto = _verdetto(voce)
            prima = letti.get(codice)
            # ⚠️ Due chiavi che si normalizzano uguali non si sovrascrivono:
            # vince il piu' prudente, come dentro `leggi_rapporto`.
            letti[codice] = (verdetto if prima is None
                             else _piu_prudente(prima, verdetto))

    confermati = 0
    rifiutati = 0
    mancanti = []
    attese = []
    for codice in codici:
        if codice not in letti:
            mancanti.append(codice)
            continue
        esito = letti[codice]["esito"]
        if esito == RIUSCITO:
            confermati += 1
        elif esito == ATTESA:
            mancanti.append(codice)
            attese.append(codice)
        else:
            rifiutati += 1

    estranee = [codice for codice in letti if codice not in attesi]
    return {"confermati": confermati, "rifiutati": rifiutati,
            "mancanti": mancanti, "estranee": estranee, "attese": attese}
