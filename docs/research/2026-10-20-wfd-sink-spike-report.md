# WFD sink per Samsung DeX — report dello spike, decisioni e prossimi passi

**Data:** 2026-10-20  
**Repo:** `/home/gabdevele/Projects/DexOnLinux`  
**Branch:** `spike/wfd-sink`  
**Ultimo commit dello spike:** `487851c spike: bring up the P2P link after group start`  
**Stato sintetico:** lo spike funziona end-to-end con **NetworkManager congelato**; la convivenza con NetworkManager acceso è l'unico blocco funzionale rimasto.

## Indice

1. [Obiettivo](#1-obiettivo)
2. [Architettura dello spike](#2-architettura-dello-spike)
3. [Cosa è stato costruito](#3-cosa-è-stato-costruito)
4. [Cronologia del lavoro](#4-cronologia-del-lavoro)
5. [Cosa abbiamo osservato](#5-cosa-abbiamo-osservato)
6. [Il bug di NetworkManager](#6-il-bug-di-networkmanager-causa-vera-dei-deauth)
7. [La sessione di successo](#7-la-sessione-di-successo)
8. [Stato attuale](#8-stato-attuale)
9. [Decisioni prese](#9-decisioni-prese)
10. [Domande aperte e rischi](#10-domande-aperte-e-rischi)
11. [Opzioni per la convivenza con NetworkManager](#11-opzioni-per-la-convivenza-con-networkmanager)
12. [Prossimi passi](#12-prossimi-passi)
13. [Riproduzione dello spike](#13-riproduzione-dello-spike-stato-attuale)
14. [Ambiente e riferimenti](#14-ambiente-e-riferimenti)
15. [Sintesi finale](#15-sintesi-finale)

Etichette usate nel documento:

- `[OSSERVATO]` — verificato su questa macchina o sul telefono.
- `[SORGENTE]` — verificato leggendo il sorgente della versione installata/upstream.
- `[DA TESTARE]` — ipotesi o comportamento non ancora validato sul campo.
- `[DECISIONE]` — scelta presa per il progetto.

---

## 1. Obiettivo

Sostituire miraclecast con un **WFD sink autonomo** per Samsung DeX che:

- usi il **wpa_supplicant di sistema** (quello già usato da NetworkManager), senza avviare un supplicant privato;
- tenga **Wi-Fi internet utilizzabile** durante la sessione DeX;
- non richieda di fermare NetworkManager o wpa_supplicant nel prodotto;
- sia portabile su Ubuntu, Debian, Arch, Fedora e sui chipset Wi-Fi comuni;
- supporti **solo Samsung DeX**, lato client, per la v1;
- sia codice leggibile, con branch dedicato e commit logici.

### 1.1 Requisiti aggiornati

- `[DECISIONE]` Il **PIN non è un requisito di prodotto**. È stato mantenuto durante lo spike solo per non cambiare due variabili insieme. Ora **PBC è accettabile** se semplifica l'architettura.
- `[DECISIONE]` Il prodotto **non deve fermare NetworkManager**. Il congelamento con `SIGSTOP` è esclusivamente una diagnostica dello spike.
- `[DECISIONE]` La v1 è **client-only**: il telefono è GO, il laptop è P2P client.

### 1.2 Non-obiettivi della v1

- Supportare Windows, TV, proiettori o altri source.
- Rendere il video WFD sul desktop: il flusso RTP viene deliberatamente scartato; DeX viene mostrato via **scrcpy su USB**.
- Sostituire il supplicant di sistema con un'istanza privata.
- Supportare il ruolo GO del laptop.

---

## 2. Architettura dello spike

```text
Telefono Samsung (GO)                         Laptop
192.168.49.1                           wlp0s20f3 (STA 5 GHz)
       │                                      │
       │  P2P 2.4 GHz (2437 MHz)              │  Wi-Fi domestica
       │  client 192.168.49.200               │  default route
       ▼                                      ▼
  p2p-wlp0s20-N  ◄── P2P client ──►      Internet / DNS
       │
       │  RTSP TCP 7236
       ▼
  WfdSink (Python)
  M1–M7, keepalive M16, RTP scartato
       │
       └── scrcpy su USB ──► DeX visualizzato sul laptop
```

Caratteristiche:

- il gruppo P2P sta su **2.4 GHz** (`freq=2437`), mentre la STA resta a 5 GHz/160 MHz;
- il laptop non installa mai una default route sul gruppo P2P;
- l'indirizzo .200 arriva dalle credenziali WPS del telefono;
- il traffico internet continua a uscire da `wlp0s20f3`.

---

## 3. Cosa è stato costruito

### 3.1 File dello spike

| File | Ruolo |
|---|---|
| `spike/rtsp.py` | sink RTSP WFD: M1–M7, SETUP, PLAY, keepalive M16, teardown |
| `spike/fake_source.py` | source WFD minimale per i test offline |
| `spike/selftest_rtsp.py` | test completo del handshake contro il fake source |
| `spike/supplicant.py` | client ctrl_iface wpa_supplicant + coda eventi |
| `spike/selftest_supplicant.py` | fake ctrl socket, risposte e code di eventi |
| `spike/dhcp.py` | client DHCP minimale, senza default route |
| `spike/selftest_dhcp.py` | test DHCP |
| `spike/linkd.py` | helper root: supplicant, link, eventi JSON, RTSP |
| `spike/selftest_events.py` | test offline della macchina a eventi P2P/WFD |
| `spike/selftest_rtsp_parser.py` | test del parser RTSP (frammenti, messaggi coalescenti) |
| `spike/run.py` | runner utente: sudo, helper, PIN, scrcpy |
| `spike/README.md` | istruzioni operative (da aggiornare, vedi §8) |

### 3.2 Cosa fa lo spike

- Parla con il wpa_supplicant **già in esecuzione** tramite control socket.
- Abilita WFD a runtime:
  - `SET wifi_display 1`
  - `WFD_SUBELEM_SET all 00000600111c4400c8`
  - `SET device_name DexOnLinux`
  - `SET p2p_go_intent 0`
  - `P2P_EXT_LISTEN 1000 1000`
- Risponde alla provision discovery del telefono:
  - display PIN: `P2P-PROV-DISC-SHOW-PIN` → `P2P_CONNECT <mac> <pin> display freq=2437 go_intent=0`;
  - PBC: `P2P-PROV-DISC-PBC-REQ` → `P2P_CONNECT <mac> pbc freq=2437 go_intent=0`.
- Preconfigura l'indirizzo WPS `.200/24` prima dell'associazione.
- Dopo il group start:
  - riporta up il link se il ciclo WPS lo ha lasciato down;
  - aggiunge l'indirizzo solo se manca;
  - non tocca la default route.
- Apre RTSP verso `192.168.49.1:7236`, esegue M1–M7 e scarta RTP.
- Lancia scrcpy su USB per mostrare DeX.
- Al termine ripristina WFD, `p2p_go_intent`, rimuove il gruppo e le interfacce orfane.

### 3.3 Test

Comando:

```bash
cd spike
python3 -m py_compile *.py
uvx ruff check .
for t in selftest_rtsp.py selftest_supplicant.py selftest_events.py selftest_dhcp.py selftest_rtsp_parser.py; do
  python3 "$t"
done
```

Risultato verificato il 2026-10-20:

```text
All checks passed!
PASS: M1-M7 handshake, keepalive and teardown completed
PASS: ctrl_iface replies and event queueing behave as expected
PASS: all linkd event tests
PASS: all DHCP tests
PASS: all RTSP framing tests
```

### 3.4 Commit principali

```text
487851c spike: bring up the P2P link after group start
92cf967 spike: always delete orphaned P2P group links
64b73e6 spike: never change the P2P link after group start
b94d371 spike: fix SETUP URL and preconfigure up interfaces
611170b spike: answer M16 keepalives with a bare 200
abb08d8 spike: preconfigure the P2P interface before association
06a7eac spike: pre-up the P2P netdev before association
d4de258 spike: bring the P2P client netdev up before RTSP
fd77cfb spike: keep the P2P group on 2.4 GHz and use the WPS address
1402dfd spike: clear stale P2P group interfaces on startup
162fcd2 spike: fall back to the P2P subnet broadcast for DHCP
6ab6ebc spike: add RTSP framing tests
128c3a9 spike: seed P2P address and wait for carrier before DHCP
7625525 docs: record live spike findings for WFD and DeX PIN
5695e80 spike: add offline tests for P2P event handling
b9e8782 spike: fix WFD advertisement and DeX display-PIN flow
4450684 spike: add root helper and user runner for live testing
8071f82 spike: add supplicant ctrl_iface and DHCP clients
67b5678 spike: add WFD sink RTSP client with fake-source self-test
```

Lo spike è deliberatamente **usa e getta**. Il prodotto vero andrà costruito in `src/dexonlinux/`, sostituendo l'orchestrazione miraclecast.

---

## 4. Cronologia del lavoro

1. **Ricerca di fattibilità** su wpa_supplicant/WFD/P2P, driver e distro.
2. **Design** del sink senza miraclecast, con supplicant di sistema e RTSP proprio.
3. **RTSP sink** con fake source e test offline.
4. **Client ctrl_iface** e DHCP minimale.
5. **Primo live test**: il telefono non vedeva il sink.
6. **Fix WFD**: `WFD_SUBELEM_SET` da solo non basta; serve `SET wifi_display 1`.
7. **DeX PIN**: il telefono usa la provision discovery display; risposta immediata con `P2P_CONNECT ... display`.
8. **Extended listen** portato a `1000 1000` per rendere affidabile la discovery.
9. **Canale 2.4 GHz**: con STA a 5 GHz/160 MHz il gruppo a 5 GHz veniva distrutto; 2437 MHz funziona.
10. **Indirizzo WPS**: `.200/24` client, `.1` GO; preconfigurazione prima dell'associazione.
11. **RTSP**: fix dell'URL di SETUP, del keepalive M16 e della sessione.
12. **Link down dopo WPS**: il netdev può restare down al group start; va riportato up.
13. **Gruppi orfani**: se `P2P_GROUP_REMOVE` fallisce, `iw dev <if> del`.
14. **Deauth misteriosi**: ogni gruppo moriva subito dopo il group start.
15. **Diagnostica**: congelato NetworkManager con `SIGSTOP`; il gruppo è rimasto stabile.
16. **Root cause**: NetworkManager rimuove il gruppo perché non ha iniziato lui la connessione.
17. **Sessione di successo**: PIN, gruppo, RTSP, DeX, scrcpy, internet 0% loss.
18. **Decisione PIN**: il PIN non è un requisito; PBC è accettabile.
19. **Prossimo bivio**: percorso NM-owned con PBC, oppure supplicant diretto + protezione dal bug NM.

---

## 5. Cosa abbiamo osservato

### 5.1 WFD e wpa_supplicant

- `[OSSERVATO]` `WFD_SUBELEM_SET all <hex>` da solo **non** pubblica il sink:
  il codice salva i subelementi ma non attiva `global->wifi_display`; serve
  `SET wifi_display 1`.
- `[OSSERVATO]` Il telefono DeX, quando è lui a iniziare, usa **WPS display PIN**:
  emette `P2P-PROV-DISC-SHOW-PIN` e mostra all'utente il PIN da inserire sul telefono.
- `[OSSERVATO]` La risposta corretta è immediata:
  `P2P_CONNECT <mac> <pin> display freq=2437 go_intent=0`.
  Aspettare `P2P-GO-NEG-REQUEST` e usare PBC in quel momento fallisce.
- `[OSSERVATO]` `P2P_EXT_LISTEN 1000 1000` rende affidabile la discovery;
  intervalli più radi perdono il singolo frame di provision discovery.
- `[OSSERVATO]` Il telefono dichiara `config_methods=0x188`, che include
  display, PBC e keypad.
- `[DA TESTARE]` Non sappiamo ancora se DeX accetta **PBC quando è il sink a iniziare**.
  Il fatto che chieda il PIN quando inizia lui non dimostra che rifiuti PBC nella
  direzione opposta.
- `[OSSERVATO]` Il MAC P2P del telefono osservato è `ce:f9:f0:2b:8a:cc`;
  il MAC dell'interfaccia GO è `ce:f9:f0:2b:0a:cc`.
- `[OSSERVATO]` Il formato WFD usato è:
  `00000600111c4400c8` = subelemento 0, lunghezza 6, Device Info `0011`,
  RTSP port `1c44` (7236), throughput `00c8` (200).
- `[SORGENTE]` Il setter D-Bus `WFDIEs` di wpa_supplicant chiama
  `wifi_display_subelem_set_from_ies()` e attiva WFD; quindi il percorso D-Bus
  è autosufficiente e non richiede `SET wifi_display 1`.

### 5.2 P2P, ruolo e indirizzi

- `[OSSERVATO]` Il gruppo funziona a **2437 MHz, 20 MHz**.
- `[OSSERVATO]` Con STA a **5200 MHz / 160 MHz** la coesistenza è stabile.
- `[OSSERVATO]` Il gruppo a 5 GHz veniva distrutto; 2.4 GHz è la scelta corretta
  per questa combinazione.
- `[OSSERVATO]` Indirizzo client `.200/24`, GO `.1`.
- `[OSSERVATO]` Il ciclo WPS (deauth/reconnect) può lasciare il netdev **DOWN**
  al momento del `P2P-GROUP-STARTED`; il link va riportato up.
- `[OSSERVATO]` L'indirizzo preconfigurato sopravvive al ciclo WPS; va aggiunto
  prima dell'associazione per evitare di cambiare netlink dopo il group start.
- `[OSSERVATO]` Il gruppo non deve avere default route: la internet resta sulla STA.
- `[OSSERVATO]` Se `P2P_GROUP_REMOVE` restituisce `FAIL`, `iw dev <if> del`
  elimina l'interfaccia orfana ed è necessario prima della sessione successiva.

### 5.3 RTSP e DeX

- `[OSSERVATO]` Il telefono ascolta RTSP su TCP **7236**.
- `[OSSERVATO]` Il sink deve usare l'URL di `wfd_presentation_URL`, non uno costruito a mano.
- `[OSSERVATO]` Il `SETUP` viene accettato con sessione `1804289383`.
- `[OSSERVATO]` Il keepalive M16 va risposto con un **`200` secco**; il campo
  `none` in coda va rimosso.
- `[OSSERVATO]` Il flusso RTP viene scartato volontariamente; DeX viene mostrato
  con scrcpy su USB.
- `[OSSERVATO]` Il primo tentativo RTSP può fallire con `Connection refused`
  subito dopo il group start; il retry lo recupera.

### 5.4 Rete e internet

- `[OSSERVATO]` La STA resta su `TIM-76794745`, 5200 MHz, 160 MHz.
- `[OSSERVATO]` La default route resta su `wlp0s20f3`.
- `[OSSERVATO]` `ping 1.1.1.1` = **0% loss** durante la sessione.
- `[OSSERVATO]` DNS funzionante.
- `[OSSERVATO]` Il traffico video P2P misurato è circa **2,76 Mbps** in RX;
  il TX è quasi solo RTSP/ack.

---

## 6. Il bug di NetworkManager (causa vera dei deauth)

### 6.1 Sequenza osservata

Subito dopo `P2P-GROUP-STARTED`:

```text
NetworkManager: P2P: WPA supplicant notified a group start but we are not
                trying to connect! Ignoring the event.
wpa_supplicant: CTRL-EVENT-DISCONNECTED ... reason=3
kernel:         deauthenticating from ... by local choice (Reason: 3=DEAUTH_LEAVING)
wpa_supplicant: nl80211: deinit ifname=p2p-wlp0s20-N
```

Non compare **nessun** `P2P-GROUP-REMOVED`.

### 6.2 Spiegazione dal sorgente

- `[SORGENTE]` In `nm-device-wifi-p2p.c`, `supplicant_iface_group_started_cb()`
  riceve il group start. Se NM non sta attivando una connessione P2P, logga
  “Ignoring the event” e non prende un riferimento all'interfaccia di gruppo.
- `[SORGENTE]` L'oggetto `NMSupplicantInterface` temporaneo viene distrutto.
- `[SORGENTE]` Il `dispose()` chiama `set_state_down(self, TRUE, ...)`.
- `[SORGENTE]` `set_state_down(..., force_remove_from_supplicant=TRUE)` chiama
  `RemoveInterface` su wpa_supplicant.
- `[SORGENTE]` wpa_supplicant esegue `wpa_supplicant_remove_iface()`:
  deautentica (`WLAN_REASON_DEAUTH_LEAVING`) e fa `nl80211: deinit`.
- `[SORGENTE]` Poiché è una rimozione di interfaccia, non viene emesso
  `P2P-GROUP-REMOVED`.

Questa sequenza spiega perché il gruppo moriva sempre nello stesso istante,
indipendentemente dalle modifiche al link o all'indirizzo.

### 6.3 Perché il congelamento funziona

`SIGSTOP` sospende il processo di NetworkManager:

- la STA resta collegata perché wpa_supplicant continua a gestirla;
- NM non esegue il callback del group start;
- NM non distrugge l'oggetto temporaneo;
- il gruppo P2P resta stabile.

`SIGSTOP` **non è** la soluzione di prodotto:

- NM congelato non risponde a D-Bus (menu GNOME, altri client, timeout);
- è fragile e richiede root;
- non è portabile né manutenibile;
- va usato solo come diagnostica.

### 6.4 Cosa offre NetworkManager 1.46

- `[OSSERVATO]` `nmcli` supporta `wifi-p2p.wfd-ies`.
  Formato accettato: `00:00:06:00:11:1c:44:00:c8` (visualizzato come
  `00000600111C4400C8`).
- `[SORGENTE]` NM 1.46 imposta i WFD IEs prima del connect
  (`nm_supplicant_manager_set_wfd_ies`).
- `[SORGENTE]` NM 1.46 **hardcoda PBC** in `act_stage2_config`.
- `[SORGENTE]` NM 1.46 **hardcoda `go_intent=7`** in
  `nm_supplicant_interface_p2p_connect()`.
- `[SORGENTE]` Con `ipv4.method=auto` e ruolo client, NM usa l'indirizzo
  assegnato dal GO (`nm_supplicant_interface_get_p2p_assigned_addr`).
- `[DA TESTARE]` Se il telefono vince comunque il GO negotiation con NM a 7.
- `[DA TESTARE]` Se DeX accetta PBC in direzione sink → telefono.

### 6.5 Perché non basta togliere il PIN

Togliere il PIN elimina il vincolo sul **WPS method**, ma non il `go_intent=7`
hardcoded di NM 1.46. Il percorso NM-owned è pulito solo se:

1. DeX accetta PBC;
2. il telefono resta GO nonostante il laptop dichiari intent 7;
3. NM configura correttamente `.200` e la route.

Se una delle tre fallisce, serve un altro approccio.

---

## 7. La sessione di successo

**Data/ora:** 2026-10-20, 23:06 CEST  
**Condizione:** NetworkManager congelato con `SIGSTOP` (diagnostica).

| Elemento | Valore |
|---|---|
| PIN | `09758780` |
| Gruppo | `p2p-wlp0s20-7` |
| Ruolo | client |
| Client IP | `192.168.49.200/24` |
| GO IP | `192.168.49.1` |
| Canale P2P | 2437 MHz, 20 MHz |
| Segnale | -27 dBm |
| RX rate | 286,7 Mbps HE-MCS 11 HE-NSS 2 |
| RTSP TCP | `192.168.49.200:60166 -> 192.168.49.1:7236` |
| RTP UDP | porta `43297`, circa 2,76 Mbps |
| Sessione RTSP | `1804289383` |
| DeX | `DeX session established` |
| Scrcpy | attivo, display 13 |
| STA | 5200 MHz, 160 MHz, TIM-76794745 |
| Internet | `ping 1.1.1.1` 0% loss |
| DNS | funzionante |

Log chiave:

```text
23:06:30 PIN RICHIESTO DAL TELEFONO: 09758780
23:06:30 P2P_CONNECT ce:f9:f0:2b:8a:cc (09758780 display) freq=2437 go_intent=0
23:06:43 group started: ifname=p2p-wlp0s20-7 role=client
23:06:43 RTSP connect ... (Connection refused); retrying
23:06:43 RTSP connected to 192.168.49.1:7236 (RTP port 43297)
23:06:44 SETUP accepted, session '1804289383'
23:06:44 streaming
23:06:44 DeX session established
```

Dopo il group start, nel journal di wpa_supplicant **non** compare alcun
`CTRL-EVENT-DISCONNECTED` né `nl80211: deinit` per il gruppo: con NM fermo,
il gruppo resta vivo.

---

## 8. Stato attuale

Verificato dopo la chiusura della sessione:

- NetworkManager: **attivo e ripreso** (`Rsl`);
- wpa_supplicant: attivo;
- internet: **0% loss**;
- nessun gruppo P2P residuo;
- `p2p-dev-wlp0s20f3`: `disconnected`;
- STA: `TIM-76794745`, 5200 MHz;
- branch: `spike/wfd-sink`;
- ultimo commit: `487851c`;
- working tree: pulito tranne `dexonlinux-handoff.md` non tracciato;
- test: tutti verdi.

### 8.1 Documentazione da aggiornare

- `spike/README.md`:
  - dice ancora che lo spike “does not stop NetworkManager”;
  - non menziona il bug NM né il workaround `SIGSTOP`;
  - va marcato il freeze come **diagnostica**, non come uso normale.
- `docs/research/2026-10-20-wfd-sink-without-miraclecast.md`:
  - aggiungere il bug NM;
  - aggiungere la sessione di successo e le metriche;
  - aggiornare le “open questions” con PBC/NM-owned.
- `docs/superpowers/specs/2026-10-20-miraclecast-free-wifi-link-design.md`:
  - rivedere l'integrazione P2P con NM;
  - il PIN non è più un requisito.

---

## 9. Decisioni prese

| # | Decisione | Motivazione | Stato |
|---|---|---|---|
| 1 | Usare il wpa_supplicant di sistema | niente supplicant privato, niente stop di NM/wpa_supplicant | valida |
| 2 | Sink client-only, telefono GO | DeX source, GO intent 0 | valida |
| 3 | P2P su 2.4 GHz | coesistenza con STA 5 GHz/160 MHz | valida |
| 4 | Nessuna default route sul P2P | internet resta sulla STA | valida |
| 5 | Indirizzo WPS `.200`, GO `.1` | il telefono lo assegna nel group event | valida |
| 6 | Preconfigurare l'indirizzo prima dell'associazione | evita netlink dopo il group start | valida |
| 7 | Riportare up il link dopo il group start | il ciclo WPS lo lascia down | valida |
| 8 | Cleanup orfani con `iw dev del` | `P2P_GROUP_REMOVE` può fallire | valida |
| 9 | `SIGSTOP` a NM solo diagnostica | prova che il bug è NM; non è prodotto | valida |
| 10 | PIN non requisito; PBC accettabile | semplifica l'architettura e apre NM-owned | nuova |
| 11 | Tenere il codice PIN finché NM-owned non è validato | è il fallback funzionante | valida |
| 12 | Provare prima NM-owned PBC | se funziona, elimina il bug NM alla radice | da fare |
| 13 | Se NM-owned fallisce, provare supplicant diretto + `managed no` | mantiene PBC e il controllo del gruppo | da fare |
| 14 | Non usare un supplicant P2P privato in v1 | complessità e rischio di conflitto sulla stessa radio | valida |

---

## 10. Domande aperte e rischi

| # | Domanda | Perché conta | Come verificare |
|---|---|---|---|
| 1 | DeX accetta PBC quando inizia il sink? | è il presupposto del percorso NM-owned | test con profilo NM `wifi-p2p` |
| 2 | Il telefono resta GO con NM a `go_intent=7`? | il sink non supporta il ruolo GO | osservare `P2P-GO-NEG-SUCCESS role=` |
| 3 | NM configura `.200` e la route dal group event? | evita link/address manuali | `ip -brief addr`, `ip route` |
| 4 | NM mantiene il gruppo e non lo rimuove? | è la prova che il bug è aggirato | journal + gruppo stabile |
| 5 | `nmcli device set p2p-dev-... managed no` preserva il ctrl P2P? | è il fallback che mantiene il controllo diretto | test e `ls /run/wpa_supplicant/` |
| 6 | Il MAC P2P del telefono è stabile tra sessioni? | il profilo NM ha `peer` fisso | osservare più sessioni |
| 7 | Il nome `DexOnLinux` resta visibile con NM? | NM 1.46 non espone un device-name nel profilo | `wpa_cli set device_name` + discovery |
| 8 | I WFD IEs del profilo NM sono pubblicati in discovery? | il telefono deve vedere il sink prima del connect | cattura probe response / UI telefono |
| 9 | NM-owned funziona su altre distro/NM? | portabilità | matrice versioni NM |
| 10 | Come gestire root/polkit nel prodotto? | il ctrl P2P è root-only; NM D-Bus ha polkit | design prodotto |

---

## 11. Opzioni per la convivenza con NetworkManager

### A. NM-owned P2P con PBC + `wfd-ies` — **candidata principale**

Flusso:

1. profilo NM `wifi-p2p` con `peer`, `wfd-ies`, `ipv4.method=auto`, `autoconnect=no`;
2. `nmcli connection up` / D-Bus `ActivateConnection`;
3. NM fa discovery, PBC connect, possiede il gruppo e configura `.200`;
4. il nostro helper aspetta il group start e fa solo RTSP;
5. cleanup con `connection down`.

Pro:

- NM non rimuove il gruppo perché è lui ad attivarlo;
- niente freeze;
- niente PIN;
- molto meno codice.

Contro/incognite:

- NM 1.46 hardcoda PBC: DeX deve accettarlo;
- NM 1.46 hardcoda `go_intent=7`: il telefono deve restare GO;
- device name e peer discovery da verificare.

### B. Supplicant diretto + impedire a NM di gestire il P2P — **fallback**

- Mantiene il flusso diretto (PBC o PIN) e `go_intent=0`.
- Si prova `nmcli device set p2p-dev-wlp0s20f3 managed no` prima della sessione.
- Rischio: NM rilascia il `mgmt_iface` e chiama `RemoveInterface` sul P2P device,
  facendo sparire il ctrl socket.
- Se il ctrl sopravvive, è la soluzione minima che mantiene il controllo diretto.
- Se il ctrl sparisce, si può provare a usare il ctrl della interfaccia principale
  o a ricreare il P2P device; da verificare.

### C. Congelare NM — **solo diagnostica**

- Funziona oggi.
- Non accettabile nel prodotto: timeout D-Bus, root, fragilità.
- Da documentare solo per riprodurre lo spike.

### D. NM più recente con `PIN_DISPLAY` / patch — **non v1**

- `main` di NM ha il supporto display PIN, ma `go_intent=7` resta hardcoded.
- Richiedere una versione patchata riduce la portabilità.
- Da considerare solo se A e B falliscono.

### E. Supplicant P2P privato — **non v1**

- È l'approccio di miraclecast; conflitti sulla stessa radio.
- Complessità e regressioni too high per la v1.

---

## 12. Prossimi passi

### Fase 0 — documentazione

- Aggiornare `spike/README.md` con:
  - bug NM;
  - workaround `SIGSTOP` solo diagnostico;
  - ordine di cleanup.
- Aggiornare il documento di ricerca con il bug NM e la sessione riuscita.
- Aggiornare la spec di prodotto: PIN non requisito, NM-owned da valutare.

### Fase 1 — test NM-owned PBC (decisione A)

Preparazione del device P2P:

```bash
sudo wpa_cli -p /run/wpa_supplicant -i p2p-dev-wlp0s20 set device_name DexOnLinux
sudo wpa_cli -p /run/wpa_supplicant -i p2p-dev-wlp0s20 set wifi_display 1
sudo wpa_cli -p /run/wpa_supplicant -i p2p-dev-wlp0s20 WFD_SUBELEM_SET all 00000600111c4400c8
sudo wpa_cli -p /run/wpa_supplicant -i p2p-dev-wlp0s20 set p2p_go_intent 0
```

Nota: NM 1.46 invia comunque `go_intent=7` nel proprio `Connect`; il set a 0
qui serve solo a lasciare il device in uno stato noto e non garantisce il ruolo
client. Il ruolo va verificato nell'evento `P2P-GO-NEG-SUCCESS`.

Profilo NM:

```bash
nmcli connection delete dex-nm-test 2>/dev/null
nmcli connection add type wifi-p2p con-name dex-nm-test \
  wifi-p2p.peer ce:f9:f0:2b:8a:cc \
  ipv4.method auto autoconnect no
nmcli connection modify dex-nm-test wifi-p2p.wfd-ies '00:00:06:00:11:1c:44:00:c8'
nmcli connection up dex-nm-test
```

Preparazione telefono:

- aprire la schermata Smart View / DeX;
- **non** selezionare DexOnLinux: deve essere NM a iniziare la connessione PBC;
- osservare se il telefono accetta o chiede il PIN.

Osservazioni:

```bash
nmcli connection show --active
iw dev | grep -A3 P2P-client
ip -brief addr show | grep p2p
ip route show dev p2p-wlp0s20-*
ping -c 3 192.168.49.1
journalctl -u NetworkManager -u wpa_supplicant -f
```

Criteri di successo:

- gruppo P2P creato, ruolo **client**;
- indirizzo `.200/24` configurato da NM;
- `ping 192.168.49.1` funziona;
- il gruppo resta stabile per almeno 60 secondi;
- nessun deauth/RemoveInterface.

Cleanup:

```bash
nmcli connection down dex-nm-test
nmcli connection delete dex-nm-test
```

Se il telefono chiede il PIN o rifiuta PBC: il percorso A non è praticabile con
NM 1.46; passare a B.

### Fase 2 — implementazione della strada scelta

Se A funziona:

- creare il profilo NM dinamicamente dal helper;
- scoprire il peer P2P (MAC/oggetto) e aggiornare `peer`;
- impostare `wfd-ies`;
- attivare/disattivare via NM;
- rimuovere il PIN da `run.py` e la gestione `SHOW-PIN` da `linkd.py`;
- non preconfigurare più link/address: li gestisce NM;
- mantenere il monitor del group event solo per avviare RTSP;
- aggiornare i test e il README.

Se B funziona:

- mantenere il flusso diretto con PBC (o PIN come fallback);
- aggiungere l'unmanage/restore del P2P device;
- gestire il caso in cui il ctrl P2P non sopravvive;
- mantenere il cleanup manuale del gruppo.

### Fase 3 — productizzazione

- Sostituire l'orchestrazione miraclecast in `src/dexonlinux/`.
- Definire helper privilegiato/systemd/polkit.
- Packaging e dipendenze per Ubuntu/Debian/Arch/Fedora.
- UX: discovery, stato, errori, retry, cleanup.
- Gestione entry “Connesso in precedenza” sul telefono.
- Test su altri chipset/driver.
- Test di regressione: più sessioni consecutive, internet 0% loss, crash/cleanup.
- Documentazione utente.

### Criteri di accettazione del prodotto

- DeX si connette senza miraclecast.
- Internet resta utilizzabile con 0% loss durante la sessione.
- NetworkManager e wpa_supplicant **non vengono fermati**.
- Nessun freeze o workaround diagnostico.
- Cleanup completo dopo chiusura, crash o Ctrl-C.
- Ripetibile su almeno due sessioni consecutive.
- Testato su almeno due distro e due chipset.

---

## 13. Riproduzione dello spike (stato attuale)

### 13.1 Test offline

```bash
cd spike
python3 selftest_rtsp.py
python3 selftest_supplicant.py
python3 selftest_events.py
python3 selftest_dhcp.py
python3 selftest_rtsp_parser.py
```

### 13.2 Sessione live con NM congelato (diagnostica)

1. Verificare che non ci siano gruppi P2P attivi:
   ```bash
   iw dev | grep -A2 P2P-client
   ```
2. Congelare NM:
   ```bash
   sudo kill -STOP $(pidof NetworkManager)
   ps -o pid,stat,comm -p $(pidof NetworkManager)   # T
   ```
3. Avviare lo spike:
   ```bash
   cd spike
   python3 run.py --interface wlp0s20f3
   ```
4. Sul telefono: Smart View / Cast → DexOnLinux → inserire il PIN mostrato.
5. Verificare internet in un altro terminale:
   ```bash
   ping -c 5 1.1.1.1
   ```
6. Alla fine, **prima** Ctrl-C nello spike e attendere il cleanup; poi:
   ```bash
   sudo kill -CONT $(pidof NetworkManager)
   ps -o pid,stat,comm -p $(pidof NetworkManager)   # S
   ```

**Ordine importante:** non fare `CONT` mentre il gruppo è attivo; NM potrebbe
processare il vecchio `GroupStarted` e rimuoverlo.

### 13.3 Recovery

```bash
sudo systemctl restart NetworkManager
```

Ripristina NM e la STA; può causare una breve interruzione della Wi-Fi.

### 13.4 Cleanup manuale di emergenza

```bash
I=p2p-dev-wlp0s20   # adattare se l'interfaccia è diversa
sudo wpa_cli -p /run/wpa_supplicant -i "$I" p2p_group_remove p2p-wlp0s20f3-0
sudo wpa_cli -p /run/wpa_supplicant -i "$I" p2p_ext_listen
sudo wpa_cli -p /run/wpa_supplicant -i "$I" WFD_SUBELEM_SET 0 ""
sudo wpa_cli -p /run/wpa_supplicant -i "$I" set wifi_display 0
sudo wpa_cli -p /run/wpa_supplicant -i "$I" set p2p_go_intent 7
```

Se il gruppo resta come interfaccia orfana:

```bash
sudo iw dev p2p-wlp0s20-N del
```

---

## 14. Ambiente e riferimenti

### 14.1 Ambiente osservato

- Laptop: ThinkPad, Intel AX201, interfaccia `wlp0s20f3`.
- STA: `TIM-76794745`, 5200 MHz, 160 MHz.
- Telefono: Samsung Galaxy S24 (SM-S921B), Android 16, One UI 8.0.
- ADB serial: `RZCX52DV4ZK`.
- P2P device MAC: `ce:f9:f0:2b:8a:cc`.
- P2P GO interface MAC: `ce:f9:f0:2b:0a:cc`.
- wpa_supplicant 2.10.
- NetworkManager 1.46.0.
- scrcpy 3.3.2.

### 14.2 Riferimenti sorgente

- NetworkManager 1.46:
  - `src/core/devices/wifi/nm-device-wifi-p2p.c`
  - `src/core/supplicant/nm-supplicant-interface.c`
  - `src/core/supplicant/nm-supplicant-manager.c`
  - `src/libnm-core-impl/nm-setting-wifi-p2p.c`
- wpa_supplicant 2.10:
  - `wpa_supplicant/dbus/dbus_new_handlers_p2p.c`
  - `wpa_supplicant/wifi_display.c`
  - `wpa_supplicant/wpa_supplicant.c`
- Documenti del repo:
  - `docs/research/2026-10-20-wfd-sink-without-miraclecast.md`
  - `docs/superpowers/specs/2026-10-20-miraclecast-free-wifi-link-design.md`
  - `spike/README.md`

### 14.3 Comandi utili di diagnosi

```bash
# stato P2P
iw dev
iw dev p2p-wlp0s20-N link
iw dev p2p-wlp0s20-N station dump

# indirizzi e route
ip -brief addr show
ip route
ip route get 192.168.49.1
ip route get 1.1.1.1

# RTSP / RTP
ss -tnp | grep 7236
ss -uan | grep 43297

# supplicant
journalctl -b -u wpa_supplicant -f
# NM
journalctl -b -u NetworkManager -f
# kernel P2P
journalctl -k -f | grep -E 'p2p|deauth|authenticate|associate'
```

---

## 15. Sintesi finale

**Funziona:** il percorso WFD/RTSP/DeX è validato end-to-end; la coesistenza
2.4 GHz P2P + 5 GHz STA tiene internet; il sink è client e il telefono è GO.

**Causa dei fallimenti:** NetworkManager rimuove il gruppo P2P quando non è
stato lui a iniziare la connessione. Il congelamento di NM lo dimostra.

**Cosa manca:** una convivenza pulita con NM acceso. La strada più promettente
è far possedere il gruppo a NM con PBC e `wfd-ies`; il PIN non è più un
requisito. Se NM 1.46 non collabora (PBC rifiutato o ruolo GO sbagliato), si
passa al supplicant diretto con PBC e alla protezione dal bug NM.

**Prossima azione:** eseguire il test NM-owned PBC descritto in §12, Fase 1,
e decidere A o B in base a ruolo, PBC e stabilità del gruppo.
