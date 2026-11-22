import { Injectable } from '@angular/core';
import { BehaviorSubject, Subject } from 'rxjs';
import { BACKEND } from '../config';
import { Analysis, InspectionState, InventoryRecord, RobotState } from '../interfaces';

/**
 * Eine Verbindung pro Client:
 *  - WebSocket (/ws):  Telemetrie (robot_state) + Analyse (analysis) herein, Steuerbefehle hinaus
 *  - WebRTC   (/offer): Kamera-Stream des Clients ans Backend (für YOLO)
 */
@Injectable({ providedIn: 'root' })
export class RobotService {
  private ws: WebSocket | null = null;
  private pc: RTCPeerConnection | null = null;
  private localStream: MediaStream | null = null;
  private clientId = crypto.randomUUID();
  private stillTimer: any = null;
  private stillCanvas = document.createElement('canvas');
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private autoReconnect = false;
  private connecting = false;
  private generation = 0;
  private videoEl: HTMLVideoElement | null = null;
  private socketTimeout: ReturnType<typeof setTimeout> | null = null;
  private cancelSocketConnect: (() => void) | null = null;

  connectionState$ = new BehaviorSubject<string>('disconnected');
  robotState$ = new BehaviorSubject<RobotState | null>(null);
  robotDefaultIp$ = new BehaviorSubject<string | null>(null);
  analysis$ = new BehaviorSubject<Analysis | null>(null);
  inspection$ = new BehaviorSubject<InspectionState | null>(null);
  inventory$ = new BehaviorSubject<InventoryRecord[]>([]);
  ack$ = new Subject<any>();
  gestureTrigger$ = new Subject<void>();
  cameraError$ = new Subject<string>();

  /** Absolute URL eines vom Backend ausgelieferten Label-Bildes (/data/...). */
  imageUrl(path: string | null | undefined): string | null {
    return path ? `${BACKEND.http}${path}` : null;
  }

  // ---------------- Verbindung ----------------
  async connect(videoEl: HTMLVideoElement) {
    this.videoEl = videoEl;
    this.autoReconnect = true;
    if (this.connecting || this.ws?.readyState === WebSocket.OPEN) return;
    if (this.reconnectTimer !== null) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
    const generation = ++this.generation;
    this.connecting = true;
    this.connectionState$.next('connecting…');
    try {
      await this.openWebSocket(generation);
      if (!this.isCurrent(generation)) return;
      // Control/telemetry work even while camera permission is pending or denied.
      this.connectionState$.next('connected');
      void this.startWebRTC(videoEl, generation).catch((error) => {
        if (!this.isCurrent(generation)) return;
        this.stopMedia();
        console.warn('Camera connection failed', error);
        this.cameraError$.next('Camera unavailable; button and Space controls remain connected.');
      });
    } catch (error) {
      if (generation !== this.generation) return;
      this.closeTransport();
      this.scheduleReconnect();
    } finally {
      if (generation === this.generation) this.connecting = false;
    }
  }

  disconnect(videoEl?: HTMLVideoElement) {
    this.autoReconnect = false;
    ++this.generation;
    this.connecting = false;
    if (this.reconnectTimer !== null) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
    this.closeTransport();
    if (videoEl) videoEl.srcObject = null;
    this.connectionState$.next('disconnected');
  }

  private isCurrent(generation: number): boolean {
    return generation === this.generation && this.autoReconnect &&
      this.ws?.readyState === WebSocket.OPEN;
  }

  private scheduleReconnect() {
    if (!this.autoReconnect || this.reconnectTimer !== null || !this.videoEl) return;
    this.connectionState$.next('reconnecting…');
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      if (this.autoReconnect && this.videoEl) void this.connect(this.videoEl);
    }, 1500);
  }

  private stopMedia() {
    this.stopStillCapture();
    if (this.pc) this.pc.onconnectionstatechange = null;
    this.pc?.close();
    this.pc = null;
    this.localStream?.getTracks().forEach((t) => t.stop());
    this.localStream = null;
    if (this.videoEl) this.videoEl.srcObject = null;
  }

  private closeTransport() {
    this.cancelSocketConnect?.();
    this.cancelSocketConnect = null;
    if (this.socketTimeout !== null) clearTimeout(this.socketTimeout);
    this.socketTimeout = null;
    this.stopMedia();
    if (this.ws) {
      this.ws.onclose = null;
      this.ws.onmessage = null;
    }
    this.ws?.close();
    this.ws = null;
    this.robotState$.next(null);
    this.analysis$.next(null);
    this.inspection$.next(null);
  }

  private openWebSocket(generation: number): Promise<void> {
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(`${BACKEND.ws}/ws?clientId=${this.clientId}`);
      this.ws = ws;
      this.cancelSocketConnect = () => reject(new Error('Connection cancelled'));
      const timeout = setTimeout(() => {
        reject(new Error('Backend connection timed out'));
        ws.close();
      }, 5000);
      this.socketTimeout = timeout;
      ws.onopen = () => {
        clearTimeout(timeout);
        if (this.ws === ws) {
          this.socketTimeout = null;
          this.cancelSocketConnect = null;
        }
        resolve();
      };
      ws.onerror = () => { clearTimeout(timeout); reject(new Error('Backend unavailable')); };
      ws.onclose = () => {
        clearTimeout(timeout);
        reject(new Error('Backend disconnected'));
        if (this.ws !== ws || generation !== this.generation) return;
        ++this.generation;
        this.connecting = false;
        this.closeTransport();
        this.scheduleReconnect();
      };
      ws.onmessage = (ev) => {
        if (!this.isCurrent(generation)) return;
        let msg;
        try { msg = JSON.parse(ev.data); } catch { return; }
        switch (msg.type) {
          case 'robot_config':
            this.robotDefaultIp$.next(msg.default_ip);
            break;
          case 'robot_state':
            this.robotState$.next(msg.state as RobotState);
            break;
          case 'analysis':
            this.analysis$.next(msg as Analysis);
            break;
          case 'inspection':
            this.inspection$.next(msg as InspectionState);
            break;
          case 'inventory':
            this.inventory$.next(msg.records as InventoryRecord[]);
            break;
          case 'ack':
            this.ack$.next(msg);
            break;
          case 'gesture_trigger':
            this.gestureTrigger$.next();
            break;
        }
      };
    });
  }

  private async startWebRTC(videoEl: HTMLVideoElement, generation: number) {
    const pc = new RTCPeerConnection({
      iceServers: [{ urls: 'stun:stun.l.google.com:19302' }],
    });
    this.pc = pc;
    pc.onconnectionstatechange = () => {
      if (this.isCurrent(generation) && pc.connectionState === 'failed') this.ws?.close();
    };

    // Hohe Auflösung + Bitrate: der Text auf Bauteil-Labels ist klein -> für OCR zählt Schärfe,
    // nicht Framerate. Niedrige FPS lassen die Bitrate in Bildqualität fliessen.
    const stream = await navigator.mediaDevices.getUserMedia({
      video: { width: { ideal: 1920 }, height: { ideal: 1080 }, frameRate: { ideal: 15 } },
    });
    if (!this.isCurrent(generation)) {
      stream.getTracks().forEach((track) => track.stop());
      pc.close();
      return;
    }
    this.localStream = stream;
    videoEl.srcObject = stream;
    videoEl.play().catch(() => {});

    stream.getTracks().forEach((t) => pc.addTrack(t, stream));

    const sender = pc.getSenders().find((s) => s.track?.kind === 'video');
    if (sender) {
      const p = sender.getParameters();
      if (!p.encodings) p.encodings = [{}];
      p.encodings[0].maxBitrate = 6_000_000;       // ~6 Mbit/s -> volle Auflösung, scharfer Text
      p.encodings[0].maxFramerate = 15;
      p.encodings[0].scaleResolutionDownBy = 1;     // nicht runterskalieren
      await sender.setParameters(p);
    }

    if (!this.isCurrent(generation)) return;
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    if (!this.isCurrent(generation)) return;
    const resp = await fetch(`${BACKEND.http}/offer`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        clientId: this.clientId,
        sdp: pc.localDescription?.sdp,
        type: pc.localDescription?.type,
      }),
      signal: AbortSignal.timeout(10000),
    });
    if (!resp.ok) throw new Error(`Camera offer failed: ${resp.status}`);
    const answer = await resp.json();
    if (!this.isCurrent(generation)) return;
    await pc.setRemoteDescription(new RTCSessionDescription(answer));
    if (!this.isCurrent(generation)) return;

    // Full-Res-Standbilder regelmässig ans Backend posten (scharf, unkomprimiert -> OCR).
    this.startStillCapture(videoEl);
  }

  /** Alle 600 ms ein Vollbild aus dem lokalen Video greifen und ans Backend posten. */
  private startStillCapture(video: HTMLVideoElement) {
    this.stopStillCapture();
    this.stillTimer = setInterval(() => {
      const w = video.videoWidth, h = video.videoHeight;
      if (!w || !h) return;
      this.stillCanvas.width = w;
      this.stillCanvas.height = h;
      const ctx = this.stillCanvas.getContext('2d');
      if (!ctx) return;
      ctx.drawImage(video, 0, 0, w, h);
      this.stillCanvas.toBlob(
        (blob) => {
          if (!blob) return;
          fetch(`${BACKEND.http}/snapshot`, {
            method: 'POST',
            headers: { 'Content-Type': 'image/jpeg' },
            body: blob,
          }).catch(() => {});
        },
        'image/jpeg',
        0.92,
      );
    }, 600);
  }
  private stopStillCapture() {
    if (this.stillTimer) {
      clearInterval(this.stillTimer);
      this.stillTimer = null;
    }
  }

  // ---------------- Steuerbefehle ----------------
  private send(cmd: object): boolean {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(cmd));
      return true;
    }
    return false;
  }

  jog(axis: 'dx' | 'dy' | 'dz' | 'drx' | 'dry' | 'drz', value: number) {
    this.send({ action: 'jog', [axis]: value });
  }
  stopJog() {
    this.send({ action: 'jog', dx: 0, dy: 0, dz: 0, drx: 0, dry: 0, drz: 0 });
  }
  stop() {
    this.send({ action: 'stop' });
  }
  moveTcp(pose: number[], speed?: number) {
    this.send({ action: 'move_tcp', pose, speed });
  }
  moveJoints(q: number[], speed?: number) {
    this.send({ action: 'move_joints', q, speed });
  }
  gripper(closed: boolean) {
    this.send({ action: 'gripper', closed });
  }
  approachUntilContact(direction: number[], maxDistance: number, forceThreshold?: number) {
    this.send({
      action: 'approach_until_contact',
      direction,
      max_distance: maxDistance,
      force_threshold: forceThreshold,
    });
  }

  // ---------------- Inspektion / Inventar ----------------
  startInspection(partType: string, source: 'manual' | 'spacebar' | 'gesture' = 'manual') {
    return this.send({ action: 'start_inspection', part_type: partType, source });
  }
  abortInspection() {
    this.send({ action: 'abort_inspection' });
  }
  listInventory() {
    this.send({ action: 'list_inventory' });
  }
  markReviewed(id: string, fields: Partial<InventoryRecord> = {}) {
    this.send({ action: 'mark_reviewed', id, ...fields });
  }
  teachPose(name: string) {
    this.send({ action: 'teach_pose', name });
  }
  setControlMode(mode: 'gesture' | 'spacebar') {
    this.send({ action: 'set_control_mode', mode });
  }
  setRobot(mock: boolean, ip: string) {
    this.send({ action: 'set_robot', mock, ip });
  }
}
