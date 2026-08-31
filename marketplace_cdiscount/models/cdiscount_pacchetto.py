# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Il pacchetto di schede mandato a Cdiscount: LA COSA CHE SI PUO' PERDERE.

`POST /products-integration` accetta fino a 10.000 schede e risponde con un
`packageId`. L'esito non torna subito: si va a ripescare dopo, e **scade in
tre giorni**. Passati quei tre giorni non esiste piu' nessun modo di sapere
cosa Cdiscount abbia fatto di quelle schede — non c'e' un archivio, non c'e'
un secondo tentativo, non c'e' nessuno che ce lo dica.

⚠️ E' per questo che questo modello esiste: e' l'unico posto in cui vive il
numero del pacchetto. Perso quello, si e' persa la corrispondenza fra cio'
che e' partito e cio' che e' tornato — e siccome un pacchetto ne porta fino a
10.000, non e' una scheda a sparire ma un catalogo.
"""
from odoo import fields, models

# Gli stati di un pacchetto, e sono tre perche' i casi sono tre: sta ancora
# lavorando, l'abbiamo letto, sono passati i tre giorni.
#
# ⚠️ HANNO UN NOME, e non e' cosmesi. Il raccoglitore (Compito 10) cerca gli
# APERTI e scrive RACCOLTO, la scadenza (Compito 11) scrive SCADUTO: sono
# stringhe che vivono in due file diversi da questo, e due copie scritte a
# mano che divergono per una lettera non fanno rumore da nessuna parte —
# `search([("stato", "=", "apperto")])` non trova niente e il raccoglitore
# smette di raccogliere IN SILENZIO. E' la stessa ragione per cui
# `cdiscount.scheda` esporta `IN_ATTESA` invece di lasciarlo scritto due
# volte.
APERTO = "aperto"
RACCOLTO = "raccolto"
SCADUTO = "scaduto"

STATI_PACCHETTO = [
    (APERTO, "In attesa dell'esito"),
    (RACCOLTO, "Esito raccolto"),
    (SCADUTO, "Scaduto senza esito"),
]

# Cosa contiene un pacchetto. Oggi una voce sola: le offerte sono la Consegna
# 2 e avranno il loro giro asincrono, con la stessa forma e un'altra scadenza.
# ⚠️ Il raccoglitore filtra su QUESTO valore: il giorno in cui nascono i
# pacchetti di offerte, quelli non devono finire sotto
# `GET /products-integration-reports`, che e' il rapporto delle SCHEDE.
TIPO_SCHEDE = "schede"
TIPI_PACCHETTO = [(TIPO_SCHEDE, "Schede prodotto")]

# ⚠️ QUANTO PRIMA SI AVVISA UNA PERSONA, e perche' dodici ore e non tre
# giorni. L'avviso non serve a dire «e' partito qualcosa» — quello lo dice il
# registro dell'invio: serve a dire «QUESTO sta per diventare irrecuperabile,
# guardalo ADESSO». Avvisare all'inizio della finestra vorrebbe dire un avviso
# per ogni pacchetto mandato, cioe' un avviso che non distingue niente e che
# si impara a chiudere senza leggere. Dodici ore sono l'ultima mezza giornata
# utile: abbastanza perche' qualcuno ci arrivi, poco perche' l'avviso ci sia
# solo quando serve davvero.
#
# ⚠️ LO STESSO NUMERO STA SCRITTO A MANO IN `views/cdiscount_pacchetto_views.
# xml`, nel filtro «Scade entro 12 ore»: una vista non puo' importare una
# costante Python. Chi cambia questo valore deve cambiare anche quello, o il
# filtro mostrera' un insieme diverso da quello per cui sono partite le
# attivita' — e chi guarda la schermata credera' di vedere tutti gli avvisi
# mentre ne vede un pezzo.
ORE_AVVISO = 12


class CdiscountPacchetto(models.Model):
    """Un invio asincrono a Cdiscount, con la sua finestra di tre giorni."""

    _name = "cdiscount.pacchetto"
    # ⚠️ I DUE MIXIN SERVONO ALL'AVVISO DI SCADENZA, e non sono decorazione.
    # `mail.activity.mixin` porta `activity_schedule`, che e' il modo con cui
    # questa casa avvisa una persona (`centrivo.order.map` per un ordine non
    # importato, `centrivo.shipment` per una consegna in ritardo): l'avviso
    # compare nelle «Attivita'» di chi lo deve guardare, non in una chat che
    # nessuno apre. `mail.thread` e' l'altra meta' obbligata: quando la
    # persona segna l'attivita' come fatta, Odoo scrive il resoconto sul
    # record con `message_post` — che senza `mail.thread` non esiste, e
    # l'attivita' esploderebbe proprio nel momento in cui qualcuno prova a
    # chiuderla. Stessa coppia, e per la stessa ragione, di
    # `centrivo_shipment_tracking.centrivo.shipment`.
    #
    # ⚠️ E l'attivita' sta SUL PACCHETTO, non sul canale: e' il pacchetto ad
    # avere una scadenza, un numero e delle schede dentro, ed e' lui che si
    # apre cliccando l'attivita'. Con l'avviso sul canale, dieci pacchetti in
    # scadenza sarebbero dieci attivita' identiche su un record che non dice
    # quale sia quello da guardare. (Vedi il rapporto del Compito 11: il
    # brief chiedeva il canale, che pero' oggi non porta i due mixin.)
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "Cdiscount — pacchetto mandato, in attesa dell'esito"
    # ⚠️ Si ordina per SCADENZA, non per nascita, benche' la casa metta
    # di solito il piu' recente in cima. Qui la lista serve a non perdere
    # un pacchetto: la domanda e' «cosa scade prima», e con `nato_il desc`
    # dentro il filtro «Scade entro 12 ore» comparirebbe per primo il
    # pacchetto MENO urgente. (`scade_il` e' sempre `nato_il` + 3 giorni,
    # quindi e' lo stesso ordine rovesciato.)
    _order = "scade_il asc, id desc"
    # Non c'e' nessun campo `name`: il pacchetto SI CHIAMA col suo numero, ed
    # e' quello che deve comparire ovunque venga nominato (nel Many2one delle
    # schede, nelle attivita' del Compito 11, nel registro).
    _rec_name = "numero"

    channel_id = fields.Many2one(
        "centrivo.channel", string="Canale", required=True,
        ondelete="cascade", index=True)

    # Il `packageId` reso da Cdiscount. ⚠️ E' un Char e non un numero anche se
    # oggi sembra numerico: e' un identificativo, non una quantita', e un
    # identificativo letto come intero perde gli zeri iniziali e, oltre una
    # certa lunghezza, la precisione. Un numero di pacchetto sbagliato di una
    # cifra e' un esito perso.
    numero = fields.Char(
        string="Numero pacchetto", required=True, index=True, copy=False,
        help="Il `packageId` reso da Cdiscount. E' l'unico appiglio "
             "sull'esito, e l'esito scade in tre giorni.")

    tipo = fields.Selection(
        TIPI_PACCHETTO, string="Contenuto", required=True,
        default=TIPO_SCHEDE)

    nato_il = fields.Datetime(
        string="Mandato il", required=True, copy=False,
        help="Quando Cdiscount ha preso il pacchetto.")

    # ⚠️ SI SCRIVE ALLA NASCITA E NON SI RICALCOLA MAI. E' un campo normale,
    # non un `compute`, e la differenza e' tutta la ragione di questo modello:
    # un calcolato «adesso + tre giorni» rifarebbe il conto a ogni lettura,
    # spingerebbe la scadenza avanti di un pezzo ogni volta che qualcuno apre
    # la schermata, e IL PACCHETTO NON SCADREBBE MAI. La finestra e' dei tre
    # giorni da quando **Cdiscount** l'ha preso, non da quando lo guardiamo
    # noi. Chi trasformasse questo campo in un `compute` spegnerebbe in
    # silenzio tutto il Compito 11 — l'avviso e la chiusura — e nessun errore
    # comparirebbe da nessuna parte.
    scade_il = fields.Datetime(
        string="L'esito scade il", required=True, copy=False,
        help="Tre giorni dal momento in cui Cdiscount ha preso il pacchetto. "
             "Passata questa data l'esito non e' piu' recuperabile: le "
             "schede restano senza verdetto.")

    stato = fields.Selection(
        STATI_PACCHETTO, string="Stato", default=APERTO, required=True,
        copy=False,
        help="«In attesa» significa che il raccoglitore ci ripassera'. "
             "«Scaduto» significa che l'esito e' perso e va riguardato a "
             "mano.")

    # ⚠️ La bandiera dell'avviso, e serve perche' l'avviso sia UNO SOLO. Il
    # raccoglitore gira ogni mezz'ora: senza questa bandiera, un pacchetto
    # nelle sue ultime dodici ore genererebbe ventiquattro attivita' uguali, e
    # ventiquattro avvisi identici insegnano a chiudere gli avvisi senza
    # leggerli. Chi la valorizza e' il Compito 11, e solo se l'avviso e'
    # davvero partito.
    #
    # ⚠️ E SI VALORIZZA SOLO DOPO CHE L'AVVISO E' PARTITO DAVVERO, mai prima:
    # scriverla e poi vedere fallire la creazione dell'attivita' vorrebbe dire
    # che quel pacchetto non avvisera' MAI PIU', e nessuno se ne accorgerebbe
    # — la bandiera direbbe che l'avviso c'e' stato.
    avvisato_scadenza = fields.Boolean(
        string="Scadenza gia' avvisata", default=False, copy=False,
        help="Vero quando l'attività «il pacchetto sta per scadere» è già "
             "stata creata. Serve a non ripeterla: il raccoglitore passa "
             "ogni mezz'ora e le ultime %s ore sono %s passaggi."
             % (ORE_AVVISO, ORE_AVVISO * 2))

    # ⚠️ LA SECONDA BANDIERA, e ha la stessa regola della prima: si accende
    # solo dopo che l'avviso e' partito davvero. Dice che di QUESTO pacchetto
    # e' gia' stato detto a una persona che il suo rapporto non nomina nessuna
    # delle schede mandate — il sospetto di una chiave letta col nome
    # sbagliato.
    #
    # ⚠️ Serve una bandiera E NON BASTA la ricerca dell'attivita' per
    # sommario: un rapporto muto lascia il pacchetto APERTO, quindi il
    # raccoglitore lo ripesca ogni mezz'ora per tre giorni. Senza bandiera,
    # basta che qualcuno segni l'attivita' come fatta — in Odoo `action_
    # feedback` CANCELLA il record `mail.activity` — perche' mezz'ora dopo ne
    # nasca un'altra, e poi un'altra, fino alla scadenza. La bandiera dice
    # «di questo pacchetto l'ho gia' detto», che e' la cosa vera.
    avvisato_rapporto_muto = fields.Boolean(
        string="Rapporto muto gia' avvisato", default=False, copy=False,
        help="Vero quando è già stata creata l'attività «il rapporto di "
             "questo pacchetto non nomina nessuna scheda». È il sospetto di "
             "un campo letto col nome sbagliato, e si dice una volta sola "
             "per pacchetto.")

    # ⚠️ Come quello di `cdiscount.scheda`, e per la stessa ragione: e'
    # l'azienda del CANALE, non una scelta a se'. Un pacchetto appartiene
    # all'azienda che l'ha mandato, e senza questo campo le regole di record
    # multi-azienda non hanno niente su cui appoggiarsi — un secondo impianto
    # vedrebbe i pacchetti del primo. `store=True` perche' ci si filtra e ci
    # si raggruppa; `related` perche' non e' un dato in piu' ma lo stesso
    # dato, e due copie divergono al primo canale spostato di azienda.
    #
    # ⚠️ E' anche cio' che rende scrivibile la riga di registro del Compito
    # 10 senza rileggere il canale: `centrivo.job.log.company_id` la vuole.
    company_id = fields.Many2one(
        "res.company", string="Azienda",
        related="channel_id.company_id", store=True, index=True)

    _sql_constraints = [
        # ⚠️ Il numero e' unico PER CANALE, non in assoluto: due canali
        # Cdiscount diversi sono due venditori diversi, e niente garantisce
        # che Octopia non riusi la stessa numerazione. Il vincolo serve a
        # impedire il doppione sullo STESSO canale, che e' il caso vero: un
        # invio ritentato dopo un errore di rete scriverebbe due volte lo
        # stesso pacchetto, e il raccoglitore chiuderebbe il primo lasciando
        # il secondo aperto per sempre.
        ("uniq_canale_numero", "unique(channel_id, numero)",
         "Questo pacchetto è già registrato su questo canale."),
    ]
