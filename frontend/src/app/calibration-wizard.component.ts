import { CommonModule } from '@angular/common';
import { Component, Input } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { RobotService } from './services/robot.service';

interface CameraErrors {
  intrinsics_rms_px: number;
  reference_rms_px: number;
}

@Component({
  selector: 'app-calibration-wizard',
  standalone: true,
  imports: [CommonModule, FormsModule, MatButtonModule],
  template: `
    <div class="previews" *ngIf="step === 'capture' || step === 'reference'">
      <div><span>Local camera</span><video autoplay playsinline muted [srcObject]="localStream"></video></div>
      <div><span>Second camera</span><video autoplay playsinline muted [srcObject]="remoteStream"></video></div>
    </div>
    @switch (step) {
      @case ('capture') {
        <section>
          <h3>Camera intrinsics</h3>
          <a [href]="boardUrl" download>Download the 7 × 5 ChArUco board</a>
          <p>Print it, mount it on a flat surface, and measure one printed square.
             Between captures, move and tilt the board so it covers different parts of both images.
             Hold it still for each capture.</p>
          <label>Measured square size (mm)
            <input type="number" min="1" step="0.1" [(ngModel)]="squareMm" [disabled]="busy">
          </label>
          <div class="actions">
            <button mat-button (click)="start()" [disabled]="busy || !squareMm || squareMm <= 0">
              {{ started ? 'Restart captures' : 'Start captures' }}
            </button>
            <button mat-flat-button (click)="capture()" [disabled]="!sessionMatches || busy">Capture both cameras</button>
            <button mat-button (click)="step = 'reference'" [disabled]="!sessionMatches || busy || captures < minimum">
              Next
            </button>
          </div>
          <p *ngIf="started" role="status">{{ captures }} captures accepted. Minimum {{ minimum }}.</p>
        </section>
      }
      @case ('reference') {
        <section>
          <h3>Register the board in the robot base frame</h3>
          <p>Fix the board at a measured pose. Its origin is the marked top-left corner.
             +X runs along the top edge to the right, +Y down the left edge, and +Z into the board.</p>
          <label>Board pose in base frame (X Y Z in metres, RX RY RZ in radians)
            <input [(ngModel)]="boardPose" placeholder="X Y Z RX RY RZ" spellcheck="false">
          </label>
          <p *ngIf="currentTcp">You can copy the current TCP pose if the active TCP frame physically coincides with the board frame.</p>
          <div class="actions">
            <button mat-button *ngIf="currentTcp" (click)="copyCurrentTcp()" [disabled]="busy">Copy current TCP pose</button>
            <button mat-button (click)="step = 'capture'" [disabled]="busy">Back</button>
            <button mat-flat-button (click)="captureReference()" [disabled]="busy">Capture reference pose</button>
          </div>
        </section>
      }
      @case ('grasp') {
        <section>
          <h3>Define the test part and simulated grasp</h3>
          <p>The target on the part is a separate rigid marker from DICT_4X4_50.
             The approach is offset along negative tool Z.</p>
          <div class="fields">
            <label>Marker ID (0–49)<input type="number" min="0" max="49" step="1" [(ngModel)]="markerId"></label>
            <label>Marker outside edge (mm)<input type="number" min="1" [(ngModel)]="markerMm"></label>
            <label>TCP offset in marker frame (X Y Z in metres)<input [(ngModel)]="tcpOffset"></label>
            <label>TCP rotation in marker frame (RX RY RZ in radians)<input [(ngModel)]="tcpRotation"></label>
            <label>Approach distance (mm)<input type="number" min="1" [(ngModel)]="approachMm"></label>
            <label>Mock workspace minimum (X Y Z in metres)<input [(ngModel)]="workspaceMin"></label>
            <label>Mock workspace maximum (X Y Z in metres)<input [(ngModel)]="workspaceMax"></label>
          </div>
          <div class="actions">
            <button mat-button (click)="step = 'reference'" [disabled]="busy">Back</button>
            <button mat-flat-button (click)="save()" [disabled]="busy">Solve and save calibration</button>
          </div>
        </section>
      }
      @case ('done') {
        <section>
          <h3>Calibration saved</h3>
          @if (result; as errors) {
            <p>Local camera RMS {{ errors.local_camera.intrinsics_rms_px | number:'1.2-2' }} px
               · reference {{ errors.local_camera.reference_rms_px | number:'1.2-2' }} px</p>
            <p>Remote camera RMS {{ errors.remote_camera.intrinsics_rms_px | number:'1.2-2' }} px
               · reference {{ errors.remote_camera.reference_rms_px | number:'1.2-2' }} px</p>
          }
          <p>Validate the reported 3D grasp against measured positions before using it to plan a physical approach.
             Handover can use this calibration in simulation now.</p>
          <button mat-button (click)="step = 'capture'; started = false">New calibration</button>
        </section>
      }
    }
    <p *ngIf="busy" role="status">Working…</p>
    <p *ngIf="error" role="alert" class="error">{{ error }}</p>
  `,
  styles: `
    section, .fields { display: grid; gap: .75rem; }
    section { margin-block: .75rem; }
    h3 { font-weight: 600; }
    label { display: grid; gap: .25rem; }
    input { width: 100%; padding: .4rem; border: 1px solid var(--border); border-radius: .25rem; }
    .actions { display: flex; flex-wrap: wrap; gap: .5rem; align-items: center; }
    .error { color: var(--danger); }
    a { text-decoration: underline; }
    .previews { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .75rem; margin-top: .75rem; }
    video { width: 100%; max-height: 11rem; aspect-ratio: 16 / 9; object-fit: contain; background: #000; }
  `,
})
export class CalibrationWizardComponent {
  @Input() currentTcp: number[] | null = null;
  @Input() localStream: MediaStream | null = null;
  @Input() remoteStream: MediaStream | null = null;

  step: 'capture' | 'reference' | 'grasp' | 'done' = 'capture';
  started = false;
  busy = false;
  error = '';
  captures = 0;
  minimum = 0;
  squareMm: number | null = null;
  private capturedSquareMm: number | null = null;
  markerId: number | null = null;
  markerMm: number | null = null;
  approachMm: number | null = null;
  boardPose = '';
  tcpOffset = '';
  tcpRotation = '';
  workspaceMin = '';
  workspaceMax = '';
  result: { local_camera: CameraErrors; remote_camera: CameraErrors } | null = null;
  readonly boardUrl: string;

  constructor(private svc: RobotService) {
    this.boardUrl = svc.imageUrl('/handover-calibration/board') ?? '';
  }

  get sessionMatches(): boolean {
    return this.started && this.squareMm === this.capturedSquareMm;
  }

  async start() {
    await this.run(async () => {
      const response = await this.svc.calibrateHandover({ action: 'start', square_size_m: (this.squareMm ?? 0) / 1000 });
      this.started = true;
      this.capturedSquareMm = this.squareMm;
      this.minimum = response.minimum;
      this.captures = response.captures;
    });
  }

  async capture() {
    await this.run(async () => {
      const response = await this.svc.calibrateHandover({ action: 'capture' });
      this.captures = response.captures;
    });
  }

  copyCurrentTcp() {
    if (this.currentTcp?.length === 6) this.boardPose = this.currentTcp.join(' ');
  }

  async captureReference() {
    await this.run(async () => {
      await this.svc.calibrateHandover({
        action: 'reference', board_in_base_pose: this.vector(this.boardPose, 6, 'Board pose'),
      });
      this.step = 'grasp';
    });
  }

  async save() {
    await this.run(async () => {
      if (this.markerId === null || !Number.isInteger(this.markerId) || this.markerId < 0 || this.markerId >= 50) {
        throw new Error('Enter a marker ID from 0 to 49.');
      }
      if (!this.markerMm || this.markerMm <= 0 || !this.approachMm || this.approachMm <= 0) {
        throw new Error('Enter the measured marker size and a positive approach distance.');
      }
      const response = await this.svc.calibrateHandover({
        action: 'save',
        grasp: {
          marker_id: this.markerId,
          marker_size_m: this.markerMm / 1000,
          tcp_in_marker_translation_m: this.vector(this.tcpOffset, 3, 'TCP offset'),
          tcp_in_marker_rvec: this.vector(this.tcpRotation, 3, 'TCP rotation'),
          approach_distance_m: this.approachMm / 1000,
          workspace_min_m: this.vector(this.workspaceMin, 3, 'Workspace minimum'),
          workspace_max_m: this.vector(this.workspaceMax, 3, 'Workspace maximum'),
        },
      });
      this.result = response.errors;
      this.step = 'done';
    });
  }

  private vector(value: string, length: number, name: string): number[] {
    const values = value.trim() ? value.trim().split(/[\s,]+/).map(Number) : [];
    if (values.length !== length || !values.every(Number.isFinite)) {
      throw new Error(`${name} needs ${length} finite numbers.`);
    }
    return values;
  }

  private async run(task: () => Promise<void>) {
    if (this.busy) return;
    this.busy = true;
    this.error = '';
    try {
      await task();
    } catch (error) {
      this.error = error instanceof Error ? error.message : 'Calibration failed.';
    } finally {
      this.busy = false;
    }
  }
}
