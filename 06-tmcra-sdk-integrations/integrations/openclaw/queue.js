import { chmod, mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { dirname } from "node:path";

const QUEUE_VERSION = 1;

function now() {
  return new Date().toISOString();
}

function nextDelay(attempts) {
  return Math.min(6 * 60 * 60 * 1000, 1000 * 2 ** Math.min(attempts, 12));
}

export class DurablePendingQueue {
  constructor({ path, logger = console, clock = Date }) {
    this.path = path;
    this.logger = logger;
    this.clock = clock;
    this.items = null;
    this.mutation = Promise.resolve();
  }

  async load() {
    if (this.items) return this.items;
    try {
      const parsed = JSON.parse(await readFile(this.path, "utf8"));
      if (parsed?.version !== QUEUE_VERSION || !Array.isArray(parsed.items)) {
        throw new Error("unsupported queue format");
      }
      this.items = parsed.items;
    } catch (error) {
      if (error?.code === "ENOENT") {
        this.items = [];
      } else {
        const quarantine = `${this.path}.corrupt-${Date.now()}`;
        await rename(this.path, quarantine).catch(() => undefined);
        this.logger.warn?.(
          `tmcra-openclaw: invalid pending queue quarantined at ${quarantine}`,
        );
        this.items = [];
      }
    }
    return this.items;
  }

  async persist() {
    await mkdir(dirname(this.path), { recursive: true, mode: 0o700 });
    const temporary = `${this.path}.${process.pid}.tmp`;
    await writeFile(
      temporary,
      JSON.stringify({ version: QUEUE_VERSION, items: this.items }, null, 2),
      { encoding: "utf8", mode: 0o600 },
    );
    await rename(temporary, this.path);
    await chmod(this.path, 0o600);
  }

  async mutate(operation) {
    const next = this.mutation.then(async () => {
      await this.load();
      const result = await operation(this.items);
      await this.persist();
      return result;
    });
    this.mutation = next.catch(() => undefined);
    return next;
  }

  async enqueue(item) {
    return this.mutate((items) => {
      if (items.some((candidate) => candidate.idempotencyKey === item.idempotencyKey)) {
        return false;
      }
      items.push({
        ...item,
        attempts: 0,
        nextAttemptAt: now(),
        enqueuedAt: now(),
      });
      return true;
    });
  }

  async size() {
    await this.load();
    return this.items.length;
  }

  async drain(send, { limit = 20, force = false } = {}) {
    return this.mutate(async (items) => {
      const currentTime = this.clock.now();
      const due = items
        .filter((item) => force || Date.parse(item.nextAttemptAt) <= currentTime)
        .slice(0, limit);
      let sent = 0;
      for (const item of due) {
        try {
          await send(item);
          const index = items.findIndex(
            (candidate) => candidate.idempotencyKey === item.idempotencyKey,
          );
          if (index >= 0) items.splice(index, 1);
          sent += 1;
        } catch (error) {
          item.attempts += 1;
          item.lastError = error?.name || "send_failed";
          item.nextAttemptAt = new Date(currentTime + nextDelay(item.attempts)).toISOString();
          this.logger.warn?.(
            `tmcra-openclaw: pending ingest remains queued (attempt ${item.attempts})`,
          );
        }
      }
      return { attempted: due.length, sent, remaining: items.length };
    });
  }
}
