/**
 * Collects tool registrations once and builds a fresh McpServer from them on demand.
 *
 * An McpServer can be connected to exactly one transport. HTTP mode used to connect the
 * one global server to every new session's transport: MCP SDK <= 1.25.3 silently rewired
 * it to the newest session, so a response could reach the wrong client (GHSA-345p-7cg4-v4c7),
 * and newer SDKs refuse the second connect outright. So index.ts registers its tools here
 * and asks for one server per connection: stdio builds one, HTTP builds one per session.
 */
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";

export class ToolRegistry {
  private readonly registrations: unknown[][] = [];

  /** Same signature as McpServer.tool, so every handler keeps its inferred argument types. */
  readonly tool = ((...args: unknown[]) => {
    this.registrations.push(args);
  }) as McpServer["tool"];

  get size(): number {
    return this.registrations.length;
  }

  /** A new server with every registered tool, ready for exactly one transport. */
  createServer(info: { name: string; version: string }): McpServer {
    const server = new McpServer(info);
    const register = server.tool.bind(server) as (...args: unknown[]) => unknown;
    for (const args of this.registrations) register(...args);
    return server;
  }
}
