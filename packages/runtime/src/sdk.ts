/**
 * The dashboard-side SDK: what components and custom dashboard code use inside the sandboxed frame.
 *
 * ```js
 * const result = await tabfree.query("by_region");   // named queries only
 * tabfree.setFilter("region", { members: ["East"] });
 * tabfree.addEventListener("state", () => redraw());
 * ```
 */

import {
  PROTOCOL_VERSION,
  parseHostMessage,
  type Column,
  type DashboardMessage,
  type DashboardState,
  type ErrorCode,
  type FilterSpec,
  type FilterValue,
  type HostMessage,
  type InitMessage,
  type ParameterSpec,
  type Scalar,
  type Theme,
  type Tuple,
} from "./protocol.js";

export interface QueryResult {
  query: string;
  columns: Column[];
  rows: Scalar[][];
  truncated: boolean;
}

export class QueryError extends Error {
  constructor(
    readonly code: ErrorCode,
    message: string,
  ) {
    super(message);
    this.name = "QueryError";
  }
}

interface Pending {
  resolve: (result: QueryResult) => void;
  reject: (error: Error) => void;
}

/** Minimal window surface the SDK needs (lets tests drive it without a real frame). */
export interface FrameWindow {
  parent: { postMessage(message: unknown, targetOrigin: string): void };
  addEventListener(type: "message", listener: (event: MessageEvent) => void): void;
  removeEventListener(type: "message", listener: (event: MessageEvent) => void): void;
}

export class TabfreeClient extends EventTarget {
  #port: MessagePort;
  #pending = new Map<string, Pending>();
  /** Results for the current state, shared by every component that names the same query. */
  #cache = new Map<string, Promise<QueryResult>>();
  #nextId = 0;
  dashboard: InitMessage["dashboard"];
  state: DashboardState;
  theme: Theme;
  filters: Record<string, FilterSpec>;
  parameters: Record<string, ParameterSpec>;

  private constructor(port: MessagePort, init: InitMessage) {
    super();
    this.#port = port;
    this.dashboard = init.dashboard;
    this.state = init.state;
    this.theme = init.theme;
    this.filters = init.filters;
    this.parameters = init.parameters;
    port.onmessage = (event: MessageEvent) => this.#receive(parseHostMessage(event.data));
  }

  /**
   * Say hello to the parent page and wait for it to hand over a private MessagePort and the
   * initial state. Only the parent window can answer; anything else is ignored.
   */
  static connect(win: FrameWindow = window, timeoutMs = 15_000): Promise<TabfreeClient> {
    return new Promise((resolve, reject) => {
      let port: MessagePort | null = null;
      const timer = setTimeout(() => {
        win.removeEventListener("message", onWindowMessage);
        reject(new Error("the TabFree host did not answer"));
      }, timeoutMs);
      const onWindowMessage = (event: MessageEvent) => {
        if (event.source !== win.parent || port !== null) return;
        const message = parseHostMessage(event.data);
        const transferred = event.ports[0];
        if (message?.type !== "connect" || !transferred) return;
        win.removeEventListener("message", onWindowMessage);
        port = transferred;
        port.onmessage = (portEvent: MessageEvent) => {
          const init = parseHostMessage(portEvent.data);
          if (init?.type !== "init" || port === null) return;
          clearTimeout(timer);
          resolve(new TabfreeClient(port, init));
        };
      };
      win.addEventListener("message", onWindowMessage);
      // The frame's origin is opaque and the parent's is unknown to it, so "*" is the only option;
      // the hello carries no data.
      win.parent.postMessage({ v: PROTOCOL_VERSION, type: "hello" }, "*");
    });
  }

  /** Run a named query from tabfree.json with the current filters and parameters. */
  query(name: string): Promise<QueryResult> {
    return this.#cached(`q:${name}`, () => this.#request({ type: "query", query: name }));
  }

  /** The members a filter control can offer. */
  domain(filter: string): Promise<QueryResult> {
    return this.#cached(`d:${filter}`, () => this.#request({ type: "domain", filter }));
  }

  #cached(key: string, load: () => Promise<QueryResult>): Promise<QueryResult> {
    const hit = this.#cache.get(key);
    if (hit) return hit;
    const promise = load();
    this.#cache.set(key, promise);
    promise.catch(() => {
      if (this.#cache.get(key) === promise) this.#cache.delete(key);
    });
    return promise;
  }

  setFilter(filter: string, value: FilterValue | null): void {
    this.#send({ v: 0, type: "setFilter", filter, value });
  }

  setParameter(parameter: string, value: Scalar): void {
    this.#send({ v: 0, type: "setParameter", parameter, value });
  }

  /** Report a mark selection; the host runs the manifest's filter and highlight actions. */
  select(source: string, tuples: Tuple[]): void {
    this.#send({ v: 0, type: "select", source, tuples });
  }

  resize(height: number): void {
    this.#send({ v: 0, type: "resize", height: Math.ceil(height) });
  }

  reportError(message: string): void {
    this.#send({ v: 0, type: "error", message });
  }

  #request(body: { type: "query"; query: string } | { type: "domain"; filter: string }): Promise<QueryResult> {
    const id = `r${++this.#nextId}`;
    return new Promise((resolve, reject) => {
      this.#pending.set(id, { resolve, reject });
      this.#send({ v: 0, id, ...body } as DashboardMessage);
    });
  }

  #send(message: DashboardMessage): void {
    this.#port.postMessage(message);
  }

  #receive(message: HostMessage | null): void {
    if (message === null) return;
    switch (message.type) {
      case "result": {
        const pending = this.#pending.get(message.id);
        if (!pending) return;
        this.#pending.delete(message.id);
        const { query, columns, rows, truncated } = message;
        pending.resolve({ query, columns, rows, truncated });
        return;
      }
      case "error": {
        const pending = message.id ? this.#pending.get(message.id) : undefined;
        if (pending && message.id) {
          this.#pending.delete(message.id);
          pending.reject(new QueryError(message.code, message.message));
        } else {
          this.dispatchEvent(new CustomEvent("error", { detail: message }));
        }
        return;
      }
      case "state":
        this.state = message.state;
        this.#cache.clear();
        this.dispatchEvent(new CustomEvent("state", { detail: message.state }));
        return;
      default:
        return;
    }
  }
}

declare global {
  interface Window {
    tabfree?: TabfreeClient;
    tabfreeReady?: Promise<TabfreeClient>;
  }
}

let ready: Promise<TabfreeClient> | null = null;

/** The frame's single client, connected on first use. */
export function client(): Promise<TabfreeClient> {
  if (ready === null) {
    ready = TabfreeClient.connect();
  }
  return ready;
}

/** For tests: forget the connected client. */
export function resetClient(next: Promise<TabfreeClient> | null = null): void {
  ready = next;
}
