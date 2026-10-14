// User media store: photos and videos uploaded on this device, kept in IndexedDB.
// Nothing leaves the device. Swap this module for a server/blob store later; keep the interface.
//
//   put(key, file)  -> Promise<void>       key = "spot:<id>" | "exercise:<id>"
//   remove(key)     -> Promise<void>
//   loadUrls()      -> Promise<{ [key]: objectURL }>

const DB = "just-standup", STORE = "media";
let urls = {};

function open() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB, 1);
    req.onupgradeneeded = () => req.result.createObjectStore(STORE);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function tx(mode, fn) {
  const db = await open();
  return new Promise((resolve, reject) => {
    const t = db.transaction(STORE, mode);
    const out = fn(t.objectStore(STORE));
    t.oncomplete = () => resolve(out.result);
    t.onerror = () => reject(t.error);
  });
}

export const put = (key, file) => tx("readwrite", (s) => s.put(file, key));
export const remove = (key) => tx("readwrite", (s) => s.delete(key));

export async function loadUrls() {
  Object.values(urls).forEach(URL.revokeObjectURL);
  urls = {};
  try {
    const [keys, blobs] = await Promise.all([tx("readonly", (s) => s.getAllKeys()), tx("readonly", (s) => s.getAll())]);
    keys.forEach((k, i) => (urls[k] = URL.createObjectURL(blobs[i])));
  } catch (err) {
    console.warn("media store unavailable:", err);
  }
  return { ...urls };
}
