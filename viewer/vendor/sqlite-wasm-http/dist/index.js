var __awaiter = (this && this.__awaiter) || function (thisArg, _arguments, P, generator) {
    function adopt(value) { return value instanceof P ? value : new P(function (resolve) { resolve(value); }); }
    return new (P || (P = Promise))(function (resolve, reject) {
        function fulfilled(value) { try { step(generator.next(value)); } catch (e) { reject(e); } }
        function rejected(value) { try { step(generator["throw"](value)); } catch (e) { reject(e); } }
        function step(result) { result.done ? resolve(result.value) : adopt(result.value).then(fulfilled, rejected); }
        step((generator = generator.apply(thisArg, _arguments || [])).next());
    });
};
// This is the user-facing API
// It runs in the user-thread (which is probably the main UI thread)
import '../deps/dist/sqlite3-worker1-promiser-bundler-friendly.js';
import { debug } from './vfs-http-types.js';
import * as VFSHTTP from './vfs-http-types.js';
import { installHttpVfs } from './vfs-http.js';
import { installSyncHttpVfs } from './vfs-sync-http.js';
export * as VFSHTTP from './vfs-http-types.js';
/**
 * Creates a new SQLite worker thread, can accept an optional HTTP backend for HTTP support.
 *
 * The sync backend is particularly inefficient in Node.js and should never be used except for unit-testing browser
 * code.
 *
 * @param {SQLiteOptions} [options] Options object
 * @param {VFSHTTP.Backend | true} [options.http] Optional HTTP backend, either a shared one or a dedicated sync one
 * @returns {Promise<SQLite.Promiser>}
 */
export function createSQLiteThread(options) {
    debug['threads']('Creating new SQLite thread', options);
    let worker;
    const r = new Promise((resolve, reject) => {
        const promiser = sqlite3Worker1Promiser({
            onready: () => {
                resolve(promiser);
            },
            worker: () => {
                try {
                    worker = new Worker(new URL('./sqlite-worker.js', import.meta.url), { type: 'module' });
                    worker.onerror = (event) => console.error('Worker bootstrap failed', event);
                    const backend = options === null || options === void 0 ? void 0 : options.http;
                    // This is the SQLite worker green light
                    if ((backend === null || backend === void 0 ? void 0 : backend.type) === 'shared') {
                        backend.createNewChannel()
                            .then((channel) => {
                            worker.postMessage({ httpChannel: channel, httpOptions: backend.options }, [channel.port]);
                        });
                    }
                    else if ((backend === null || backend === void 0 ? void 0 : backend.type) === 'sync') {
                        worker.postMessage({ httpChannel: true, httpOptions: backend.options });
                    }
                    else {
                        worker.postMessage({});
                    }
                    return worker;
                }
                catch (e) {
                    console.error('Failed to create SQLite worker', e);
                    reject(e);
                }
            }
        });
    }).then((p) => {
        p.close = () => {
            worker.terminate();
        };
        return p;
    });
    return r;
}
const noSharedBufferMsg = 'SharedArrayBuffer is not available. ' +
    'If your browser supports it, the webserver must send ' +
    '"Cross-Origin-Opener-Policy: same-origin "' +
    'and "Cross-Origin-Embedder-Policy: require-corp" headers. ' +
    'Alternatively, if you do not intend to use concurrent connections, ' +
    'pass `sync` to `createHttpBackend` to explicitly create a synchronous ' +
    'HTTP backend and suppress this warning message.';
/**
 * Creates a new HTTP backend worker that can support multiple SQLite threads.
 * The cache is shared only if the environment supports SharedArrayBuffer.
 *
 * This is always the case in Node.js, but it requires a cross-origin isolated
 * environment in the browser.
 *
 * @param {VFSHTTP.Options} [options] Options object
 * @returns {VFSHTTP.Backend}
 */
export function createHttpBackend(options) {
    debug['threads']('Creating new HTTP VFS backend thread');
    if (typeof SharedArrayBuffer === 'undefined' || (options === null || options === void 0 ? void 0 : options.backendType) === 'sync') {
        if ((options === null || options === void 0 ? void 0 : options.backendType) === 'shared')
            throw new Error(noSharedBufferMsg);
        if ((options === null || options === void 0 ? void 0 : options.backendType) !== 'sync')
            console.warn(noSharedBufferMsg + ' Falling back to the legacy HTTP backend.');
        return {
            type: 'sync',
            worker: null,
            options,
            createNewChannel: () => {
                throw new Error('Sync backend does not support channels');
            },
            close: () => Promise.resolve(),
            terminate: () => undefined
        };
    }
    let nextId = 1;
    const worker = new Worker(new URL('./vfs-http-worker.js', import.meta.url), { type: 'module' });
    worker.postMessage({ msg: 'init', options });
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const consumers = {};
    worker.onmessage = ({ data }) => {
        debug['threads']('Received control message reply', data);
        switch (data.msg) {
            case 'ack':
                if (!consumers[data.id]) {
                    console.error('Invalid response received from backend', data);
                    return;
                }
                debug['threads']('New HTTP VFS channel created', consumers);
                consumers[data.id].resolve({
                    port: consumers[data.id].channel.port2,
                    shm: data.shm
                });
                clearTimeout(consumers[data.id].timeout);
                delete consumers[data.id].resolve;
                delete consumers[data.id].timeout;
                return;
        }
    };
    return {
        type: 'shared',
        worker,
        options,
        createNewChannel: function () {
            debug['threads']('Creating a new HTTP VFS channel');
            const channel = new MessageChannel();
            const id = nextId++;
            worker.postMessage({ msg: 'handshake', port: channel.port1, id }, [channel.port1]);
            return new Promise((resolve, reject) => {
                var _a;
                const timeout = setTimeout(() => {
                    delete consumers[id];
                    reject('Timeout while waiting on backend');
                }, (_a = options === null || options === void 0 ? void 0 : options.timeout) !== null && _a !== void 0 ? _a : VFSHTTP.defaultOptions.timeout);
                consumers[id] = { id, channel, resolve, timeout };
            });
        },
        terminate: function () {
            worker.terminate();
        },
        close: function () {
            debug['threads']('Closing the HTTP VFS channel');
            worker.postMessage({ msg: 'close' });
            return new Promise((resolve, reject) => {
                var _a;
                const timeout = setTimeout(() => {
                    reject('Timeout while waiting on backend');
                }, (_a = options === null || options === void 0 ? void 0 : options.timeout) !== null && _a !== void 0 ? _a : VFSHTTP.defaultOptions.timeout);
                worker.onmessage = ({ data }) => {
                    debug['threads']('Received close response', data);
                    if (data.msg === 'ack' && data.id === undefined) {
                        resolve();
                        clearTimeout(timeout);
                    }
                };
            });
        },
    };
}
/**
 * Initialize synchronous SQLite in the current thread, can accept an optional HTTP backend for HTTP support.
 *
 * The sync backend is particularly inefficient in Node.js and should never be used except for unit-testing browser
 * code.
 *
 * @param {SQLiteOptions} [options] Options object
 * @param {VFSHTTP.Backend | true} [options.http] Optional HTTP backend, either a shared one or a dedicated sync one
 * @returns {Promise<SQLite.SQLite3>}
 */
export function initSyncSQLite(options) {
    debug['threads']('Initializing synchronous SQLite', options);
    return import('#sqlite3.js')
        .then((mod) => mod.default())
        .then((sqlite3) => {
        const backend = options === null || options === void 0 ? void 0 : options.http;
        if ((backend === null || backend === void 0 ? void 0 : backend.type) === 'shared') {
            return backend.createNewChannel().then((channel) => {
                installHttpVfs(sqlite3, channel, backend.options);
                return sqlite3;
            });
        }
        else if ((backend === null || backend === void 0 ? void 0 : backend.type) === 'sync') {
            installSyncHttpVfs(sqlite3, backend.options);
            return sqlite3;
        }
        return sqlite3;
    });
}
/**
 * Higher-level API for working with a pool
 * @param {number} [opts.workers] Number of concurrent workers to spawn, @default 1
 * @param {VFSHTTP.Options} [opts.httpOptions] Options to pass to the HTTP backend
 */
export function createSQLiteHTTPPool(opts) {
    var _a;
    return __awaiter(this, void 0, void 0, function* () {
        const backend = createHttpBackend(opts === null || opts === void 0 ? void 0 : opts.httpOptions);
        const workers = [];
        const startq = [];
        for (let i = 0; i < ((_a = opts.workers) !== null && _a !== void 0 ? _a : 1); i++) {
            startq.push(createSQLiteThread({ http: backend })
                .then((worker) => workers.push({
                worker,
                busy: null
            }))
                .then(() => undefined));
        }
        yield Promise.all(startq);
        return {
            backendType: backend.type,
            open: (url) => Promise.all(workers.map((w) => w.worker('open', {
                filename: 'file:' + encodeURI(url),
                vfs: 'http'
            })))
                .then(() => undefined),
            close: () => Promise.all(workers.map((w) => w.worker.close()))
                .then(() => backend.close()),
            exec: function (sql, bind, opts) {
                return __awaiter(this, void 0, void 0, function* () {
                    let w;
                    do {
                        w = workers.find((w) => !w.busy);
                        if (!w)
                            yield Promise.race(workers.map((w) => w.busy)).catch(() => undefined);
                    } while (!w);
                    const results = [];
                    w.busy = w.worker('exec', {
                        sql,
                        bind,
                        rowMode: opts === null || opts === void 0 ? void 0 : opts.rowMode,
                        callback: (row) => {
                            if (row && typeof row === 'object') {
                                results.push((row.row && typeof row.row === 'object') ? row.row : row);
                            }
                        }
                    })
                        .then(() => undefined)
                        .finally(() => {
                        if (!w)
                            throw new Error('Lost worker pool');
                        w.busy = null;
                    });
                    yield w.busy;
                    return results;
                });
            }
        };
    });
}
//# sourceMappingURL=index.js.map
