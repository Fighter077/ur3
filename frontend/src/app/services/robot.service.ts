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

  connectionState$ = new BehaviorSubject<string>('getrennt');
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
    this.pc?.close();
    this.pc = null;
    this.localStream?.getTracks().forEach((t) => t.stop());
    this.localStream = null;
    if (videoEl) videoEl.srcObject = null;
    this.ws?.close();
    this.ws = null;
    this.connectionState$.next('getrennt');
  }

  private openWebSocket(): Promise<void> {
    return new Promise((resolve) => {
      this.ws = new WebSocket(`${BACKEND.ws}/ws?clientId=${this.clientId}`);
      this.ws.onopen = () => resolve();
      this.ws.onclose = () => this.connectionState$.next('getrennt');
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
    this.connectionState$.next('verbinde…');
    this.pc = new RTCPeerConnection({
      iceServers: [{ urls: 'stun:stun.l.google.com:19302' }],
    });
    this.pc.onconnectionstatechange = () =>
      this.connectionState$.next(this.pc?.connectionState || 'getrennt');

    this.localStream = await navigator.mediaDevices.getUserMedia({
      video: { width: { ideal: 1280 }, height: { ideal: 720 }, frameRate: { ideal: 20 } },
    });
    videoEl.srcObject = this.localStream;
    videoEl.play().catch(() => {});

    this.localStream.getTracks().forEach((t) => this.pc!.addTrack(t, this.localStream!));

    // Bandbreite begrenzen (wie in der Referenz)
    const sender = this.pc.getSenders().find((s) => s.track?.kind === 'video');
    if (sender) {
      const p = sender.getParameters();
      if (!p.encodings) p.encodings = [{}];
      p.encodings[0].maxBitrate = 800_000;
      p.encodings[0].maxFramerate = 20;
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
  listInventory() {
    this.send({ action: 'list_inventory' });
  }
  markReviewed(id: string, fields: Partial<InventoryRecord> = {}) {
    this.send({ action: 'mark_reviewed', id, ...fields });
  }
  teachPose(name: string) {
    this.send({ action: 'teach_pose', name });
  }
}
