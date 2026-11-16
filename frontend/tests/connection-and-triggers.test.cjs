// Run with: node --test tests/connection-and-triggers.test.cjs
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const ts = require('typescript');

function setup() {
  const sockets = [], timers = new Map();
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
    crypto: { randomUUID: () => 'test-client' },
    WebSocket: Socket,
    RTCPeerConnection: class { close() {} },
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
      HostListener: noopDecorator, ViewChild: noopDecorator,
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
    service, app, video, sockets, timers, media,
    retry() {
      const entry = [...timers.entries()].find(([, t]) => t.delay === 1500);
      assert.ok(entry, 'A reconnect must be scheduled');
      timers.delete(entry[0]); entry[1].fn();
    },
  };
}

const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };

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
  assert.equal(h.sockets[0].sent[0].action, 'set_control_mode');
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
