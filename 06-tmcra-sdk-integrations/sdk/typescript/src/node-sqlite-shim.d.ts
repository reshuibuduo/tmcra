declare module "node:sqlite" {
  export class DatabaseSync {
    constructor(path: string);
    exec(sql: string): void;
    prepare(sql: string): {
      run(...values: unknown[]): void;
      all(...values: unknown[]): unknown[];
    };
    close(): void;
  }
}
