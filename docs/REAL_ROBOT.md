# Anbindung an den echten UR3e – Schritt für Schritt

Die App läuft standardmäßig im **Mock** (`ROBOT_MOCK=true`): alles ohne Hardware testbar.
Für den **echten UR3e** ändert sich nur wenig Code – vor allem **konfigurierst** du (IP, Greifer)
und **teachst** die Posen. Diese Anleitung geht genau durch, was zu tun ist.

> **Brauche ich einen Real-time-Kernel?** **Nein.** Der RTK-Hinweis der LVA betrifft den
> **Franka**-Roboter (dessen FCI/`franka_ros` braucht Echtzeit). Wir steuern den **UR3e** über
> **`ur_rtde` (RTDE)** mit `moveL`/`moveJ` – das läuft auf einem normalen Linux-Kernel (auch Fedora),
> ohne ROS und ohne Mini-PC. Ein RT-Kernel wäre nur für hochfrequentes Servoing (`servoJ`/500 Hz)
> nötig, das nutzen wir nicht.

Betroffen sind im Code genau drei Stellen:
1. **Verbindung**: `backend/robot/controller.py` → `RTDEController` (via `ur_rtde`). Nur aktiv bei
   `ROBOT_MOCK=false`. Fertig implementiert.
2. **Posen**: `backend/data/poses.json` (per „Teach" aus der UI/WS befüllt). Solange die Datei
   fehlt, gelten Platzhalter aus `backend/robot/inspection.py` (`DEFAULT_POSES`).
3. **Greifer**: `RTDEController.set_gripper()` – über `GRIPPER_TYPE` (`digital` oder `robotiq`).

---

## 0. Voraussetzungen (Hardware)

- UR3e mit Control Box, Teach-Pendant (PolyScope), Not-Halt erreichbar.
- Endeffektor/Greifer: in dieser LVA der **Robotiq Hand-E** (2-Finger-Parallelgreifer; wird wie die
  Robotiq-2F-Serie über das URCap-Socket angesteuert). Alternativ ein Greifer an einem Digital-Ausgang.
- 3D-gedruckter Halter mit Insert je Bauteiltyp (HDD/optisch/RAM).
- Stationäre Kamera (Webcam), die den Bereich **vor dem Greifer** sieht (wo das Teil gehalten wird).
- PC im selben Netz wie der Roboter, auf dem Podman/die App läuft.

## 1. Netzwerk herstellen (Roboter ↔ dieser PC)

Nur Ethernet + IP – kein ROS, kein Mini-PC, kein RT-Kernel.

1. **Kabel**: Ethernet in den **Port unter dem Bügel am Boden der UR-Control-Box** (Kabel durch die
   Öffnung an der Basis) ↔ Ethernet-Port dieses PCs (oder beide in Switch/Router).
2. **Roboter-IP herausfinden** (im Labor oft schon konfiguriert): am Pendant
   **Hamburger-Menü → About → General** zeigt die aktuellen **Netzwerk-Einstellungen** (IP). Diese IP
   kommt später in `ROBOT_IP`. Ändern/fest vergeben bei Bedarf unter **Settings → System → Network**
   (*Static Address*, z. B. `192.168.1.100` / Maske `255.255.255.0`).
   (Die `172.16.0.x`-Angaben der LVA gelten dem **Franka**, nicht dem UR.)
3. **Diesen PC** ins selbe Subnetz: Ethernet-Interface statisch, z. B. `192.168.1.10/24`
   (Fedora: *Settings → Network → Wired → IPv4 → Manual*). Beispiel-IP an die des Roboters anpassen.
4. **Testen**: `ping <Roboter-IP>` muss antworten.
5. **Container erreicht den Roboter automatisch**: Das Backend läuft mit `--network=host`, teilt sich
   also das Netz dieses PCs. Pingt der PC den Roboter, erreicht ihn auch der Container – keine extra
   Container-Netzwerkconfig.
6. Firewall: `ur_rtde` nutzt RTDE-/Dashboard-Ports (u. a. **30004**, 29999, 30001–30003); im selben
   LAN normalerweise offen.

## 2. Robotereinstellungen am Teach-Pendant

1. **TCP einrichten**: Installation → General → **TCP**. Den Tool-Center-Point des Greifers
   (Offset + ggf. Orientierung) eintragen. *Wichtig*, sonst stimmen alle TCP-Posen nicht.
2. **Payload/Schwerpunkt**: Installation → General → **Payload** (Greifer + Teilgewicht).
3. **Montage**: Installation → General → **Mounting** (Einbaulage) korrekt setzen.
4. **Sicherheit (ISO/TS 15066)**: Installation → **Safety** → Kraft-/Geschwindigkeits-/Leistungs-
   grenzen für kollaborativen Betrieb konfigurieren; ggf. Sicherheitsebenen/Zonen. Mit Passwort
   bestätigen. (Kernargument des Posters: kraftbegrenzter Cobot-Betrieb.)
5. **Remote Control aktivieren**: in **Settings** Remote Control freischalten; dann erscheint in der
   **Fußzeile** der **Local/Remote**-Umschalter – auf **Remote** stellen. `ur_rtde` lädt sein
   Steuerskript über RTDE auf den Roboter – dafür muss der Roboter **eingeschaltet, die Bremsen
   gelöst** und im **Remote-Control-Modus** sein (e-Series).
6. Roboter **initialisieren**: Power on → **Brakes release**.

> Je nach PolyScope-Version heißen Menüs leicht anders; Prinzip bleibt gleich.

## 3. Backend auf den echten Roboter umstellen

In `.env` (aus `.env.example` kopiert):

```bash
ROBOT_MOCK=false
ROBOT_IP=192.168.1.100          # IP aus Schritt 1
# Greifer (siehe Schritt 5):
GRIPPER_TYPE=robotiq            # oder: digital | none
# GRIPPER_DO_PIN=0              # nur bei GRIPPER_TYPE=digital
# GRIPPER_DO_CLOSED=true        # HIGH am DO = geschlossen?
```

Neu starten: `./scripts/podman-up.sh`.
Prüfen: `curl http://localhost:8080/health` → `"mock": false` und `"robot_connected": true`.
Bei Verbindungsfehler bricht das Backend früh mit klarer Meldung ab (Log: `podman logs ur3-backend`).

## 4. Posen teachen

Die sechs benötigten Posen: `home`, `holder`, `holder_up`, `front`, `back`, `collection`.

1. In der Web-UI **Verbinden**. Telemetrie zeigt die aktuelle TCP-Pose.
2. Mit den **Jog-Buttons** (oder von Hand im Freedrive am Pendant) den Roboter an die Zielstelle
   fahren.
3. Im Abschnitt **„Posen teachen"** den passenden Button klicken → die aktuelle TCP-Pose wird als
   diese Pose in `backend/data/poses.json` gespeichert (überlebt Neustart, da gemountet).
4. Reihenfolge-Tipp:
   - `holder` = Greifposition im Halter, `holder_up` = ein Stück darüber (Abheben).
   - `front` = Teil mittig vor der Kamera (Vorderseite), `back` = selbe Stelle, Handgelenk **180°
     gedreht** (Rückseite). Am besten `front` teachen, dann nur Wrist3 um π drehen und als `back`.
   - `collection` = Ablage/Collection-Area, `home` = sichere Ausgangslage.
5. Danach einen Zyklus starten – der Roboter fährt die geteachten Posen ab.

Der Pickup fährt zuerst `holder_up` an, öffnet dort den Greifer, fährt zu `holder`, schließt
den Greifer und fährt wieder zu `holder_up`. Die Bildschirm-Aufhellung ist nur nach erreichter
Inspektionspose während des Aufnahme-/OCR-Fensters aktiv; die obere Steuerleiste bleibt sichtbar.
Die Fahrt `front` → `back` nutzt `INSPECT_FLIP_SPEED` (Standard 0,15 m/s).
Normale TCP-Fahrten nutzen `ROBOT_TCP_SPEED` (Standard 0,12 m/s). Vor der Aufhellung muss die
volle Zielpose für `INSPECT_STILL_S` (Standard 0,3 s) durchgehend erreicht und der Roboter still sein.
Nach der Rückseitenaufnahme fährt der Roboter direkt zur separat geteachten Pose
`back_clearance`, dann zur Collection-Area. Es gibt keinen automatischen Hub über `back`.
`back_clearance` kann über den gleichnamigen Teach-Button gespeichert werden. Die Transitbahnen
müssen am Aufbau auf Reichweite und Freiraum geprüft werden.

> Ohne `poses.json` nutzt das System die Platzhalter aus `DEFAULT_POSES` (nur fürs Mock sinnvoll).

## 5. Greifer anbinden

`set_gripper()` ist implementiert, du wählst nur die Variante per `GRIPPER_TYPE`:

- **`robotiq`** (Robotiq **Hand-E** / 2F über URCap): Robotiq-URCap am Pendant installieren/aktivieren.
  Das Backend spricht den Greifer über das URCap-Socket (Port **63352**) an (Aktivieren, Position
  0=offen/255=zu, Speed/Force) – gleiches Protokoll für Hand-E und 2F. Keine weitere Config nötig
  außer der Roboter-IP.
- **`digital`** (einfacher Greifer/Pneumatik/Relais): an einen **Digital-Ausgang** der Control Box.
  `GRIPPER_DO_PIN` = Pin-Nummer, `GRIPPER_DO_CLOSED` = ob HIGH „geschlossen" bedeutet. Das Backend
  schaltet den Ausgang via `setStandardDigitalOut`.
- **`none`**: nur Statusflag (kein echter Greifer) – zum Trockentest der Bewegungen.

Verkabelung/Luft/Strom des Greifers gemäß Herstellerdoku; am Pendant testen, dass Öffnen/Schließen
funktioniert, bevor die App ihn fährt.

## 6. Kamera positionieren

- Die **stationäre Kamera** so montieren, dass sie das Teil **an der `front`/`back`-Pose** formatfüllend
  und scharf sieht (gutes, blendfreies Licht – entscheidend für OCR).
- Die App streamt die im Browser gewählte Kamera. Praktisch: die App auf dem Laptop öffnen, dessen
  Webcam fest montiert ist, **oder** eine USB-Kamera als Standardkamera des Browsers wählen.
- Optional (bessere Treffer): Label-ROI/Zoom – aktuell liest die OCR das ganze Bild. Für die
  Feinarbeit kann man die `front/back`-Posen näher an die Kamera legen.

## 7. Erster sicherer Testlauf

1. **Reduzierte Geschwindigkeit** am Pendant (Speed-Slider ~20–30 %). Hand am Not-Halt.
2. `GRIPPER_TYPE=none`, erst nur Bewegungen prüfen: Zyklus starten, Posen beobachten.
3. Dann Greifer aktivieren, mit einem unkritischen Teil testen.
4. Settle-/Pausenzeiten in `.env` justieren (`INSPECT_SETTLE_S`, `STEP_PAUSE_S`), bis Bewegung,
   Stillstand und Kameraaufnahme sauber zusammenpassen.
5. OCR-Qualität über `OCR_ATTEMPTS` und die Beleuchtung/Pose verbessern.

## 8. Sicherheit (immer)

- Kollaborative Kraft-/Geschwindigkeitsgrenzen (ISO/TS 15066) am Pendant **vor** dem ersten Lauf.
- Not-Halt in Reichweite; Arbeitsbereich frei; niemand im Bewegungsraum bei den ersten Läufen.
- Die App kennt einen **NOT-HALT**-Button (sendet `stop` → `speedStop`/`stopL`).

## 9. Troubleshooting

| Symptom | Ursache / Fix |
|---|---|
| `health` zeigt `robot_connected: false` | Roboter nicht im Remote-Control-Modus, Bremsen nicht gelöst, falsche IP, Firewall. |
| Backend bricht beim Start ab | `RTDEControlInterface` kann Skript nicht laden → Remote Control aktivieren, Roboter betriebsbereit. |
| Roboter fährt falsche Posen | TCP nicht/falsch gesetzt, oder Posen im Mock geteacht. Posen am echten Roboter neu teachen. |
| Greifer reagiert nicht | falscher `GRIPPER_TYPE`/Pin; Robotiq-URCap nicht aktiv; Greifer am Pendant testen. |
| OCR erkennt nichts | Label zu klein/unscharf/blendend; `front`-Pose näher, besseres Licht, `OCR_ATTEMPTS` erhöhen. |
## Two-camera pose estimation prototype

The accompanying [Ali et al. paper](Multi-View_Camera_Pose_Estimation_for_Robotic_Arm_.pdf)
combines views from a single arm-mounted camera using known robot poses. For two fixed
cameras, the offline prototype in `backend/perception/multiview_pose.py` triangulates known
target features and fits their rigid geometry in the robot-base frame. It reports per-camera
reprojection errors in pixels and the smallest triangulation angle. Small angles mean depth is
weakly constrained, even if the reprojection errors are low.

1. Calibrate each fixed camera at the operating resolution. Express the extrinsics in the same
   robot-base frame. Use a calibration target with known positions in that frame to estimate
   the transform from base coordinates into each camera.
2. Capture both views while the part is stationary. Mark the same four or more non-collinear
   points in both images. A rigid tag on the part gives points with known metric positions.
   Use the original pixels and preserve the same point order across all three arrays.
3. Supply a JSON file containing `target_points_m` (an array of `[x, y, z]` in the target
   frame) and `cameras` (two entries). Each camera entry takes `intrinsics` (OpenCV's
   3×3 camera matrix), `distortion`, `base_to_camera_rvec` (Rodrigues vector in radians),
   `base_to_camera_translation_m` (3 values), and `image_points_px` (one `[u, v]` per target
   point).

Save the JSON as `backend/data/observations.json` to run it with the vision dependencies
in the backend container.

```bash
podman exec ur3-backend python /app/perception/multiview_pose.py /app/data/observations.json
```

The result describes the target frame in robot-base coordinates. This tool takes recorded
observations and has no robot-command connection.
