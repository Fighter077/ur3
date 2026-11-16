# UR3 – Robot-Assisted Inspection & Documentation of Removed PC Components

Dockerisierte Web-App zum Projekt **Gruppe 03 (ASM1, TU Wien, WS 2026/27)**:
Ein **UR3e** nimmt ein ausgebautes PC-Bauteil (HDD, optisches Laufwerk, RAM) aus einem Halter,
führt es durch feste **Inspektionsposen vor einer stationären Kamera**, fotografiert beide Seiten,
und eine Software liest per **OCR** das Label (Hersteller/Modell/Kapazität) und legt einen
**Digital-Product-Passport-Datensatz** im Inventar an. Der Arbeiter disassembliert unterdessen
weiter und löst den Zyklus per **Handgeste** (oder Knopf) aus. Unsicheres → **manuelle Review**.

> Anforderung: `Exercise/ASM1_Poster_20261115 - final.pdf`. (Die frühere Datei
> `Projektidee_FlexDA_UR3e.pdf` war **nicht** die Anforderung.)

## Ablauf

```
Arbeiter: Teil in Halter legen ──▶ Geste "open" halten ──▶ weiter disassemblieren
                                        │
UR3e:   Greifen ──▶ Front vor Kamera ──▶ 180° ──▶ Rückseite ──▶ in Collection-Area ablegen
                                        │
Software:                         Frame-Capture ──▶ OCR ──▶ Inventar (DPP) + Bilder
                                                               └─ unidentified ──▶ Review
```

## Architektur

- **backend/** – Python (aiohttp). `server.py` bündelt:
  - `rtc.py` – WebRTC-Empfang der **stationären Kamera**; pro Frame: Frame-Puffer füllen +
    MediaPipe-Hände + **Gesten-Trigger**.
  - `robot/inspection.py` – **Inspektions-State-Machine** (Pick → Inspektionsposen → beide Seiten
    fotografieren → OCR → Sortieren), asynchron, Bewegungen im Executor.
  - `robot/controller.py` – UR3e via RTDE, mit **Mock-Fallback** (Entwicklung ohne Hardware).
  - `perception/` – `hands.py` (MediaPipe HandLandmarker, 21 Landmarks → Geste),
    `gesture_trigger.py` (Zyklus-Auslöser), `ocr.py` (Tesseract-Label-OCR),
    `detector.py` (optionale YOLO-Objekterkennung, Standard aus).
  - `inventory.py` – Digital Product Passport: Datensätze (JSON) + Label-Bilder.
  - `websocket_hub.py` – Telemetrie/Inspektionsstatus/Inventar raus, Befehle rein.
- **frontend/** – Angular 19: Kamera + Gesten-Overlay, Zyklus-Steuerung/-Status,
  **Inventar-Tabelle** (mit Bildern + Review), manuelle Roboter-Steuerung, Telemetrie.

## Schnellstart (Podman)

```bash
cp .env.example .env               # ggf. ROBOT_MOCK / TRIGGER_GESTURE anpassen
podman compose up -d --build       # Projekt "ur3": Backend (host-net) + Frontend
```

In **Podman Desktop** erscheint das als ein gruppiertes Projekt „ur3 (compose)".
Alternativ ohne Compose: `./scripts/podman-up.sh` (startet dieselben Container per `podman run`).

- Frontend: <http://localhost:4200>
- Backend-Health: <http://localhost:8080/health>
- Logs: `podman compose logs -f backend` · Stoppen: `podman compose down`

Standard: **Simulation** (`ROBOT_MOCK=true`) – komplett ohne UR3e testbar. Der Inspektionszyklus
läuft dann mit simulierten Bewegungen; ohne Kamera entstehen Datensätze ohne Bild (`review`).

**Echten UR3e anbinden:** genaue Schritt-für-Schritt-Anleitung in
**[docs/REAL_ROBOT.md](docs/REAL_ROBOT.md)** (Netzwerk, Remote Control, Posen teachen, Greifer,
Kamera, sicherer Erstlauf).

> **Warum Podman (nicht Docker Desktop)?** Der WebRTC-Medienstrom läuft über **UDP**; Docker Desktop
> (Linux-VM) lässt UDP nicht durch → Verbindung bleibt auf „connecting". Rootless Podman nutzt das
> echte Host-Netz, deshalb hat der `backend`-Service `network_mode: host`. Compose läuft über
> **`podman compose`** (nutzt den Podman-Socket) – **nicht** über Docker Desktops `docker compose`.

## Bedienung

1. **Verbinden** → die Kamera (Browser) wird als „stationäre Kamera" ans Backend gestreamt.
2. Zyklus starten – zwei Wege:
   - **Geste**: die in `TRIGGER_GESTURE` gesetzte Geste (Default `open` = offene Hand) stabil
     in die Kamera halten; der Fortschrittsbalken füllt sich, dann startet der Zyklus.
   - **Button**: Bauteiltyp wählen und **„Zyklus starten"** (entspricht dem Knopf im Poster).
3. Der Datensatz erscheint im **Inventar**; `review`-Fälle lassen sich als geprüft markieren.

## Perzeption & Kamera

- **Geste** (MediaPipe HandLandmarker): rein als **Trigger**; der Roboter wird manuell/standalone
  gestartet (keine direkte Roboter-Software-Kopplung, wie im Poster).
- **OCR** (`ocr.py`): liest Hersteller/Modell/Kapazität. Default **EasyOCR** (neuronales OCR, robust
  bei Foto-Text; Tesseract als Fallback via `OCR_ENGINE=tesseract`). Davor **Label-ROI-Vorverarbeitung**
  (`LABEL_CROP`): das Label wird im Bild gesucht, entzerrt, zugeschnitten und hochskaliert → bessere
  effektive Auflösung. Heuristische Feldextraktion (Herstellerliste + Regex); unsichere Fälle → `review`.
- **Inspektionsposen** in `robot/inspection.py` sind **Platzhalter fürs Konzept/den Mock** und
  werden am realen Aufbau eingeteacht.

## Status / nächste Schritte

- [x] Web-App (Podman), WebRTC-Kamera, Telemetrie, Mock-/RTDE-Controller
- [x] Gesten-Trigger (MediaPipe) für den Zyklusstart
- [x] Inspektions-State-Machine (Pick → beide Seiten → OCR → Sortieren), im Mock verifiziert
- [x] OCR (Tesseract) + Inventar / Digital Product Passport (JSON + Bilder) + Review
- [ ] Reale Inspektionsposen einteachen + am echten UR3e testen (ROBOT_MOCK=false)
- [ ] Label-Ausschnitt gezielt anfahren/zuschneiden → OCR-Treffer verbessern (ggf. Fine-Tuning)
- [ ] Greifer/Endeffektor konkret anbinden (`set_gripper`)
- [ ] Baseline-Modi A (manuell) / B (kamera-assistiert) fürs Poster-Experiment
```
