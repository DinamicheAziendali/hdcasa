# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Il file dei contenuti: titoli, descrizioni e immagini che Odoo non ha.

I testi di una scheda Cdiscount vengono da **Shopify, non da Odoo**: Shopify
li ha piu' completi e le sue immagini sono gia' pubbliche, cioe' scaricabili
da Cdiscount. Odoo quei testi non li ha, quindi entrano da un file CSV
composto fuori.

⚠️ **Il francese e' il vero collo di bottiglia.** Dei 604 prodotti del
perimetro solo 257 hanno un nome francese: le altre 347 schede semplicemente
non si possono comporre. Questo file NON risolve il problema — non traduce
niente, non indovina niente — deve solo dire con chiarezza CHI manca. Da qui
le due regole che tengono in piedi tutto il modulo:

1. **Gli scarti si dicono, non si buttano.** Ogni riga che non entra produce
   uno scarto che nomina il CODICE e il NUMERO DI RIGA. Una riga sparita in
   silenzio e' una scheda che non arrivera' mai sul catalogo, e nessuno sapra'
   perche': con 347 prodotti gia' assenti per il francese, una riga in meno
   non si distingue dal rumore di fondo se non la si nomina.
2. **Un codice ripetuto non diventa un errore tecnico a valle.** Sulla
   piattaforma esterna il doppione finiva in un `IntegrityError` e in una
   pagina d'errore, e nel gemello peggiore il rollback si portava via la
   traccia di un pacchetto GIA' accettato dal marketplace — cioe' si perdeva
   la prova di un lavoro riuscito per colpa di una riga scritta due volte.
   Qui il doppione si scarta con un messaggio, e **vince il primo**.

⚠️ Gli scarti si **restituiscono**, non si registrano: questo file non conosce
Odoo, e chi lo chiama decide se scriverli in un registro, mostrarli a video o
allegarli a un rapporto.

⚠️ **E NESSUN MESSAGGIO NOMINA QUESTA FUNZIONE.** Portavano tutti il prefisso
«leggi_csv(): », ed e' stato tolto. Chi legge queste frasi non e' un
programmatore: e' la persona che ha appena caricato il file e ha il foglio di
calcolo aperto di fianco. Un nome di funzione davanti alla frase le dice che
il guasto e' nel programma — cioe' che non c'e' niente da fare qui — proprio
mentre e' nell'unico istante in cui puo' correggere la colonna sbagliata in
trenta secondi. Il resto della frase gia' dice cosa e' successo e cosa fare:
il prefisso toglieva soltanto. (`tools/test_cdiscount_contenuti.py` pinna che
non torni.)

⚠️ Il tipo del rifiuto e' un contratto: qui si solleva **solo ValueError**, e
solo per cio' che rende illeggibile il FILE INTERO (le intestazioni, la
codifica, una virgoletta mai chiusa). Tutto cio' che riguarda una singola riga
diventa uno scarto, mai un'eccezione. E' la stessa regola di
`cdiscount_schede`, che infatti cattura ValueError riga per riga: un tipo
diverso farebbe morire l'intero caricamento invece di scartare la riga
sbagliata. ⚠️ E il contratto vale ANCHE per cio' che solleva il modulo `csv`:
`csv.Error` NON e' un ValueError, e lasciandolo passare chi cattura ValueError
non prende niente — in Odoo diventa una pagina rossa invece di uno scarto.

⚠️ **UNA VIRGOLETTA MAI CHIUSA INGHIOTTE IL RESTO DEL FILE, e in silenzio.**
E' il difetto piu' grave che questo file possa avere, ed e' stato misurato: su

    HDC1;Cabine;"Une cabine 70x70;https://a/1.jpg     <- aperta e mai chiusa
    HDC2;...
    HDC3;...
    HDC4;...

il modulo `csv` non solleva NIENTE — si mangia le quattro righe in un record
solo e le rende come un unico campo lunghissimo. Prima della correzione qui si
otteneva UNA riga scartata («ha 3 campi invece di 4», alla riga 5, accusando
HDC1) e nessuna traccia di HDC2, HDC3 e HDC4. Tre bugie in un colpo: la
vittima al posto della causa, un motivo falso, e l'ultima riga risucchiata al
posto di quella dove la virgoletta si apre.

⚠️ E morde davvero: le descrizioni francesi che vengono da Shopify sono piene
di virgolette — pollici, citazioni, HTML — e con 347 prodotti gia' assenti per
il francese nessuno noterebbe altri 600 spariti. Da qui `_virgoletta_spaiata`,
che ripercorre il testo con la stessa regola del modulo `csv` PRIMA di leggerlo
e si ferma nominando la riga dove la virgoletta si apre. Non e' un conteggio
di virgolette: contarle rifiuterebbe un titolo con un pollice dentro
(«Cabine 70" x 70»), che invece e' legittimo.

Questo file NON importa Odoo: si prova con tools/test_cdiscount_contenuti.py.
"""
import csv
import io

# ⚠️ Doppia forma di import, e serve DAVVERO: dentro Odoo questi file sono un
# pacchetto (import relativo), mentre i test di tools/ mettono la sola cartella
# `connectors/` sul percorso (import assoluto). Si riusa `_testo`, che conosce
# la trappola del `False` di Odoo e toglie gli spazi ai bordi.
try:
    from .cdiscount_client import SEGNO_TAGLIO
    from .cdiscount_schede import _testo
except ImportError:  # eseguito fuori da Odoo, dai test di tools/
    from cdiscount_client import SEGNO_TAGLIO
    from cdiscount_schede import _testo

# Le colonne attese, nell'ordine in cui e' comodo scriverle. ⚠️ L'ordine qui
# e' una PREFERENZA, non un obbligo: vedi `_colonne`.
INTESTAZIONI = ("codice", "titolo", "descrizione", "immagini")

# Il separatore delle colonne. ⚠️ E' il punto e virgola, non la virgola: la
# virgola separa le IMMAGINI dentro la loro colonna, e i due usi non si
# possono scambiare senza che ogni riga con piu' di un'immagine si spezzi in
# colonne che non esistono.
SEPARATORE = ";"
SEPARATORE_IMMAGINI = ","

# Il segno che Excel mette in testa a un CSV salvato in UTF-8. Non e' un dato:
# senza toglierlo la prima intestazione si chiamerebbe «﻿codice» e il file
# INTERO verrebbe rifiutato per un carattere che nessuno vede.
BOM = "\ufeff"

# Quanto di un codice finisce dentro uno scarto. ⚠️ Un campo di un CSV e' un
# valore che arriva da fuori come tutti gli altri: su un file da migliaia di
# righe un codice che porta dentro una pagina intera farebbe megabyte di
# scarti. E' la lezione gia' pagata sul dettaglio d'errore del client.
MAX_CODICE_NELLO_SCARTO = 60


def _pulisci(testo):
    """Il testo del file, pronto da leggere, o un ValueError che dice perche'.

    ⚠️ Si accettano anche i `bytes`, e non e' pigrizia: dentro Odoo un
    allegato arriva in binario, e chi lo carica non deve indovinare la
    codifica. La codifica e' UTF-8 (`utf-8-sig`, che si mangia il BOM di
    Excel), e se il file non e' UTF-8 ci si ferma con un ValueError che dice
    cosa fare — non si legge «alla meno peggio» sostituendo i caratteri
    illeggibili, perche' un accento francese diventato «?» finirebbe cosi'
    com'e' su un catalogo pubblico, e questo modulo esiste proprio per i testi
    francesi.
    """
    if isinstance(testo, (bytes, bytearray)):
        try:
            testo = bytes(testo).decode("utf-8-sig")
        except UnicodeDecodeError as errore:
            # ⚠️ Un ValueError, non l'UnicodeDecodeError che verrebbe da solo:
            # e' il tipo che tutto il connettore rispetta. (UnicodeDecodeError
            # E' un ValueError, ma il messaggio da solo non dice cosa fare.)
            raise ValueError(
                "Il file non e' in UTF-8 (%s). Va risalvato in "
                "UTF-8: leggerlo lo stesso sostituirebbe gli accenti "
                "francesi con dei punti interrogativi, e finirebbero cosi' "
                "sul catalogo." % errore)
    elif not isinstance(testo, str):
        raise ValueError(
            "Il contenuto e' %r, e non e' il testo di un file "
            "CSV." % (testo,))
    # Un BOM sopravvive anche a chi ci passa gia' del testo decodificato male.
    # ⚠️ Si toglie SOLO in testa: un BOM in mezzo a un titolo francese e' un
    # carattere del valore, e cancellarlo ovunque lo modificherebbe in
    # silenzio — proprio il genere di ritocco muto che questo file vieta.
    if testo.startswith(BOM):
        testo = testo[len(BOM):]
    if "\x00" in testo:
        # ⚠️ Un byte NUL passa la decodifica UTF-8 (e' un carattere valido) e
        # poi fa sollevare al modulo `csv` un «line contains NUL», che NON e'
        # un ValueError. La causa vera e' quasi sempre un export UTF-16 o
        # UTF-32 SENZA BOM: i suoi byte di riempimento si leggono come NUL.
        # Meglio dirlo qui, col nome della malattia invece del sintomo.
        raise ValueError(
            "Il file contiene dei byte nulli. Quasi sempre vuol "
            "dire che e' stato salvato in UTF-16 o UTF-32 senza BOM e non in "
            "UTF-8: va risalvato in UTF-8.")
    return testo


def _virgoletta_spaiata(testo):
    """La riga dove si apre una virgoletta che non si chiude piu', o 0.

    ⚠️ Si ripercorre il testo con la STESSA regola del modulo `csv`, non
    contando le virgolette. Contarle sarebbe piu' corto e sbagliato: una
    descrizione francese con un pollice dentro — «Cabine 70" x 70» — ha un
    numero dispari di virgolette ed e' perfettamente leggibile, perche' una
    virgoletta apre un campo SOLO se sta all'inizio del campo. Rifiutarla
    bloccherebbe un file buono, e su questo modulo un blocco falso costa
    quanto un silenzio.

    La regola, identica a quella di `csv.reader` con le impostazioni
    predefinite: una virgoletta all'inizio di un campo lo apre; dentro un
    campo aperto, due virgolette di fila sono una virgoletta scritta, e una
    sola lo chiude; ovunque altro la virgoletta e' un carattere come gli
    altri. Se alla fine del testo si e' ancora dentro un campo aperto, tutto
    quello che seguiva la virgoletta e' stato inghiottito.
    """
    riga = 1
    aperta_a = 0
    inizio_campo = True
    dentro = False
    posizione = 0
    quanti = len(testo)
    while posizione < quanti:
        carattere = testo[posizione]
        if dentro:
            if carattere == '"':
                if testo[posizione + 1:posizione + 2] == '"':
                    # Due di fila: e' una virgoletta scritta dentro il valore.
                    posizione += 2
                    continue
                dentro = False
                inizio_campo = False
            elif carattere in "\r\n":
                # Un a capo DENTRO le virgolette e' legittimo (una descrizione
                # su piu' righe), ma la riga avanza lo stesso: serve a dire
                # dove si e' aperta quella che non si chiude.
                if (carattere == "\r"
                        and testo[posizione + 1:posizione + 2] == "\n"):
                    posizione += 1
                riga += 1
            posizione += 1
            continue
        if carattere == '"' and inizio_campo:
            dentro = True
            aperta_a = riga
            inizio_campo = False
        elif carattere == SEPARATORE:
            inizio_campo = True
        elif carattere in "\r\n":
            if (carattere == "\r"
                    and testo[posizione + 1:posizione + 2] == "\n"):
                posizione += 1
            riga += 1
            inizio_campo = True
        else:
            # ⚠️ Uno spazio conta come contenuto: ` "abc"` NON e' un campo fra
            # virgolette per il modulo `csv`, e non deve esserlo nemmeno qui.
            inizio_campo = False
        posizione += 1
    return aperta_a if dentro else 0


def _colonne(grezze):
    """Le intestazioni lette, o un ValueError che dice quali servono.

    ⚠️ **L'ORDINE DELLE COLONNE NON CONTA, il loro nome si'.** Il file lo
    compone chi esporta da Shopify, e l'ordine delle colonne di un export non
    e' il nostro: rifiutare un file buono solo perche' il titolo viene prima
    del codice sarebbe un blocco senza nessun guadagno. I valori si prendono
    per NOME, quindi non possono finire nel campo sbagliato.

    ⚠️ Maiuscole e spazi attorno al nome sono formattazione, non sostanza —
    Excel scrive «Codice» da solo — e si perdonano. E' la stessa scelta gia'
    fatta sullo stato di un rapporto. Sui VALORI invece le maiuscole non si
    toccano mai: li' sono un dato.

    ⚠️ Una colonna in piu' o in meno FERMA il file, e il confronto e' sugli
    elenchi ordinati, non sugli insiemi: un insieme lascerebbe passare
    «codice;codice;titolo;immagini» — quattro colonne di cui una ripetuta e
    una mancante — e la descrizione arriverebbe vuota su ogni riga.
    """
    pulite = [_testo(una).lower() for una in grezze]
    if sorted(pulite) != sorted(INTESTAZIONI):
        raise ValueError(
            "Le intestazioni sono %s e non si sanno leggere. "
            "Servono esattamente queste quattro colonne, separate da «%s», in "
            "qualunque ordine: %s."
            % (pulite or "assenti", SEPARATORE, SEPARATORE.join(INTESTAZIONI)))
    return pulite


def _corto(codice):
    """Il codice dentro uno scarto: nominato, ma non infinito."""
    if len(codice) > MAX_CODICE_NELLO_SCARTO:
        return codice[:MAX_CODICE_NELLO_SCARTO] + SEGNO_TAGLIO
    return codice


def _scarto(numero, codice, motivo):
    """Uno scarto: la riga, il codice, e il perche'. Sempre tutti e tre.

    ⚠️ Il numero di riga e il codice non sono decorazione. Un file di contenuti
    ha migliaia di righe e chi lo corregge lavora su un foglio di calcolo: uno
    scarto che non dice a QUALE riga andare, o di QUALE prodotto parla, e'
    indistinguibile da un silenzio — e il silenzio e' esattamente cio' che
    questo modulo esiste per non produrre.
    """
    chi = "codice «%s»" % _corto(codice) if codice else "senza codice"
    return "riga %d, %s: %s" % (numero, chi, motivo)


def _immagini(grezzo):
    """Gli indirizzi di una colonna «immagini», spezzati sulla virgola.

    ⚠️ Un pezzo VUOTO non si butta via. `cdiscount_schede._immagini_valide`
    rifiuta l'immagine vuota nominandone la posizione, e lo fa apposta: la
    prima immagine e' la copertina, e toglierne una fa scivolare avanti tutte
    le altre, mandando il prodotto in vetrina con la foto sbagliata senza che
    resti traccia da nessuna parte. Toglierla QUI sarebbe lo stesso danno,
    solo commesso un passo prima e senza nemmeno un errore.

    ⚠️ La colonna interamente vuota e' un'altra cosa: e' «questo prodotto non
    ha immagini», e rende `[]` — cosi' la scheda viene rifiutata piu' avanti
    con «nessuna immagine», che e' la notizia vera, invece che con «l'immagine
    in posizione 1 e' vuota», che manderebbe a cercare un guasto inesistente.
    """
    testo = _testo(grezzo)
    if not testo:
        return []
    return [pezzo.strip() for pezzo in testo.split(SEPARATORE_IMMAGINI)]


def leggi_csv(testo):
    """Le righe del file dei contenuti, e tutto cio' che non e' entrato.

    Rende `(righe, scarti)`, dove ogni riga e'
    `{"codice": …, "titolo": …, "descrizione": …, "immagini": [indirizzi]}` e
    ogni scarto e' una frase che nomina la riga e il codice.

    Solleva `ValueError` solo per cio' che rende illeggibile il file intero:
    la codifica e le intestazioni. Una riga sbagliata non ferma le altre.
    """
    testo = _pulisci(testo)
    # ⚠️ PRIMA di leggere: una virgoletta mai chiusa si mangia tutto quel che
    # segue senza sollevare niente, e i prodotti inghiottiti non si possono
    # nemmeno nominare uno per uno — sono spariti dentro un campo solo. Non
    # essendo enumerabili non possono diventare scarti, e allora il file NON
    # si legge affatto: meglio ricaricarlo tutto una volta corretto che
    # aggiornare mezzo catalogo credendo di averlo aggiornato tutto.
    # La scansione parte solo se una virgoletta c'e' davvero: la stragrande
    # maggioranza dei file non ne ha nemmeno una.
    if '"' in testo:
        aperta_a = _virgoletta_spaiata(testo)
        if aperta_a:
            raise ValueError(
                "Alla riga %d c'e' una virgoletta aperta e mai "
                "chiusa, e da li' in poi il file non si e' letto: tutto quel "
                "che segue e' finito dentro quel valore. Non si carica "
                "niente, perche' i prodotti inghiottiti non si possono "
                "nemmeno elencare. Va chiusa la virgoletta alla riga %d (o "
                "raddoppiata, se doveva essere un pollice)." % (aperta_a,
                                                                aperta_a))
    # `newline=""` come vuole la documentazione del modulo `csv`: senza, un
    # a capo dentro un valore fra virgolette spezzerebbe la riga in due — e un
    # file con il solo «\r» come fine riga, che gli strumenti vecchi
    # producono ancora, farebbe sollevare `csv.Error` invece di leggersi.
    lettore = csv.reader(io.StringIO(testo, newline=""), delimiter=SEPARATORE)
    try:
        grezze = next(lettore)
    except StopIteration:
        raise ValueError(
            "Il file e' vuoto, manca perfino la riga delle "
            "intestazioni. Servono le colonne %s separate da «%s»."
            % (SEPARATORE.join(INTESTAZIONI), SEPARATORE))
    colonne = _colonne(grezze)
    dove = {nome: posizione for posizione, nome in enumerate(colonne)}
    dove_codice = dove["codice"]

    # Dove il codice e' entrato la prima volta: serve a dirlo nello scarto del
    # doppione. ⚠️ Ci finiscono SOLO i codici delle righe accettate: se la
    # prima occorrenza e' stata scartata (per esempio perche' aveva meno campi
    # della testata), la seconda non e' un doppione — di quel prodotto non e'
    # ancora entrato niente.
    visti = {}
    # ⚠️ `csv.Error` NON e' un ValueError: deriva direttamente da Exception, e
    # lasciandolo passare chi cattura ValueError attorno a `leggi_csv` non
    # prende NIENTE — in Odoo diventa una pagina rossa invece di uno scarto.
    # Ci arrivano davvero: un campo oltre i 128 KB (che e' come si presenta
    # una virgoletta spaiata su un file grande) e i byte nulli di un export
    # UTF-16. Tutti e due hanno gia' un controllo dedicato piu' sopra, che
    # spiega la causa; questo e' la rete sotto, per cio' che non abbiamo
    # previsto.
    try:
        righe, scarti = _leggi_righe(lettore, colonne, dove, dove_codice,
                                     visti)
    except csv.Error as errore:
        raise ValueError(
            "Il file non si e' potuto leggere fino in fondo "
            "(%s). Non si carica niente: quel che segue il punto in cui la "
            "lettura si e' fermata non e' stato nemmeno visto." % errore)

    if not righe and not scarti:
        # ⚠️ Un file con le sole intestazioni non e' un caricamento riuscito:
        # e' un file che qualcuno credeva pieno. Reso vuoto e in silenzio
        # sembrerebbe «zero prodotti da aggiornare», che e' una notizia
        # tranquilla; qui invece e' quasi sempre l'export sbagliato.
        scarti.append("il file non contiene nessuna riga oltre alle "
                      "intestazioni: non c'e' niente da caricare.")
    return righe, scarti


def _leggi_righe(lettore, colonne, dove, dove_codice, visti):
    """Le righe e gli scarti, riga per riga. Vedi `leggi_csv`."""
    righe = []
    scarti = []
    for grezza in lettore:
        # ⚠️ `line_num` e' la riga FISICA del file, non il numero d'ordine del
        # record: e' quella che chi corregge vede nel foglio di calcolo, e
        # resta giusta anche quando un valore fra virgolette occupa piu' righe.
        numero = lettore.line_num
        if not grezza:
            # Una riga completamente vuota non porta via niente — non c'e'
            # nessun prodotto da perdere — ma si dice lo stesso: e' l'unico
            # modo perche' i conti fra le righe del file e le righe lette
            # tornino, e un file pieno di righe vuote e' un file composto male
            # di cui vale la pena accorgersi.
            scarti.append(_scarto(
                numero, "",
                "riga vuota, non c'e' niente da leggere. Si salta."))
            continue
        if len(grezza) != len(colonne):
            # ⚠️ NON si riempie il buco e NON si buttano i campi in piu'. Una
            # riga piu' corta di un campo fa scivolare tutto: la descrizione
            # finisce nella colonna delle immagini e il titolo in quella della
            # descrizione, e la scheda parte con i testi scambiati senza che
            # nessun controllo a valle possa accorgersene. Il codice, se sta
            # dov'e' atteso, si nomina lo stesso: serve a ritrovare la riga.
            codice = (_testo(grezza[dove_codice])
                      if dove_codice < len(grezza) else "")
            scarti.append(_scarto(
                numero, codice,
                "ha %d campi invece di %d. Non si completa e non si taglia: "
                "un campo di troppo o di meno fa scivolare i valori nelle "
                "colonne sbagliate, e i testi partirebbero scambiati."
                % (len(grezza), len(colonne))))
            continue

        codice = _testo(grezza[dove_codice])
        if not codice:
            # Senza codice la riga non e' attaccabile a nessun prodotto: non
            # c'e' modo di indovinare di chi siano quei testi.
            scarti.append(_scarto(
                numero, "",
                "manca il codice, e senza codice questi testi non si possono "
                "attaccare a nessun prodotto."))
            continue
        if codice in visti:
            # ⚠️ VINCE IL PRIMO, e il doppione si dice. Tenere l'ultimo
            # farebbe dipendere il contenuto della scheda dall'ordine delle
            # righe; farlo diventare un errore tecnico a valle e' proprio il
            # guasto da cui questa regola nasce.
            scarti.append(_scarto(
                numero, codice,
                "questo codice era gia' alla riga %d, e vale quella. Un "
                "codice ripetuto non si lascia arrivare piu' in la': a valle "
                "diventa un errore tecnico, e un errore tecnico si porta via "
                "anche il lavoro gia' riuscito." % visti[codice]))
            continue

        visti[codice] = numero
        righe.append({
            "codice": codice,
            "titolo": _testo(grezza[dove["titolo"]]),
            "descrizione": _testo(grezza[dove["descrizione"]]),
            "immagini": _immagini(grezza[dove["immagini"]]),
        })
    return righe, scarti
