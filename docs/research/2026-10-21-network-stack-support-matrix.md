# Stack di rete e matrice di supporto per DexOnLinux

**Data:** 2026-10-21  
**Repo:** `/home/gabdevele/Projects/DexOnLinux`  
**Branch:** `spike/wfd-sink`  
**Ambito:** analisi degli stack di rete che possono ospitare il WFD sink e comportamento del prodotto quando lo stack non è supportato.

Etichette:

- `[OSSERVATO]` — verificato su questa macchina.
- `[SORGENTE]` — verificato nel sorgente della versione indicata.
- `[DA VERIFICARE]` — comportamento probabile ma non ancora validato.
- `[PROPOSTA]` — decisione proposta, in attesa di istruzioni.

---

## 0. Sommario esecutivo

**No: non tutte le distro target usano NetworkManager + wpa_supplicant.**

La distro non è la variabile decisiva. La variabile vera è lo **stack di rete**:

- NetworkManager è un **manager**, non un backend Wi-Fi;
- per la Wi-Fi può usare **wpa_supplicant** oppure **iwd**;
- alcuni sistemi non usano NetworkManager affatto (systemd-networkd, ifupdown, wicked, ConnMan, scelta manuale);
- anche su Ubuntu o Fedora l'utente può cambiare backend.

La strategia proposta è quindi:

1. **targetizzare gli stack, non le distro**;
2. supportare in v1 **NetworkManager + wpa_supplicant** e **wpa_supplicant standalone**;
3. trattare **iwd** come backend di seconda fase, tecnicamente promettente ma da validare con DeX;
4. **rilevare** lo stack all'avvio e fallire con un messaggio chiaro se non è supportato;
5. **non** fermare, sostituire o dirottare lo stack dell'utente;
6. valutare il percorso **NM-owned** perché, se funziona, può coprire sia wpa_supplicant sia iwd tramite NetworkManager.

---

## 1. Perché la distro non basta

NetworkManager è un demone di gestione della rete. Per la parte Wi-Fi si appoggia a un backend:

- **wpa_supplicant** — backend storico, usato dalla maggior parte dei desktop;
- **iwd** — backend alternativo, supportato da NetworkManager e usato da alcune distro/configurazioni;
- in teoria altri stack, ma fuori dal nostro target.

Quindi due sistemi con la stessa distro possono avere stack diversi:

- Ubuntu con NetworkManager + wpa_supplicant;
- Ubuntu con NetworkManager + iwd;
- Arch senza NetworkManager, con systemd-networkd + wpa_supplicant;
- Arch con iwd standalone;
- Debian server con ifupdown + wpa_supplicant;
- openSUSE con wicked + wpa_supplicant;
- sistemi embedded con ConnMan.

Inoltre il **driver** è indipendente dalla distro: lo stesso stack può funzionare su Intel AX201 e fallire su un chipset che non supporta la concorrenza STA + P2P.

**Conclusione:** la matrice di supporto deve essere per **stack + driver**, non per nome della distro.

---

## 2. Stack possibili

| Stack | Chi gestisce la Wi-Fi | P2P/WFD | Note | Stato per DexOnLinux |
|---|---|---|---|---|
| NetworkManager + wpa_supplicant | NM, tramite wpa_supplicant | sì, se wpa_supplicant ha `CONFIG_P2P` + `CONFIG_WIFI_DISPLAY` | caso più diffuso sui desktop | **Tier 1** |
| wpa_supplicant standalone + systemd-networkd/ifupdown/wicked | wpa_supplicant | sì, se compilato e configurato | nessun NM che rimuove il gruppo | **Tier 1** |
| NetworkManager + iwd | NM, tramite iwd | iwd supporta P2P/WFD; NM 1.46 ha un device P2P iwd con WFD IEs | PBC; PIN display limitato/non supportato | **Tier 2** |
| iwd standalone | iwd | iwd supporta P2P/WFD | serve un adapter iwd dedicato | **Tier 2** |
| ConnMan | ConnMan | non testato nel nostro caso | fuori target desktop | **non supportato v1** |
| Altri stack embedded/proprietari | vario | sconosciuto | non prioritario | **non supportato v1** |

---

## 3. Default tipici per distro

Tabella indicativa. I default cambiano nel tempo e l'utente può sempre scegliere un altro stack; va verificato sul sistema, non assunto dalla distro.

| Distro / contesto | Stack tipico | Note |
|---|---|---|
| Ubuntu desktop | NetworkManager + wpa_supplicant | iwd possibile ma non default |
| Debian desktop | NetworkManager + wpa_supplicant | server/minimal possono usare ifupdown o systemd-networkd + wpa_supplicant |
| Fedora Workstation | NetworkManager + wpa_supplicant | backend iwd opzionale; Atomic/Silverblue usano NM |
| Arch Linux | nessun default | l'utente sceglie: NM+wpa_supplicant, iwd, systemd-networkd, netctl, ecc. |
| Linux Mint / Pop!_OS | NetworkManager + wpa_supplicant | |
| openSUSE | NM su desktop, wicked su server | entrambi usano wpa_supplicant per la Wi-Fi |
| Alpine / postmarketOS | spesso iwd, a volte wpa_supplicant | fuori dal target desktop principale |
| NixOS / Gentoo | scelta dell'utente | NM o networkd + wpa_supplicant, oppure iwd |

`[DA VERIFICARE]` Le versioni esatte dei default vanno ricontrollate al momento del packaging; la tabella serve a orientare, non a certificare ogni release.

---

## 4. Cosa richiede DexOnLinux

Indipendentemente dallo stack, servono:

1. **P2P/WFD**
   - un backend che supporti Wi-Fi Direct;
   - WFD Information Elements;
   - provision discovery / WPS;
   - GO negotiation con il telefono come GO.

2. **Accesso al P2P**
   - control socket di wpa_supplicant, oppure D-Bus di iwd/NM;
   - permessi root/polkit adeguati.

3. **Coesistenza con la STA**
   - il driver deve permettere STA + P2P client;
   - il gruppo P2P va su 2.4 GHz se la STA è a 5 GHz/160 MHz;
   - nessuna default route sul gruppo P2P.

4. **Coesistenza con il manager**
   - se NM gestisce il P2P, non dobbiamo subire il `RemoveInterface` del bug noto;
   - se il P2P è diretto, il manager non deve rimuovere il gruppo;
   - se iwd gestisce il P2P, serve il percorso iwd.

5. **Indirizzo e routing**
   - indirizzo client `.200/24`, GO `.1`;
   - route `192.168.49.0/24` sul gruppo;
   - internet sulla STA.

6. **RTSP/DeX**
   - connessione a `192.168.49.1:7236`;
   - M1–M7, keepalive M16, RTP scartato;
   - scrcpy su USB per il display.

---

## 5. Matrice di supporto proposta per la v1

### Tier 1 — supportato

#### 5.1 NetworkManager + wpa_supplicant

- È il caso di gran parte dei desktop Ubuntu/Debian/Fedora/Mint/Pop.
- Richiede la soluzione al bug NM:
  - percorso **NM-owned** con PBC e `wfd-ies`, oppure
  - supplicant diretto + protezione dal `RemoveInterface`.
- `[DA TESTARE]` PBC e ruolo GO con NM 1.46.
- `[SORGENTE]` NM 1.46 hardcoda PBC e `go_intent=7` nel percorso wpa_supplicant.

#### 5.2 wpa_supplicant standalone

- systemd-networkd, ifupdown, wicked, configurazioni manuali.
- Nessun NetworkManager che rimuove il gruppo: il bug NM non si applica.
- Richiede che wpa_supplicant sia compilato con P2P/WFD e che il control interface sia disponibile.
- È il percorso più semplice e il migliore per il debug.

### Tier 2 — da valutare, non promesso

#### 5.3 NetworkManager + iwd

- `[SORGENTE]` iwd 3.12 ha supporto P2P e WFD (`src/p2p.c`;
  `WFD_DEV_INFO_TYPE_PRIMARY_SINK`, WFD IE builder).
- `[SORGENTE]` NetworkManager 1.46 ha `nm-device-iwd-p2p.c` con:
  - WFD IEs dal profilo `wifi-p2p`;
  - registrazione WFD presso iwd;
  - `PushButton` per il WPS;
  - `wps-method=pin` non implementato in quel percorso.
- `[SORGENTE]` iwd usa `P2P_GO_INTENT 2`; il telefono, con intent più alto,
  dovrebbe restare GO.
- `[DA TESTARE]` DeX con iwd: PBC, ruolo, IP, stabilità, coesistenza STA.
- `[DA TESTARE]` Il bug di rimozione gruppo esiste anche nel percorso iwd di NM?
  È codice diverso; non assumerlo.

#### 5.4 iwd standalone

- Serve un adapter iwd dedicato (D-Bus `net.connman.iwd`), non il ctrl_iface
  di wpa_supplicant.
- Interessante per Alpine/postmarketOS e per sistemi minimal.
- Non prioritario per la v1 desktop.

### Non supportato in v1

- ConnMan e stack embedded: nessun percorso P2P/WFD validato.
- Sistemi senza wpa_supplicant/iwd con P2P.
- Driver senza combinazione STA + P2P.
- Installazioni Flatpak/Snap: la sandbox non può gestire il P2P dell'host;
  serve un pacchetto nativo o un helper privilegiato.

---

## 6. iwd: opportunità e limiti

iwd è interessante perché:

- è un backend moderno e supportato da NetworkManager;
- ha P2P e WFD nel sorgente;
- con NM-owned P2P potrebbe evitare il bug del percorso wpa_supplicant;
- usa un GO intent basso (2), quindi il telefono dovrebbe vincere e restare GO;
- PBC è ora accettabile, quindi il limite del PIN display non è bloccante.

Limiti e incognite:

- `[SORGENTE]` NetworkManager 1.46, nel percorso iwd, rifiuta `wps-method=pin`
  e usa `PushButton` (PBC).
- `[SORGENTE]` NM `main` rifiuta esplicitamente `PIN_DISPLAY` con iwd:
  “IWD does not support the WPS PIN-display method”.
- `[DA TESTARE]` DeX accetta PBC con iwd?
- `[DA TESTARE]` Il ruolo GO è corretto con `P2P_GO_INTENT 2`?
- `[DA TESTARE]` La coesistenza STA + P2P funziona sul driver in uso?
- `[DA TESTARE]` La versione iwd della distro è abbastanza recente?
- `[DA TESTARE]` Il percorso NM+iwd è compilato nella NM della distro?

**Conclusione:** iwd non è un ripiego teorico; è un backend plausibile, ma non va promesso finché non è provato con DeX.

---

## 7. Rilevamento automatico: il “doctor”

Il prodotto deve capire lo stack prima di provare a connettersi.

### 7.1 Cose da rilevare

- Processi/D-Bus:
  - NetworkManager;
  - wpa_supplicant;
  - iwd;
  - ConnMan.
- Backend Wi-Fi di NetworkManager:
  - wpa_supplicant o iwd;
  - configurazione `wifi.backend` se presente.
- P2P:
  - `iw dev` mostra un `P2P-device`?
  - esiste `/run/wpa_supplicant/p2p-dev-*`?
  - `nmcli device` mostra un device `wifi-p2p`?
  - iwd espone P2P su D-Bus?
- WFD:
  - wpa_supplicant accetta `WFD_SUBELEM_SET`/`WFD_SUBELEM_GET`?
  - il profilo NM accetta `wfd-ies`?
  - iwd espone WFD?
- Driver:
  - `iw list` mostra combinazioni con managed + P2P?
  - supporta due canali o la combinazione richiesta?
- Permessi:
  - ctrl socket accessibile?
  - D-Bus policy consente P2P?
  - root/polkit disponibili?

### 7.2 Output desiderato

Esempi:

```text
Network stack: NetworkManager 1.46 + wpa_supplicant 2.10
P2P: available (p2p-dev-wlp0s20f3)
WFD: available
Driver: STA + P2P client supported
Status: SUPPORTED (NM-safe path required)
```

```text
Network stack: NetworkManager + iwd
P2P: available via iwd
WFD: available via iwd
Status: NOT SUPPORTED IN V1 (iwd backend adapter not implemented)
Action: switch NetworkManager to wpa_supplicant or wait for iwd support
```

```text
Network stack: wpa_supplicant standalone
P2P: available
WFD: available
Status: SUPPORTED (direct backend)
```

```text
Network stack: ConnMan
P2P/WFD: not validated
Status: NOT SUPPORTED
```

### 7.3 Regola

Il doctor **non modifica nulla**. Diagnostica e spiega; le modifiche le fa
l'utente, consapevolmente.

---

## 8. Cosa facciamo con uno stack non supportato

### 8.1 Fail fast, con spiegazione

- Nessun tentativo “alla cieca”.
- Nessun freeze di NetworkManager.
- Nessun supplicant privato.
- Nessun cambio automatico di backend.
- Messaggio chiaro con:
  - stack rilevato;
  - cosa manca;
  - due opzioni praticabili;
  - link alla documentazione.

### 8.2 Opzioni da offrire

A seconda dello stack:

- **NetworkManager + iwd**
  - passare NM a wpa_supplicant (modifica consapevole, può richiedere restart);
  - oppure attendere l'adapter iwd.
- **ConnMan**
  - usare una configurazione supportata;
  - oppure eseguire wpa_supplicant standalone per il P2P, se non confligge
    (da verificare, non garantito).
- **Driver senza STA + P2P**
  - nessuna soluzione software: serve un altro adattatore/chipset.
- **Flatpak/Snap**
  - installare il pacchetto nativo con helper privilegiato.

### 8.3 Cosa non fare

- Non fermare NetworkManager.
- Non fermare wpa_supplicant.
- Non avviare un supplicant privato sulla stessa radio.
- Non cambiare il backend dell'utente senza consenso.
- Non dichiarare “supportato” un sistema solo perché la distro è nella lista.

---

## 9. Architettura del codice proposta

Una piccola astrazione per non spargere `if distro` nel codice:

| Componente | Responsabilità |
|---|---|
| `NetworkStackProbe` | rileva NM/iwd/wpa_supplicant/ConnMan, backend, P2P, WFD, driver |
| `P2PBackend` | interfaccia: prepare, start, wait_group, stop |
| `WpaSupplicantBackend` | ctrl_iface diretto (Tier 1 standalone) |
| `NmWpaSupplicantBackend` | NM-owned (Tier 1 NM) |
| `IwdBackend` | iwd D-Bus (Tier 2, futuro) |
| `WfdSession` | RTSP/DeX, indipendente dal backend |
| `Doctor` | output leggibile e consigli |

Regole:

- la logica RTSP non deve sapere se il gruppo è stato creato da NM, wpa_supplicant o iwd;
- il backend riceve solo: “prepara WFD”, “connetti”, “dammi il group event”,
  “chiudi”;
- il doctor è separato dal percorso di connessione;
- niente astrazione speculativa: in v1 si implementano solo i backend necessari.

---

## 10. Impatto sul percorso NM-owned

Se il percorso **NM-owned P2P con PBC + `wfd-ies`** funziona, diventa la scelta
architetturalmente più forte:

- NM possiede il gruppo;
- niente bug `RemoveInterface` del percorso diretto;
- NM astrae il backend: lo stesso codice D-Bus può valere per wpa_supplicant e iwd;
- niente root per il ctrl_iface (polkit/NM gestiscono i permessi);
- niente PIN.

Ma restano le incognite:

- `[SORGENTE]` NM 1.46 + wpa_supplicant: PBC hardcoded, `go_intent=7`;
- `[SORGENTE]` NM 1.46 + iwd: `PushButton` (PBC), `go_intent` gestito da iwd (2);
- `[DA TESTARE]` DeX accetta PBC in direzione sink → telefono;
- `[DA TESTARE]` il telefono resta GO con NM a 7 (wpa_supplicant) o iwd a 2;
- `[DA TESTARE]` NM configura `.200` e la route in entrambi i backend.

Se NM-owned funziona su **almeno uno** dei due backend, la matrice si allarga
senza scrivere due adapter. Se funziona su entrambi, è il percorso ideale.

---

## 11. Rischi e domande aperte

| # | Rischio / domanda | Impatto | Verifica |
|---|---|---|---|
| 1 | DeX rifiuta PBC | NM-owned non praticabile | test con profilo NM |
| 2 | Telefono non resta GO con NM a 7 | ruolo errato | osservare `P2P-GO-NEG-SUCCESS` |
| 3 | NM+iwd non compilato nella distro | Tier 2 non disponibile | `nmcli`/D-Bus, log NM |
| 4 | iwd troppo vecchio | niente WFD/P2P | versione iwd |
| 5 | Driver senza STA+P2P | nessuna soluzione software | `iw list` |
| 6 | Default distro diversi da quelli attesi | supporto imprevisto | doctor sul campo |
| 7 | ConnMan/embedded | fuori target | messaggio chiaro |
| 8 | Permessi/root/polkit | UX e packaging | test su distro |
| 9 | Flatpak/Snap | P2P inaccessibile | pacchetto nativo |
| 10 | Immutable distro | installazione helper | rpm-ostree/guide |

---

## 12. Decisioni proposte

Sono proposte, in attesa delle istruzioni operative.

| # | Proposta |
|---|---|
| D1 | Targetizzare **stack**, non distro. |
| D2 | v1 supporta **NetworkManager + wpa_supplicant** e **wpa_supplicant standalone**. |
| D3 | **iwd** è Tier 2: adapter futuro, non promessa v1. |
| D4 | Ogni sistema non supportato riceve un **doctor** con errore chiaro e opzioni. |
| D5 | Mai fermare NM/wpa_supplicant, mai supplicant privato, mai cambio automatico di backend. |
| D6 | Il percorso **NM-owned PBC + `wfd-ies`** è da testare per primo perché può coprire NM+wpa_supplicant e NM+iwd. |
| D7 | Documentazione per stack, non per distro; matrice hardware separata. |
| D8 | Packaging nativo con helper privilegiato; niente Flatpak/Snap per il P2P. |

---

## 13. Comandi di diagnostica

### 13.1 Processi e stack

```bash
ps -eo pid,comm,args | grep -E 'NetworkManager|wpa_supplicant|iwd|connmand' | grep -v grep
systemctl is-active NetworkManager wpa_supplicant iwd 2>/dev/null
nmcli --version 2>/dev/null
wpa_supplicant -v 2>/dev/null | head -2
iwd --version 2>/dev/null
```

### 13.2 NetworkManager

```bash
nmcli general
nmcli device
nmcli device | grep -i p2p
nmcli connection show
grep -R "wifi.backend" /etc/NetworkManager 2>/dev/null
journalctl -b -u NetworkManager | grep -i -E 'backend|iwd|p2p' | tail -20
```

### 13.3 wpa_supplicant / P2P / driver

```bash
iw dev
iw list | sed -n '/valid interface combinations/,/^$/p'
ls -l /run/wpa_supplicant/ 2>/dev/null
sudo wpa_cli -p /run/wpa_supplicant -i p2p-dev-wlp0s20 help 2>/dev/null | grep -i -E 'p2p|wfd'
sudo wpa_cli -p /run/wpa_supplicant -i p2p-dev-wlp0s20 WFD_SUBELEM_GET 0 2>/dev/null
```

### 13.4 iwd

```bash
iwctl adapter list 2>/dev/null
busctl introspect net.connman.iwd /net/connman/iwd 2>/dev/null | head -40
journalctl -b -u iwd | tail -30
```

### 13.5 Stato P2P durante una sessione

```bash
iw dev
iw dev p2p-wlp0s20-N link
iw dev p2p-wlp0s20-N station dump
ip -brief addr show
ip route
ss -tnp | grep 7236
ss -uan | grep 43297
```

---

## 14. Riferimenti

### 14.1 Documenti del repo

- [`docs/research/2026-10-20-wfd-sink-spike-report.md`](2026-10-20-wfd-sink-spike-report.md)
  — report completo dello spike, bug NM, sessione riuscita e prossimi passi.
- [`docs/research/2026-10-20-wfd-sink-without-miraclecast.md`](2026-10-20-wfd-sink-without-miraclecast.md)
  — ricerca di fattibilità WFD/P2P.
- [`docs/superpowers/specs/2026-10-20-miraclecast-free-wifi-link-design.md`](../superpowers/specs/2026-10-20-miraclecast-free-wifi-link-design.md)
  — design del prodotto.

### 14.2 Sorgenti verificati

- NetworkManager 1.46.0:
  - `src/core/devices/wifi/nm-device-wifi-p2p.c`
  - `src/core/devices/wifi/nm-device-iwd-p2p.c`
  - `src/core/supplicant/nm-supplicant-interface.c`
  - `src/core/supplicant/nm-supplicant-manager.c`
  - `src/libnm-core-impl/nm-setting-wifi-p2p.c`
- iwd 3.12 (upstream, clone 2026-03-13):
  - `src/p2p.c`
  - `src/p2putil.c`
  - `src/ie.c`
- wpa_supplicant 2.10:
  - `wpa_supplicant/dbus/dbus_new_handlers_p2p.c`
  - `wpa_supplicant/wifi_display.c`
  - `wpa_supplicant/wpa_supplicant.c`

### 14.3 Ambiente locale

- Ubuntu 24.04, NetworkManager 1.46.0, wpa_supplicant 2.10.
- Intel AX201, `wlp0s20f3`.
- Samsung Galaxy S24 (SM-S921B), Android 16, One UI 8.0.

---

## 15. Sintesi

- La distro non determina lo stack: **NetworkManager può usare wpa_supplicant o iwd**,
  e alcuni sistemi non usano NM affatto.
- La v1 deve supportare **NM + wpa_supplicant** e **wpa_supplicant standalone**.
- **iwd** è tecnicamente promettente (P2P/WFD, GO intent 2, NM 1.46 con iwd P2P),
  ma va testato con DeX e non va promesso.
- Ogni stack non supportato deve produrre un **errore chiaro**, non un tentativo
  distruttivo.
- Il percorso **NM-owned PBC** è la strada che, se funziona, copre più stack con
  meno codice.
- La matrice va per **stack + driver**, non per distro.
