// This is the VFS layer for the shared backend
// It run in each SQLite worker thread that uses it
// and it is fully synchronous
import * as VFSHTTP from './vfs-http-types.js';
import { debug } from './vfs-http-types.js';
const openFiles = {};
export function installHttpVfs(sqlite3, backend, options) {
    if (typeof SharedArrayBuffer === 'undefined') {
        throw new Error('SharedArrayBuffer is not available. ' +
            'If your browser supports it, the webserver must send ' +
            '"Cross-Origin-Opener-Policy: same-origin "' +
            'and "Cross-Origin-Embedder-Policy: require-corp" headers.');
    }
    if (!backend ||
        !(backend.port instanceof MessagePort) ||
        !(backend.shm instanceof SharedArrayBuffer))
        throw new Error('No backend message channel in options');
    const lock = new Int32Array(backend.shm, backend.shm.byteLength - Int32Array.BYTES_PER_ELEMENT);
    const shm = new Uint8Array(backend.shm, 0, backend.shm.byteLength - Int32Array.BYTES_PER_ELEMENT);
    const capi = sqlite3.capi;
    const wasm = sqlite3.wasm;
    const sqlite3_vfs = capi.sqlite3_vfs;
    const sqlite3_file = capi.sqlite3_file;
    const sqlite3_io_methods = capi.sqlite3_io_methods;
    const httpVfs = new sqlite3_vfs();
    const httpIoMethods = new sqlite3_io_methods();
    httpVfs.$iVersion = 1;
    httpVfs.$szOsFile = capi.sqlite3_file.structInfo.sizeof;
    httpVfs.$mxPathname = 1024;
    httpVfs.$zName = wasm.allocCString('http');
    httpVfs.$xDlOpen = httpVfs.$xDlError = httpVfs.$xDlSym = httpVfs.$xDlClose = null;
    // This the sync/async wait mechanism
    // We send messages to the async backend that manages multiple consumers
    // Then we wait synchronously on a SharedArrayBuffer
    const sendAndWait = (msg) => {
        var _a;
        Atomics.store(lock, 0, VFSHTTP.SYNC.WORKMSG);
        backend.port.postMessage(msg);
        let r, rc;
        do {
            r = Atomics.wait(lock, 0, VFSHTTP.SYNC.WORKMSG, (_a = options === null || options === void 0 ? void 0 : options.timeout) !== null && _a !== void 0 ? _a : VFSHTTP.defaultOptions.timeout);
            rc = Atomics.load(lock, 0);
            // If the backend manages to complete the operation before the wait
            // on the next iteration will be having a pending notify that must
            // be consumed
        } while (r === 'ok' && rc === VFSHTTP.SYNC.WORKMSG);
        if (r === 'timed-out') {
            console.error('Backend timeout', r, lock, msg);
            return -1;
        }
        return rc;
    };
    const ioSyncWrappers = {
        xCheckReservedLock: function (fid, out) {
            debug['vfs']('xCheckReservedLock', fid, out);
            wasm.poke(out, 0, 'i32');
            return 0;
        },
        xClose: function (fid) {
            debug['vfs']('xClose', fid);
            if (!openFiles[fid]) {
                return capi.SQLITE_NOTFOUND;
            }
            delete openFiles[fid];
            return 0;
        },
        xDeviceCharacteristics: function (fid) {
            debug['vfs']('xDeviceCharacteristics', fid);
            return capi.SQLITE_IOCAP_IMMUTABLE;
        },
        xFileControl: function (fid, op, arg) {
            debug['vfs']('xFileControl', fid, op, arg);
            if (op === capi.SQLITE_FCNTL_SYNC)
                return capi.SQLITE_OK;
            return capi.SQLITE_NOTFOUND;
        },
        xFileSize: function (fid, size) {
            debug['vfs']('xFileSize', fid, size);
            if (!openFiles[fid]) {
                return capi.SQLITE_NOTFOUND;
            }
            const r = sendAndWait({ msg: 'xFilesize', url: openFiles[fid].url });
            if (r !== 0) {
                return capi.SQLITE_IOERR;
            }
            const sz = new BigUint64Array(backend.shm, 0, 1)[0];
            debug['vfs']('file size is ', sz);
            wasm.poke(size, sz, 'i64');
            return 0;
        },
        xLock: function (fid, lock) {
            debug['vfs']('xLock', fid, lock);
            return 0;
        },
        xRead: function (fid, dest, n, offset) {
            debug['vfs']('xRead', fid, dest, n, offset);
            if (Number(offset) > Number.MAX_SAFE_INTEGER) {
                // CampToCamp are not supported
                return capi.SQLITE_TOOBIG;
            }
            if (!openFiles[fid]) {
                return capi.SQLITE_NOTFOUND;
            }
            const r = sendAndWait({ msg: 'xRead', url: openFiles[fid].url, n, offset });
            if (r !== 0) {
                console.error('xRead', r);
                return capi.SQLITE_IOERR;
            }
            wasm.heap8u().set(shm.subarray(0, n), dest);
            return capi.SQLITE_OK;
        },
        xSync: function (fid, flags) {
            debug['vfs']('xSync', fid, flags);
            return 0;
        },
        xTruncate: function (fid, size) {
            debug['vfs']('xTruncate', fid, size);
            return 0;
        },
        xUnlock: function (fid, lock) {
            debug['vfs']('xUnlock', fid, lock);
            return 0;
        },
        xWrite: function (fid, src, n, offset) {
            debug['vfs']('xWrite', fid, src, n, offset);
            return capi.SQLITE_READONLY;
        }
    };
    const vfsSyncWrappers = {
        xAccess: function (vfs, name, flags, out) {
            debug['vfs']('xAccess', vfs, name, flags, out);
            if ((flags & capi.SQLITE_OPEN_READONLY) === 0) {
                wasm.poke(out, 0, 'i32');
                return capi.SQLITE_OK;
            }
            const url = wasm.cstrToJs(name);
            const r = sendAndWait({ msg: 'xAccess', url });
            if (r !== 0) {
                console.error('xAccess', r);
                return capi.SQLITE_IOERR;
            }
            const result = new Uint32Array(backend.shm, 0, 1)[0];
            wasm.poke(out, result, 'i32');
            return capi.SQLITE_OK;
        },
        xCurrentTime: function (vfs, out) {
            debug['vfs']('xCurrentTime', vfs, out);
            wasm.poke(out, 2440587.5 + (new Date().getTime() / 86400000), 'double');
            return 0;
        },
        xCurrentTimeInt64: function (vfs, out) {
            debug['vfs']('xCurrentTimeInt64', vfs, out);
            wasm.poke(out, (BigInt(2440587.5) * BigInt(86400000)) + BigInt(new Date().getTime()), 'i64');
            return 0;
        },
        xDelete: function (vfs, name, doSyncDir) {
            debug['vfs']('xDelete', vfs, name, doSyncDir);
            return capi.SQLITE_READONLY;
        },
        xFullPathname: function (vfs, name, nOut, pOut) {
            debug['vfs']('xFullPathname', vfs, name, nOut, pOut);
            const i = wasm.cstrncpy(pOut, name, nOut);
            return i < nOut ? 0 : capi.SQLITE_CANTOPEN;
        },
        xGetLastError: function (vfs, nOut, pout) {
            debug['vfs']('xGetLastError', vfs, nOut, pout);
            return 0;
        },
        xOpen: function (vfs, name, fid, flags, pOutFlags) {
            debug['vfs']('xOpen', vfs, name, fid, flags, pOutFlags);
            if (name === 0) {
                console.error('HTTP VFS does not support anonymous files');
                return capi.SQLITE_CANTOPEN;
            }
            if (typeof name !== 'number') {
                return capi.SQLITE_ERROR;
            }
            wasm.poke(pOutFlags, capi.SQLITE_OPEN_READONLY, 'i32');
            const url = wasm.cstrToJs(name);
            const fh = Object.create(null);
            fh.fid = fid;
            fh.url = url;
            fh.sq3File = new sqlite3_file(fid);
            fh.sq3File.$pMethods = httpIoMethods.pointer;
            openFiles[fid] = fh;
            const r = sendAndWait({ msg: 'xOpen', url });
            if (r < 0) {
                console.error('xOpen', r);
                return capi.SQLITE_IOERR;
            }
            if (r !== 0) {
                debug['vfs']('xOpen', r);
                return capi.SQLITE_CANTOPEN;
            }
            return capi.SQLITE_OK;
        }
    };
    sqlite3.vfs.installVfs({
        io: { struct: httpIoMethods, methods: ioSyncWrappers },
        vfs: { struct: httpVfs, methods: vfsSyncWrappers }
    });
    sqlite3.oo1.DB.dbCtorHelper.setVfsPostOpenSql(httpVfs.pointer, function (oo1Db, sqlite3) {
        var _a;
        sqlite3.capi.sqlite3_busy_timeout(oo1Db, (_a = options === null || options === void 0 ? void 0 : options.timeout) !== null && _a !== void 0 ? _a : VFSHTTP.defaultOptions.timeout);
        sqlite3.capi.sqlite3_exec(oo1Db, [
            'PRAGMA journal_mode=DELETE;',
            'PRAGMA cache_size=0;'
        ], 0, 0, 0);
    });
}
//# sourceMappingURL=vfs-http.js.map