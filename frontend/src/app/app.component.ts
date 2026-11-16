import { ChangeDetectorRef, Component, ElementRef, NgZone, OnDestroy, OnInit, ViewChild } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { MatToolbarModule } from '@angular/material/toolbar';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatSelectModule } from '@angular/material/select';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { MatTableModule } from '@angular/material/table';
import { MatChipsModule } from '@angular/material/chips';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatDividerModule } from '@angular/material/divider';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { RobotService } from './services/robot.service';
import { Analysis, Hand, InspectionState, InventoryRecord, RobotState, SimState } from './interfaces';

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
    MatFormFieldModule, MatSelectModule, MatProgressBarModule, MatTableModule,
    MatChipsModule, MatTooltipModule, MatDividerModule, MatSnackBarModule,
  ],
  templateUrl: './app.component.html',
  styleUrl: './app.component.scss',
})
export class AppComponent implements OnInit, OnDestroy {
  @ViewChild('video') videoEl!: ElementRef<HTMLVideoElement>;

  connectionState = 'getrennt';
  connected = false;
  robot: RobotState | null = null;
  analysis: Analysis | null = null;
  inspection: InspectionState | null = null;
  inventory: InventoryRecord[] = [];

  partTypes = ['HDD', 'optical', 'RAM'];
  partType = 'HDD';
  jogSpeed = 0.03;
  poseNames = ['home', 'holder', 'holder_up', 'front', 'back', 'collection'];
  invColumns = ['id', 'type', 'manufacturer', 'model', 'capacity', 'status', 'images', 'actions'];

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
  ) {
    this.svc.connectionState$.subscribe((s) => {
      this.connectionState = s;
      this.connected = s === 'connected';
    });
    this.svc.robotState$.subscribe((r) => (this.robot = r));
    this.svc.analysis$.subscribe((a) => this.onAnalysis(a));
    this.svc.inspection$.subscribe((i) => (this.inspection = i));
    this.svc.inventory$.subscribe((inv) => (this.inventory = inv));
    this.svc.ack$.subscribe((a) => this.onAck(a));
  }

  private onAck(a: any) {
    if (!a) return;
    if (a.action === 'teach_pose') {
      const r = a.result || {};
      this.snack.open(r.ok ? `Pose „${r.name}" gespeichert` : `Teach fehlgeschlagen: ${r.error || ''}`,
        'OK', { duration: 2500 });
    } else if (a.action === 'start_inspection' && !a.ok) {
      this.snack.open(`Start nicht möglich: ${a.result?.error || ''}`, 'OK', { duration: 2500 });
    } else if (a.ok === false && a.error) {
      this.snack.open(`Fehler: ${a.error}`, 'OK', { duration: 2500 });
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
  ngOnDestroy() {
    cancelAnimationFrame(this.raf);
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
    if (this.connected || this.connectionState === 'verbinde…') {
      this.svc.disconnect(this.videoEl.nativeElement);
    } else {
      await this.svc.connect(this.videoEl.nativeElement);
    }
  }

  // --- Inspektion ---
  get cycleRunning(): boolean {
    const s = this.inspection?.state;
    return !!s && s !== 'idle' && s !== 'DONE' && s !== 'ERROR';
  }
  startInspection() {
    this.svc.startInspection(this.partType);
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
  estop() { this.svc.stop(); }
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
