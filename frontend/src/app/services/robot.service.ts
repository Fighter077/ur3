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

  connectionState$ = new BehaviorSubject<string>('disconnected');
  robotState$ = new BehaviorSubject<RobotState | null>(null);
  analysis$ = new BehaviorSubject<Analysis | null>(null);
  inspection$ = new BehaviorSubject<InspectionState | null>(null);
  inventory$ = new BehaviorSubject<InventoryRecord[]>([]);
  ack$ = new Subject<any>();

  /** Absolute URL eines vom Backend ausgelieferten Label-Bildes (/data/...). */
  imageUrl(path: string | null | undefined): string | null {
    return path ? `${BACKEND.http}${path}` : null;
  }

  // ---------------- Verbindung ----------------
  async connect(videoEl: HTMLVideoElement) {
    await this.openWebSocket();
    await this.startWebRTC(videoEl);
  }

  disconnect(videoEl?: HTMLVideoElement) {
    this.stopStillCapture();
    this.pc?.close();
    this.pc = null;
    this.localStream?.getTracks().forEach((t) => t.stop());
    this.localStream = null;
    if (videoEl) videoEl.srcObject = null;
    this.ws?.close();
    this.ws = null;
    this.connectionState$.next('disconnected');
  }

  private openWebSocket(): Promise<void> {
    return new Promise((resolve) => {
      this.ws = new WebSocket(`${BACKEND.ws}/ws?clientId=${this.clientId}`);
      this.ws.onopen = () => resolve();
      this.ws.onclose = () => this.connectionState$.next('disconnected');
      this.ws.onmessage = (ev) => {
        const msg = JSON.parse(ev.data);
        switch (msg.type) {
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
        }
      };
    });
  }

  private async startWebRTC(videoEl: HTMLVideoElement) {
    this.connectionState$.next('connecting…');
    this.pc = new RTCPeerConnection({
      iceServers: [{ urls: 'stun:stun.l.google.com:19302' }],
    });
    this.pc.onconnectionstatechange = () =>
      this.connectionState$.next(this.pc?.connectionState || 'disconnected');

    // Hohe Auflösung + Bitrate: der Text auf Bauteil-Labels ist klein -> für OCR zählt Schärfe,
    // nicht Framerate. Niedrige FPS lassen die Bitrate in Bildqualität fliessen.
    this.localStream = await navigator.mediaDevices.getUserMedia({
      video: { width: { ideal: 1920 }, height: { ideal: 1080 }, frameRate: { ideal: 15 } },
    });
    videoEl.srcObject = this.localStream;
    videoEl.play().catch(() => {});

    this.localStream.getTracks().forEach((t) => this.pc!.addTrack(t, this.localStream!));

    const sender = this.pc.getSenders().find((s) => s.track?.kind === 'video');
    if (sender) {
      const p = sender.getParameters();
      if (!p.encodings) p.encodings = [{}];
      p.encodings[0].maxBitrate = 6_000_000;       // ~6 Mbit/s -> volle Auflösung, scharfer Text
      p.encodings[0].maxFramerate = 15;
      p.encodings[0].scaleResolutionDownBy = 1;     // nicht runterskalieren
      await sender.setParameters(p);
    }

    const offer = await this.pc.createOffer();
    await this.pc.setLocalDescription(offer);
    const resp = await fetch(`${BACKEND.http}/offer`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        clientId: this.clientId,
        sdp: this.pc.localDescription?.sdp,
        type: this.pc.localDescription?.type,
      }),
    });
    const answer = await resp.json();
    await this.pc.setRemoteDescription(new RTCSessionDescription(answer));

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
  private send(cmd: object) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(cmd));
    }
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
  startInspection(partType: string) {
    this.send({ action: 'start_inspection', part_type: partType });
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
