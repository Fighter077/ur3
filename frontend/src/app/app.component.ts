import { AfterViewInit, ChangeDetectorRef, Component, ElementRef, HostListener, NgZone, OnDestroy, OnInit, ViewChild } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { MatToolbarModule } from '@angular/material/toolbar';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { MatTableModule } from '@angular/material/table';
import { MatChipsModule } from '@angular/material/chips';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatDividerModule } from '@angular/material/divider';
import { MatButtonToggleModule } from '@angular/material/button-toggle';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { TranslatePipe, TranslateService } from '@ngx-translate/core';
import { RobotService } from './services/robot.service';
import { Analysis, ControlMode, Hand, HandoverObservation, InspectionSource, InspectionState, InventoryRecord, RobotState, SimState } from './interfaces';

// MediaPipe-Hand: Verbindungen der 21 Landmarks (Handgelenk + 5 Finger) für das Overlay.
const HAND_EDGES: [number, number][] = [
  [0, 1], [1, 2], [2, 3], [3, 4],
  [0, 5], [5, 6], [6, 7], [7, 8],
  [5, 9], [9, 10], [10, 11], [11, 12],
  [9, 13], [13, 14], [14, 15], [15, 16],
  [13, 17], [17, 18], [18, 19], [19, 20],
  [0, 17],
];

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [
    CommonModule, FormsModule,
    MatToolbarModule, MatButtonModule, MatIconModule, MatCardModule,
    MatFormFieldModule, MatInputModule, MatSelectModule, MatProgressBarModule, MatTableModule,
    MatChipsModule, MatTooltipModule, MatDividerModule, MatSnackBarModule,
    MatButtonToggleModule, TranslatePipe,
  ],
  templateUrl: './app.component.html',
  styleUrl: './app.component.scss',
})
export class AppComponent implements OnInit, AfterViewInit, OnDestroy {
  @ViewChild('video') videoEl!: ElementRef<HTMLVideoElement>;
  @ViewChild('interfaceQrDialog') interfaceQrDialog!: ElementRef<HTMLDialogElement>;
  interfaceQr: { url: string; png: string } | null = null;
  interfaceQrLoading = false;

  connectionState = 'disconnected';
  connected = false;
  useNetworkCamera = false;
  remoteCameraAvailable = false;
  remoteCameraStream: MediaStream | null = null;
  localCameraFailed = false;
  networkCameraReady = false;
  localCameraAspectRatio: number | null = null;
  networkCameraAspectRatio: number | null = null;
  networkCameraFailed = false;
  robot: RobotState | null = null;
  analysis: Analysis | null = null;
  inspection: InspectionState | null = null;
  inventory: InventoryRecord[] = [];

  partTypes = ['auto', 'HDD', 'optical', 'RAM'];
  partType = 'auto';
  controlMode: ControlMode = 'gesture';
  private controlModeEdited = false;
  localHandover: HandoverObservation | null = null;
  remoteHandover: HandoverObservation | null = null;
  handoverReady = false;
  robotUseReal = false;              // false = Simulation (Mock), true = echter UR3e
  robotIp = '';
  robotIpEdited = false;
  jogSpeed = 0.03;
  poseNames = ['home', 'holder', 'holder_up', 'front', 'back', 'back_clearance', 'collection'];
  private flashSuppressed = false;
  private startPending = false;
  private destroyed = false;
  invColumns = ['id', 'type', 'manufacturer', 'model', 'capacity', 'status', 'images', 'actions'];
  // Ablaufschritte in Reihenfolge (i18n-Keys; Reihenfolge = Ablauf)
  steps = ['steps.pick', 'steps.front', 'steps.back', 'steps.read', 'steps.sort', 'steps.done'];

  // Virtueller Greifer – Zielwerte (vom Backend) und weich interpolierte Istwerte.
  hasSim = false;
  private tx = 0.5; private ty = 0.16; private tOpen = 1;
  gx = 0.5; gy = 0.16; gOpen = 1;
  gHolding = false; gMode = 'ready'; gAction = 'bereit';
  handoverProgress = 0; handoverActive = false;
  private raf = 0;

  constructor(
    private svc: RobotService,
    private zone: NgZone,
    private cdr: ChangeDetectorRef,
    private snack: MatSnackBar,
    private t: TranslateService,
  ) {
    this.svc.connectionState$.pipe(takeUntilDestroyed()).subscribe((s) => {
      this.connectionState = s;
      this.connected = s === 'connected';
      if (this.connected) {
        this.localCameraFailed = false;
        this.networkCameraFailed = false;
        if (this.controlModeEdited) this.svc.setControlMode(this.controlMode);
      } else {
        this.startPending = false;
        this.useNetworkCamera = false;
        this.clearHandover();
      }
    });
    this.svc.robotState$.pipe(takeUntilDestroyed()).subscribe((r) => (this.robot = r));
    this.svc.robotDefaultIp$.pipe(takeUntilDestroyed()).subscribe((ip) => {
      if (ip !== null && !this.robotIpEdited) this.robotIp = ip;
    });
    this.svc.analysis$.pipe(takeUntilDestroyed()).subscribe((a) => this.onAnalysis(a));
    this.svc.inspection$.pipe(takeUntilDestroyed()).subscribe((i) => {
      this.inspection = i;
      if (i && i.state !== 'idle') this.startPending = false;
      if (i?.state === 'PICK') this.flashSuppressed = false;
    });
    this.svc.inventory$.pipe(takeUntilDestroyed()).subscribe((inv) => (this.inventory = inv));
    this.svc.ack$.pipe(takeUntilDestroyed()).subscribe((a) => this.onAck(a));
    this.svc.gestureTrigger$.pipe(takeUntilDestroyed()).subscribe(() => {
      if (this.controlMode === 'gesture') this.startInspection('gesture');
    });
    this.svc.cameraError$.pipe(takeUntilDestroyed()).subscribe((message) => {
      this.localCameraFailed = true;
      this.snack.open(message, 'OK', { duration: 6000 });
    });
    this.svc.remoteCameraAvailable$.pipe(takeUntilDestroyed()).subscribe((available) => {
      this.remoteCameraAvailable = available;
      this.networkCameraFailed = false;
      if (!available) {
        this.clearHandover();
        this.networkCameraReady = false;
        this.networkCameraAspectRatio = null;
      }
    });
    this.svc.networkCameraReady$.pipe(takeUntilDestroyed()).subscribe((ready) => {
      this.networkCameraReady = ready;
    });
    this.svc.remoteCameraStream$.pipe(takeUntilDestroyed()).subscribe((stream) => {
      this.remoteCameraStream = stream;
      if (!stream) this.networkCameraAspectRatio = null;
    });
    this.svc.cameraSelection$.pipe(takeUntilDestroyed()).subscribe((network) => {
      this.useNetworkCamera = network;
    });
    this.svc.controlMode$.pipe(takeUntilDestroyed()).subscribe((mode) => {
      if (this.controlMode !== mode) this.clearHandover();
      this.controlMode = mode;
    });
    this.svc.handoverObservation$.pipe(takeUntilDestroyed()).subscribe((observation) => {
      if (this.controlMode !== 'handover') return;
      if (observation.network_camera) this.remoteHandover = observation;
      else this.localHandover = observation;
      this.handoverReady = observation.ready;
    });
    this.svc.networkCameraEnded$.pipe(takeUntilDestroyed()).subscribe(() => {
      if (this.remoteCameraAvailable) this.onNetworkCameraError();
    });
  }

  private onAck(a: any) {
    if (!a) return;
    if (a.action === 'teach_pose') {
      const r = a.result || {};
      const msg = r.ok
        ? this.t.instant('ack.teachSaved', { name: r.name })
        : this.t.instant('ack.teachFailed', { error: r.error || '' });
      this.snack.open(msg, 'OK', { duration: 2500 });
    } else if (a.action === 'set_robot') {
      const r = a.result || {};
      const msg = r.ok
        ? this.t.instant(r.mock ? 'robot.switchedMock' : 'robot.switchedReal', { ip: r.ip })
        : this.t.instant('robot.failed', { error: r.error || '' });
      this.snack.open(msg, 'OK', { duration: r.ok ? 3000 : 4500 });
      this.robotUseReal = !r.mock; // Toggle an die Realität anpassen (Fallback auf Mock sichtbar)
    } else if (a.action === 'abort_inspection') {
      if (a.result?.was_running) this.snack.open(this.t.instant('ack.aborted'), 'OK', { duration: 2500 });
    } else if (a.action === 'start_inspection' && !a.ok) {
      this.startPending = false;
      this.snack.open(this.t.instant('ack.startFailed', { error: a.result?.error || '' }), 'OK', { duration: 2500 });
    } else if (a.ok === false && a.error) {
      this.snack.open(this.t.instant('ack.error', { error: a.error }), 'OK', { duration: 2500 });
    }
  }

  ngOnInit() {
    // Interpolations-Schleife außerhalb der Angular-Zone, um CD nicht pro Frame zu triggern.
    this.zone.runOutsideAngular(() => {
      const tick = () => {
        const k = 0.25;
        const dx = this.tx - this.gx, dy = this.ty - this.gy, dOpen = this.tOpen - this.gOpen;
        this.gx += dx * k;
        this.gy += dy * k;
        this.gOpen += dOpen * k;
        // nur neu zeichnen, wenn der Greifer sichtbar ist und sich merklich bewegt
        if (this.hasSim && (Math.abs(dx) + Math.abs(dy) + Math.abs(dOpen) > 0.0005)) {
          this.cdr.detectChanges();
        }
        this.raf = requestAnimationFrame(tick);
      };
      this.raf = requestAnimationFrame(tick);
    });
  }
  ngAfterViewInit() {
    queueMicrotask(() => {
      if (!this.destroyed) void this.svc.connect(this.videoEl.nativeElement);
    });
  }

  ngOnDestroy() {
    this.destroyed = true;
    cancelAnimationFrame(this.raf);
    this.svc.disconnect(this.videoEl?.nativeElement);
  }

  private onAnalysis(a: Analysis | null) {
    this.analysis = a;
    if (a?.sim) {
      const s: SimState = a.sim;
      this.tx = s.x; this.ty = s.y; this.tOpen = s.opening;
      this.gHolding = s.holding; this.gMode = s.mode; this.gAction = s.action;
      this.hasSim = true;
    }
    if (a?.handover) {
      this.handoverProgress = a.handover.progress;
      this.handoverActive = a.handover.active;
    }
  }

  // Greifer-Geometrie im SVG-Raum (0..100). s = halbe Backenöffnung.
  get grip() {
    const x = this.gx * 100, y = this.gy * 100;
    const s = 1.2 + this.gOpen * 3.0;
    return {
      x, y, s,
      armY2: Math.max(0, y - 6),                       // Arm von oben bis Handgelenk
      wristX: x - 3.5, wristY: y - 7, wristW: 7, wristH: 4,
      jawLX: x - 1.5, jawRX: x + 1.5, jawBaseY: y - 3, // Backen-Ansatz
      leftTipX: x - s, rightTipX: x + s, tipY: y + 4,  // Backen-Spitzen
      partX: x - 2.6, partY: y - 0.5, labelY: y + 2,   // gehaltenes Teil
      actionX: x + s + 2, actionY: y + 1,
    };
  }

  async toggleConnection() {
    if (this.connected || this.connectionState === 'connecting…' || this.connectionState === 'reconnecting…') {
      this.svc.disconnect(this.videoEl.nativeElement);
    } else {
      await this.svc.connect(this.videoEl.nativeElement);
    }
  }

  async showInterfaceQr() {
    this.interfaceQrDialog.nativeElement.showModal();
    if (this.interfaceQrLoading) return;
    this.interfaceQr = null;
    this.interfaceQrLoading = true;
    try {
      this.interfaceQr = await this.svc.getInterfaceQr();
    } catch {
      this.interfaceQr = null;
    } finally {
      this.interfaceQrLoading = false;
    }
  }

  retryRemoteCamera() {
    this.networkCameraFailed = false;
    this.svc.retryRemoteCamera();
  }

  retryLocalCamera() {
    this.localCameraFailed = false;
    this.svc.retryLocalCamera();
  }

  selectCamera(useNetworkCamera: boolean) {
    if (this.svc.selectCamera(useNetworkCamera)) this.useNetworkCamera = useNetworkCamera;
  }

  onNetworkCameraError() {
    if (this.networkCameraFailed) return;
    this.networkCameraReady = false;
    this.networkCameraFailed = true;
    this.snack.open(this.t.instant('cam.networkError'), 'OK', { duration: 6000 });
  }

  private clearHandover() {
    this.localHandover = null;
    this.remoteHandover = null;
    this.handoverReady = false;
  }

  setControlMode(mode: ControlMode) {
    this.controlModeEdited = true;
    if (this.controlMode !== mode) this.clearHandover();
    this.controlMode = mode;
    this.svc.setControlMode(mode);
  }

  applyRobot() {
    this.svc.setRobot(!this.robotUseReal, this.robotIp);
  }

  // Leertaste startet den Zyklus, wenn der Spacebar-Modus aktiv ist.
  @HostListener('window:keydown', ['$event'])
  onKeydown(e: KeyboardEvent) {
    if (e.code === 'Escape') {        // ESC bricht die laufende Sequenz sofort ab
      this.flashSuppressed = true;
      this.svc.abortInspection();
      return;
    }
    if (e.code !== 'Space' || this.controlMode !== 'spacebar') return;
    if (e.target instanceof HTMLElement &&
        e.target.closest('input, textarea, select, button, [contenteditable="true"], [role="combobox"]')) return;
    e.preventDefault();
    if (!e.repeat && !e.isComposing) this.startInspection('spacebar');
  }

  // --- Inspektion ---
  get cycleRunning(): boolean {
    const s = this.inspection?.state;
    return this.startPending || (!!s && s !== 'idle' && s !== 'DONE' && s !== 'ERROR' && s !== 'ABORTED');
  }

  // Welcher Schritt ist gerade aktiv? (Index in `steps`)
  get activeStepIndex(): number {
    const ins = this.inspection;
    if (!ins) return -1;
    switch (ins.state) {
      case 'PICK': return 0;
      case 'INSPECT': return ins.step.startsWith('back') ? 2 : 1;
      case 'READ': return 3;
      case 'SORT': return 4;
      case 'DONE': return 5;
      default: return -1; // idle / ERROR
    }
  }
  stepState(i: number): 'done' | 'current' | 'pending' {
    const a = this.activeStepIndex;
    if (a < 0) return 'pending';
    return i < a ? 'done' : i === a ? 'current' : 'pending';
  }
  stepIcon(i: number): string {
    const s = this.stepState(i);
    return s === 'done' ? 'check_circle' : s === 'current' ? 'radio_button_checked' : 'radio_button_unchecked';
  }

  startInspection(source: InspectionSource = 'manual') {
    if (!this.connected || this.cycleRunning) return;
    if (source === 'handover' && (!this.handoverReady || !this.robot?.mock)) return;
    this.startPending = true;
    if (!this.svc.startInspection(this.partType, source)) this.startPending = false;
  }
  markReviewed(rec: InventoryRecord) {
    this.svc.markReviewed(rec.id);
  }
  img(path: string | null | undefined) {
    return this.svc.imageUrl(path);
  }

  // --- Jog ---
  startJog(axis: 'dx' | 'dy' | 'dz' | 'drx' | 'dry' | 'drz', dir: number) {
    this.svc.jog(axis, dir * this.jogSpeed);
  }
  stopJog() { this.svc.stopJog(); }
  get flashOn(): boolean {
    return this.connected && !this.flashSuppressed &&
      this.robot !== null && !this.robot.moving &&
      this.inspection?.state === 'INSPECT' && this.inspection?.capture_active === true;
  }
  estop() {
    this.flashSuppressed = true;
    this.svc.abortInspection();
  }
  gripper(closed: boolean) { this.svc.gripper(closed); }
  teachPose(name: string) { this.svc.teachPose(name); }

  fmt(v: number | undefined, d = 3): string {
    return v === undefined || v === null ? '–' : v.toFixed(d);
  }

  // --- Overlay: Hand/Finger (MediaPipe) ---
  handLines(h: Hand): { x1: number; y1: number; x2: number; y2: number }[] {
    const lm = h.landmarks;
    return HAND_EDGES.filter(([a, b]) => lm[a] && lm[b]).map(([a, b]) => ({
      x1: lm[a][0] * 100, y1: lm[a][1] * 100, x2: lm[b][0] * 100, y2: lm[b][1] * 100,
    }));
  }
  handDots(h: Hand): { x: number; y: number; tip: boolean }[] {
    const tips = new Set([4, 8, 12, 16, 20]);
    return h.landmarks.map((p, i) => ({ x: p[0] * 100, y: p[1] * 100, tip: tips.has(i) }));
  }
}
