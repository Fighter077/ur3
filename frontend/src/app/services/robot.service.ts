import { Injectable } from '@angular/core';
import { BehaviorSubject, Subject } from 'rxjs';
import { BACKEND } from '../config';
import { Analysis, ControlMode, HandoverObservation, InspectionSource, InspectionState, InventoryRecord, RobotState } from '../interfaces';

/**
 * Eine Verbindung pro Client:
 *  - WebSocket (/ws):  Telemetrie (robot_state) + Analyse (analysis) herein, Steuerbefehle hinaus
 *  - WebRTC   (/offer): Kamera-Stream des Clients ans Backend (für YOLO)
 */
@Injectable({ providedIn: 'root' })
export class RobotService {
  private ws: WebSocket | null = null;
  private pc: RTCPeerConnection | null = null;
  private remotePeer: RTCPeerConnection | null = null;
  private remoteSourceId: string | null = null;
  private remoteTimeout: ReturnType<typeof setTimeout> | null = null;
  private localStream: MediaStream | null = null;
  private clientId = crypto.randomUUID?.() ?? Array.from(
    crypto.getRandomValues(new Uint8Array(16)),
    (byte) => byte.toString(16).padStart(2, '0'),
  ).join('');
  private stillTimer: any = null;
  private stillCanvas = document.createElement('canvas');
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private autoReconnect = false;
  private connecting = false;
  private generation = 0;
  private videoEl: HTMLVideoElement | null = null;
  private socketTimeout: ReturnType<typeof setTimeout> | null = null;
  private cancelSocketConnect: (() => void) | null = null;
  private useNetworkCamera = false;

  connectionState$ = new BehaviorSubject<string>('disconnected');
  robotState$ = new BehaviorSubject<RobotState | null>(null);
  robotDefaultIp$ = new BehaviorSubject<string | null>(null);
  analysis$ = new BehaviorSubject<Analysis | null>(null);
  inspection$ = new BehaviorSubject<InspectionState | null>(null);
  inventory$ = new BehaviorSubject<InventoryRecord[]>([]);
  ack$ = new Subject<any>();
  gestureTrigger$ = new Subject<void>();
  cameraError$ = new Subject<string>();
  remoteCameraAvailable$ = new BehaviorSubject(false);
  remoteCameraStream$ = new BehaviorSubject<MediaStream | null>(null);
  networkCameraReady$ = new BehaviorSubject(false);
  networkCameraEnded$ = new Subject<void>();
  cameraSelection$ = new Subject<boolean>();
  controlMode$ = new Subject<ControlMode>();
  handoverObservation$ = new Subject<HandoverObservation>();

  selectCamera(useNetworkCamera: boolean): boolean {
    if (!this.send({ action: 'select_camera', network: useNetworkCamera })) return false;
    this.useNetworkCamera = useNetworkCamera;
    this.analysis$.next(null);
    return true;
  }

  /** Absolute URL eines vom Backend ausgelieferten Label-Bildes (/data/...). */
  imageUrl(path: string | null | undefined): string | null {
    return path ? `${BACKEND.http}${path}` : null;
  }

  async getInterfaceQr(): Promise<{ url: string; png: string }> {
    const response = await fetch(`${BACKEND.http}/interface-qr`, {
      cache: 'no-store',
      signal: AbortSignal.timeout(5000),
    });
    if (!response.ok) throw new Error(`QR code unavailable: ${response.status}`);
    return response.json();
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
      this.retryLocalCamera();
    } catch (error) {
      if (generation !== this.generation) return;
      this.closeTransport();
      this.scheduleReconnect();
    } finally {
      if (generation === this.generation) this.connecting = false;
    }
  }

  retryLocalCamera() {
    const generation = this.generation;
    if (!this.videoEl || this.pc || !this.isCurrent(generation)) return;
    const started = this.startWebRTC(this.videoEl, generation);
    const peer = this.pc;
    void started.catch((error) => {
      if (!this.isCurrent(generation) || this.pc !== peer) return;
      this.stopMedia();
      console.warn('Camera connection failed', error);
      this.cameraError$.next('Local camera unavailable. Robot controls remain connected.');
    });
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
    this.useNetworkCamera = false;
    this.setRemoteSource(null);
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
            this.setRemoteSource(msg.remote_camera_id);
            this.applyCameraSelection(msg.remote_camera_selected === true);
            if (msg.control_mode === 'gesture' || msg.control_mode === 'spacebar' || msg.control_mode === 'handover') {
              this.controlMode$.next(msg.control_mode);
            }
            break;
          case 'control_mode':
            this.controlMode$.next(msg.mode);
            break;
          case 'handover_observation':
            this.handoverObservation$.next(msg);
            break;
          case 'camera_source':
            this.setRemoteSource(msg.sourceId);
            this.applyCameraSelection(msg.network === true);
            break;
          case 'camera_selection':
            this.applyCameraSelection(msg.network === true);
            break;
          case 'network_camera_ended':
            if (msg.sourceId === this.remoteSourceId && this.remotePeer?.connectionState === 'connected') {
              this.failRemoteCamera(this.remotePeer);
            }
            break;
          case 'robot_state':
            this.robotState$.next(msg.state as RobotState);
            break;
          case 'analysis':
            if ((msg.network_camera === true) === this.useNetworkCamera) {
              this.analysis$.next(msg as Analysis);
            }
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
            if ((msg.network_camera === true) === this.useNetworkCamera) this.gestureTrigger$.next();
            break;
        }
      };
    });
  }

  private async startWebRTC(videoEl: HTMLVideoElement, generation: number) {
    const pc = new RTCPeerConnection();
    this.pc = pc;
    pc.onconnectionstatechange = () => {
      if (this.isCurrent(generation) && pc.connectionState === 'failed') {
        this.stopMedia();
        this.cameraError$.next('Local camera connection failed.');
      }
    };

    // Hohe Auflösung + Bitrate: der Text auf Bauteil-Labels ist klein -> für OCR zählt Schärfe,
    // nicht Framerate. Niedrige FPS lassen die Bitrate in Bildqualität fliessen.
    const stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: 'environment' }, width: { ideal: 1920 }, frameRate: { ideal: 15 } },
    });
    if (!this.isCurrent(generation)) {
      stream.getTracks().forEach((track) => track.stop());
      pc.close();
      return;
    }
    this.localStream = stream;
    videoEl.srcObject = stream;
    videoEl.play().catch(() => {});

    const track = stream.getVideoTracks()[0];
    pc.addTransceiver(track, { direction: 'sendonly', streams: [stream] });

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
    await this.negotiateCamera(pc, '/offer');
    if (!this.isCurrent(generation)) return;

    // Full-Res-Standbilder regelmässig ans Backend posten (scharf, unkomprimiert -> OCR).
    this.startStillCapture(videoEl);
  }

  private setRemoteSource(sourceId: unknown) {
    const id = typeof sourceId === 'string' && sourceId ? sourceId : null;
    if (id === this.remoteSourceId) return;
    this.closeRemoteCamera();
    this.applyCameraSelection(false);
    if (this.remoteSourceId !== null) this.remoteCameraAvailable$.next(false);
    this.remoteSourceId = id;
    if (id !== null) {
      this.remoteCameraAvailable$.next(true);
      this.retryRemoteCamera();
    }
  }

  private applyCameraSelection(network: boolean) {
    if (this.useNetworkCamera === network) return;
    this.useNetworkCamera = network;
    this.analysis$.next(null);
    this.cameraSelection$.next(network);
  }

  retryRemoteCamera() {
    if (!this.remoteSourceId || this.remotePeer || !this.isCurrent(this.generation)) return;
    const sourceId = this.remoteSourceId;
    const pc = new RTCPeerConnection();
    this.remotePeer = pc;
    pc.addTransceiver('video', { direction: 'recvonly' });
    pc.ontrack = ({ track, streams }) => {
      if (this.remotePeer !== pc) return;
      this.remoteCameraStream$.next(streams[0] ?? new MediaStream([track]));
      track.onended = () => this.failRemoteCamera(pc);
    };
    pc.onconnectionstatechange = () => {
      if (this.remotePeer !== pc) return;
      if (pc.connectionState === 'connected') {
        if (this.remoteTimeout !== null) clearTimeout(this.remoteTimeout);
        this.remoteTimeout = null;
        this.networkCameraReady$.next(true);
      } else if (pc.connectionState === 'failed' || pc.connectionState === 'closed') {
        this.failRemoteCamera(pc);
      }
    };
    this.remoteTimeout = setTimeout(() => this.failRemoteCamera(pc), 15000);
    void this.negotiateCamera(pc, '/network-camera', sourceId).catch(() => this.failRemoteCamera(pc));
  }

  private failRemoteCamera(pc: RTCPeerConnection) {
    if (this.remotePeer !== pc) return;
    this.closeRemoteCamera();
    this.networkCameraEnded$.next();
  }

  private closeRemoteCamera() {
    if (this.remoteTimeout !== null) clearTimeout(this.remoteTimeout);
    this.remoteTimeout = null;
    const pc = this.remotePeer;
    this.remotePeer = null;
    pc?.close();
    this.remoteCameraStream$.next(null);
    this.networkCameraReady$.next(false);
  }

  private async negotiateCamera(pc: RTCPeerConnection, path: string, sourceId?: string) {
    await pc.setLocalDescription(await pc.createOffer());
    const deadline = Date.now() + 5000;
    while (pc.iceGatheringState !== 'complete' && pc.signalingState !== 'closed' && Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
    if (pc.signalingState === 'closed') return;
    const response = await fetch(`${BACKEND.http}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        clientId: this.clientId, sourceId,
        sdp: pc.localDescription?.sdp, type: pc.localDescription?.type,
      }),
      signal: AbortSignal.timeout(10000),
    });
    if (!response.ok) throw new Error(`Camera offer failed: ${response.status}`);
    const answer = await response.json();
    if (pc.connectionState !== 'closed') await pc.setRemoteDescription(answer);
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
          fetch(`${BACKEND.http}/snapshot?clientId=${encodeURIComponent(this.clientId)}`, {
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
  startInspection(partType: string, source: InspectionSource = 'manual') {
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
  setControlMode(mode: ControlMode) {
    this.send({ action: 'set_control_mode', mode });
  }
  setRobot(mock: boolean, ip: string) {
    this.send({ action: 'set_robot', mock, ip });
  }
}
