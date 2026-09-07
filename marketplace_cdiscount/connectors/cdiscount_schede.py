# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Com'e' fatta una scheda Cdiscount, e quante ne stanno in un pacchetto.

Solo forma: qui non si parla con nessuno. Il corpo composto qui finisce dentro
`POST /products-integration`, che accetta da 1 a 10.000 schede per volta e
risponde con un `packageId` — l'esito si va a ripescare dopo, e scade in tre
giorni.

⚠️ **Un campo troppo lungo NON si taglia: si rifiuta.** Un titolo mozzo non
rompe niente, viene accettato da Cdiscount e finisce su un catalogo pubblico
con un nome a meta': nessuno se ne accorge, e a differenza di un rifiuto non
lascia traccia da nessuna parte. Fermarsi costa una riga da correggere,
tagliare costa una scheda sbagliata in vetrina per mesi.

⚠️ **Ogni rifiuto nomina il prodotto.** Un pacchetto porta fino a 10.000
schede: un ValueError che non dice QUALE riga e' inutile, e questa lezione e'
gia' stata pagata su Kaufland (vedi `kaufland_offerte.corpo_offerta`).

⚠️ I nomi dei campi seguono la documentazione «Product feature v2», riletta
il 2026-09-02 dopo la sonda sul vero (`docs/cdiscount-misurato-2026-09-02.md`):
`sellerProductReference`, `gtin`, `title`, `description`, `brand` (testo
libero), `categoryCode`, `sellerPictureUrls`. La chiamata che scrive non si e'
potuta misurare — non esiste un sandbox — quindi vanno riconfermati alla
prima spedizione vera.

Questo file NON importa Odoo: si prova con tools/test_cdiscount_schede.py.
"""

# I tetti dichiarati da Cdiscount per una scheda.
MAX_TITOLO = 132
MAX_DESCRIZIONE = 2000
# ⚠️ Il tetto di un pacchetto `POST /products-integration`. Sforarlo non fa
# passare le prime diecimila: fa rifiutare il pacchetto intero, e con
# l'asincrono il rifiuto si scopre solo andandolo a ripescare.
MAX_PER_PACCHETTO = 10000
# L'unico schema ammesso per le immagini. Cdiscount se le scarica da solo.
SCHEMA_IMMAGINI = "https://"


def _testo(valore):
    """Il valore come testo pulito.

    ⚠️ `False` vale come assente esattamente come `None`: in Odoo un campo
    `Char` non valorizzato si legge `False`, e senza questa guardia una marca
    non compilata partirebbe verso Cdiscount come la stringa «False» su tutte
    le schede del pacchetto.

    ⚠️ Il confronto e' sull'identita' (`is False`) e non sull'uguaglianza,
    perche' in Python `0 == False`: lo zero e' un valore buono per un codice.
    """
    if valore is None or valore is False:
        return ""
    return str(valore).strip()


def _chi(codice):
    """Come si chiama il prodotto dentro un messaggio d'errore."""
    riferimento = _testo(codice)
    return "Scheda %s" % (riferimento or "<senza riferimento venditore>")


def _campo_obbligatorio(valore, chi, nome, perche):
    """Il campo come testo, o un ValueError che dice a chi manca e perche'."""
    testo = _testo(valore)
    if not testo:
        raise ValueError("%s: manca %s. %s" % (chi, nome, perche))
    return testo


def _entro_il_limite(testo, limite, chi, nome):
    """Il testo, se ci sta. Altrimenti ci si ferma: non si taglia.

    ⚠️ Qui non c'e' nessun `[:limite]`, ed e' voluto. Questa e' la regola
    opposta a quella del dettaglio d'errore del client, dove si tronca per non
    riempire un registro: li' si tratta di un log, qui di cio' che leggono i
    clienti su un catalogo pubblico.
    """
    if len(testo) > limite:
        raise ValueError(
            "%s: %s e' lungo %d caratteri e il massimo e' %d. Non lo si "
            "accorcia da qui: un testo tagliato a meta' viene accettato da "
            "Cdiscount e finisce in vetrina cosi'. Va riscritto."
            % (chi, nome, len(testo), limite))
    return testo


def _immagini_valide(immagini, chi):
    """Gli indirizzi delle immagini, tutti HTTPS, o un errore che dice a chi.

    ⚠️ E' lo stesso ostacolo gia' incontrato su Kaufland: un'immagine che il
    marketplace non riesce a scaricare fa cadere la scheda **senza dire che
    era l'immagine**. Da qui si vede solo lo schema; che l'indirizzo sia
    davvero pubblico e raggiungibile — e almeno 500x500 — non e' verificabile
    da codice, e resta la prima cosa da guardare quando una scheda viene
    rifiutata senza un motivo comprensibile.
    """
    if isinstance(immagini, str):
        # Una stringa e' iterabile: senza questa guardia si controllerebbe
        # carattere per carattere, e il rifiuto direbbe che «h» non e' HTTPS —
        # mandando a cercare il guasto nell'immagine, che invece va bene.
        raise ValueError(
            "%s: le immagini sono %r, cioe' un solo indirizzo invece di un "
            "elenco. Serve una lista." % (chi, immagini))
    if immagini is None or immagini is False:
        # Il campo non compilato in Odoo. Non e' un errore di tipo: e'
        # semplicemente una scheda senza immagini, e come tale viene rifiutata
        # qui sotto.
        immagini = []
    if not isinstance(immagini, (list, tuple)):
        # ⚠️ Un ValueError, non il TypeError che verrebbe da solo iterando un
        # numero: chi carica il file dei contenuti cattura ValueError riga per
        # riga per mostrare gli scarti, e un TypeError gli farebbe morire
        # l'intero caricamento invece di scartare la riga sbagliata.
        raise ValueError(
            "%s: le immagini sono %r, e non sono un elenco di indirizzi."
            % (chi, immagini))

    indirizzi = []
    for posizione, grezzo in enumerate(immagini, start=1):
        uno = _testo(grezzo)
        # ⚠️ Un indirizzo vuoto NON si butta via in silenzio. La prima
        # immagine e' la COPERTINA: scartarne una fa scivolare avanti tutte le
        # altre, e il prodotto va in vetrina con la foto sbagliata senza che
        # resti traccia da nessuna parte. E' lo stesso danno silenzioso del
        # titolo tagliato, spostato su un altro campo. Il vuoto arriva da
        # Odoo come `False`, che e' proprio il caso di un campo immagine non
        # compilato.
        if not uno:
            raise ValueError(
                "%s: l'immagine in posizione %d (la prima e' la 1) e' %r, "
                "cioe' vuota. Non la si toglie in silenzio: la prima immagine "
                "e' la copertina, e togliendone una le altre scivolano avanti."
                % (chi, posizione, grezzo))
        # Lo schema si confronta in minuscolo e SOLO in testa: «https» che
        # compare piu' avanti nell'indirizzo non conta. ⚠️ `_testo` ha gia'
        # tolto gli spazi ai bordi, e serve: un indirizzo con uno spazio in
        # coda supera questo controllo e poi Cdiscount non riesce a
        # scaricarlo — cioe' lo si scopre tre giorni dopo, ripescando l'esito
        # del pacchetto, e senza che nessuno dica che era l'immagine.
        if not uno.lower().startswith(SCHEMA_IMMAGINI):
            raise ValueError(
                "%s: l'immagine in posizione %d e' %r e non e' HTTPS. "
                "Cdiscount va a scaricarsela da solo, e se non ci riesce la "
                "scheda cade senza dire che era colpa dell'immagine."
                % (chi, posizione, uno))
        indirizzi.append(uno)

    if not indirizzi:
        raise ValueError(
            "%s: nessuna immagine. Cdiscount pretende almeno un'immagine "
            "HTTPS pubblica, e una scheda senza immagini non si vende "
            "comunque." % chi)
    return indirizzi


def corpo_scheda(codice, gtin, titolo, descrizione, immagini, categoria,
                 marca):
    """Il corpo di una scheda dentro `POST /products-integration`.

    `codice` e' il nostro riferimento venditore (lo SKU), ed e' anche il nome
    con cui il prodotto compare in ogni rifiuto qui sotto.
    """
    chi = _chi(codice)
    _campo_obbligatorio(
        codice, chi, "il riferimento venditore",
        "E' il nostro SKU, ed e' cio' che lega la scheda all'offerta: senza, "
        "la scheda non e' ricollegabile a niente.")
    _campo_obbligatorio(
        gtin, chi, "il GTIN",
        "Non esiste una ricerca per GTIN prima di mandare: se manca, non c'e' "
        "nessun modo di sapere se la scheda esiste gia'.")
    titolo_pulito = _entro_il_limite(
        _campo_obbligatorio(titolo, chi, "il titolo",
                            "E' il nome che legge il cliente."),
        MAX_TITOLO, chi, "il titolo")
    descrizione_pulita = _entro_il_limite(
        _campo_obbligatorio(descrizione, chi, "la descrizione",
                            "Cdiscount la pretende su una scheda nuova."),
        MAX_DESCRIZIONE, chi, "la descrizione")
    # ⚠️ La categoria dev'essere di **LIVELLO 3**, e questo da qui NON si puo'
    # verificare: una categoria di primo livello ha la stessa forma — sei
    # caratteri — supera qualunque controllo scrivibile qui, e viene rifiutata
    # da loro a pacchetto gia' mandato, cioe' quando l'esito si va a ripescare.
    # Si controlla solo che ci sia. Il livello lo garantisce chi la sceglie
    # sulla scheda canale, leggendola da `GET /categories`.
    categoria_pulita = _campo_obbligatorio(
        categoria, chi, "la categoria",
        "Cdiscount rifiuta le schede senza categoria, e la vuole di LIVELLO "
        "3: una di primo livello ha la stessa forma e viene rifiutata da "
        "loro.")
    marca_pulita = _campo_obbligatorio(
        marca, chi, "la marca",
        "Cdiscount la confronta col proprio elenco, e una scheda senza marca "
        "non passa.")
    indirizzi = _immagini_valide(immagini, chi)

    # ⚠️ I nomi di queste chiavi vengono dalla documentazione, non dal vero:
    # vedi l'avvertenza in testa al file prima di modificarli o di fidarsene.
    return {
        "sellerProductReference": _testo(codice),
        "gtin": _testo(gtin),
        "title": titolo_pulito,
        "description": descrizione_pulita,
        "categoryCode": categoria_pulita,
        "brand": marca_pulita,
        # ⚠️ LETTO sulla documentazione ufficiale il 2026-09-02: le immagini
        # si chiamano `sellerPictureUrls` e portano l'indice esplicito, da 1.
        # La prima versione spediva `images`, un nome che Octopia non
        # conosce. L'ordine resta quello dato: l'indice 1 e' la copertina.
        "sellerPictureUrls": [{"index": posizione, "url": uno}
                              for posizione, uno in enumerate(indirizzi,
                                                              start=1)],
    }


def pacchetti(righe):
    """Le righe divise in pacchetti da `MAX_PER_PACCHETTO`.

    Nessuna riga da' nessun pacchetto: un pacchetto vuoto sarebbe una chiamata
    sprecata e un numero da sorvegliare per tre giorni senza motivo.
    """
    # Si materializza una volta sola: le righe possono arrivare da un
    # generatore o da un recordset Odoo, e le fette qui sotto non funzionano
    # su un generatore.
    # ⚠️ Un ValueError, non il TypeError che verrebbe da solo: e' il tipo che
    # rispetta tutto il resto del file, ed e' quello che chi carica le righe
    # sa catturare.
    try:
        elenco = list(righe)
    except TypeError:
        raise ValueError(
            "pacchetti(): le righe sono %r, e non sono un elenco di schede."
            % (righe,))
    return [elenco[inizio:inizio + MAX_PER_PACCHETTO]
            for inizio in range(0, len(elenco), MAX_PER_PACCHETTO)]
