export class MemoryPendingTurnQueue {
    records = new Map();
    async enqueue(record) {
        const current = this.records.get(record.idempotencyKey);
        if (current && JSON.stringify(current.body) !== JSON.stringify(record.body)) {
            throw new Error(`pending turn ${record.idempotencyKey} already exists with a different body`);
        }
        if (!current)
            this.records.set(record.idempotencyKey, record);
    }
    async update(idempotencyKey, patch) {
        const current = this.records.get(idempotencyKey);
        if (!current)
            return;
        this.records.set(idempotencyKey, { ...current, ...patch, updatedAt: Date.now() });
    }
    async remove(idempotencyKey) {
        this.records.delete(idempotencyKey);
    }
    async list() {
        return Object.freeze([...this.records.values()].map((record) => ({ ...record, body: { ...record.body, messages: [...record.body.messages] } })));
    }
}
async function nodeFileSystem() {
    return (await import("node:fs/promises"));
}
async function nodePath() {
    return (await import("node:path"));
}
/**
 * Small JSON-file queue. It is opt-in so browser consumers remain zero-runtime
 * dependency; Node consumers can point it at an application data directory.
 * Writes use a temporary file followed by rename for crash-safe replacement.
 */
export class FilePendingTurnQueue {
    writeChain = Promise.resolve();
    filePath;
    constructor(filePath) {
        this.filePath = filePath;
        if (!filePath.trim())
            throw new TypeError("filePath is required");
    }
    async readState() {
        const fs = await nodeFileSystem();
        try {
            const raw = await fs.readFile(this.filePath, "utf8");
            const parsed = JSON.parse(raw);
            if (parsed.version !== 1 || !parsed.records || typeof parsed.records !== "object") {
                throw new Error("invalid TMCRA pending queue format");
            }
            return { version: 1, records: parsed.records };
        }
        catch (error) {
            if (error instanceof Error && "code" in error && error.code === "ENOENT") {
                return { version: 1, records: {} };
            }
            throw error;
        }
    }
    async writeState(state) {
        const fs = await nodeFileSystem();
        const path = await nodePath();
        await fs.mkdir(path.dirname(this.filePath), { recursive: true });
        const temporaryPath = `${this.filePath}.tmp-${processSafeRandom()}`;
        await fs.writeFile(temporaryPath, `${JSON.stringify(state)}\n`, "utf8");
        await fs.rename(temporaryPath, this.filePath);
    }
    async mutate(mutator) {
        const operation = this.writeChain.then(async () => {
            const state = await this.readState();
            mutator(state);
            await this.writeState(state);
        });
        this.writeChain = operation.catch(() => undefined);
        return operation;
    }
    async enqueue(record) {
        await this.mutate((state) => {
            const current = state.records[record.idempotencyKey];
            if (current && JSON.stringify(current.body) !== JSON.stringify(record.body)) {
                throw new Error(`pending turn ${record.idempotencyKey} already exists with a different body`);
            }
            if (!current)
                state.records[record.idempotencyKey] = record;
        });
    }
    async update(idempotencyKey, patch) {
        await this.mutate((state) => {
            const current = state.records[idempotencyKey];
            if (!current)
                return;
            state.records[idempotencyKey] = { ...current, ...patch, updatedAt: Date.now() };
        });
    }
    async remove(idempotencyKey) {
        await this.mutate((state) => {
            delete state.records[idempotencyKey];
        });
    }
    async list() {
        await this.writeChain;
        const state = await this.readState();
        return Object.freeze(Object.values(state.records).map((record) => ({ ...record, body: { ...record.body, messages: [...record.body.messages] } })));
    }
}
export function createFilePendingTurnQueue(filePath) {
    return new FilePendingTurnQueue(filePath);
}
/**
 * Optional SQLite queue for Node 22+ runtimes exposing `node:sqlite`.
 * The import is lazy so the SDK remains usable in browsers and Node 18.
 */
export class SqlitePendingTurnQueue {
    databasePath;
    database;
    constructor(databasePath, database) {
        this.databasePath = databasePath;
        this.database = database;
        this.database.exec(`
      CREATE TABLE IF NOT EXISTS tmcra_pending_turns (
        idempotency_key TEXT PRIMARY KEY,
        payload_json TEXT NOT NULL,
        updated_at INTEGER NOT NULL
      )
    `);
    }
    static async open(databasePath) {
        if (!databasePath.trim())
            throw new TypeError("databasePath is required");
        try {
            const sqlite = await import("node:sqlite");
            return new SqlitePendingTurnQueue(databasePath, new sqlite.DatabaseSync(databasePath));
        }
        catch (error) {
            throw new Error("SqlitePendingTurnQueue requires a Node runtime with node:sqlite", { cause: error });
        }
    }
    async enqueue(record) {
        const existing = this.database.prepare("SELECT payload_json FROM tmcra_pending_turns WHERE idempotency_key = ?").all(record.idempotencyKey)[0];
        if (existing && existing.payload_json !== JSON.stringify(record)) {
            throw new Error(`pending turn ${record.idempotencyKey} already exists with a different body`);
        }
        if (!existing) {
            this.database.prepare("INSERT INTO tmcra_pending_turns(idempotency_key, payload_json, updated_at) VALUES (?, ?, ?)").run(record.idempotencyKey, JSON.stringify(record), Date.now());
        }
    }
    async update(idempotencyKey, patch) {
        const current = this.find(idempotencyKey);
        if (!current)
            return;
        const updated = { ...current, ...patch, updatedAt: Date.now() };
        this.database.prepare("UPDATE tmcra_pending_turns SET payload_json = ?, updated_at = ? WHERE idempotency_key = ?").run(JSON.stringify(updated), Date.now(), idempotencyKey);
    }
    async remove(idempotencyKey) {
        this.database.prepare("DELETE FROM tmcra_pending_turns WHERE idempotency_key = ?").run(idempotencyKey);
    }
    async list() {
        const rows = this.database.prepare("SELECT payload_json FROM tmcra_pending_turns ORDER BY updated_at ASC").all();
        return Object.freeze(rows.map((row) => JSON.parse(row.payload_json)));
    }
    close() {
        this.database.close();
    }
    find(idempotencyKey) {
        const row = this.database.prepare("SELECT payload_json FROM tmcra_pending_turns WHERE idempotency_key = ?").all(idempotencyKey)[0];
        return row?.payload_json ? JSON.parse(row.payload_json) : undefined;
    }
}
function processSafeRandom() {
    const webCrypto = globalThis.crypto;
    if (webCrypto?.randomUUID)
        return webCrypto.randomUUID();
    return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}
//# sourceMappingURL=queue.js.map