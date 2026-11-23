// Run with: node --test tests/connection-and-triggers.test.cjs
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const ts = require('typescript');

function setup({ secure = true } = {}) {
  const sockets = [], peers = [], requests = [], timers = new Map();
  let timerId = 0;
  class Socket {
    static OPEN = 1;
    readyState = 0;
    sent = [];
    constructor() { sockets.push(this); }
    open() { this.readyState = 1; this.onopen?.(); }
    close() { this.readyState = 3; this.onclose?.(); }
    send(data) { this.sent.push(JSON.parse(data)); }
    message(msg) { this.onmessage?.({ data: JSON.stringify(msg) }); }
  }
  const media = { getUserMedia: async () => { throw new Error('No camera'); } };
  const context = {
    exports: {}, console: { warn() {} },
    document: { createElement: () => ({}) },
    crypto: secure ? { randomUUID: () => 'test-client' } : { getRandomValues: (buffer) => buffer.fill(42) },
    WebSocket: Socket,
    RTCPeerConnection: class {
      iceGatheringState = 'complete';
      connectionState = 'new';
      signalingState = 'stable';
      constructor() { peers.push(this); }
      close() { this.connectionState = this.signalingState = 'closed'; }
      addTransceiver(track, options) { this.transceiver = { track, ...options }; }
      getSenders() { return []; }
      async createOffer() { return { type: 'offer', sdp: 'test offer' }; }
      async setLocalDescription(desc) { this.localDescription = desc; }
      async setRemoteDescription(desc) { this.remoteDescription = desc; }
    },
    AbortSignal,
    fetch: async (url, init) => {
      if (url === 'http://backend/interface-qr') {
        return { ok: true, json: async () => ({ url: 'https://192.0.2.10:8443/', png: 'test-png' }) };
      }
      requests.push({ url, payload: JSON.parse(init.body) });
      return { ok: true, json: async () => ({ type: 'answer', sdp: 'test answer' }) };
    },
    navigator: { mediaDevices: media },
    setTimeout: (fn, delay) => { const id = ++timerId; timers.set(id, { fn, delay }); return id; },
    clearTimeout: (id) => timers.delete(id), setInterval: () => 0, clearInterval() {},
    queueMicrotask, HTMLElement: class {},
  };
  const noopDecorator = () => () => {};
  const placeholder = new Proxy({}, { get: () => class {} });
  const modules = {
    '@angular/core': {
      Injectable: noopDecorator, Component: noopDecorator,
      HostListener: noopDecorator, ViewChild: noopDecorator, Input: noopDecorator,
    },
    '@angular/core/rxjs-interop': { takeUntilDestroyed: () => (value) => value },
    rxjs: require('rxjs'),
    '../config': { BACKEND: { ws: 'ws://backend', http: 'http://backend' } },
  };
  context.require = (name) => modules[name] || placeholder;
  function load(file) {
    context.exports = {};
    const source = fs.readFileSync(path.join(__dirname, '..', file), 'utf8');
    const js = ts.transpileModule(source, { compilerOptions: {
      target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS,
      experimentalDecorators: true,
    } }).outputText;
    vm.runInNewContext(`(function(exports, require) {\n${js}\n})(exports, require);`, context, { filename: file });
    return context.exports;
  }
  const Service = load('src/app/services/robot.service.ts').RobotService;
  const service = new Service();
  const App = load('src/app/app.component.ts').AppComponent;
  const app = new App(service, {}, {}, { open() {} }, { instant: (s) => s });
  const video = { srcObject: null, play: async () => {} };
  app.videoEl = { nativeElement: video };
  return {
    service, app, video, sockets, peers, requests, timers, media, load,
    retry() {
      const entry = [...timers.entries()].find(([, t]) => t.delay === 1500);
      assert.ok(entry, 'A reconnect must be scheduled');
      timers.delete(entry[0]); entry[1].fn();
    },
  };
}

const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };

test('calibration wizard sends measured dimensions and a robot-base reference pose', async () => {
  const h = setup();
  const { CalibrationWizardComponent } = h.load('src/app/calibration-wizard.component.ts');
  const payloads = [];
  const wizard = new CalibrationWizardComponent({
    imageUrl: (url) => url,
    async calibrateHandover(payload) {
      payloads.push(JSON.parse(JSON.stringify(payload)));
      return { minimum: 8, captures: payload.action === 'capture' ? 1 : 0 };
    },
  });
  wizard.squareMm = 30;
  await wizard.start();
  assert.equal(wizard.sessionMatches, true);
  await wizard.capture();
  assert.equal(wizard.captures, 1);
  wizard.currentTcp = [0.1, 0.2, 0.3, 0, 0, 1.57];
  wizard.copyCurrentTcp();
  await wizard.captureReference();
  assert.equal(wizard.step, 'grasp');
  assert.deepEqual(payloads, [
    { action: 'start', square_size_m: 0.03 },
    { action: 'capture' },
    { action: 'reference', board_in_base_pose: [0.1, 0.2, 0.3, 0, 0, 1.57] },
  ]);
});

test('QR button opens the dialog with the HTTPS address from the backend', async () => {
  const { app } = setup();
  let opened = false;
  app.interfaceQrDialog = { nativeElement: { showModal() { opened = true; } } };
  const request = app.showInterfaceQr();
  assert.equal(opened, true);
  assert.equal(app.interfaceQrLoading, true);
  await request;
  assert.equal(app.interfaceQrLoading, false);
  assert.equal(app.interfaceQr?.url, 'https://192.0.2.10:8443/');
});

test('page load connects automatically; camera denial leaves controls connected', async () => {
  const h = setup();
  assert.equal(h.app.partType, 'auto');
  h.app.ngAfterViewInit();
  await flush();
  assert.equal(h.sockets.length, 1);
  h.sockets[0].open();
  await flush();
  assert.equal(h.service.connectionState$.value, 'connected');
  assert.equal(h.app.connected, true);
  assert.equal(h.sockets[0].sent.length, 0);
  h.service.disconnect();
});

test('joining without a secure-context UUID still requests camera and reads current control mode', async () => {
  const h = setup({ secure: false });
  let permissionRequests = 0;
  h.media.getUserMedia = async () => {
    permissionRequests++;
    throw new Error('No local camera');
  };
  h.app.ngAfterViewInit();
  await flush();
  h.sockets[0].open();
  await flush();
  assert.equal(h.app.connected, true);
  assert.equal(h.sockets[0].sent.length, 0);
  h.sockets[0].message({ type: 'robot_config', control_mode: 'spacebar' });
  assert.equal(h.app.controlMode, 'spacebar');
  h.sockets[0].close();
  h.retry();
  h.sockets[1].open();
  await flush();
  assert.equal(h.app.connected, true);
  assert.equal(permissionRequests, 2);
  h.service.disconnect();
});

test('backend loss reconnects once and resynchronizes control mode', async () => {
  const h = setup();
  h.app.setControlMode('spacebar');
  const first = h.service.connect(h.video);
  void h.service.connect(h.video);
  assert.equal(h.sockets.length, 1);
  h.sockets[0].open(); await first; await flush();
  h.sockets[0].close();
  assert.equal(h.service.connectionState$.value, 'reconnecting…');
  h.retry();
  assert.equal(h.sockets.length, 2);
  h.sockets[1].open(); await flush();
  assert.equal(h.app.connected, true);
  assert.equal(h.sockets[1].sent[0].mode, 'spacebar');
  assert.equal(h.sockets[1].sent.some((c) => c.action === 'start_inspection'), false);
  h.service.disconnect();
});

test('second camera negotiates a receive-only WebRTC preview and switches analysis without reconnecting', async () => {
  const h = setup();
  let permissionRequests = 0;
  h.media.getUserMedia = async () => {
    permissionRequests++;
    throw new Error('No local camera');
  };
  const pending = h.service.connect(h.video);
  h.sockets[0].open();
  await pending;
  await flush();
  h.sockets[0].message({ type: 'robot_config', remote_camera_id: null });
  assert.equal(h.app.remoteCameraAvailable, false);
  h.sockets[0].message({ type: 'camera_source', sourceId: 'other-browser' });
  await new Promise(setImmediate);
  assert.equal(h.app.remoteCameraAvailable, true);
  assert.equal(h.app.connected, true);
  assert.equal(permissionRequests, 1);
  assert.equal(h.app.useNetworkCamera, false);
  const peer = h.peers.at(-1);
  assert.equal(peer.transceiver.direction, 'recvonly');
  assert.deepEqual(h.requests.at(-1), {
    url: 'http://backend/network-camera',
    payload: { clientId: 'test-client', sourceId: 'other-browser', sdp: 'test offer', type: 'offer' },
  });
  assert.equal(peer.remoteDescription?.type, 'answer');
  const stream = {};
  peer.ontrack({ track: {}, streams: [stream] });
  peer.connectionState = 'connected';
  peer.onconnectionstatechange();
  assert.equal(h.app.networkCameraReady, true);
  assert.equal(h.app.remoteCameraStream, stream);

  h.app.selectCamera(true);
  assert.equal(h.app.useNetworkCamera, true);
  assert.deepEqual(h.sockets[0].sent.at(-1), { action: 'select_camera', network: true });
  h.sockets[0].message({ type: 'analysis', network_camera: false });
  assert.equal(h.app.analysis, null);
  h.sockets[0].message({ type: 'analysis', network_camera: true });
  assert.equal(h.app.analysis.network_camera, true);

  h.sockets[0].message({ type: 'camera_source', sourceId: null });
  assert.equal(h.app.remoteCameraAvailable, false);
  assert.equal(peer.connectionState, 'closed');
  assert.equal(h.app.remoteCameraStream, null);
  assert.equal(h.app.networkCameraAspectRatio, null);
  assert.equal(h.app.useNetworkCamera, false);
  assert.equal(h.app.connected, true);
  assert.equal(h.sockets.length, 1);
  assert.equal(permissionRequests, 1);
  h.service.disconnect();
});

test('initial backend failure retries; intentional disconnect cancels retries', async () => {
  const h = setup();
  const pending = h.service.connect(h.video);
  h.sockets[0].onerror();
  h.sockets[0].close();
  await pending; await flush();
  assert.equal(h.service.connectionState$.value, 'reconnecting…');
  h.service.disconnect();
  assert.equal(h.timers.size, 0);
  assert.equal(h.service.connectionState$.value, 'disconnected');
});

test('handover needs both observations and an explicit start in mock mode', async () => {
  const h = setup();
  const pending = h.service.connect(h.video);
  const socket = h.sockets[0];
  socket.open();
  await pending;
  h.app.setControlMode('handover');
  socket.message({ type: 'robot_state', state: { mock: true } });
  socket.message({ type: 'handover_observation', network_camera: false, hands: [], objects: [], ready: false });
  h.app.startInspection('handover');
  assert.equal(socket.sent.some((cmd) => cmd.action === 'start_inspection'), false);
  socket.message({ type: 'handover_observation', network_camera: true, hands: [], objects: [], ready: true });
  assert.equal(socket.sent.some((cmd) => cmd.action === 'start_inspection'), false);
  socket.message({ type: 'robot_state', state: { mock: false } });
  h.app.startInspection('handover');
  assert.equal(socket.sent.some((cmd) => cmd.action === 'start_inspection'), false);
  socket.message({ type: 'robot_state', state: { mock: true } });
  h.app.startInspection('handover');
  assert.deepEqual(socket.sent.at(-1), { action: 'start_inspection', part_type: 'auto', source: 'handover' });
  h.service.disconnect();
});

test('camera permission resolving after disconnect stops the acquired tracks', async () => {
  const h = setup();
  let grant;
  h.media.getUserMedia = () => new Promise((resolve) => { grant = resolve; });
  const pending = h.service.connect(h.video);
  h.sockets[0].open(); await pending;
  h.service.disconnect();
  let stopped = false;
  grant({ getTracks: () => [{ stop: () => { stopped = true; } }] });
  await flush();
  assert.equal(stopped, true);
  assert.equal(h.video.srcObject, null);
});

test('disconnect while connecting cancels the connection timeout and retry', async () => {
  const h = setup();
  const pending = h.service.connect(h.video);
  h.service.disconnect();
  await pending;
  assert.equal(h.timers.size, 0);
  assert.equal(h.service.connectionState$.value, 'disconnected');
});

test('gesture invokes button start path with selected part and blocks duplicate starts', async () => {
  const h = setup();
  h.app.partType = 'RAM';
  const pending = h.service.connect(h.video);
  h.sockets[0].open(); await pending; await flush();
  h.sockets[0].message({ type: 'gesture_trigger' });
  h.app.startInspection();
  h.sockets[0].message({ type: 'gesture_trigger' });
  const starts = h.sockets[0].sent.filter((c) => c.action === 'start_inspection');
  assert.equal(starts.length, 1);
  assert.equal(starts[0].part_type, 'RAM');
  assert.equal(starts[0].source, 'gesture');
  h.service.disconnect();
});

test('Space uses same start path, ignores repeats, and honors mode/running state', async () => {
  const h = setup();
  const pending = h.service.connect(h.video);
  h.sockets[0].open(); await pending; await flush();
  const key = { code: 'Space', target: null, repeat: false, isComposing: false, preventDefault() {} };
  h.app.onKeydown(key); // gesture mode
  h.app.setControlMode('spacebar');
  h.app.onKeydown({ ...key, repeat: true });
  assert.equal(h.sockets[0].sent.some((c) => c.action === 'start_inspection'), false);
  h.app.onKeydown(key);
  h.app.onKeydown(key);
  const starts = h.sockets[0].sent.filter((c) => c.action === 'start_inspection');
  assert.equal(starts.length, 1);
  assert.equal(starts[0].source, 'spacebar');
  assert.equal(starts[0].part_type, 'auto');
  h.service.disconnect();
});
