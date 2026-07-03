# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Trasporti astratti: come si effettuano materialmente le chiamate.

Il trasporto è separato dal connettore: un connettore (es. BricoBravo) "sa cosa
chiedere", il trasporto "sa come chiederlo" (HTTP REST o file CSV su FTP). Così
la stessa logica di mapping può essere riusata cambiando solo il mezzo.

Questa è OSSATURA: i corpi sono minimi, con docstring chiari su cosa faranno.
La logica reale (chiamate, retry, parsing) sarà rifinita nel task successivo
testando sulla sandbox.
"""
import logging
import time

_logger = logging.getLogger(__name__)

# Codici di stato HTTP considerati "ritentabili" (errore lato server).
RETRYABLE_STATUS = (500, 502, 503, 504)


class TransportError(Exception):
    """Errore di rete/trasporto (timeout, connessione, ecc.).

    Sollevata quando la richiesta non arriva nemmeno a una risposta HTTP, così
    il chiamante (connettore) può loggarla in modo chiaro.
    """


class TransportResponse(object):
    """Risposta di trasporto normalizzata.

    Espone:
      - status_code : codice HTTP (int)
      - json        : corpo JSON parsato (dict/list) oppure None se non parsabile
      - text        : corpo grezzo come testo (fallback diagnostico)
    """

    def __init__(self, status_code, json=None, text=None):
        self.status_code = status_code
        self.json = json
        self.text = text

    @property
    def ok(self):
        return 200 <= (self.status_code or 0) < 300


class TransportBase(object):
    """Contratto base di un trasporto."""

    def __init__(self, base_url=None, timeout=30):
        self.base_url = (base_url or "").rstrip("/")
        self.timeout = timeout

    def request(self, *args, **kwargs):
        """Esegue una richiesta. Da implementare nelle sottoclassi concrete."""
        raise NotImplementedError


class RestTransport(TransportBase):
    """Trasporto per API REST (es. BricoBravo via header token / JWT).

    Predisposto per:
      - header di autenticazione (es. {"sh-token": <API_KEY>}), passati dal
        connettore che legge la chiave dal channel (MAI dal codice);
      - retry automatico sugli errori 5xx con backoff;
      - logging dell'esito su centrivo.job.log (a cura del connettore).
    """

    def __init__(self, base_url=None, default_headers=None, timeout=30,
                 max_retries=3, backoff=2):
        super().__init__(base_url=base_url, timeout=timeout)
        self.default_headers = default_headers or {}
        self.max_retries = max_retries
        self.backoff = backoff

    def request(self, method, path, headers=None, params=None, json=None):
        """Esegue una richiesta REST reale con retry sui 5xx.

        Comportamento:
          - compone l'URL come base_url + path (oppure usa `path` se è già un URL);
          - imposta Accept/Content-Type application/json + gli header passati
            (es. il token di auth dal connettore);
          - timeout di self.timeout secondi;
          - su risposta 5xx ritenta fino a max_retries con backoff incrementale
            (1s, 2s, 4s, ...); su 4xx NON ritenta;
          - su errore di rete (timeout/connessione) solleva TransportError;
          - ritorna una TransportResponse (status_code, json parsato, text grezzo).

        NB: la api_key/token NON viene mai loggata: nel debug stampiamo solo
        metodo e URL, mai gli header.
        """
        # `requests` è disponibile nell'immagine Odoo. Import locale per non
        # imporre la dipendenza all'import del modulo.
        import requests

        url = path if path.startswith("http") else "%s/%s" % (
            self.base_url, path.lstrip("/"))
        merged_headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        merged_headers.update(self.default_headers)
        merged_headers.update(headers or {})

        attempt = 0
        while True:
            attempt += 1
            try:
                # NB: non logghiamo gli header (contengono il token).
                _logger.debug("RestTransport %s %s (tentativo %s)",
                              method, url, attempt)
                resp = requests.request(
                    method, url,
                    headers=merged_headers,
                    params=params,
                    json=json,
                    timeout=self.timeout,
                )
            except requests.exceptions.RequestException as exc:
                # Errore di rete: ritenta se ci sono tentativi residui, altrimenti
                # solleva un errore chiaro per il chiamante.
                if attempt <= self.max_retries:
                    time.sleep(self.backoff ** (attempt - 1))
                    continue
                raise TransportError(
                    "Errore di rete verso %s: %s" % (url, exc))

            # Retry solo sui 5xx ritentabili.
            if resp.status_code in RETRYABLE_STATUS and attempt <= self.max_retries:
                time.sleep(self.backoff ** (attempt - 1))
                continue

            # Parsing JSON tollerante: se non è JSON, teniamo il testo grezzo.
            parsed = None
            try:
                parsed = resp.json()
            except ValueError:
                parsed = None
            return TransportResponse(
                status_code=resp.status_code,
                json=parsed,
                text=resp.text,
            )


class CsvSerializer(object):
    """Serializzatore CSV generico e riusabile (default separatore ';').

    Trasforma una lista di righe (dict o liste/tuple) in una stringa CSV con
    intestazioni passate dal chiamante, encoding UTF-8 (la stringa va poi
    codificata da chi la serve), gestione corretta di virgole, separatori e
    campi vuoti tramite il modulo `csv` della stdlib.

    NON sa nulla di alcun marketplace: la scelta di QUALI colonne e QUALI dati
    spetta al connettore concreto. Qui si serializza soltanto.
    """

    def __init__(self, delimiter=";"):
        self.delimiter = delimiter

    def serialize(self, headers, rows):
        """Ritorna la stringa CSV (intestazioni + righe).

        - `headers`: lista di nomi colonna (l'ordine è quello di output).
        - `rows`: iterabile di righe; ogni riga può essere un dict (lette per
          chiave d'intestazione) oppure una lista/tupla allineata a `headers`.
        Valori None/assenti diventano stringa vuota.
        """
        import csv
        import io

        buffer = io.StringIO()
        writer = csv.writer(
            buffer, delimiter=self.delimiter, lineterminator="\n",
            quoting=csv.QUOTE_MINIMAL)
        writer.writerow(list(headers))
        for row in rows:
            if isinstance(row, dict):
                writer.writerow([self._cell(row.get(h)) for h in headers])
            else:
                writer.writerow([self._cell(v) for v in row])
        return buffer.getvalue()

    @staticmethod
    def _cell(value):
        return "" if value is None else value


class CsvTransport(TransportBase):
    """Trasporto per feed CSV su FTP/SFTP.

    Usato nelle fasi v2/v3 di BricoBravo per gli EXPORT da Odoo verso il
    marketplace (giacenze e catalogo). Odoo resta la fonte: questi sono export,
    non import. OSSATURA: implementazione reale in un task dedicato.
    """

    def __init__(self, host=None, username=None, remote_dir="/", timeout=30):
        super().__init__(base_url=host, timeout=timeout)
        self.host = host
        self.username = username
        self.remote_dir = remote_dir

    def upload(self, filename, content):
        """Carica un file CSV sul server remoto (FTP/SFTP). Da implementare."""
        raise NotImplementedError(
            "CsvTransport.upload: export CSV (giacenze/catalogo) previsto v2/v3.")

    def download(self, filename):
        """Scarica un file dal server remoto. Da implementare se servirà."""
        raise NotImplementedError


class SftpTransport(TransportBase):
    """Trasporto SFTP (paramiko) per scaricare file da un server fornitore.

    Usato dal modulo centrivo_sync_fornitori per prelevare i file di catalogo
    (xlsx) e giacenze (csv) dei fornitori dropship. Auth semplice
    username/password. Sola lettura lato fornitore (download). I file hanno NOME
    FISSO in root dell'FTP: il path viene passato esplicitamente, nessuna logica
    "prendi il più recente".

    NB sicurezza: la password NON viene MAI loggata né inserita nei messaggi di
    errore. paramiko è importato LOCALMENTE (come `requests` in RestTransport):
    così il modulo si installa/carica anche se paramiko non è presente
    nell'immagine; l'errore scatta solo al momento del download reale, con un
    messaggio chiaro per l'amministratore.
    """

    def __init__(self, host=None, username=None, password=None, port=22,
                 timeout=30):
        super().__init__(base_url=host, timeout=timeout)
        self.host = host
        self.username = username
        self.password = password
        self.port = int(port or 22)

    def download(self, remote_path):
        """Scarica `remote_path` dal server SFTP e ne ritorna i byte grezzi.

        Solleva TransportError su qualunque problema (paramiko assente,
        connessione, autenticazione, file mancante), senza mai esporre la
        password.
        """
        try:
            import paramiko
        except ImportError:
            raise TransportError(
                "paramiko non è disponibile nell'immagine Odoo: necessario per "
                "il trasporto SFTP. Aggiungerlo alle dipendenze dell'immagine "
                "(es. pip install paramiko) e riavviare.")

        transport = None
        sftp = None
        try:
            transport = paramiko.Transport((self.host, self.port))
            transport.connect(username=self.username, password=self.password)
            sftp = paramiko.SFTPClient.from_transport(transport)
            with sftp.open(remote_path, "rb") as handle:
                return handle.read()
        except TransportError:
            raise
        except Exception as exc:
            # Mai includere la password nel messaggio.
            raise TransportError(
                "Errore SFTP verso %s:%s (file %s): %s" % (
                    self.host, self.port, remote_path, exc))
        finally:
            try:
                if sftp is not None:
                    sftp.close()
            except Exception:
                pass
            try:
                if transport is not None:
                    transport.close()
            except Exception:
                pass


class FtpTransport(TransportBase):
    """Trasporto FTP / FTPS-esplicito (ftplib, stdlib) per scaricare file.

    Alternativa a SftpTransport per i fornitori che espongono un FTP CLASSICO
    (porta 21) invece dell'SFTP/SSH (porta 22). Alcuni fornitori dropship (es.
    Cardinale) offrono solo FTP: questo trasporto colma quel caso senza
    dipendenze esterne (ftplib è nella stdlib Python).

    - `use_tls=False` → FTP in chiaro (le credenziali viaggiano non cifrate: usare
      solo se il fornitore non offre alternative);
    - `use_tls=True`  → FTPS ESPLICITO (AUTH TLS su porta 21): stessa porta
      dell'FTP ma con canale di controllo e dati cifrati (`prot_p`).

    Modalità PASSIVA (default di ftplib) per attraversare NAT/firewall. Timeout
    sul socket per non restare appesi se la porta non risponde. Sola lettura
    (download). NB sicurezza: la password NON viene MAI loggata né inserita nei
    messaggi di errore.
    """

    def __init__(self, host=None, username=None, password=None, port=21,
                 use_tls=False, timeout=30):
        super().__init__(base_url=host, timeout=timeout)
        self.host = host
        self.username = username
        self.password = password
        self.port = int(port or 21)
        self.use_tls = use_tls

    def download(self, remote_path):
        """Scarica `remote_path` dal server FTP e ne ritorna i byte grezzi.

        Solleva TransportError su qualunque problema (connessione, auth, file
        mancante), senza mai esporre la password.
        """
        import ftplib
        import io

        ftp = None
        try:
            ftp = (ftplib.FTP_TLS(timeout=self.timeout) if self.use_tls
                   else ftplib.FTP(timeout=self.timeout))
            ftp.connect(self.host, self.port)
            ftp.login(self.username or "anonymous", self.password or "")
            if self.use_tls:
                # Cifra anche il canale DATI (non solo il controllo).
                ftp.prot_p()
            buffer = io.BytesIO()
            ftp.retrbinary("RETR %s" % remote_path, buffer.write)
            return buffer.getvalue()
        except Exception as exc:
            # Mai includere la password nel messaggio.
            raise TransportError(
                "Errore FTP verso %s:%s (file %s): %s" % (
                    self.host, self.port, remote_path, exc))
        finally:
            if ftp is not None:
                try:
                    ftp.quit()
                except Exception:
                    try:
                        ftp.close()
                    except Exception:
                        pass
