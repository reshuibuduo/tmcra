import type { IngestRequest, JobStatus } from "./models.ts";

export interface PendingTurnRecord {
  readonly version: 1;
  readonly idempotencyKey: string;
  readonly scopeName: string;
  readonly sessionId: string;
  readonly messageIds: readonly string[];
  readonly body: IngestRequest;
  readonly createdAt: number;
  readonly updatedAt: number;
  readonly jobId?: string;
  readonly statusUrl?: string;
  readonly observedStatus?: JobStatus | string;
  readonly lastError?: string;
}

export interface PendingTurnQueue {
  enqueue(record: PendingTurnRecord): Promise<void>;
  update(idempotencyKey: string, patch: Partial<Omit<PendingTurnRecord, "version" | "idempotencyKey">>): Promise<void>;
  remove(idempotencyKey: string): Promise<void>;
  list(): Promise<readonly PendingTurnRecord[]>;
}

export class MemoryPendingTurnQueue implements PendingTurnQueue {
  private readonly records = new Map<string, PendingTurnRecord>();

  async enqueue(record: PendingTurnRecord): Promise<void> {
    const current = this.records.get(record.idempotencyKey);
    if (current && JSON.stringify(current.body) !== JSON.stringify(record.body)) {
      throw new Error(`pending turn ${record.idempotencyKey} already exists with a different body`);
    }
    if (!current) this.records.set(record.idempotencyKey, record);
  }

  async update(idempotencyKey: string, patch: Partial<Omit<PendingTurnRecord, "version" | "idempotencyKey">>): Promise<void> {
    const current = this.records.get(idempotencyKey);
    if (!current) return;
    this.records.set(idempotencyKey, { ...current, ...patch, updatedAt: Date.now() });
  }

  async remove(idempotencyKey: string): Promise<void> {
    this.records.delete(idempotencyKey);
  }

  async list(): Promise<readonly PendingTurnRecord[]> {
    return Object.freeze([...this.records.values()].map((record) => ({ ...record, body: { ...record.body, messages: [...record.body.messages] } })));
  }
}

interface QueueState {
  version: 1;
  records: Record<string, PendingTurnRecord>;
}

interface FileSystemPromises {
  readFile(path: string, encoding: "utf8"): Promise<string>;
  writeFile(path: string, data: string, encoding: "utf8"): Promise<void>;
  rename(from: string, to: string): Promise<void>;
  mkdir(path: string, options: { recursive: true }): Promise<void>;
}

async function nodeFileSystem(): Promise<FileSystemPromises> {
  return (await import("node:fs/promises")) as unknown as FileSystemPromises;
}

async function nodePath(): Promise<{ dirname(path: string): string }> {
  return (await import("node:path")) as unknown as { dirname(path: string): string };
}

/**
 * Small JSON-file queue. It is opt-in so browser consumers remain zero-runtime
 * dependency; Node consumers can point it at an application data directory.
 * Writes use a temporary file followed by rename for crash-safe replacement.
 */
export class FilePendingTurnQueue implements PendingTurnQueue {
  private writeChain: Promise<void> = Promise.resolve();
  readonly filePath: string;

  constructor(filePath: string) {
    this.filePath = filePath;
    if (!filePath.trim()) throw new TypeError("filePath is required");
  }

  private async readState(): Promise<QueueState> {
    const fs = await nodeFileSystem();
    try {
      const raw = await fs.readFile(this.filePath, "utf8");
      const parsed = JSON.parse(raw) as Partial<QueueState>;
      if (parsed.version !== 1 || !parsed.records || typeof parsed.records !== "object") {
        throw new Error("invalid TMCRA pending queue format");
      }
      return { version: 1, records: parsed.records as Record<string, PendingTurnRecord> };
    } catch (error) {
      if (error instanceof Error && "code" in error && (error as { code?: unknown }).code === "ENOENT") {
        return { version: 1, records: {} };
      }
      throw error;
    }
  }

  private async writeState(state: QueueState): Promise<void> {
    const fs = await nodeFileSystem();
    const path = await nodePath();
    await fs.mkdir(path.dirname(this.filePath), { recursive: true });
    const temporaryPath = `${this.filePath}.tmp-${processSafeRandom()}`;
    await fs.writeFile(temporaryPath, `${JSON.stringify(state)}\n`, "utf8");
    await fs.rename(temporaryPath, this.filePath);
  }

  private async mutate(mutator: (state: QueueState) => void): Promise<void> {
    const operation = this.writeChain.then(async () => {
      const state = await this.readState();
      mutator(state);
      await this.writeState(state);
    });
    this.writeChain = operation.catch(() => undefined);
    return operation;
  }

  async enqueue(record: PendingTurnRecord): Promise<void> {
    await this.mutate((state) => {
      const current = state.records[record.idempotencyKey];
      if (current && JSON.stringify(current.body) !== JSON.stringify(record.body)) {
        throw new Error(`pending turn ${record.idempotencyKey} already exists with a different body`);
      }
      if (!current) state.records[record.idempotencyKey] = record;
    });
  }

  async update(idempotencyKey: string, patch: Partial<Omit<PendingTurnRecord, "version" | "idempotencyKey">>): Promise<void> {
    await this.mutate((state) => {
      const current = state.records[idempotencyKey];
      if (!current) return;
      state.records[idempotencyKey] = { ...current, ...patch, updatedAt: Date.now() };
    });
  }

  async remove(idempotencyKey: string): Promise<void> {
    await this.mutate((state) => {
      delete state.records[idempotencyKey];
    });
  }

  async list(): Promise<readonly PendingTurnRecord[]> {
    await this.writeChain;
    const state = await this.readState();
    return Object.freeze(Object.values(state.records).map((record) => ({ ...record, body: { ...record.body, messages: [...record.body.messages] } })));
  }
}

export function createFilePendingTurnQueue(filePath: string): FilePendingTurnQueue {
  return new FilePendingTurnQueue(filePath);
}

interface SqliteDatabase {
  exec(sql: string): void;
  prepare(sql: string): {
    run(...values: unknown[]): void;
    all(...values: unknown[]): unknown[];
  };
  close(): void;
}

/**
 * Optional SQLite queue for Node 22+ runtimes exposing `node:sqlite`.
 * The import is lazy so the SDK remains usable in browsers and Node 18.
 */
export class SqlitePendingTurnQueue implements PendingTurnQueue {
  readonly databasePath: string;
  private readonly database: SqliteDatabase;

  private constructor(databasePath: string, database: SqliteDatabase) {
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

  static async open(databasePath: string): Promise<SqlitePendingTurnQueue> {
    if (!databasePath.trim()) throw new TypeError("databasePath is required");
    try {
      const sqlite = await import("node:sqlite") as unknown as {
        DatabaseSync: new (path: string) => SqliteDatabase;
      };
      return new SqlitePendingTurnQueue(databasePath, new sqlite.DatabaseSync(databasePath));
    } catch (error) {
      throw new Error("SqlitePendingTurnQueue requires a Node runtime with node:sqlite", { cause: error });
    }
  }

  async enqueue(record: PendingTurnRecord): Promise<void> {
    const existing = this.database.prepare("SELECT payload_json FROM tmcra_pending_turns WHERE idempotency_key = ?").all(record.idempotencyKey)[0] as { payload_json?: string } | undefined;
    if (existing && existing.payload_json !== JSON.stringify(record)) {
      throw new Error(`pending turn ${record.idempotencyKey} already exists with a different body`);
    }
    if (!existing) {
      this.database.prepare("INSERT INTO tmcra_pending_turns(idempotency_key, payload_json, updated_at) VALUES (?, ?, ?)").run(record.idempotencyKey, JSON.stringify(record), Date.now());
    }
  }

  async update(idempotencyKey: string, patch: Partial<Omit<PendingTurnRecord, "version" | "idempotencyKey">>): Promise<void> {
    const current = this.find(idempotencyKey);
    if (!current) return;
    const updated = { ...current, ...patch, updatedAt: Date.now() };
    this.database.prepare("UPDATE tmcra_pending_turns SET payload_json = ?, updated_at = ? WHERE idempotency_key = ?").run(JSON.stringify(updated), Date.now(), idempotencyKey);
  }

  async remove(idempotencyKey: string): Promise<void> {
    this.database.prepare("DELETE FROM tmcra_pending_turns WHERE idempotency_key = ?").run(idempotencyKey);
  }

  async list(): Promise<readonly PendingTurnRecord[]> {
    const rows = this.database.prepare("SELECT payload_json FROM tmcra_pending_turns ORDER BY updated_at ASC").all() as Array<{ payload_json: string }>;
    return Object.freeze(rows.map((row) => JSON.parse(row.payload_json) as PendingTurnRecord));
  }

  close(): void {
    this.database.close();
  }

  private find(idempotencyKey: string): PendingTurnRecord | undefined {
    const row = this.database.prepare("SELECT payload_json FROM tmcra_pending_turns WHERE idempotency_key = ?").all(idempotencyKey)[0] as { payload_json?: string } | undefined;
    return row?.payload_json ? JSON.parse(row.payload_json) as PendingTurnRecord : undefined;
  }
}

function processSafeRandom(): string {
  const webCrypto = globalThis.crypto;
  if (webCrypto?.randomUUID) return webCrypto.randomUUID();
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}
