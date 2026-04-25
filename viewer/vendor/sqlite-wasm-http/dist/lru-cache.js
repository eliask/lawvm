export default class LRUCache {
  constructor(options = {}) {
    this.maxSize = Number.isFinite(options.maxSize) ? Number(options.maxSize) : Infinity;
    this.sizeCalculation = typeof options.sizeCalculation === 'function' ? options.sizeCalculation : (() => 1);
    this.map = new Map();
    this.totalSize = 0;
  }

  _entrySize(value) {
    try {
      const size = Number(this.sizeCalculation(value));
      return Number.isFinite(size) && size > 0 ? size : 0;
    } catch {
      return 0;
    }
  }

  _evictIfNeeded() {
    while (this.totalSize > this.maxSize && this.map.size > 0) {
      const oldestKey = this.map.keys().next().value;
      const oldestValue = this.map.get(oldestKey);
      this.totalSize -= this._entrySize(oldestValue);
      this.map.delete(oldestKey);
    }
  }

  get(key) {
    if (!this.map.has(key)) return undefined;
    const value = this.map.get(key);
    this.map.delete(key);
    this.map.set(key, value);
    return value;
  }

  set(key, value) {
    if (this.map.has(key)) {
      const oldValue = this.map.get(key);
      this.totalSize -= this._entrySize(oldValue);
      this.map.delete(key);
    }
    this.map.set(key, value);
    this.totalSize += this._entrySize(value);
    this._evictIfNeeded();
    return this;
  }

  delete(key) {
    if (!this.map.has(key)) return false;
    const value = this.map.get(key);
    this.totalSize -= this._entrySize(value);
    return this.map.delete(key);
  }

  clear() {
    this.map.clear();
    this.totalSize = 0;
  }

  has(key) {
    return this.map.has(key);
  }
}
