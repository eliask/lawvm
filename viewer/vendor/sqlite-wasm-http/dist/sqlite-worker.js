// This is the entry point for an SQLite worker thread
import { installHttpVfs } from './vfs-http.js';
import { installSyncHttpVfs } from './vfs-sync-http.js';
import { debug } from './vfs-http-types.js';
import sqlite3InitModule from '../deps/dist/sqlite3-bundler-friendly.mjs';
debug['threads']('SQLite worker started');
globalThis.onmessage = ({ data }) => {
    debug['threads']('SQLite received green light', data);
    const msg = data;
    sqlite3InitModule().then((sqlite3) => {
        debug['threads']('SQLite init');
        sqlite3.initWorker1API();
        if (typeof msg.httpChannel === 'object') {
            installHttpVfs(sqlite3, msg.httpChannel, msg.httpOptions);
        }
        else if (msg.httpChannel === true) {
            if (typeof globalThis.XMLHttpRequest === 'undefined') {
                throw new Error('Browser worker XHR is unavailable; sync HTTP backend requires XMLHttpRequest in workers.');
            }
            installSyncHttpVfs(sqlite3, msg.httpOptions);
        }
    });
};
//# sourceMappingURL=sqlite-worker.js.map
