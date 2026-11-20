import { beforeAll, describe, expect, it } from "vitest";
import { setLocaleSetting, t } from "./i18n";
import { CATALOGUE, catalogueIdFor, plans, presetFor, providersFor, unavailableLine } from "./providers";
import type { Capability, CatalogueProvider } from "./types";

/** a catalogue entry with just the fields the list functions read */
function fake(id: string, name: string, capabilities: Capability[], regions: Array<"cn" | "global"> = ["cn", "global"]): CatalogueProvider {
  return {
    id,
    name,
    name_zh: name,
    protocol: "openai",
    base_url: `https://${id}.example.com/v1`,
    key_url: `https://${id}.example.com/key`,
    auth: ["key"],
    regions,
    capabilities,
    defaults: {},
    note: "",
    note_zh: "",
    verified: "2026-11",
  };
}

describe("the own-key catalogue (contract C11)", () => {
  // the sentences below are the English ones; `t` follows the machine's language otherwise
  beforeAll(() => setLocaleSetting("en"));

  it("is the runtime's file, slimmed to the local servers and custom, chat on every entry", () => {
    expect(CATALOGUE.map((p) => p.id).sort()).toEqual(["custom", "lm-studio", "ollama", "vllm"]);
    for (const p of CATALOGUE) expect(p.capabilities, p.id).toContain("chat");
    expect(CATALOGUE.find((p) => p.id === "vllm")?.capabilities).toEqual(["chat"]);
  });

  it("lists no cloud provider: the local servers and custom are not in the list", () => {
    expect(providersFor("cn")).toEqual([]);
    expect(providersFor("global")).toEqual([]);
    expect(providersFor("global", "image")).toEqual([]);
    expect(providersFor("cn", "video")).toEqual([]);
  });

  it("writes the one sentence without a list when no provider has the capability", () => {
    expect(unavailableLine(t, "image", "global", "en")).toBe("Pictures need a provider with image models.");
    expect(unavailableLine(t, "video", "cn", "en")).toBe("Clips need a provider with video models.");
    expect(unavailableLine(t, "vision", "global", "en")).toBe("The hands need a model that sees pictures.");
    expect(unavailableLine(t, "chat", "global", "en")).toBe("Chat needs a model.");
  });

  it("writes the one sentence with the region's providers, four at most, when a list is given", () => {
    const list = [
      fake("acme", "Acme", ["chat", "vision", "image", "video"]),
      fake("globex", "Globex", ["chat", "image"]),
      fake("initech", "Initech", ["chat", "image"]),
      fake("umbrella", "Umbrella", ["chat", "image"]),
      fake("hooli", "Hooli", ["chat", "image"]),
    ];
    const line = unavailableLine(t, "image", "global", "en", list);
    expect(line).toMatch(/^Pictures need a provider with image models: .+ or .+\.$/);
    expect(line.split(", ").length).toBeLessThanOrEqual(4);
    expect(unavailableLine(t, "video", "cn", "en", list)).toBe("Clips need a provider with video models: Acme.");
  });

  it("maps the catalogue's ids to the runtime's presets and back", () => {
    expect(presetFor("ollama")).toBe("ollama");
    expect(presetFor("vllm")).toBe("vllm");
    expect(presetFor("custom")).toBe("custom");
    expect(catalogueIdFor("ollama")).toBe("ollama");
    expect(catalogueIdFor("vllm")).toBe("vllm");
  });

  it("knows no plans: the catalogue holds local servers only", () => {
    expect(plans()).toEqual([]);
  });
});
